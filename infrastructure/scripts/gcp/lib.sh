#!/usr/bin/env bash
# Shared helpers for the GCP deployment adapter.
#
# ADR-0008 section 7 makes environment deployment an adapter around a common
# application artifact: the build knows nothing about Cloud Run, and everything
# that does know about Cloud Run lives here and in the scripts that source this
# file. Nothing in this directory builds an image.
#
# Sourced, never executed. Every function is prefixed `care_` because a sourced
# file shares the caller's namespace.
#
# CONFIGURATION
#
# Resource identifiers come from the environment, not from constants, so the
# same scripts serve staging, a production environment that does not exist yet,
# and any other installation of the same architecture (ES-08 sections 78, 123).
# Names default to the convention the OpenTofu module implements —
# care-<environment>-<resource> — and every one of them can be overridden when
# an installation does not follow it.
#
#   CARE_ENVIRONMENT   required   logical environment: staging, prod, ...
#   GCP_PROJECT_ID     required   project the environment lives in
#   GCP_REGION         required   region the environment lives in
#
#   CARE_API_SERVICE              default care-<env>-api
#   CARE_WORKER_SERVICE           default care-<env>-worker
#   CARE_INIT_JOB                 default care-<env>-init
#   CARE_APP_JOBS                 default care-<env>-cleanup-token-slots,
#                                         care-<env>-cleanup-uploads
#   CARE_ARTIFACT_REPOSITORY      default care-<env>
#   CARE_IMAGE_NAME               default care
#   CARE_TASKS_QUEUE              default care-<env>-tasks

set -euo pipefail

# ---------------------------------------------------------------------------
# Output
#
# Deployment output is read by whoever is looking at a failed run, so a phase
# is announced before it starts and a failure says which phase it was
# (ADR-0008 section 50, ES-08 section 184).
# ---------------------------------------------------------------------------

CARE_FAILURES=0

care_phase() { printf '\n==> %s\n' "$*"; }
care_ok()    { printf 'ok    %s\n' "$*"; }
care_warn()  { printf 'warn  %s\n' "$*" >&2; }

care_fail() {
  printf 'FAIL  %s\n' "$*" >&2
  CARE_FAILURES=$((CARE_FAILURES + 1))
}

care_abort() {
  printf '\nerror: %s\n' "$*" >&2
  exit 1
}

# GitHub renders this above the log, so the values a reviewer needs are visible
# without opening a job (ES-08 sections 83, 185). Harmless outside CI.
care_summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"
  fi
}

# A workflow output, when running under GitHub Actions.
care_output() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

care_require_command() {
  command -v "$1" >/dev/null 2>&1 || care_abort "$1 is required and was not found on PATH"
}

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

care_load_environment() {
  care_require_command gcloud

  [ -n "${CARE_ENVIRONMENT:-}" ] || care_abort \
    "CARE_ENVIRONMENT is not set. This is deployment target metadata and has no default: the deployment adapter must be told which environment it is acting on."
  [ -n "${GCP_PROJECT_ID:-}" ] || care_abort \
    "GCP_PROJECT_ID is not set for environment '${CARE_ENVIRONMENT}'. Configure it as a GitHub environment variable, or export it for a local run."
  [ -n "${GCP_REGION:-}" ] || care_abort \
    "GCP_REGION is not set for environment '${CARE_ENVIRONMENT}'."

  CARE_NAME_PREFIX="${CARE_NAME_PREFIX:-care-${CARE_ENVIRONMENT}}"

  CARE_API_SERVICE="${CARE_API_SERVICE:-${CARE_NAME_PREFIX}-api}"
  CARE_WORKER_SERVICE="${CARE_WORKER_SERVICE:-${CARE_NAME_PREFIX}-worker}"
  CARE_INIT_JOB="${CARE_INIT_JOB:-${CARE_NAME_PREFIX}-init}"
  CARE_APP_JOBS="${CARE_APP_JOBS:-${CARE_NAME_PREFIX}-cleanup-token-slots,${CARE_NAME_PREFIX}-cleanup-uploads}"
  CARE_ARTIFACT_REPOSITORY="${CARE_ARTIFACT_REPOSITORY:-${CARE_NAME_PREFIX}}"
  CARE_IMAGE_NAME="${CARE_IMAGE_NAME:-care}"
  CARE_TASKS_QUEUE="${CARE_TASKS_QUEUE:-${CARE_NAME_PREFIX}-tasks}"

  CARE_IMAGE_REPOSITORY="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${CARE_ARTIFACT_REPOSITORY}/${CARE_IMAGE_NAME}"

  export CARE_NAME_PREFIX CARE_API_SERVICE CARE_WORKER_SERVICE CARE_INIT_JOB \
    CARE_APP_JOBS CARE_ARTIFACT_REPOSITORY CARE_IMAGE_NAME CARE_TASKS_QUEUE \
    CARE_IMAGE_REPOSITORY
}

care_print_environment() {
  cat <<EOF
    environment          ${CARE_ENVIRONMENT}
    project              ${GCP_PROJECT_ID}
    region               ${GCP_REGION}
    api service          ${CARE_API_SERVICE}
    worker service       ${CARE_WORKER_SERVICE}
    init job             ${CARE_INIT_JOB}
    application jobs     ${CARE_APP_JOBS}
    image repository     ${CARE_IMAGE_REPOSITORY}
EOF
}

