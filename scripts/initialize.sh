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

# ADR-0004: the PostgreSQL cache table is created explicitly, here, and never on
# application startup -- several Cloud Run instances start at once, and the API
# must not be racing to create its own cache table.
#
# Called unconditionally on purpose. With no table name argument the command
# walks settings.CACHES and acts only on DatabaseCache aliases, so it creates
# the table when CARE_CACHE_BACKEND=postgres and does nothing under redis,
# locmem or dummy. That keeps the condition in one place -- the cache
# configuration itself -- instead of duplicating the backend name here where it
# could drift. It is idempotent, and it opens no Redis connection, so a
# PostgreSQL-cache deployment can initialize with no broker running.
python manage.py createcachetable

python manage.py compilemessages -v 0
python manage.py sync_permissions_roles
python manage.py sync_valueset
