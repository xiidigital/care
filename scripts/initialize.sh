#!/bin/bash
# Schema and reference-data initialization.
#
# Before ADR-0003 this sequence existed only inside the Celery Beat entrypoints,
# which made migrating the database a side effect of starting a scheduler: no
# beat process meant no migrations. It lives here so it can be run on its own --
# as a Cloud Run Job, a deploy step, or by an operator -- with no broker, no
# worker and no scheduler involved.
#
# The beat entrypoints now call this script, so local Docker Compose keeps its
# existing startup ordering and nothing about the traditional runtime changes.
#
# It is not run by the API or by the task worker. Ordinary instance startup must
# not migrate: several Cloud Run instances start concurrently.

set -eo pipefail

python manage.py migrate --noinput
python manage.py compilemessages -v 0
python manage.py sync_permissions_roles
python manage.py sync_valueset
