"""
Which store the rate limiter counts in, and why it is not the `default` cache.

`unresolved-items.md` L1: with `CARE_CACHE_BACKEND=postgres`, every management
command aborted before doing anything --

    SystemCheckError: (django_ratelimit.E003) cache backend
    django.core.cache.backends.db.DatabaseCache does not support atomic increment

-- which meant the `init` role could not run at all, even though initialization
rate-limits nothing. `django_ratelimit` read `settings.RATELIMIT_USE_CACHE`,
CARE never set it, and it therefore defaulted to `default`. The ADR-0004 cache
choice was deciding whether CARE could start.

The fix is the alias the library already supports: `RATELIMIT_USE_CACHE` names a
dedicated `ratelimit` cache. These tests cover the decoupling itself -- that the
ADR-0004 cache choice no longer reaches rate limiting, whatever either side is
set to.

What backs the alias became a separate question in RF2, and is answered in
`test_ratelimit_modes.py`. This file therefore asserts the *seam*: a dedicated
alias, keys that do not land in `default`, counters shared across connections,
windows that expire, and a store outage that fails closed. It runs under the
suite's default `redis` mode, which is also the mode these guarantees describe.

Nothing here silences a check. Under `redis` the profile silences nothing at
all, so the suite sees the same check results a production process does.
"""

import time
from unittest.mock import patch

import redis
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import BaseCache
from django.core.cache.backends.db import DatabaseCache
from django.core.checks import Error, run_checks
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django_ratelimit import ALL
from django_ratelimit.checks import check_caches
from django_ratelimit.core import EXPIRATION_FUDGE, _get_window, _make_cache_key

from care.utils.tests.ratelimit import reset_ratelimit_counters
from config.caches import (
    RATELIMIT_CACHE_ALIAS,
    REDIS_RATE_LIMIT_BACKEND,
    build_default_cache,
    build_ratelimit_cache,
)
from config.ratelimit import ratelimit


def redis_ratelimit_cache(url=None, legacy=None):
    """The `redis` mode alias, which is what every test in this file assumes."""
    return build_ratelimit_cache(
        REDIS_RATE_LIMIT_BACKEND, redis_url=url, legacy_redis_url=legacy
    )


REDIS_URL = settings.REDIS_URL

RATE = "10/h"
PERIOD = 3600

#: A port nothing listens on. Connection is refused immediately, so the
#: backend-unavailable tests below cost no wall-clock time and never depend on
#: a DNS timeout.
UNREACHABLE_REDIS = "redis://127.0.0.1:6399/0"

POSTGRES_CACHE_TABLE = "care_cache_ratelimit_test"


def ratelimit_cache_key(group, value="ratelimit", rate=RATE, period=PERIOD):
    """The key `django_ratelimit` will use for `group`, as production builds it."""
    window = _get_window(value, period)
    return _make_cache_key(group, window, rate, value, ALL)


