#!/bin/bash
# Runtime role: scheduler (ADR-0006).
#
# Its whole responsibility is deciding *when* periodic work should be triggered.
# It holds none of that work's logic: every periodic operation is a registered
# task or a management command, callable without a scheduler, which is what lets
# a managed deployment replace this process with a platform scheduler and change
# nothing else.
#
# It no longer runs scripts/initialize.sh. Until ES-06 it did, which made
# migrating the database a side effect of starting a scheduler -- no beat
# process meant no migrations, and a deployment that scaled beat to zero
# silently stopped initializing. Initialization is now its own role; run
# scripts/initialize.sh as a deployment step before promoting a new revision.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-scheduler}"
printf "beat" > /tmp/container-probe

set -eo pipefail

if [ -z "${DATABASE_URL}" ]; then
  export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

if [ -z "${REDIS_URL}" ]; then
  export REDIS_URL="rediss://:${REDIS_AUTH_TOKEN}@${REDIS_HOST}:${REDIS_PORT}/${REDIS_DATABASE}?ssl_cert_reqs=none"
fi


./wait_for_db.sh
./wait_for_redis.sh

touch /tmp/healthy

celery --app=config.celery_app beat --loglevel=info
