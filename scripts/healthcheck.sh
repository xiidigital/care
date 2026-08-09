#!/bin/bash
# Container liveness probe.
#
# What is probed follows the process's *transport*, not its runtime role: an
# HTTP task worker and the API are both reached over HTTP, while a Celery task
# worker carrying the same `task_worker` role answers only over the broker. Each
# entrypoint therefore declares the probe it can answer.
#
#   http    an HTTP-serving process -- the `api` role, or `task_worker` over
#           HTTP. /ping/ is registered for every role.
#   celery  a Celery worker.
#   beat    a Celery scheduler. The marker is written before beat is exec'd, so
#           this proves the container started rather than that beat is still
#           running; recorded as a known limitation in
#           docs/xii/architecture/inventory/unresolved-items.md.
#
# The `init` role has no probe. It is a finite process whose health is its exit
# status.

PROBE_FILE=/tmp/container-probe

if [ ! -f "$PROBE_FILE" ]; then
    echo "No container probe declared at $PROBE_FILE"
    exit 1
fi

CONTAINER_PROBE=$(cat "$PROBE_FILE")
case "$CONTAINER_PROBE" in
    http)
        curl -fsS http://localhost:${PORT:-9000}/ping/ || exit 1
        ;;
    celery)
        celery -A config.celery_app inspect ping -d celery@$HOSTNAME || exit 1
        ;;
    beat)
        ls /tmp/healthy || exit 1
        ;;
    *)
        echo "Unknown container probe: $CONTAINER_PROBE"
        exit 1
        ;;
esac
