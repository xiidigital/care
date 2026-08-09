#!/bin/bash
# Runtime role: init (ADR-0006). Schema and reference-data initialization.
#
# Before ADR-0003 this sequence existed only inside the Celery Beat entrypoints,
# which made migrating the database a side effect of starting a scheduler: no
# beat process meant no migrations. ES-03 moved it here; ES-06 removed the last
# caller, so no long-running process runs it any more. Initialization is a
# deployment operation with its own role -- a Cloud Run Job, a compose service,
# a deploy step or an operator -- and needs no broker, worker or scheduler.
#
# The role is ephemeral. It runs the sequence, stops at the first failure
# (`set -eo pipefail`), exits non-zero on failure and zero on success. Its
# health is its exit status; it serves no HTTP and must not stay running.
#
# It is never run by the API or by the task worker. Ordinary instance startup
# must not migrate: several instances start concurrently.
#
# Where concurrent execution would be unsafe, it is protected by the database
# rather than by ordering: `sync_permissions_roles` runs under a PostgreSQL
# transaction-scoped advisory lock (ADR-0005), so a second initializer contends
# on the lock instead of interleaving a delete-and-rebuild of the permission
# table. `migrate` relies on PostgreSQL's transactional DDL, and `sync_valueset`
# is an idempotent upsert of a fixed set of rows.
export CARE_PROCESS_ROLE="${CARE_PROCESS_ROLE:-init}"

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
