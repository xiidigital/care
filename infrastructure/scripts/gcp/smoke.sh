#!/usr/bin/env bash
# Non-destructive smoke verification for a deployed CARE environment.
#
# ADR-0008 section 45: after a production deployment, verify — but create no
# persistent state. Everything here is a read or an anonymous request that the
# environment already serves. Nothing is enqueued, nothing is written to a
# bucket, no Job is executed and no counter is deliberately exhausted.
#
# The heavier checks belong to staging acceptance (acceptance.sh), which is
# allowed synthetic state. If a property can only be proven by creating data,
# it is proven in staging on the same digest.
#
# Usage:
#   CARE_ENVIRONMENT=prod GCP_PROJECT_ID=... GCP_REGION=us-central1 \
#     infrastructure/scripts/gcp/smoke.sh --digest sha256:...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

DIGEST_INPUT=""
SOURCE_SHA="${CARE_SOURCE_SHA:-}"

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --digest)     DIGEST_INPUT="$2"; shift 2 ;;
    --source-sha) SOURCE_SHA="$2"; shift 2 ;;
    -h|--help)    usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

care_load_environment
care_require_command curl

DIGEST=""
if [ -n "$DIGEST_INPUT" ]; then
  DIGEST="$(care_normalize_digest "$DIGEST_INPUT")"
fi

care_phase "Smoke target"
care_print_environment
[ -n "$DIGEST" ] && echo "    expected digest      ${DIGEST}"

API_URL="$(care_service_url "$CARE_API_SERVICE")"
[ -n "$API_URL" ] || care_abort "could not resolve the URL of ${CARE_API_SERVICE}"

care_phase "API"
PING_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${API_URL}/ping/" || echo 000)"
if [ "$PING_STATUS" = "200" ]; then
  care_ok "API /ping/ 200"
else
  care_fail "API /ping/ returned ${PING_STATUS}"
fi

HEALTH_BODY="$(curl -s --max-time 30 "${API_URL}/health/" || echo '')"
if printf '%s' "$HEALTH_BODY" | grep -q '"name": "Database"'; then
  if printf '%s' "$HEALTH_BODY" | grep -qE '"code": [45][0-9][0-9]'; then
    care_fail "health reported a failing check: ${HEALTH_BODY}"
  else
    care_ok "health checks report OK"
  fi
else
  care_fail "health endpoint did not return the expected checks"
fi

VERSION_BODY="$(curl -s --max-time 30 "${API_URL}/app_version/" || echo '')"
REPORTED_VERSION="$(printf '%s' "$VERSION_BODY" | sed -n 's/.*"version": "\([^"]*\)".*/\1/p')"
if [ -n "$REPORTED_VERSION" ]; then
  care_ok "API reports version ${REPORTED_VERSION}"
  if [ -n "$SOURCE_SHA" ] && [ "$REPORTED_VERSION" != "$SOURCE_SHA" ]; then
    care_fail "API reports ${REPORTED_VERSION}, expected the promoted commit ${SOURCE_SHA}"
  fi
else
  care_fail "API did not report a version"
fi

care_phase "Deployed digest"
if [ -n "$DIGEST" ]; then
  care_verify_service_digest "$CARE_API_SERVICE" "$DIGEST" || true
  care_verify_service_digest "$CARE_WORKER_SERVICE" "$DIGEST" || true
  care_verify_job_digest "$CARE_INIT_JOB" "$DIGEST" || true
else
  care_warn "no digest given; deployed image is $(care_service_image "$CARE_API_SERVICE")"
fi

care_phase "Worker isolation"
care_verify_worker_private || true

# An explicit empty body, because Cloud Run's frontend answers a POST with no
# Content-Length with 411 before Django ever sees the request -- which would
# prove nothing about which routes this role registers.
API_TASK_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
  -X POST -H 'Content-Type: application/json' --data '' \
  "${API_URL}/internal/tasks/execute/" || echo 000)"
if [ "$API_TASK_STATUS" = "404" ]; then
  care_ok "API does not serve the task endpoint (404)"
else
  care_fail "API answered the task endpoint with ${API_TASK_STATUS}; expected 404"
fi

care_summary "### Smoke verification — ${CARE_ENVIRONMENT}"
care_summary ""
care_summary "- digest: \`${DIGEST:-unspecified}\`"
care_summary "- api: ${API_URL}"
care_summary "- version reported: \`${REPORTED_VERSION:-unknown}\`"
care_summary "- failures: ${CARE_FAILURES}"

if [ "$CARE_FAILURES" -gt 0 ]; then
  care_abort "${CARE_FAILURES} smoke check(s) failed"
fi

echo ""
echo "==> Smoke verification passed"
