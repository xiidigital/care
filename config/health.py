"""
Health reporting that follows the selected backends and the process's role.

Two things are decided here.

*What the cache probe means.* Before ADR-0004 the health endpoint ran one cache
probe and, separately, a Redis broker probe, so "is CARE healthy?" always meant
"is Redis up?".

*Which probes a process runs at all.* ADR-0006 requires that a health endpoint
never declare a process unhealthy because a dependency belonging to a different
runtime role is unavailable. A task worker has no public API router and must not
be judged on one; an API serving a Cloud Tasks deployment has no Celery broker
and must not be judged on one either. :func:`build_health_checks` composes the
probe list from the role and the selected backends rather than from a fixed list.

Cache health follows the selected cache backend.

Before ADR-0004 the health endpoint ran one cache probe and, separately, a Redis
broker probe, so "is CARE healthy?" always meant "is Redis up?". With the cache
backend configurable that is no longer a sound question: a PostgreSQL-cache
deployment has no Redis to check, and a Dummy-cache deployment has nothing to
check at all.

ES-04 section 21 asks each profile to be probed for what it actually depends on:

``postgres``
    The cache table exists and a set/get round trip works.
``redis``
    The Redis-backed cache answers a set/get round trip.
``locmem``
    Nothing external. Reported healthy without a dependency check.
``dummy``
    Reported as intentionally non-operational rather than broken.

The ES-03 finding that queue-length health is meaningless under Cloud Tasks is
resolved here: that probe is registered only when Celery is the selected task
backend, so it is absent rather than permanently failing.
"""

from django.core.cache import caches
from django.db import connections
from healthy_django.healthcheck.base import HealthCheck
from healthy_django.healthcheck.celery_queue_length import (
    DjangoCeleryQueueLengthHealthCheck,
)
from healthy_django.healthcheck.django_database import DjangoDatabaseHealthCheck

from config.caches import (
    DUMMY_CACHE_BACKEND,
    LOCMEM_CACHE_BACKEND,
    POSTGRES_CACHE_BACKEND,
)
from config.runtime import (
    API_ROLE,
    INIT_ROLE,
    SCHEDULER_ROLE,
    TASK_WORKER_ROLE,
)
from config.tasks import CELERY_BACKEND

_PROBE_KEY = "care_cache_healthcheck"
_PROBE_VALUE = 1


class CacheHealthCheck(HealthCheck):
    """
    Probe the configured cache in the way that backend can actually fail.

    ``backend`` is passed in from settings rather than re-derived here, so the
    check cannot drift from the value that built ``CACHES``.
    """

    title = "Cache Check"

    required_params = ["connection_name", "backend"]

    health_code_pretty = {200: "All OK", 500: "Down"}

    def check(self):
        backend = self.params["backend"]

        if backend == DUMMY_CACHE_BACKEND:
            # Not a failure. Someone chose to run without a cache, and a probe
            # that expects a value back would report a permanently broken
            # service.
            return 200, {"backend": backend, "caching": "disabled"}

        if backend == LOCMEM_CACHE_BACKEND:
            # Process-local: there is no external dependency whose health could
            # differ from the process's own. Deliberately no round trip, so this
            # cannot be mistaken for evidence that cache is shared.
            return 200, {"backend": backend, "shared": False}

        meta = {"backend": backend}

        if backend == POSTGRES_CACHE_BACKEND:
            table = caches[self.params["connection_name"]]._table  # noqa: SLF001
            meta["table"] = table
            if table not in connections["default"].introspection.table_names():
                # The distinctive PostgreSQL-cache failure: the table was never
                # created. Name it, because a bare set/get failure here looks
                # identical to the database being down.
                meta["error"] = (
                    f"cache table {table!r} does not exist; "
                    "run `python manage.py createcachetable`"
                )
                return 500, meta

        try:
            cache = caches[self.params["connection_name"]]
            cache.set(_PROBE_KEY, _PROBE_VALUE)
            if cache.get(_PROBE_KEY) != _PROBE_VALUE:
                # Redis with IGNORE_EXCEPTIONS swallows the connection error and
                # returns None, so a wrong value is how an outage presents.
                meta["error"] = "cache did not return the value written"
                return 500, meta
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {e}"
            return 500, meta

        return 200, meta


class TaskRegistryHealthCheck(HealthCheck):
    """
    The worker's own readiness question: can it execute anything?

    A task worker that starts with an unimportable handler module accepts
    deliveries and fails every one of them. The registry is what decides which
    names exist, so loading it is the check -- and it is the whole check. No
    connection to the task transport is attempted: delivery is inbound under
    Cloud Tasks and outbound-only under Celery, so a worker probing the queue
    would be reporting on a dependency it does not use to receive work.

    Imported inside :meth:`check` because the registry pulls in application
    models, and settings must not.
    """

    title = "Task Registry Check"

    health_code_pretty = {200: "All OK", 500: "Down"}

    def check(self):
        try:
            from care.utils.tasks.registry import registered_task_names

            names = registered_task_names()
        except Exception as e:
            return 500, {"error": f"{type(e).__name__}: {e}"}

        if not names:
            # An empty registry is not a healthy worker: nothing it is sent can
            # succeed, and every delivery would be retried to exhaustion.
            return 500, {"registered_tasks": 0, "error": "no tasks are registered"}

        return 200, {"registered_tasks": len(names)}


def build_health_checks(*, role, cache_backend, task_backend, broker_url):
    """
    The probes this process should answer for, and no others.

    ``api``
        Database and cache -- what serving a request needs. The Celery queue
        probe is included only when Celery is actually the task backend, so a
        Cloud Tasks API does not report unhealthy for a broker it never uses.
    ``task_worker``
        Database, cache and the task registry. No public-API dependency: route
        isolation is the point of the role, not a fault.
    ``scheduler``
        Database, plus the broker it publishes to under Celery. Deliberately no
        cache or storage probe -- deciding *when* work runs needs neither.
    ``init``
        Nothing. It is a finite process whose health is its exit status, and it
        serves no HTTP for a probe to reach.
    """
    if role == INIT_ROLE:
        return []

    checks = [
        DjangoDatabaseHealthCheck(
            "Database", slug="main_database", connection_name="default"
        )
    ]

    if role in (API_ROLE, TASK_WORKER_ROLE):
        checks.append(
            CacheHealthCheck(
                "Cache",
                slug="main_cache",
                connection_name="default",
                backend=cache_backend,
            )
        )

    if role == TASK_WORKER_ROLE:
        checks.append(TaskRegistryHealthCheck("Task Registry", slug="task_registry"))

    if task_backend == CELERY_BACKEND and role in (API_ROLE, SCHEDULER_ROLE):
        checks.append(
            DjangoCeleryQueueLengthHealthCheck(
                "Celery Queue Length",
                slug="celery_queue_length",
                broker=broker_url,
                queue_name="celery",
                info_length=50,
                warning_length=0,  # this skips the 300 status code
                alert_length=200,
            )
        )

    return checks