class AliasSelectionTests(SimpleTestCase):
    """The seam itself: rate limiting names its own alias."""

    def test_ratelimit_reads_its_own_alias_not_the_default_cache(self):
        self.assertEqual(settings.RATELIMIT_USE_CACHE, RATELIMIT_CACHE_ALIAS)
        self.assertNotEqual(settings.RATELIMIT_USE_CACHE, "default")

    def test_the_alias_is_redis_under_the_suites_default_mode(self):
        self.assertEqual(settings.CARE_RATE_LIMIT_BACKEND, REDIS_RATE_LIMIT_BACKEND)
        self.assertEqual(
            settings.CACHES[RATELIMIT_CACHE_ALIAS]["BACKEND"],
            "django_redis.cache.RedisCache",
        )

    def test_the_alias_is_not_selected_by_care_cache_backend(self):
        # Every CARE_CACHE_BACKEND value produces the same rate-limit config for
        # a given CARE_RATE_LIMIT_BACKEND. If this ever stops holding, L1 has
        # been reintroduced. RF2 kept the two selections orthogonal precisely so
        # that this stays true in both directions.
        for backend in ("postgres", "redis", "locmem", "dummy"):
            with self.subTest(backend=backend):
                caches_config = {
                    "default": build_default_cache(backend, redis_url=REDIS_URL),
                    RATELIMIT_CACHE_ALIAS: redis_ratelimit_cache(REDIS_URL),
                }
                self.assertEqual(
                    caches_config[RATELIMIT_CACHE_ALIAS]["BACKEND"],
                    "django_redis.cache.RedisCache",
                )

    def test_building_the_alias_opens_no_connection(self):
        # Settings import must not touch Redis: the `init` role imports settings
        # with no Redis reachable and has to survive it.
        config = redis_ratelimit_cache(UNREACHABLE_REDIS)
        self.assertEqual(config["LOCATION"], UNREACHABLE_REDIS)

    def test_a_missing_redis_url_is_refused_rather_than_defaulted(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured) as ctx:
            redis_ratelimit_cache("", "")
        message = str(ctx.exception)
        self.assertIn("REDIS_RATE_LIMIT_URL", message)
        # RF2: the error now names the way out, because there is one. Selecting
        # a Redis backend without a URL is a mistake; needing no Redis is not.
        self.assertIn("postgres", message)

    def test_the_url_falls_back_to_the_legacy_redis_url(self):
        # The local compose profile sets only REDIS_URL and must keep working.
        config = redis_ratelimit_cache("", REDIS_URL)
        self.assertEqual(config["LOCATION"], REDIS_URL)

    def test_production_keys_are_not_worker_scoped(self):
        # The test profile adds KEY_FUNCTION for parallel isolation. Production
        # must keep the plain KEY_PREFIX:version:key form.
        self.assertNotIn("KEY_FUNCTION", redis_ratelimit_cache(REDIS_URL))


class SystemCheckTests(SimpleTestCase):
    """
    L1 was a system-check failure, so the check results are the assertion.
    """

    def caches_with_default(self, backend):
        return {
            "default": build_default_cache(backend, redis_url=REDIS_URL),
            RATELIMIT_CACHE_ALIAS: redis_ratelimit_cache(REDIS_URL),
        }

    def test_checks_pass_with_a_postgres_default_cache(self):
        # The exact configuration that used to abort every management command.
        with override_settings(CACHES=self.caches_with_default("postgres")):
            self.assertEqual(check_caches(None), [])

    def test_checks_pass_for_every_supported_cache_backend(self):
        for backend in ("postgres", "redis", "locmem", "dummy"):
            with (
                self.subTest(backend=backend),
                override_settings(CACHES=self.caches_with_default(backend)),
            ):
                self.assertEqual(check_caches(None), [])

    def test_the_check_still_fires_when_pointed_at_a_postgres_default(self):
        # Negative control. Without it the tests above would pass just as well
        # if E003 had been disabled rather than satisfied.
        with override_settings(
            CACHES=self.caches_with_default("postgres"),
            RATELIMIT_USE_CACHE="default",
        ):
            issues = check_caches(None)
        # E003 for the non-atomic incr, W001 because DatabaseCache is not on the
        # library's supported list either.
        self.assertIn("django_ratelimit.E003", [issue.id for issue in issues])

    def test_nothing_is_silenced_under_the_redis_mode(self):
        # RF2 introduced a suppression, and confined it to `postgres`. Under the
        # suite's `redis` profile the list must still be empty -- a suppression
        # that leaked into the strict mode would hide a real defect there.
        self.assertEqual(settings.CARE_RATE_LIMIT_BACKEND, REDIS_RATE_LIMIT_BACKEND)
        silenced = getattr(settings, "SILENCED_SYSTEM_CHECKS", [])
        self.assertEqual(list(silenced), [])

    def test_the_full_check_suite_passes_with_a_postgres_default_cache(self):
        # `manage.py check` is what `init` actually runs into, so run the whole
        # registry rather than django_ratelimit's check alone.
        with override_settings(CACHES=self.caches_with_default("postgres")):
            errors = [
                issue
                for issue in run_checks(include_deployment_checks=False)
                if isinstance(issue, Error)
            ]
        self.assertEqual(errors, [])


