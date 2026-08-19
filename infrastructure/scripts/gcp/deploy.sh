#!/usr/bin/env bash
# Deploy an already-published immutable digest to a GCP CARE environment.
#
# This is the GCP deployment adapter (ADR-0008 section 7). It consumes an
# artifact; it never produces one. There is no docker build in this file and
# there must never be one: a deployment that can build is a deployment that can
# ship something staging never saw.
#
# ORDER, AND WHY IT IS THIS ORDER
#
#   1  the digest must already exist in the registry
#   2  init Job image -> selected digest
#   3  init runs; a failure stops everything here (ADR-0008 section 11)
#   4  worker -> selected digest, ready, still private
#   5  API -> selected digest, ready
#   6  application Jobs -> selected digest
#   7  every deployed image is read back and compared with the request
#
# Init first because an API revision must not meet a schema it does not expect.
# Worker before API because the API is what produces task payloads, and a
# payload whose handler does not exist yet is a task that fails until it is
# retried into the void (ADR-0008 section 12).
#
# Usage:
#   CARE_ENVIRONMENT=staging GCP_PROJECT_ID=... GCP_REGION=us-central1 \
#     infrastructure/scripts/gcp/deploy.sh --digest sha256:...
#
#   ... --dry-run     print what would happen and change nothing
#   ... --no-jobs     leave the scheduled application Jobs on their current image
#
# Every identifier is read from the environment; see lib.sh. Nothing here names
# a project, a service or an environment.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

DIGEST_INPUT=""
DRY_RUN=0
UPDATE_JOBS=1
INIT_TIMEOUT="${CARE_INIT_TIMEOUT:-1800}"
READY_TIMEOUT="${CARE_READY_TIMEOUT:-600}"

usage() {
  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --digest|--image) DIGEST_INPUT="$2"; shift 2 ;;
    --dry-run)        DRY_RUN=1; shift ;;
    --no-jobs)        UPDATE_JOBS=0; shift ;;
    -h|--help)        usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$DIGEST_INPUT" ] || care_abort "--digest is required. Production and staging deployments select an artifact; they do not derive one from the current branch (ES-08 section 43)."

care_load_environment
DIGEST="$(care_normalize_digest "$DIGEST_INPUT")"
IMAGE_REF="$(care_image_ref "$DIGEST")"

care_phase "Deployment plan"
care_print_environment
echo "    digest               ${DIGEST}"
echo "    image reference      ${IMAGE_REF}"
echo "    update jobs          $([ "$UPDATE_JOBS" -eq 1 ] && echo yes || echo no)"

if [ "$DRY_RUN" -eq 1 ]; then
  care_phase "Dry run"
  echo "Would update, in order:"
  echo "  1. job     ${CARE_INIT_JOB}          -> ${IMAGE_REF}"
  echo "  2. execute ${CARE_INIT_JOB}"
  echo "  3. service ${CARE_WORKER_SERVICE}    -> ${IMAGE_REF}"
  echo "  4. service ${CARE_API_SERVICE}       -> ${IMAGE_REF}"
  if [ "$UPDATE_JOBS" -eq 1 ]; then
    echo "  5. jobs    ${CARE_APP_JOBS}         -> ${IMAGE_REF}"
  fi
  echo ""
  echo "Cloud Run has no server-side no-op update, so nothing beyond this is previewable."
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. The artifact must exist
# ---------------------------------------------------------------------------

care_phase "Artifact"
care_require_digest_exists "$DIGEST"

PREVIOUS_API_IMAGE="$(care_service_image "$CARE_API_SERVICE")"
PREVIOUS_WORKER_IMAGE="$(care_service_image "$CARE_WORKER_SERVICE")"
PREVIOUS_DIGEST="${PREVIOUS_API_IMAGE#*@}"

