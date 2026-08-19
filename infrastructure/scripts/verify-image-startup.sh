#!/usr/bin/env bash
# Start a CARE production image and prove it serves, without GCP.
#
# ES-08 section 99 asks for a bounded container startup verification before an
# image is published or deployed, and section 23 forbids requiring Cloud SQL,
# Redis, GCS, Cloud Tasks, Secret Manager or SMTP to build one. This script is
# where those two meet: it runs the real entrypoints against a throwaway
# PostgreSQL, in the Redis-free composition, and asserts what the roles are
# supposed to do.
#
# What it proves:
#
#   init          scripts/initialize.sh completes and exits zero
#   api           the API role answers /ping/
#   task_worker   the worker role answers /ping/ and does NOT serve the
#                 public API surface (ES-06 route isolation)
#   Redis-free    no REDIS_URL is set anywhere and nothing waits for one
#
# What it does not prove: anything about a deployed environment. That is
# acceptance, and it runs against the environment (infrastructure/scripts/gcp).
#
# Usage:
#   infrastructure/scripts/verify-image-startup.sh --image care:local
#   infrastructure/scripts/verify-image-startup.sh --image care:local \
#       --network ci --database-url postgres://care:care@db:5432/care
#
# With no --database-url the script starts a PostgreSQL container and removes it
# afterwards, so a workstation needs only docker.

set -euo pipefail

IMAGE=""
DATABASE_URL=""
NETWORK=""
KEEP=0
STARTUP_TIMEOUT=120
RUN_ID="care-verify-$$"

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)        IMAGE="$2"; shift 2 ;;
    --database-url) DATABASE_URL="$2"; shift 2 ;;
    --network)      NETWORK="$2"; shift 2 ;;
    --timeout)      STARTUP_TIMEOUT="$2"; shift 2 ;;
    --keep)         KEEP=1; shift ;;
    -h|--help)      usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$IMAGE" ] || { echo "error: --image is required" >&2; exit 1; }

OWNED_NETWORK=0
OWNED_DB=0
CONTAINERS=()

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    return 0
  fi
  for container in "${CONTAINERS[@]:-}"; do
    if [ -n "$container" ]; then
      docker rm -f "$container" >/dev/null 2>&1 || true
    fi
  done
  if [ "$OWNED_DB" -eq 1 ]; then
    docker rm -f "${RUN_ID}-db" >/dev/null 2>&1 || true
  fi
  if [ "$OWNED_NETWORK" -eq 1 ]; then
    docker network rm "${RUN_ID}-net" >/dev/null 2>&1 || true
  fi
  return 0
}
trap cleanup EXIT

if [ -z "$NETWORK" ]; then
  NETWORK="${RUN_ID}-net"
  docker network create "$NETWORK" >/dev/null
  OWNED_NETWORK=1
fi

if [ -z "$DATABASE_URL" ]; then
  echo "==> Starting a throwaway PostgreSQL"
  docker run -d --name "${RUN_ID}-db" --network "$NETWORK" \
    -e POSTGRES_USER=care -e POSTGRES_PASSWORD=care -e POSTGRES_DB=care \
    postgres:16-alpine >/dev/null
  OWNED_DB=1

  READY=0
  for _ in $(seq 1 60); do
    if docker exec "${RUN_ID}-db" pg_isready -U care >/dev/null 2>&1; then
      READY=1
      break
    fi
    sleep 2
  done
  if [ "$READY" -ne 1 ]; then
    echo "error: PostgreSQL did not become ready" >&2
    exit 1
  fi
  DATABASE_URL="postgres://care:care@${RUN_ID}-db:5432/care"
fi

# The Redis-free managed composition (ADR-0004, ADR-0005, ADR-0006), configured
# as staging is. REDIS_URL is deliberately absent: if any of this needed Redis
# the containers would not start, which is the point of checking here.
#
# The Cloud Tasks values are syntactic. The backend validates that they are set
# and opens no connection at import; nothing in this script dispatches a task.
COMMON_ENV=(
  -e "DJANGO_SETTINGS_MODULE=config.settings.deployment"
  -e "DATABASE_URL=${DATABASE_URL}"
  -e "DJANGO_SECRET_KEY=startup-verification-only-not-a-deployment-key"
  -e "DJANGO_SECURE_SSL_REDIRECT=false"
  -e "DJANGO_ALLOWED_HOSTS=[\"*\"]"
  -e "DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend"
  -e "CARE_STORAGE_BACKEND=local"
  -e "CARE_CACHE_BACKEND=postgres"
  -e "CARE_RATE_LIMIT_BACKEND=postgres"
  -e "CARE_TASK_BACKEND=cloud_tasks"
  -e "GCP_PROJECT_ID=startup-verification"
  -e "GCP_TASKS_QUEUE=startup-verification"
  -e "GCP_WORKER_URL=http://startup-verification.invalid"
  -e "GUNICORN_WORKERS=1"
)

