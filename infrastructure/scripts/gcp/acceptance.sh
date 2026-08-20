#!/usr/bin/env bash
# Staging acceptance for a deployed CARE environment.
#
# ADR-0008 section 44 separates this from a health check. `/ping/` answers
# whether a process is up; acceptance answers whether the release satisfies the
# architecture the previous phases established — that the worker is private,
# that Cloud Tasks reaches it, that storage round-trips, that the cache and the
# rate limiter are PostgreSQL, that no Redis is in the composition, and that
# every role runs the digest that was asked for.
#
# It runs against the running environment. Nothing here is provable locally,
# which is why it exists as a stage rather than as a test (ADR-0008 section 30).
#
# A failed required check exits non-zero, and the digest is then not promotable
# (ES-08 section 41).
#
# Usage:
#   CARE_ENVIRONMENT=staging GCP_PROJECT_ID=... GCP_REGION=us-central1 \
#     infrastructure/scripts/gcp/acceptance.sh --digest sha256:...
#
#   --digest        verify every role runs this digest (recommended)
#   --source-sha    verify /app_version/ reports this commit
#   --skip-tasks    skip the Cloud Tasks dispatch check
#   --skip-storage  skip the Cloud Storage round trip
#   --skip-jobs     skip the scheduled-Job execution check
#
# The skips exist for an identity that deliberately lacks those permissions
# (ES-08 section 117), not to make a red run green.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

DIGEST_INPUT=""
SOURCE_SHA="${CARE_SOURCE_SHA:-}"
SKIP_TASKS=0
SKIP_STORAGE=0
SKIP_JOBS=0
TASK_NAME="${CARE_ACCEPTANCE_TASK:-cleanup_expired_token_slots}"
TASK_WAIT="${CARE_ACCEPTANCE_TASK_WAIT:-180}"

usage() {
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --digest)       DIGEST_INPUT="$2"; shift 2 ;;
    --source-sha)   SOURCE_SHA="$2"; shift 2 ;;
    --skip-tasks)   SKIP_TASKS=1; shift ;;
    --skip-storage) SKIP_STORAGE=1; shift ;;
    --skip-jobs)    SKIP_JOBS=1; shift ;;
    -h|--help)      usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

care_load_environment
care_require_command curl

DIGEST=""
if [ -n "$DIGEST_INPUT" ]; then
  DIGEST="$(care_normalize_digest "$DIGEST_INPUT")"
fi

care_phase "Acceptance target"
care_print_environment
[ -n "$DIGEST" ] && echo "    expected digest      ${DIGEST}"
[ -n "$SOURCE_SHA" ] && echo "    expected commit      ${SOURCE_SHA}"

API_URL="$(care_service_url "$CARE_API_SERVICE")"
[ -n "$API_URL" ] || care_abort "could not resolve the URL of ${CARE_API_SERVICE}"

RESULTS=()
record() { RESULTS+=("$1|$2|$3"); }

# ---------------------------------------------------------------------------
# 1. API liveness
# ---------------------------------------------------------------------------

care_phase "API"

# Wait for the URL to serve before asserting anything about it.
#
# A Cloud Run revision reporting Ready is not the same as the service URL
# routing to it: the deployment reported `care-staging-api is ready` and this
# check, seventeen seconds later, got a 500 from Google's frontend rather than
# from Django -- the signature of no healthy instance behind the URL yet. The
# API was serving normally by the time anyone looked, so the acceptance was
# reporting on its own timing rather than on the deployment.
#
# Bounded, and it does not decide anything: if the API never answers, the
# assertion below still runs and still fails, with the status it actually got.
# The loop only removes the race.
API_READY_TIMEOUT="${API_READY_TIMEOUT:-180}"
api_deadline=$((SECONDS + API_READY_TIMEOUT))
while [ "$SECONDS" -lt "$api_deadline" ]; do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${API_URL}/ping/" || echo 000)" = "200" ]; then
    break
  fi
  sleep 5
done

PING_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${API_URL}/ping/" || echo 000)"
if [ "$PING_STATUS" = "200" ]; then
  care_ok "API /ping/ 200"
  record "api-ping" "pass" "200"
else
  care_fail "API /ping/ returned ${PING_STATUS}"
  record "api-ping" "FAIL" "$PING_STATUS"
fi

# ---------------------------------------------------------------------------
# 2. Dependency health, and what the cache actually is
#
# /health/ is composed from the runtime role and the selected backends
# (config/health.py), so its body is evidence about the deployed composition and
# not only about liveness. `backend: postgres` and `table: care_cache` are the
# ADR-0004 cache; a Redis backend here would mean the Redis-free profile was
# not what got deployed.
# ---------------------------------------------------------------------------