# ---------------------------------------------------------------------------
# Artifact identity
#
# A digest, always. A tag is a label that can be moved after it was read, so it
# is not evidence that two environments run the same artifact (ADR-0008
# section 4).
# ---------------------------------------------------------------------------

care_normalize_digest() {
  local input="$1"
  case "$input" in
    sha256:*) printf '%s' "$input" ;;
    *@sha256:*) printf '%s' "${input#*@}" ;;
    *) care_abort "'${input}' is not an image digest. Pass sha256:<hex> or <repository>@sha256:<hex>; a tag is not an artifact identity (ADR-0008 section 4)." ;;
  esac
}

care_image_ref() {
  printf '%s@%s' "$CARE_IMAGE_REPOSITORY" "$1"
}

# The digest must already exist in the registry. A deployment that would create
# it is a build, and this adapter does not build (ES-08 section 30).
care_require_digest_exists() {
  local digest="$1"
  local ref
  ref="$(care_image_ref "$digest")"

  if gcloud artifacts docker images describe "$ref" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
    care_ok "digest exists in ${CARE_ARTIFACT_REPOSITORY}: ${digest}"
    return 0
  fi

  care_abort "digest not found: ${ref}
       The deployment adapter never builds. Publish the image first, or pass a digest that exists in this environment's Artifact Registry repository."
}

# ---------------------------------------------------------------------------
# Deployed state
#
# What a service or job is actually running, read back from the platform. A
# successful `gcloud run ... update` is not evidence that the requested digest
# is serving (ES-08 section 87).
# ---------------------------------------------------------------------------

care_service_image() {
  gcloud run services describe "$1" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(spec.template.spec.containers[0].image)' 2>/dev/null || true
}

care_job_image() {
  gcloud run jobs describe "$1" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(spec.template.spec.template.spec.containers[0].image)' 2>/dev/null || true
}

care_service_url() {
  gcloud run services describe "$1" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(status.url)' 2>/dev/null || true
}

care_service_revision() {
  gcloud run services describe "$1" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(status.latestReadyRevisionName)' 2>/dev/null || true
}

care_verify_service_digest() {
  local service="$1" digest="$2" actual
  actual="$(care_service_image "$service")"
  if [ "$actual" = "$(care_image_ref "$digest")" ]; then
    care_ok "${service} runs the requested digest"
    return 0
  fi
  care_fail "${service} runs ${actual:-<unknown>}, expected $(care_image_ref "$digest")"
  return 1
}

care_verify_job_digest() {
  local job="$1" digest="$2" actual
  actual="$(care_job_image "$job")"
  if [ "$actual" = "$(care_image_ref "$digest")" ]; then
    care_ok "${job} runs the requested digest"
    return 0
  fi
  care_fail "${job} runs ${actual:-<unknown>}, expected $(care_image_ref "$digest")"
  return 1
}

# Cloud Run's own readiness, not an HTTP probe: a service can answer while an
# older revision still serves traffic.
care_wait_service_ready() {
  local service="$1" timeout="${2:-600}"
  local deadline=$((SECONDS + timeout))
  local status

  while [ "$SECONDS" -lt "$deadline" ]; do
    status="$(gcloud run services describe "$service" \
      --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      --format='value(status.conditions.filter("type:Ready").extract("status").flatten())' 2>/dev/null || true)"
    case "$status" in
      True) care_ok "${service} is ready (revision $(care_service_revision "$service"))"; return 0 ;;
      False)
        care_fail "${service} reported Ready=False"
        gcloud run services describe "$service" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
          --format='value(status.conditions)' >&2 || true
        return 1
        ;;
    esac
    sleep 5
  done

  care_fail "${service} did not become ready within ${timeout}s"
  return 1
}

# ---------------------------------------------------------------------------
# The worker IAM boundary
#
# The task endpoint carries no application-layer authentication by design
# (ES-06), so Cloud Run IAM is the whole boundary. Release automation must not
# be able to weaken it without the deployment failing (ES-08 section 88).
# ---------------------------------------------------------------------------

care_verify_worker_private() {
  local policy anonymous status url

  policy="$(gcloud run services get-iam-policy "$CARE_WORKER_SERVICE" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(bindings.members.flatten())' 2>/dev/null || true)"

  anonymous="$(printf '%s\n' "$policy" | grep -E '^(allUsers|allAuthenticatedUsers)$' || true)"
  if [ -n "$anonymous" ]; then
    care_fail "worker IAM policy grants ${anonymous}; the task endpoint must never be publicly invocable"
    return 1
  fi
  care_ok "worker IAM policy contains no public member"

  url="$(care_service_url "$CARE_WORKER_SERVICE")"
  if [ -z "$url" ]; then
    care_fail "could not resolve the worker URL"
    return 1
  fi

  status="$(curl -s -o /dev/null -w '%{http_code}' "${url}/internal/tasks/execute/" || echo 000)"
  if [ "$status" = "403" ] || [ "$status" = "401" ]; then
    care_ok "anonymous worker invocation rejected before Django (${status})"
    return 0
  fi

  care_fail "anonymous worker invocation returned ${status}; expected 401 or 403"
  return 1
}
