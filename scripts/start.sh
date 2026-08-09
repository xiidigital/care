#!/bin/bash
# Runtime role: api (ADR-0006).
#
# Serves the public application API. It does not migrate, does not create the
# cache table, does not sync permissions or valuesets, and runs no scheduling
# loop: those belong to the init role and are run once per deployment by
# scripts/initialize.sh. Several API instances start concurrently, and none of
# them may race to mutate the schema.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-api}"
printf "http" > /tmp/container-probe

set -eo pipefail

if [ -z "${DATABASE_URL}" ]; then
  export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

if [ -z "${REDIS_URL}" ]; then
  export REDIS_URL="rediss://:${REDIS_AUTH_TOKEN}@${REDIS_HOST}:${REDIS_PORT}/${REDIS_DATABASE}?ssl_cert_reqs=none"
fi


# https://docs.gunicorn.org/en/stable/settings.html#access-log-format
GUNICORN_LOG_FORMAT="${GUNICORN_LOG_FORMAT:="%(h)s %(l)s %(t)s \"%(r)s\" %(s)s %(M)s %(b)s \"%(f)s\" \"%(a)s\""}"
GUNICORN_ACCESS_LOGFILE="${GUNICORN_ACCESS_LOGFILE:="-"}"
GUNICORN_ERROR_LOGFILE="${GUNICORN_ERROR_LOGFILE:="-"}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:="2"}"

./wait_for_db.sh
./wait_for_redis.sh

python manage.py collectstatic --noinput
python manage.py compilemessages -v 0


gunicorn --config python:config.gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-9000} --chdir=/app --workers $GUNICORN_WORKERS \
  --access-logformat "$GUNICORN_LOG_FORMAT" --access-logfile $GUNICORN_ACCESS_LOGFILE --error-logfile $GUNICORN_ERROR_LOGFILE