HEALTH_BODY="$(curl -s --max-time 30 "${API_URL}/health/" || echo '')"
if printf '%s' "$HEALTH_BODY" | grep -q '"name": "Database"' && \
   printf '%s' "$HEALTH_BODY" | grep -q '"name": "Cache"'; then
  if printf '%s' "$HEALTH_BODY" | grep -qE '"code": 200.*"code": 200'; then
    care_ok "health: Database and Cache both report 200"
    record "health" "pass" "database+cache 200"
  else
    care_fail "health reported a non-200 check: ${HEALTH_BODY}"
    record "health" "FAIL" "non-200 check"
  fi

  if printf '%s' "$HEALTH_BODY" | grep -q '"backend": "postgres"'; then
    CACHE_TABLE="$(printf '%s' "$HEALTH_BODY" | sed -n 's/.*"table": "\([^"]*\)".*/\1/p')"
    care_ok "cache backend is postgres (table ${CACHE_TABLE:-unknown})"
    record "cache-postgres" "pass" "${CACHE_TABLE:-unknown}"
  else
    care_fail "cache backend is not postgres: ${HEALTH_BODY}"
    record "cache-postgres" "FAIL" "not postgres"
  fi
else
  care_fail "health endpoint did not return the expected checks: ${HEALTH_BODY}"
  record "health" "FAIL" "unexpected body"
fi

# ---------------------------------------------------------------------------
# 3. Build identity
# ---------------------------------------------------------------------------

VERSION_BODY="$(curl -s --max-time 30 "${API_URL}/app_version/" || echo '')"
REPORTED_VERSION="$(printf '%s' "$VERSION_BODY" | sed -n 's/.*"version": "\([^"]*\)".*/\1/p')"
if [ -n "$REPORTED_VERSION" ]; then
  care_ok "API reports version ${REPORTED_VERSION}"
  record "app-version" "pass" "$REPORTED_VERSION"
  # Prefix either way, because APP_VERSION is a build argument and the two
  # publication paths abbreviate differently: CI passes the full commit sha,
  # publish-image.sh defaults to twelve characters. Both identify one commit.
  if [ -n "$SOURCE_SHA" ]; then
    case "$SOURCE_SHA" in
      "$REPORTED_VERSION"*) care_ok "reported version identifies the expected commit" ;;
      *)
        case "$REPORTED_VERSION" in
          "$SOURCE_SHA"*) care_ok "reported version identifies the expected commit" ;;
          *)
            care_fail "API reports version ${REPORTED_VERSION}, expected the source commit ${SOURCE_SHA}"
            record "app-version-match" "FAIL" "$REPORTED_VERSION"
            ;;
        esac
        ;;
    esac
  fi
else
  care_fail "API did not report a version"
  record "app-version" "FAIL" "no version"
fi

# ---------------------------------------------------------------------------
# 4. Same digest everywhere (ES-08 section 49)
# ---------------------------------------------------------------------------

if [ -n "$DIGEST" ]; then
  care_phase "Deployed digest"
  care_verify_service_digest "$CARE_API_SERVICE" "$DIGEST" && record "digest-api" "pass" "$DIGEST" || record "digest-api" "FAIL" "mismatch"
  care_verify_service_digest "$CARE_WORKER_SERVICE" "$DIGEST" && record "digest-worker" "pass" "$DIGEST" || record "digest-worker" "FAIL" "mismatch"
  care_verify_job_digest "$CARE_INIT_JOB" "$DIGEST" && record "digest-init" "pass" "$DIGEST" || record "digest-init" "FAIL" "mismatch"

  IFS=',' read -r -a JOBS <<< "${CARE_APP_JOBS}"
  for job in "${JOBS[@]}"; do
    job="$(printf '%s' "$job" | tr -d '[:space:]')"
    [ -n "$job" ] || continue
    care_verify_job_digest "$job" "$DIGEST" && record "digest-${job}" "pass" "$DIGEST" || record "digest-${job}" "FAIL" "mismatch"
  done
fi

# ---------------------------------------------------------------------------
# 5. The worker IAM boundary, and route isolation
# ---------------------------------------------------------------------------

care_phase "Worker isolation"
if care_verify_worker_private; then
  record "worker-private" "pass" "anonymous invocation rejected"
else
  record "worker-private" "FAIL" "worker is reachable anonymously"
fi

