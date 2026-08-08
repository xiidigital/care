"""
Cache health reflects the selected backend.

ES-04 sections 21 and 35. The two rules that shape these tests:

- no Redis health call executes when Redis cache is not selected;
- no PostgreSQL cache-table check executes when PostgreSQL cache is not
  selected.

Before ADR-0004 a single probe ran against whatever `default` was, so the health
endpoint effectively asked "is Redis up?" no matter how CARE was configured.
"""

from unittest.mock import patch

from django.core.cache import caches
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from config.caches import build_default_cache
from config.health import CacheHealthCheck

POSTGRES_TABLE = "care_cache_test"


def check(backend):
    return CacheHealthCheck(
        "Cache", slug="main_cache", connection_name="default", backend=backend
    ).check()


class DummyCacheHealthTests(SimpleTestCase):
    @override_settings(CACHES={"default": build_default_cache("dummy")})
    def test_dummy_reports_healthy_and_intentionally_disabled(self):
        # A set/get probe would report 500 forever, because DummyCache never
        # returns what was written. Running without a cache is a choice, not a
        # fault.
        code, meta = check("dummy")
        self.assertEqual(code, 200)
        self.assertEqual(meta["caching"], "disabled")

    @override_settings(CACHES={"default": build_default_cache("dummy")})
    def test_dummy_does_not_touch_the_cache(self):
        with patch.object(
            type(caches["default"]), "set", side_effect=AssertionError("probed")
        ):
            self.assertEqual(check("dummy")[0], 200)


class LocMemCacheHealthTests(SimpleTestCase):
    @override_settings(CACHES={"default": build_default_cache("locmem")})
    def test_locmem_reports_healthy_without_an_external_check(self):
        code, meta = check("locmem")
        self.assertEqual(code, 200)
        self.assertFalse(meta["shared"])

    @override_settings(CACHES={"default": build_default_cache("locmem")})
    def test_locmem_runs_no_round_trip(self):
        # There is no external dependency whose health could differ from the
        # process's own, and a passing probe must not be mistaken for evidence
        # that the cache is shared between instances.
        with patch.object(
            type(caches["default"]), "set", side_effect=AssertionError("probed")
        ):
            self.assertEqual(check("locmem")[0], 200)


class RedisNotProbedWhenUnselectedTests(SimpleTestCase):
    """The first of the two ES-04 section 35 rules."""

    @override_settings(CACHES={"default": build_default_cache("locmem")})
    def test_locmem_health_opens_no_redis_connection(self):
        with patch("django_redis.get_redis_connection") as get_conn:
            check("locmem")
        get_conn.assert_not_called()

    @override_settings(CACHES={"default": build_default_cache("dummy")})
    def test_dummy_health_opens_no_redis_connection(self):
        with patch("django_redis.get_redis_connection") as get_conn:
            check("dummy")
        get_conn.assert_not_called()


class PostgresCacheHealthTests(TestCase):
    """The second rule, plus the diagnostic that names the missing table."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with override_settings(
            CACHES={"default": build_default_cache("postgres", table=POSTGRES_TABLE)}
        ):
            call_command("createcachetable", verbosity=0)

    @override_settings(
        CACHES={"default": build_default_cache("postgres", table=POSTGRES_TABLE)}
    )
    def test_healthy_when_the_table_exists(self):
        code, meta = check("postgres")
        self.assertEqual(code, 200)
        self.assertEqual(meta["table"], POSTGRES_TABLE)

    @override_settings(
        CACHES={
            "default": build_default_cache("postgres", table="care_cache_missing_table")
        }
    )
    def test_missing_table_is_reported_by_name(self):
        # Without this the operator sees a generic cache failure and has no way
        # to tell "database down" from "initialization step never ran".
        code, meta = check("postgres")
        self.assertEqual(code, 500)
        self.assertIn("care_cache_missing_table", meta["error"])
        self.assertIn("createcachetable", meta["error"])

    @override_settings(CACHES={"default": build_default_cache("locmem")})
    def test_no_table_check_runs_when_postgres_is_not_selected(self):
        with patch(
            "django.db.backends.base.introspection.BaseDatabaseIntrospection.table_names"
        ) as table_names:
            check("locmem")
        table_names.assert_not_called()


class RedisCacheHealthTests(SimpleTestCase):
    @override_settings(
        CACHES={
            "default": build_default_cache(
                "redis", redis_url="redis://redis:6379", key_prefix="care-health-test"
            )
        }
    )
    def test_healthy_against_a_reachable_redis(self):
        code, meta = check("redis")
        self.assertEqual(code, 200)
        self.assertEqual(meta["backend"], "redis")

    @override_settings(
        CACHES={
            "default": build_default_cache(
                "redis",
                redis_url="redis://127.0.0.1:6399",
                key_prefix="care-health-test",
            )
        }
    )
    def test_unreachable_redis_reports_unhealthy(self):
        # IGNORE_EXCEPTIONS is True on the default cache, so an outage presents
        # as a wrong value rather than an exception. The check has to catch
        # that, otherwise a dead Redis reports healthy.
        code, meta = check("redis")
        self.assertEqual(code, 500)
        self.assertIn("error", meta)
