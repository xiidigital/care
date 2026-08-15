#!/bin/bash
# Runtime role: task_worker, HTTP transport (ADR-0006).
#
# The same application image as the API, started with a different role. The role
# is what changes the route surface: config/urls.py registers the private
# task-execution endpoint for `task_worker` and the public application for
# `api`, and never both.
#
# SECURITY -- this process has no application-layer authentication on the task
# endpoint, by decision (ES-03, carried forward by ES-06 section 14). It is safe
# only behind a platform authorization boundary that rejects unauthenticated
# invocation; on Cloud Run that means IAM, with invoker permission granted to
# the Cloud Tasks service account alone. It MUST NOT be deployed with
# unauthenticated public invocation. Enforcing that binding is ES-07.
#
# Like the API, it performs no deployment initialization: see
# scripts/initialize.sh.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-task_worker}"
printf "http" > /tmp/container-probe

set -eo pipefail

if [ -z "${DATABASE_URL}" ]; then
  export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

GUNICORN_LOG_FORMAT="${GUNICORN_LOG_FORMAT:="%(h)s %(l)s %(t)s \"%(r)s\" %(s)s %(M)s %(b)s \"%(f)s\" \"%(a)s\""}"
GUNICORN_ACCESS_LOGFILE="${GUNICORN_ACCESS_LOGFILE:="-"}"
GUNICORN_ERROR_LOGFILE="${GUNICORN_ERROR_LOGFILE:="-"}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:="2"}"

./wait_for_db.sh
./wait_for_redis.sh

# Handlers render reports from templates that reference static assets, and send
# localized email, so the worker needs both the static manifest and the compiled
# catalogues -- but it does not need to build them. docker/prod.Dockerfile does
# that once per image, for every role (unresolved-items.md L8).

gunicorn --config python:config.gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-9000} --chdir=/app --workers $GUNICORN_WORKERS \
  --access-logformat "$GUNICORN_LOG_FORMAT" --access-logfile $GUNICORN_ACCESS_LOGFILE --error-logfile $GUNICORN_ERROR_LOGFILE
