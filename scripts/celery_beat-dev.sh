#!/bin/bash
# Runtime role: scheduler, with reload (ADR-0006).
#
# The local counterpart of scripts/celery_beat.sh: it decides when periodic work
# runs and does nothing else. It performs no initialization -- that is the
# `init` service in docker-compose.local.yaml.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-scheduler}"
printf "beat" > /tmp/container-probe

set -euo pipefail

./scripts/wait_for_db.sh
./scripts/wait_for_redis.sh

touch /tmp/healthy

watchmedo \
    auto-restart --directory=./ --pattern=*.py --recursive -- \
    celery --workdir="$(pwd)" -A config.celery_app beat --loglevel=INFO
