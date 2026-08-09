#!/bin/bash
# Runtime role: task_worker, Celery transport, with reload (ADR-0006).
#
# Until ES-06 this script ran `worker -B`, one process carrying both the worker
# and the scheduler, and it ran scripts/initialize.sh first -- so a single local
# container quietly owned three responsibilities. It now owns one. The scheduler
# is scripts/celery_beat-dev.sh and initialization is the `init` service in
# docker-compose.local.yaml.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-task_worker}"
printf "celery" > /tmp/container-probe

set -euo pipefail

./scripts/wait_for_db.sh
./scripts/wait_for_redis.sh

watchmedo \
    auto-restart --directory=./ --pattern=*.py --recursive -- \
    celery --workdir="$(pwd)" -A config.celery_app worker --loglevel=INFO