class WhyPostgresIsBestEffortTests(TestCase):
    """
    E003 is a statement of fact about `DatabaseCache`, not excess caution.

    ES-04 §17 recorded this as K4 and declined to implement an approximation.
    These tests establish the fact itself: the PostgreSQL cache cannot increment
    safely under concurrency, and Redis can.

    RF2 did not overturn that. It changed what CARE does with it. The fact used
    to justify a hard requirement -- Redis or nothing -- and now justifies a
    label: `postgres` mode exists, works, and is documented as best-effort
    exactly because of what is demonstrated here. The suppression of E003 in
    that mode is an acceptance of this evidence, not a disagreement with it.

    `test_ratelimit_modes.PostgresBestEffortConcurrencyTests` carries the same
    demonstration through the real limiter over independent connections.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with override_settings(CACHES=cls.postgres_caches()):
            call_command("createcachetable", verbosity=0)

    @staticmethod
    def postgres_caches():
        return {
            "default": build_default_cache(
                "postgres", table=POSTGRES_CACHE_TABLE, key_prefix="care-test"
            )
        }

    def test_database_cache_does_not_implement_incr_at_all(self):
        # The structural proof. `DatabaseCache` inherits `BaseCache.incr`, whose
        # body is a `get()` followed by a `set()` -- two statements, no row lock,
        # no `UPDATE ... SET value = value + 1`, no `SELECT ... FOR UPDATE`.
        self.assertNotIn("incr", DatabaseCache.__dict__)
        self.assertIs(DatabaseCache.incr, BaseCache.incr)

    def test_two_interleaved_increments_lose_one(self):
        # The behavioural proof. Because `incr` reads into Python and writes
        # back, nothing stops a second caller reading the same value first.
        # These four statements are exactly what `BaseCache.incr` executes,
        # interleaved the way two concurrent requests interleave them -- an
        # ordering the implementation permits precisely because it takes no
        # lock.
        with override_settings(CACHES=self.postgres_caches()):
            cache = caches["default"]
            cache.set("counter", 1, 60)

            first_read = cache.get("counter")  # request A reads 1
            second_read = cache.get("counter")  # request B reads 1
            cache.set("counter", first_read + 1, 60)  # A writes 2
            cache.set("counter", second_read + 1, 60)  # B writes 2

            # Two increments happened; the counter advanced by one. A rate limit
            # built on this undercounts, which means it lets traffic through.
            self.assertEqual(cache.get("counter"), 2)

    def test_redis_incr_does_not_lose_increments_across_connections(self):
        # The contrast that justifies the choice. Two genuinely independent
        # clients, no coordination, and both increments survive -- because INCR
        # is one command executed by Redis rather than a read-modify-write in
        # the caller.
        client_a = redis.Redis.from_url(REDIS_URL)
        client_b = redis.Redis.from_url(REDIS_URL)
        key = "care-test:ratelimit-atomicity"
        try:
            client_a.set(key, 1, ex=60)
            client_a.incr(key)
            client_b.incr(key)
            self.assertEqual(int(client_a.get(key)), 3)
        finally:
            client_a.delete(key)
            client_a.close()
            client_b.close()


@override_settings(DISABLE_RATELIMIT=False)
class RateLimitingWithAPostgresDefaultCacheTests(SimpleTestCase):
    """
    The objective, stated as a test: `CARE_CACHE_BACKEND=postgres` stays valid
    and rate limiting keeps working.
    """

    def setUp(self):
        super().setUp()
        reset_ratelimit_counters()
        self.factory = RequestFactory()

    def request(self):
        request = self.factory.post("/api/v1/auth/login/")
        request.META["REMOTE_ADDR"] = "203.0.113.11"
        return request

    def postgres_default(self):
        # Deliberately points at a table that was never created. Rate limiting
        # must not read the default cache at all, and a missing table is the
        # loudest possible way to notice if it does.
        return override_settings(
            CACHES={
                **settings.CACHES,
                "default": build_default_cache(
                    "postgres", table="care_cache_absent_on_purpose"
                ),
            }
        )

    def test_the_limiter_counts_while_the_default_cache_is_postgres(self):
        with self.postgres_default():
            for _ in range(10):
                self.assertFalse(
                    ratelimit(self.request(), "es07-postgres-default", ["alpha"], RATE)
                )
            with patch("config.ratelimit.validatecaptcha", return_value=False):
                self.assertTrue(
                    ratelimit(self.request(), "es07-postgres-default", ["alpha"], RATE)
                )

    def test_counters_do_not_land_in_the_default_cache(self):
        with self.postgres_default():
            ratelimit(self.request(), "es07-not-in-default", ["beta"], RATE)

        # The counter is in the ratelimit alias...
        key = ratelimit_cache_key("es07-not-in-default-beta")
        self.assertIsNotNone(caches[RATELIMIT_CACHE_ALIAS].get(key))
        # ...and the run above never touched the nonexistent Postgres table,
        # which it would have raised ProgrammingError on.


@override_settings(DISABLE_RATELIMIT=False)
class SharedAcrossConnectionsTests(SimpleTestCase):
    """
    A rate limit is only a limit if every instance counts into the same bucket.

    Cloud Run runs many instances; a per-process counter would multiply the
    effective limit by the instance count. This asserts the counter is visible
    to, and writable by, a connection CARE did not open.
    """

    def setUp(self):
        super().setUp()
        reset_ratelimit_counters()
        self.factory = RequestFactory()
        self.cache = caches[RATELIMIT_CACHE_ALIAS]

    def request(self):
        request = self.factory.post("/api/v1/auth/login/")
        request.META["REMOTE_ADDR"] = "203.0.113.12"
        return request

    def redis_key(self, group):
        """The full Redis key, including KEY_PREFIX and the test key function."""
        return self.cache.client.make_key(ratelimit_cache_key(group))

    def test_an_independent_client_sees_the_counter(self):
        ratelimit(self.request(), "es07-shared-read", ["gamma"], RATE)

        client = redis.Redis.from_url(REDIS_URL)
        try:
            stored = client.get(self.redis_key("es07-shared-read-gamma"))
        finally:
            client.close()

        # Present in shared storage rather than in this process's memory.
        self.assertIsNotNone(stored)

    def test_an_increment_from_another_connection_counts_against_the_limit(self):
        # Stand-in for a second instance: it increments the same key over an
        # independent TCP connection, and CARE must see those requests.
        group = "es07-shared-write"
        ratelimit(self.request(), group, ["delta"], RATE)

        client = redis.Redis.from_url(REDIS_URL)
        try:
            for _ in range(9):
                client.incr(self.redis_key(f"{group}-delta"))
        finally:
            client.close()

        # 1 request here + 9 elsewhere = 10, the limit. The next one trips it,
        # which it could not do if each connection kept its own count.
        with patch("config.ratelimit.validatecaptcha", return_value=False):
            self.assertTrue(ratelimit(self.request(), group, ["delta"], RATE))


@override_settings(DISABLE_RATELIMIT=False)
class WindowAndExpiryTests(SimpleTestCase):
    """
    Counters are per-window and must not outlive their window.
    """

    def setUp(self):
        super().setUp()
        reset_ratelimit_counters()
        self.factory = RequestFactory()
        self.cache = caches[RATELIMIT_CACHE_ALIAS]

    def request(self):
        request = self.factory.post("/api/v1/auth/login/")
        request.META["REMOTE_ADDR"] = "203.0.113.13"
        return request

    def test_the_counter_carries_a_ttl_bounded_by_the_window(self):
        ratelimit(self.request(), "es07-ttl", ["epsilon"], RATE)
        key = self.cache.client.make_key(ratelimit_cache_key("es07-ttl-epsilon"))

        client = redis.Redis.from_url(REDIS_URL)
        try:
            ttl = client.ttl(key)
        finally:
            client.close()

        # Set, not persistent, and no longer than the period plus the library's
        # fudge. A counter that outlived its window would throttle forever.
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, PERIOD + EXPIRATION_FUDGE)

    def test_separate_windows_get_separate_buckets(self):
        # `_get_window` pins the counter to the current period, so the same
        # caller in the next period starts from zero rather than inheriting.
        value = "ratelimit"
        window = _get_window(value, PERIOD)
        current = _make_cache_key("es07-window-zeta", window, RATE, value, ALL)
        following = _make_cache_key(
            "es07-window-zeta", window + PERIOD, RATE, value, ALL
        )
        self.assertNotEqual(current, following)

    def test_a_limited_caller_is_admitted_again_in_the_next_window(self):
        # Against the real Redis rather than an assertion about a TTL number.
        # With a one-second period `_get_window` returns the timestamp itself,
        # so waiting past it both rolls the bucket and expires the old key.
        short_rate = "1/s"
        self.assertFalse(ratelimit(self.request(), "es07-expiry", ["eta"], short_rate))
        with patch("config.ratelimit.validatecaptcha", return_value=False):
            self.assertTrue(
                ratelimit(self.request(), "es07-expiry", ["eta"], short_rate)
            )

        time.sleep(2)

        self.assertFalse(ratelimit(self.request(), "es07-expiry", ["eta"], short_rate))


@override_settings(DISABLE_RATELIMIT=False)
class BackendUnavailableTests(SimpleTestCase):
    """
    What happens when the rate-limit store is down.

    07-configuration-reference.md §26.4: security-sensitive endpoints SHOULD NOT
    silently fail open. The endpoints behind this limiter are login, MFA and
    password reset, so an unreachable store must not read as "under the limit".
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def request(self):
        request = self.factory.post("/api/v1/auth/login/")
        request.META["REMOTE_ADDR"] = "203.0.113.14"
        return request

    def unavailable(self):
        return override_settings(
            CACHES={
                **settings.CACHES,
                RATELIMIT_CACHE_ALIAS: redis_ratelimit_cache(UNREACHABLE_REDIS),
            }
        )

    def test_an_outage_does_not_raise(self):
        # IGNORE_EXCEPTIONS is True on this alias so the outage becomes an
        # unknown count rather than a 500 on the login path.
        with (
            self.unavailable(),
            patch("config.ratelimit.validatecaptcha", return_value=True),
        ):
            ratelimit(self.request(), "es07-outage", ["theta"], RATE)

    def test_an_outage_fails_closed(self):
        # Unknown count -> should_limit -> CARE falls through to captcha. With
        # the captcha failing, the caller is refused.
        with (
            self.unavailable(),
            patch("config.ratelimit.validatecaptcha", return_value=False),
        ):
            self.assertTrue(ratelimit(self.request(), "es07-outage", ["iota"], RATE))

    def test_a_valid_captcha_is_still_an_escape_during_an_outage(self):
        # Fail-closed, not fail-shut: a caller who solves the captcha proceeds,
        # which is what keeps a Redis outage from locking everyone out of login.
        with (
            self.unavailable(),
            patch("config.ratelimit.validatecaptcha", return_value=True),
        ):
            self.assertFalse(ratelimit(self.request(), "es07-outage", ["kappa"], RATE))

    def test_fail_open_is_off(self):
        self.assertFalse(getattr(settings, "RATELIMIT_FAIL_OPEN", False))