FAILURES=0
fail() { echo "FAIL  $*" >&2; FAILURES=$((FAILURES + 1)); }
pass() { echo "ok    $*"; }

curl_in_network() {
  # The application image ships curl, so no second image is needed and the
  # request originates on the same network the services are on.
  docker run --rm --network "$NETWORK" --entrypoint curl "$IMAGE" "$@"
}

wait_for_http() {
  local host="$1"
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl_in_network -fsS -o /dev/null "http://${host}:9000/ping/" 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# ---------------------------------------------------------------------------
# init
#
# The committed initialization contract, invoked as a deployment invokes it.
# Its health is its exit status (ADR-0008 section 10).
# ---------------------------------------------------------------------------

echo "==> init role"
if docker run --name "${RUN_ID}-init" --network "$NETWORK" \
     "${COMMON_ENV[@]}" -e CARE_PROCESS_ROLE=init \
     "$IMAGE" ./initialize.sh; then
  pass "initialize.sh completed"
else
  fail "initialize.sh failed"
  docker logs "${RUN_ID}-init" 2>&1 | tail -40 >&2 || true
fi
CONTAINERS+=("${RUN_ID}-init")

# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

echo "==> api role"
docker run -d --name "${RUN_ID}-api" --network "$NETWORK" \
  "${COMMON_ENV[@]}" -e CARE_PROCESS_ROLE=api \
  "$IMAGE" ./start.sh >/dev/null
CONTAINERS+=("${RUN_ID}-api")

if wait_for_http "${RUN_ID}-api"; then
  pass "api answers /ping/"
else
  fail "api did not answer /ping/ within ${STARTUP_TIMEOUT}s"
  docker logs "${RUN_ID}-api" 2>&1 | tail -40 >&2 || true
fi

# ---------------------------------------------------------------------------
# task_worker
#
# Route isolation is a property of the role and is cheap to check here: the
# worker registers the task endpoint and not the application API, so a request
# for an API route must not be answered (ES-06 route surfaces).
# ---------------------------------------------------------------------------

echo "==> task_worker role"
docker run -d --name "${RUN_ID}-worker" --network "$NETWORK" \
  "${COMMON_ENV[@]}" -e CARE_PROCESS_ROLE=task_worker \
  "$IMAGE" ./start-worker.sh >/dev/null
CONTAINERS+=("${RUN_ID}-worker")

if wait_for_http "${RUN_ID}-worker"; then
  pass "task_worker answers /ping/"
else
  fail "task_worker did not answer /ping/ within ${STARTUP_TIMEOUT}s"
  docker logs "${RUN_ID}-worker" 2>&1 | tail -40 >&2 || true
fi

WORKER_API_STATUS="$(curl_in_network -s -o /dev/null -w '%{http_code}' \
  "http://${RUN_ID}-worker:9000/api/v1/facility/" 2>/dev/null || echo 000)"
if [ "$WORKER_API_STATUS" = "404" ]; then
  pass "task_worker does not serve the application API (404)"
else
  fail "task_worker answered an application API route with ${WORKER_API_STATUS}; expected 404"
fi

API_TASK_STATUS="$(curl_in_network -s -o /dev/null -w '%{http_code}' \
  "http://${RUN_ID}-api:9000/internal/tasks/execute/" 2>/dev/null || echo 000)"
if [ "$API_TASK_STATUS" = "404" ]; then
  pass "api does not serve the task endpoint (404)"
else
  fail "api answered the task endpoint with ${API_TASK_STATUS}; expected 404"
fi

# ---------------------------------------------------------------------------
# Redis-free
# ---------------------------------------------------------------------------

if docker logs "${RUN_ID}-api" 2>&1 | grep -q "Redis is not required by the selected configuration"; then
  pass "api started without waiting for Redis"
else
  fail "api did not report the Redis-free path; check CARE_CACHE_BACKEND and CARE_TASK_BACKEND"
fi

echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "==> ${FAILURES} startup check(s) failed" >&2
  exit 1
fi
echo "==> Startup verification passed"
