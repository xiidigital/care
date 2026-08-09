#!/bin/bash

# Redis is a capability dependency, not a CARE runtime requirement (ADR-0006).
#
# Every entrypoint used to block here unconditionally, which made Redis a
# prerequisite for serving even where nothing in the selected configuration
# used it -- a permanent cold-start failure for a deployment running the
# PostgreSQL cache and an HTTP task transport, with no Redis to answer.
#
# Two selections make Redis a *startup* dependency:
#
#   CARE_CACHE_BACKEND=redis   the default cache is Redis
#   CARE_TASK_BACKEND=celery   the broker is Redis
#
# The `recent_views` cache alias is Redis-backed under every configuration, and
# deliberately does not gate startup: it serves specific API endpoints rather
# than the process, so its absence degrades those endpoints instead of refusing
# to start the service. That distinction is recorded in
# docs/xii/architecture/inventory/cache-and-redis.md.

CARE_CACHE_BACKEND_SELECTED=$(echo "${CARE_CACHE_BACKEND:-redis}" | tr '[:upper:]' '[:lower:]')
CARE_TASK_BACKEND_SELECTED=$(echo "${CARE_TASK_BACKEND:-celery}" | tr '[:upper:]' '[:lower:]')

if [ "$CARE_CACHE_BACKEND_SELECTED" != "redis" ] && [ "$CARE_TASK_BACKEND_SELECTED" != "celery" ]; then
  >&2 echo "Redis is not required by the selected configuration (CARE_CACHE_BACKEND=$CARE_CACHE_BACKEND_SELECTED, CARE_TASK_BACKEND=$CARE_TASK_BACKEND_SELECTED); not waiting for it."
  exit 0
fi

redis_ready() {
python << END
import sys
import redis
try:
    redis_client = redis.Redis.from_url("${REDIS_URL}")
    redis_client.ping()
except (redis.exceptions.ConnectionError, redis.exceptions.ResponseError):
    sys.exit(-1)
sys.exit(0)
END
}

MAX_RETRIES=30
RETRY_COUNT=0
until redis_ready; do
  if [ "$RETRY_COUNT" -ge "$MAX_RETRIES" ]; then
    >&2 echo 'Failed to connect to Redis after 30 attempts. Exiting.'
    exit 1
  fi
  >&2 echo 'Waiting for Redis to become available...'
  sleep 1
  RETRY_COUNT=$((RETRY_COUNT + 1))
done
>&2 echo 'Redis is available'