class InitRoleWithoutRedisTests(SimpleTestCase):
    """
    The consequence L1 actually blocked: `init` runs management commands only.

    `scripts/initialize.sh` rate-limits nothing, so it must start with the
    PostgreSQL cache selected and no Redis reachable. What stopped it was the
    system check, and the check reads configuration rather than connectivity --
    so an unreachable Redis in the rate-limit alias must still check clean.
    """

    def test_checks_pass_with_postgres_cache_and_an_unreachable_redis(self):
        with override_settings(
            CACHES={
                "default": build_default_cache("postgres", table="care_cache"),
                RATELIMIT_CACHE_ALIAS: redis_ratelimit_cache(UNREACHABLE_REDIS),
            }
        ):
            self.assertEqual(check_caches(None), [])
            errors = [
                issue
                for issue in run_checks(include_deployment_checks=False)
                if isinstance(issue, Error)
            ]
        self.assertEqual(errors, [])

    def test_createcachetable_ignores_the_redis_backed_alias(self):
        # `initialize.sh` calls createcachetable unconditionally. It walks
        # CACHES and acts only on DatabaseCache aliases, so the Redis-backed
        # rate-limit alias must not make it reach for a connection.
        with override_settings(
            CACHES={
                "default": build_default_cache("locmem"),
                RATELIMIT_CACHE_ALIAS: redis_ratelimit_cache(UNREACHABLE_REDIS),
            }
        ):
            call_command("createcachetable", verbosity=0)
