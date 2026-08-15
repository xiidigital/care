#!/usr/bin/env bash
# Runtime role: api (ADR-0006). The development server, same responsibilities
# and same exclusions as scripts/start.sh.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-api}"
printf "http" > /tmp/container-probe

set -euo pipefail

./scripts/wait_for_db.sh
./scripts/wait_for_redis.sh

# Kept here on purpose, and only here.
#
# The production image builds these once and ships them (docker/prod.Dockerfile),
# which is why scripts/start.sh no longer runs them. Local development is the
# opposite arrangement: docker/dev.Dockerfile builds no assets, and
# docker-compose.local.yaml bind-mounts the working tree over /app, so anything
# a build produced would be shadowed by the host checkout anyway. The sources
# also change while the container runs, which is the case a build-time artefact
# cannot serve. So the developer entrypoint builds them at start.
echo "running collectstatic..."
python manage.py collectstatic --noinput
python manage.py compilemessages -v 0

echo "starting server..."
if [[ "${ATTACH_DEBUGGER}" == "true" ]]; then
  echo "waiting for debugger..."
  python -m debugpy --wait-for-client --listen 0.0.0.0:9876 manage.py runserver_plus 0.0.0.0:9000 --print-sql
else
  python manage.py runserver_plus 0.0.0.0:9000 --print-sql
fi
