#!/bin/bash
printf "celery" > /tmp/container-role

set -euo pipefail

./scripts/wait_for_db.sh
./scripts/wait_for_redis.sh

# Local compatibility only. The target runtime runs this as a separate deploy
# job; see scripts/initialize.sh.
./scripts/initialize.sh

watchmedo \
    auto-restart --directory=./ --pattern=*.py --recursive -- \
    celery --workdir="$(pwd)" -A config.celery_app worker -B --loglevel=INFO
