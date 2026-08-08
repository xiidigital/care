"""
Cache health reporting that follows the selected cache backend.

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

Celery health stays separate and is not touched here. The ES-03 finding that
queue-length health is meaningless under Cloud Tasks remains its own item.
"""

from django.core.cache import caches
from django.db import connections
from healthy_django.healthcheck.base import HealthCheck

from config.caches import (
    DUMMY_CACHE_BACKEND,
    LOCMEM_CACHE_BACKEND,
    POSTGRES_CACHE_BACKEND,
)

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
