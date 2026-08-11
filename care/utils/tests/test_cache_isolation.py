"""
Parallel-test cache isolation -- the E7 defect class.

ES-04 section 34.

**Root cause, reproduced before the fix.** `config/settings/test.py` pointed all
16 `--parallel` workers at one Redis database. `django_redis`'s `clear()` calls
`FLUSHDB`, which empties the whole database and ignores `KEY_PREFIX`, so the
`cache.clear()` in the setUp of `test_reset_password_api` and
`test_valueset_api` destroyed cache state the other workers were mid-assertion
on. Six tests in two families failed intermittently: the rate-limit tests as
`200 != 429`, the favorites tests as missing cached entries. Running just those
three modules in parallel reproduced it 4 times out of 4.

**Fix.** The test profile uses LocMem for the `default` cache. Each worker is a
separate process with its own cache, so a clear cannot reach across workers and
no key can collide. The one alias that must stay on Redis -- `ratelimit`, whose
atomic `INCR` has no portable equivalent -- is namespaced per worker with
`KEY_FUNCTION` instead. (`locks` moved to PostgreSQL advisory locks in ES-05 and
`recent_views` to a PostgreSQL model in RF1.)

**Not fixed by hiding shared state.** ES-04 section 18 forbids that, so these
tests assert the isolation mechanism directly rather than asserting that the six
symptoms stopped appearing.
"""

from unittest.mock import patch

from django.conf import settings
from django.core.cache import caches
from django.test import SimpleTestCase, override_settings

from config.caches import (
    RATELIMIT_CACHE_ALIAS,
    build_default_cache,
    worker_scoped_key,
)


class TestProfileIsolationTests(SimpleTestCase):
    def test_default_cache_is_process_local(self):
        # The property that removes the defect class: one worker's cache is not
        # reachable from another process at all.
        self.assertEqual(
            caches["default"].__class__.__module__,
            "django.core.cache.backends.locmem",
        )

    def test_clearing_the_default_cache_cannot_reach_another_worker(self):
        # Two LocMem caches with different LOCATIONs stand in for two worker
        # processes. Under the old shared-Redis profile this assertion failed
        # by construction, because FLUSHDB emptied everything.
        with override_settings(
            CACHES={
                "worker_a": build_default_cache("locmem", key_prefix="a"),
                "worker_b": build_default_cache("locmem", key_prefix="b"),
            }
        ):
            caches["worker_a"].set("shared", "value")
            caches["worker_b"].set("shared", "value")

            caches["worker_a"].clear()

            self.assertIsNone(caches["worker_a"].get("shared"))
            self.assertEqual(caches["worker_b"].get("shared"), "value")

    def test_rate_limit_state_survives_another_workers_clear(self):
        # The exact E7 symptom, expressed as a property. django_ratelimit keeps
        # its counter in the cache; a concurrent clear used to reset it and the
        # test that expected 429 saw 200.
        with override_settings(
            CACHES={
                "worker_a": build_default_cache("locmem", key_prefix="a"),
                "worker_b": build_default_cache("locmem", key_prefix="b"),
            }
        ):
            caches["worker_b"].add("rl:reset-request-alice", 1, 60)
            caches["worker_b"].incr("rl:reset-request-alice")

            caches["worker_a"].clear()

            self.assertEqual(caches["worker_b"].get("rl:reset-request-alice"), 2)


class WorkerScopedKeyTests(SimpleTestCase):
    """
    The namespacing applied to the Redis-only recent-views alias.
    """

    def test_key_includes_the_worker_id(self):
        with patch("django.test.runner._worker_id", 3):
            self.assertEqual(
                worker_scoped_key("lock:x", "care-locks", 1), "care-locks:w3:1:lock:x"
            )

    def test_different_workers_get_different_keys(self):
        with patch("django.test.runner._worker_id", 1):
            first = worker_scoped_key("lock:patient-create", "care-locks", 1)
        with patch("django.test.runner._worker_id", 2):
            second = worker_scoped_key("lock:patient-create", "care-locks", 1)
        self.assertNotEqual(first, second)

    def test_same_worker_gets_a_stable_key(self):
        with patch("django.test.runner._worker_id", 1):
            first = worker_scoped_key("lock:x", "care-locks", 1)
            second = worker_scoped_key("lock:x", "care-locks", 1)
        self.assertEqual(first, second)

    def test_redis_backed_aliases_use_the_worker_scoped_key_function(self):
        self.assertEqual(
            settings.CACHES[RATELIMIT_CACHE_ALIAS]["KEY_FUNCTION"],
            "config.caches.worker_scoped_key",
        )


class ProductionKeySemanticsTests(SimpleTestCase):
    """
    ES-04 section 34: production cache key semantics must not change by
    accident while fixing test isolation.

    The worker-scoped key function is configured only in `config/settings/test`.
    Production keys keep the plain `KEY_PREFIX:version:key` form.
    """

    def test_production_settings_do_not_use_the_worker_scoped_key_function(self):
        from config.caches import (
            POSTGRES_RATE_LIMIT_BACKEND,
            REDIS_RATE_LIMIT_BACKEND,
            build_ratelimit_cache,
        )

        # Both counting modes, since RF2 gave the alias a second backend and
        # neither may pick up the test profile's per-worker namespacing.
        for backend, kwargs in (
            (REDIS_RATE_LIMIT_BACKEND, {"redis_url": "redis://localhost:6379"}),
            (POSTGRES_RATE_LIMIT_BACKEND, {}),
        ):
            with self.subTest(backend=backend):
                production_ratelimit = build_ratelimit_cache(backend, **kwargs)
                self.assertNotIn("KEY_FUNCTION", production_ratelimit)

    def test_default_cache_key_function_is_djangos_own(self):
        production = build_default_cache("postgres", table="care_cache")
        self.assertNotIn("KEY_FUNCTION", production)