# An explicit empty body, because Cloud Run's frontend answers a POST with no
# Content-Length with 411 before Django ever sees the request -- which would
# prove nothing about which routes this role registers.
API_TASK_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
  -X POST -H 'Content-Type: application/json' --data '' \
  "${API_URL}/internal/tasks/execute/" || echo 000)"
if [ "$API_TASK_STATUS" = "404" ]; then
  care_ok "API does not serve the task endpoint (404)"
  record "route-isolation" "pass" "404"
else
  care_fail "API answered the task endpoint with ${API_TASK_STATUS}; expected 404"
  record "route-isolation" "FAIL" "$API_TASK_STATUS"
fi

# ---------------------------------------------------------------------------
# 6. Runtime composition, read from the deployment (ES-08 sections 89, 161)
#
# Names and selections only. No secret value is read, and none is printed: the
# runtime reads those from Secret Manager and the deployment identity has no
# business seeing them.
# ---------------------------------------------------------------------------

care_phase "Runtime composition"
read_env() {
  gcloud run services describe "$1" \
    --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format="value(spec.template.spec.containers[0].env.filter(\"name:$2\").extract(\"value\").flatten())" 2>/dev/null || true
}

expect_env() {
  local service="$1" name="$2" expected="$3" actual
  actual="$(read_env "$service" "$name")"
  if [ "$actual" = "$expected" ]; then
    care_ok "${service}: ${name}=${actual}"
    record "env-${service}-${name}" "pass" "$actual"
  else
    care_fail "${service}: ${name}=${actual:-<unset>}, expected ${expected}"
    record "env-${service}-${name}" "FAIL" "${actual:-unset}"
  fi
}

expect_env "$CARE_API_SERVICE" "CARE_PROCESS_ROLE" "api"
expect_env "$CARE_WORKER_SERVICE" "CARE_PROCESS_ROLE" "task_worker"
expect_env "$CARE_API_SERVICE" "CARE_CACHE_BACKEND" "postgres"
expect_env "$CARE_API_SERVICE" "CARE_RATE_LIMIT_BACKEND" "postgres"
expect_env "$CARE_API_SERVICE" "CARE_TASK_BACKEND" "cloud_tasks"
expect_env "$CARE_API_SERVICE" "CARE_STORAGE_BACKEND" "gcs"

# Redis-free is a property of what is deployed, not of what was intended. A
# REDIS_URL in the composition would mean something reintroduced it
# (ADR-0008 section 31).
for service in "$CARE_API_SERVICE" "$CARE_WORKER_SERVICE"; do
  REDIS_VALUE="$(read_env "$service" "REDIS_URL")"
  if [ -z "$REDIS_VALUE" ]; then
    care_ok "${service}: no REDIS_URL in the composition"
    record "redis-free-${service}" "pass" "absent"
  else
    care_fail "${service}: REDIS_URL is set; the Redis-free profile is not what is deployed"
    record "redis-free-${service}" "FAIL" "present"
  fi
done

REDIS_INSTANCES="$(gcloud redis instances list --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --format='value(name)' 2>/dev/null || true)"
if [ -z "$REDIS_INSTANCES" ]; then
  care_ok "no Memorystore instance exists in ${GCP_REGION}"
  record "redis-free-infra" "pass" "none"
else
  care_warn "Memorystore instances exist in this project/region: ${REDIS_INSTANCES}"
  record "redis-free-infra" "warn" "$REDIS_INSTANCES"
fi

EMAIL_BACKEND="$(read_env "$CARE_WORKER_SERVICE" "DJANGO_EMAIL_BACKEND")"
care_ok "worker email backend: ${EMAIL_BACKEND:-<Django default>}"
record "email-backend" "pass" "${EMAIL_BACKEND:-django default}"

# ---------------------------------------------------------------------------
# 7. PostgreSQL rate limiting (ES-08 section 174)
#
# Sequential requests only. The PostgreSQL counter is best-effort by decision
# (RF2): it does not promise the Redis backend's concurrency guarantee, and
# acceptance must not fail for the absence of a property nobody claimed.
#
# The endpoint is the password-reset token check with a random token: it is
# anonymous, it is limited at 10/h on (token, ip), and an invalid token creates
# nothing. The counters expire on their own.
# ---------------------------------------------------------------------------

care_phase "Rate limiting"
RL_TOKEN="acceptance-$(date +%s)-$RANDOM"
RL_LIMITED=0
for _ in $(seq 1 12); do
  RL_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H 'Content-Type: application/json' \
    -d "{\"token\": \"${RL_TOKEN}\"}" \
    "${API_URL}/api/v1/password_reset/check/" || echo 000)"
  if [ "$RL_STATUS" = "429" ]; then
    RL_LIMITED=1
    break
  fi