# The rollback candidate is what was actually deployed, read from the platform,
# not the previous tag and not the previous commit (ES-08 section 92).
if [ -n "$PREVIOUS_DIGEST" ] && [ "$PREVIOUS_DIGEST" != "$PREVIOUS_API_IMAGE" ]; then
  care_ok "previous API digest (rollback candidate): ${PREVIOUS_DIGEST}"
  care_output "previous_digest" "$PREVIOUS_DIGEST"
else
  care_warn "no previous digest could be read from ${CARE_API_SERVICE}; this may be the first deployment"
fi

if [ "$PREVIOUS_API_IMAGE" = "$IMAGE_REF" ] && [ "$PREVIOUS_WORKER_IMAGE" = "$IMAGE_REF" ]; then
  care_ok "environment already runs this digest; redeploying it anyway so init and verification run"
fi

# ---------------------------------------------------------------------------
# 2-3. Initialization
#
# The repository's initialization contract, invoked as the Job that carries it
# (ADR-0008 section 10). The sequence itself is scripts/initialize.sh and is not
# reimplemented here.
#
# The two markers below bound the state-changing part of the deployment: after a
# cancellation, an operator can tell from the log whether init started and
# whether it finished (ES-08 section 52).
# ---------------------------------------------------------------------------

care_phase "Initialization"
gcloud run jobs update "$CARE_INIT_JOB" \
  --image "$IMAGE_REF" \
  --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
  --quiet >/dev/null
care_ok "${CARE_INIT_JOB} image updated"
care_verify_job_digest "$CARE_INIT_JOB" "$DIGEST" || care_abort "init Job did not take the requested digest"

echo "INIT-STARTED ${CARE_INIT_JOB} $(date -u +%Y-%m-%dT%H:%M:%SZ)"

INIT_STATUS=0
timeout "$INIT_TIMEOUT" gcloud run jobs execute "$CARE_INIT_JOB" \
  --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
  --wait --quiet || INIT_STATUS=$?

INIT_EXECUTION="$(gcloud run jobs executions list \
  --job "$CARE_INIT_JOB" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
  --sort-by=~metadata.creationTimestamp --limit 1 \
  --format='value(metadata.name)' 2>/dev/null || true)"

echo "INIT-FINISHED ${INIT_EXECUTION:-<unknown>} exit=${INIT_STATUS} $(date -u +%Y-%m-%dT%H:%M:%SZ)"
care_output "init_execution" "${INIT_EXECUTION}"

if [ "$INIT_STATUS" -ne 0 ]; then
  care_summary "### Deployment failed during initialization"
  care_summary ""
  care_summary "- environment: \`${CARE_ENVIRONMENT}\`"
  care_summary "- digest: \`${DIGEST}\`"
  care_summary "- init execution: \`${INIT_EXECUTION:-unknown}\`"
  care_summary ""
  care_summary "No worker or API revision was deployed. The database may hold a partially applied migration; inspect the execution before retrying."
  care_abort "initialization failed (execution ${INIT_EXECUTION:-unknown}).
       No new worker or API revision has been deployed. This is deliberate: a
       failed initialization stops the deployment (ADR-0008 section 11).
       Inspect it with:
         gcloud run jobs executions describe ${INIT_EXECUTION:-<execution>} --project ${GCP_PROJECT_ID} --region ${GCP_REGION}"
fi

care_ok "initialization succeeded (execution ${INIT_EXECUTION:-unknown})"

# ---------------------------------------------------------------------------
# 4. Worker, before the API
# ---------------------------------------------------------------------------

care_phase "Task worker"
gcloud run services update "$CARE_WORKER_SERVICE" \
  --image "$IMAGE_REF" \
  --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
  --quiet >/dev/null
care_ok "${CARE_WORKER_SERVICE} update submitted"

care_wait_service_ready "$CARE_WORKER_SERVICE" "$READY_TIMEOUT" || care_abort "worker revision did not become ready; the API has not been touched"
care_verify_service_digest "$CARE_WORKER_SERVICE" "$DIGEST" || care_abort "worker is not running the requested digest"
care_verify_worker_private || care_abort "the worker IAM boundary is not intact; refusing to continue"

WORKER_REVISION="$(care_service_revision "$CARE_WORKER_SERVICE")"

