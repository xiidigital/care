#!/bin/bash
# Runtime role: task_worker, Celery transport (ADR-0006).
#
# The role says what this process is responsible for -- executing asynchronous
# CARE work -- and says nothing about how that work arrives. This entrypoint and
# scripts/start-worker.sh carry the same role over different transports, and run
# the same registered handlers.
#
# It performs no deployment initialization: see scripts/initialize.sh.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-task_worker}"
printf "celery" > /tmp/container-probe

set -eo pipefail

if [ -z "${DATABASE_URL}" ]; then
  export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

if [ -z "${REDIS_URL}" ]; then
  export REDIS_URL="rediss://:${REDIS_AUTH_TOKEN}@${REDIS_HOST}:${REDIS_PORT}/${REDIS_DATABASE}?ssl_cert_reqs=none"
fi


./wait_for_db.sh
./wait_for_redis.sh

python manage.py collectstatic --noinput
python manage.py compilemessages -v 0

celery --app=config.celery_app worker --max-tasks-per-child=6 --loglevel=info --concurrency=${CELERY_WORKER_CONCURRENCY:-1}