done

if [ "$RL_LIMITED" -eq 1 ]; then
  care_ok "rate limiting is counting (429 after repeated sequential requests)"
  record "rate-limit" "pass" "429"
else
  care_fail "no 429 after 12 sequential requests; the PostgreSQL rate-limit store is not counting"
  record "rate-limit" "FAIL" "no 429"
fi

# ---------------------------------------------------------------------------
# 8. Cloud Tasks dispatch (ES-08 section 171)
#
# The real path: a task enqueued on the environment's queue, delivered with an
# OIDC token minted for the invoker identity, accepted by Cloud Run IAM, and
# executed by the worker. The task is a registered maintenance operation with an
# empty payload, so it creates nothing and deletes only what has already
# expired.
# ---------------------------------------------------------------------------

if [ "$SKIP_TASKS" -eq 0 ]; then
  care_phase "Cloud Tasks dispatch"
  WORKER_URL="$(care_service_url "$CARE_WORKER_SERVICE")"
  INVOKER_SA="${CARE_TASKS_INVOKER_SA:-${CARE_NAME_PREFIX}-tasks-inv@${GCP_PROJECT_ID}.iam.gserviceaccount.com}"
  DISPATCH_MARK="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if gcloud tasks create-http-task \
       --queue "$CARE_TASKS_QUEUE" \
       --project "$GCP_PROJECT_ID" --location "$GCP_REGION" \
       --url "${WORKER_URL}/internal/tasks/execute/" \
       --method POST \
       --header "Content-Type: application/json" \
       --oidc-service-account-email "$INVOKER_SA" \
       --oidc-token-audience "$WORKER_URL" \
       --body-content "{\"version\": 1, \"task\": \"${TASK_NAME}\", \"payload\": {}}" \
       --quiet >/dev/null 2>&1; then
    care_ok "task ${TASK_NAME} enqueued on ${CARE_TASKS_QUEUE}"

    EXECUTED=0
    DEADLINE=$((SECONDS + TASK_WAIT))
    while [ "$SECONDS" -lt "$DEADLINE" ]; do
      if gcloud logging read \
           "resource.type=cloud_run_revision AND resource.labels.service_name=${CARE_WORKER_SERVICE} AND timestamp>=\"${DISPATCH_MARK}\" AND textPayload:\"${TASK_NAME}\"" \
           --project "$GCP_PROJECT_ID" --limit 1 --format='value(textPayload)' 2>/dev/null | grep -q .; then
        EXECUTED=1
        break
      fi
      sleep 10
    done

    if [ "$EXECUTED" -eq 1 ]; then
      care_ok "worker executed ${TASK_NAME}"
      record "cloud-tasks" "pass" "$TASK_NAME"
    else
      care_fail "no worker log entry for ${TASK_NAME} within ${TASK_WAIT}s"
      record "cloud-tasks" "FAIL" "not observed"
    fi
  else
    care_fail "could not enqueue a task on ${CARE_TASKS_QUEUE}; check cloudtasks.tasks.create and iam.serviceAccountUser on ${INVOKER_SA}"
    record "cloud-tasks" "FAIL" "enqueue refused"
  fi
else
  care_warn "Cloud Tasks dispatch check skipped"
  record "cloud-tasks" "skip" "requested"
fi

# ---------------------------------------------------------------------------
# 9. Cloud Storage round trip (ES-08 section 173)
#
# One small object, written, read back, compared and deleted. Bucket contents
# are never listed or printed.
# ---------------------------------------------------------------------------

if [ "$SKIP_STORAGE" -eq 0 ]; then
  care_phase "Cloud Storage"
  BUCKET="${CARE_ACCEPTANCE_BUCKET:-}"

  # Ask the deployment which bucket it uses, rather than searching the project
  # for one. The name is on the running service (config.tf sets
  # CARE_FACILITY_STORAGE_BUCKET), and reading a service description is a
  # permission this identity already exercises for every digest and composition
  # check above. Listing buckets project-wide is a broader grant, and ES-08
  # section 117 says not to widen an identity to make a check pass.
  if [ -z "$BUCKET" ]; then
    BUCKET="$(read_env "$CARE_API_SERVICE" "CARE_FACILITY_STORAGE_BUCKET")"
  fi

  # Last resort, and explicitly tolerant of failure. Without `|| true` a denied
  # or empty list aborts the whole script under `set -euo pipefail`, before the
  # care_fail below can report anything -- which is how a real run ended at
  # "==> Cloud Storage" with exit 1 and no message.
  if [ -z "$BUCKET" ]; then
    BUCKET="$(gcloud storage buckets list --project "$GCP_PROJECT_ID" \
      --filter="name:${CARE_NAME_PREFIX}-facility" --format='value(name)' 2>/dev/null | head -1 || true)"
  fi

  if [ -z "$BUCKET" ]; then
    care_fail "could not resolve a bucket for ${CARE_NAME_PREFIX}; set CARE_ACCEPTANCE_BUCKET"
    record "gcs" "FAIL" "no bucket"
  else
    OBJECT="acceptance/$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
    PAYLOAD="care acceptance $(date -u +%s) $$"
    TMP_IN="$(mktemp)"; TMP_OUT="$(mktemp)"
    printf '%s' "$PAYLOAD" > "$TMP_IN"

    if gcloud storage cp "$TMP_IN" "gs://${BUCKET}/${OBJECT}" --quiet >/dev/null 2>&1 &&
       gcloud storage cp "gs://${BUCKET}/${OBJECT}" "$TMP_OUT" --quiet >/dev/null 2>&1 &&
       [ "$(cat "$TMP_OUT")" = "$PAYLOAD" ]; then
      care_ok "object round trip through gs://${BUCKET} matched byte for byte"
      record "gcs" "pass" "round trip"
    else
      care_fail "object round trip through gs://${BUCKET} failed"
      record "gcs" "FAIL" "round trip"
    fi

    gcloud storage rm "gs://${BUCKET}/${OBJECT}" --quiet >/dev/null 2>&1 || \
      care_warn "could not delete the acceptance object gs://${BUCKET}/${OBJECT}; remove it manually"
    rm -f "$TMP_IN" "$TMP_OUT"
  fi
else
  care_warn "Cloud Storage check skipped"
  record "gcs" "skip" "requested"
fi

# ---------------------------------------------------------------------------
# 10. A scheduled Job, executed on demand (ES-08 section 172)
#
# The Scheduler's own schedule is not touched. Executing the Job directly proves
# what acceptance needs to know: the Job runs this artifact and its identity can
# do its work.
# ---------------------------------------------------------------------------

if [ "$SKIP_JOBS" -eq 0 ]; then
  care_phase "Scheduled Job"
  SCHEDULED_JOB="${CARE_ACCEPTANCE_JOB:-${CARE_NAME_PREFIX}-cleanup-token-slots}"
  if gcloud run jobs execute "$SCHEDULED_JOB" \
       --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --wait --quiet >/dev/null 2>&1; then
    care_ok "${SCHEDULED_JOB} executed successfully"
    record "scheduled-job" "pass" "$SCHEDULED_JOB"
  else
    care_fail "${SCHEDULED_JOB} execution failed"
    record "scheduled-job" "FAIL" "$SCHEDULED_JOB"
  fi

  SCHEDULER_JOBS="$(gcloud scheduler jobs list --project "$GCP_PROJECT_ID" --location "$GCP_REGION" \
    --format='value(name)' 2>/dev/null | grep "${CARE_NAME_PREFIX}" || true)"
  if [ -n "$SCHEDULER_JOBS" ]; then
    care_ok "Cloud Scheduler jobs present for ${CARE_NAME_PREFIX}"
    record "scheduler" "pass" "present"
  else
    care_warn "no Cloud Scheduler jobs found for ${CARE_NAME_PREFIX}"
    record "scheduler" "warn" "none found"
  fi
else
  care_warn "scheduled Job check skipped"
  record "scheduled-job" "skip" "requested"
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

care_phase "Acceptance result"
printf '%-34s %-6s %s\n' "CHECK" "RESULT" "DETAIL"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r name result detail <<< "$row"
  printf '%-34s %-6s %s\n' "$name" "$result" "$detail"
done

care_summary "### Staging acceptance — ${CARE_ENVIRONMENT}"
care_summary ""
care_summary "| check | result | detail |"
care_summary "| --- | --- | --- |"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r name result detail <<< "$row"
  care_summary "| ${name} | ${result} | ${detail} |"
done
care_summary ""
care_summary "digest: \`${DIGEST:-not verified}\`"

if [ "$CARE_FAILURES" -gt 0 ]; then
  care_summary ""
  care_summary "**Acceptance failed.** This digest is not eligible for promotion."
  care_abort "${CARE_FAILURES} acceptance check(s) failed; this digest is not promotable"
fi

echo ""
echo "==> Acceptance passed for ${DIGEST:-the deployed artifact}"
care_output "acceptance" "passed"