# ---------------------------------------------------------------------------
# 5. API
# ---------------------------------------------------------------------------

care_phase "API"
gcloud run services update "$CARE_API_SERVICE" \
  --image "$IMAGE_REF" \
  --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
  --quiet >/dev/null
care_ok "${CARE_API_SERVICE} update submitted"

care_wait_service_ready "$CARE_API_SERVICE" "$READY_TIMEOUT" || care_abort "API revision did not become ready"
care_verify_service_digest "$CARE_API_SERVICE" "$DIGEST" || care_abort "API is not running the requested digest"

API_REVISION="$(care_service_revision "$CARE_API_SERVICE")"
API_URL="$(care_service_url "$CARE_API_SERVICE")"

# ---------------------------------------------------------------------------
# 6. Application Jobs
#
# The scheduled Jobs run the same artifact. Their schedules belong to
# infrastructure delivery and are not touched here (ES-08 section 38).
# ---------------------------------------------------------------------------

JOB_RESULTS="skipped"
if [ "$UPDATE_JOBS" -eq 1 ] && [ -n "$CARE_APP_JOBS" ]; then
  care_phase "Application Jobs"
  JOB_RESULTS=""
  IFS=',' read -r -a JOBS <<< "$CARE_APP_JOBS"
  for job in "${JOBS[@]}"; do
    job="$(printf '%s' "$job" | tr -d '[:space:]')"
    [ -n "$job" ] || continue
    if ! gcloud run jobs describe "$job" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" >/dev/null 2>&1; then
      care_fail "job ${job} does not exist in ${CARE_ENVIRONMENT}; CARE_APP_JOBS names a Job this environment does not have"
      continue
    fi
    gcloud run jobs update "$job" \
      --image "$IMAGE_REF" \
      --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      --quiet >/dev/null
    care_verify_job_digest "$job" "$DIGEST"
    JOB_RESULTS="${JOB_RESULTS}${job} "
  done
fi

# ---------------------------------------------------------------------------
# 7. Same-digest verification across the whole environment
# ---------------------------------------------------------------------------

care_phase "Deployed digest"
care_verify_service_digest "$CARE_API_SERVICE" "$DIGEST" || true
care_verify_service_digest "$CARE_WORKER_SERVICE" "$DIGEST" || true
care_verify_job_digest "$CARE_INIT_JOB" "$DIGEST" || true

care_output "image_digest" "$DIGEST"
care_output "image_ref" "$IMAGE_REF"
care_output "api_url" "$API_URL"
care_output "api_revision" "$API_REVISION"
care_output "worker_revision" "$WORKER_REVISION"

care_phase "Summary"
cat <<EOF
    environment          ${CARE_ENVIRONMENT}
    digest               ${DIGEST}
    init execution       ${INIT_EXECUTION:-unknown}
    worker revision      ${WORKER_REVISION}
    api revision         ${API_REVISION}
    api url              ${API_URL}
    jobs updated         ${JOB_RESULTS:-none}
    previous digest      ${PREVIOUS_DIGEST:-unknown}
EOF

care_summary "### CARE deployment — ${CARE_ENVIRONMENT}"
care_summary ""
care_summary "| field | value |"
care_summary "| --- | --- |"
care_summary "| digest | \`${DIGEST}\` |"
care_summary "| source commit | \`${CARE_SOURCE_SHA:-unknown}\` |"
care_summary "| init execution | \`${INIT_EXECUTION:-unknown}\` |"
care_summary "| worker revision | \`${WORKER_REVISION}\` |"
care_summary "| api revision | \`${API_REVISION}\` |"
care_summary "| api url | ${API_URL} |"
care_summary "| previous digest | \`${PREVIOUS_DIGEST:-unknown}\` |"

if [ "$CARE_FAILURES" -gt 0 ]; then
  care_abort "${CARE_FAILURES} deployment check(s) failed"
fi

echo ""
echo "==> Deployment complete. Acceptance has not run; it is a separate stage."
