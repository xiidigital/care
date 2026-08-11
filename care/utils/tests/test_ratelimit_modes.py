"""
What each `CARE_RATE_LIMIT_BACKEND` mode actually promises.

RF2 replaced one guarantee plus a hard Redis dependency with three modes and
three different guarantees. The modes are not interchangeable, and the whole
value of the change depends on the difference being visible rather than
discovered in production, so it is asserted here.

`redis`
    Strict. Redis `INCR` is one command executed by the server, so concurrent
    requests cannot lose an increment. Its behaviour is covered end to end in
    `test_ratelimit_backend.py`, which runs under this mode by default; what is
    added here is that the mode reports itself as strict and silences nothing.

`postgres`
    Best-effort, and the tests say so. It counts correctly when requests arrive
    one at a time, enforces the configured threshold, isolates callers and
    expires with its window -- and it undercounts under concurrency, which
    `PostgresBestEffortConcurrencyTests` demonstrates over independent
    PostgreSQL connections rather than describing. That test exists to document
    the limitation. It is not a bug to be fixed later, and a change that makes
    it fail is a change that altered the mode's semantics.

`disabled`
    Nothing is counted and nothing is contacted. The assertions are about
    absence: no cache handler access, no counter operation, no configured alias
    and no installed app.

The orthogonality claim is also tested here. `CARE_CACHE_BACKEND` and
`CARE_RATE_LIMIT_BACKEND` answer different questions and every combination of
them is valid, including the two that look redundant.
"""

import threading
import time
from unittest.mock import patch

from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.core.checks import Error, run_checks
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone
from django_ratelimit import ALL
from django_ratelimit.checks import check_caches
from django_ratelimit.core import _get_window, _make_cache_key

from config.caches import (
    BEST_EFFORT_SEMANTICS,
    DISABLED_RATE_LIMIT_BACKEND,
    POSTGRES_RATE_LIMIT_BACKEND,
    RATELIMIT_CACHE_ALIAS,
    RATELIMIT_NON_ATOMIC_CHECK,
    REDIS_RATE_LIMIT_BACKEND,
    STRICT_ATOMIC_SEMANTICS,
    SUPPORTED_RATE_LIMIT_BACKENDS,
    build_default_cache,
    build_ratelimit_cache,
    build_ratelimit_caches,
    rate_limit_semantics,
    ratelimit_installed_apps,
    ratelimit_silenced_checks,
    validate_rate_limit_backend,
)
from config.ratelimit import rate_limiting_enabled, ratelimit

REDIS_URL = "redis://localhost:6379"

#: Its own table, so nothing here can be satisfied by a cache table another
#: responsibility created. Each `--parallel` worker owns a cloned database, so
#: the table -- and every counter in it -- belongs to one worker.
RATELIMIT_TEST_TABLE = "care_ratelimit_cache_test"
RATELIMIT_TEST_PREFIX = "care-test-rl"

RATE = "10/h"
PERIOD = 3600


def postgres_ratelimit_alias(**overrides):
    """The `postgres` mode alias, pointed at this suite's own table."""
    return build_ratelimit_cache(
        POSTGRES_RATE_LIMIT_BACKEND,
        table=RATELIMIT_TEST_TABLE,
        key_prefix=RATELIMIT_TEST_PREFIX,
        **overrides,
    )


def postgres_mode(**cache_overrides):
    """Select `postgres` rate limiting with rate limiting actually switched on.

    `default` is LocMem here on purpose. It is not what is under test, and
    making it something the rate limiter could accidentally succeed against
    would weaken every assertion about where the counters went.
    """
    return override_settings(
        CARE_RATE_LIMIT_BACKEND=POSTGRES_RATE_LIMIT_BACKEND,
        DISABLE_RATELIMIT=False,
        SILENCED_SYSTEM_CHECKS=ratelimit_silenced_checks(POSTGRES_RATE_LIMIT_BACKEND),
        CACHES={
            "default": build_default_cache("locmem", key_prefix="care-test"),
            RATELIMIT_CACHE_ALIAS: postgres_ratelimit_alias(**cache_overrides),
        },
    )


def create_ratelimit_table():
    with override_settings(CACHES={RATELIMIT_CACHE_ALIAS: postgres_ratelimit_alias()}):
        call_command("createcachetable", verbosity=0)


def drop_ratelimit_table():
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {RATELIMIT_TEST_TABLE}")


def counter_rows():
    """Every row in the rate-limit table, as (key, value-ish, expires)."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT cache_key, expires FROM {RATELIMIT_TEST_TABLE} ORDER BY cache_key"  # noqa: S608
        )
        return cursor.fetchall()


def stored_count(group, value="ratelimit", rate=RATE, period=PERIOD):
    """The counter `django_ratelimit` holds for `group`, read back from the alias."""
    window = _get_window(value, period)
    key = _make_cache_key(group, window, rate, value, ALL)
    return caches[RATELIMIT_CACHE_ALIAS].get(key)


def auth_request(ip="203.0.113.20"):
    request = RequestFactory().post("/api/v1/auth/login/")
    request.META["REMOTE_ADDR"] = ip
    return request


class NoRedisContact:
    """Fail the test if anything opens a Redis connection inside the block.

    Everything django_redis does reaches the network through redis-py's
    `AbstractConnection.connect`, so one patch covers the client, the pool and
    any library that borrowed them. Asserting on configuration alone would not
    catch a stray direct client, and the Redis-free profile's whole claim is
    that no such call happens.
    """

    def __enter__(self):
        def refuse(*args, **kwargs):
            msg = "Redis was contacted in a mode that must not contact Redis."
            raise AssertionError(msg)

        self._patcher = patch(
            "redis.connection.AbstractConnection.connect", side_effect=refuse
        )
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


class BackendSelectionTests(SimpleTestCase):
    """`CARE_RATE_LIMIT_BACKEND` is its own variable, validated on its own."""

    def test_the_three_supported_values(self):
        self.assertEqual(
            SUPPORTED_RATE_LIMIT_BACKENDS,
            (
                REDIS_RATE_LIMIT_BACKEND,
                POSTGRES_RATE_LIMIT_BACKEND,
                DISABLED_RATE_LIMIT_BACKEND,
            ),
        )
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            with self.subTest(backend=backend):
                self.assertEqual(validate_rate_limit_backend(backend), backend)

    def test_an_unsupported_value_is_refused_and_says_what_is_supported(self):
        # A typo must stop the process rather than quietly select something.
        # Falling back to `disabled` would turn a misspelling into silently
        # unlimited login attempts; falling back to `redis` would demand an
        # instance the deployment may not have.
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_rate_limit_backend("postgresql")
        message = str(ctx.exception)
        self.assertIn("CARE_RATE_LIMIT_BACKEND", message)
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            self.assertIn(backend, message)

    def test_the_default_is_redis(self):
        # Backward compatibility: an existing deployment that sets nothing keeps
        # the strict semantics it already had, and never silently degrades.
        from config.settings import base

        self.assertEqual(base.REDIS_RATE_LIMIT_BACKEND, REDIS_RATE_LIMIT_BACKEND)
        self.assertEqual(SUPPORTED_RATE_LIMIT_BACKENDS[0], REDIS_RATE_LIMIT_BACKEND)

    def test_the_semantics_of_each_mode_are_distinct(self):
        self.assertEqual(
            rate_limit_semantics(REDIS_RATE_LIMIT_BACKEND), STRICT_ATOMIC_SEMANTICS
        )
        self.assertEqual(
            rate_limit_semantics(POSTGRES_RATE_LIMIT_BACKEND), BEST_EFFORT_SEMANTICS
        )
        self.assertIsNone(rate_limit_semantics(DISABLED_RATE_LIMIT_BACKEND))
        # The one thing that must never be true.
        self.assertNotEqual(
            rate_limit_semantics(POSTGRES_RATE_LIMIT_BACKEND),
            rate_limit_semantics(REDIS_RATE_LIMIT_BACKEND),
        )

    def test_building_any_mode_opens_no_connection(self):
        # Settings import runs in the `init` role with nothing reachable.
        with NoRedisContact():
            for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
                with self.subTest(backend=backend):
                    build_ratelimit_cache(backend, redis_url=REDIS_URL)


class OrthogonalBackendCombinationTests(SimpleTestCase):
    """
    Cache and rate limiting are separate selections, and stay separate.

    RF2 section 12 lists the combinations that must work. None of them is
    rejected, including the ones that look redundant: a deployment may cache in
    PostgreSQL to save a Redis round trip while still wanting strict counters,
    or cache in Redis while refusing to depend on it for security limits.
    """

    COMBINATIONS = (
        ("postgres", POSTGRES_RATE_LIMIT_BACKEND),
        ("postgres", REDIS_RATE_LIMIT_BACKEND),
        ("redis", POSTGRES_RATE_LIMIT_BACKEND),
        ("redis", REDIS_RATE_LIMIT_BACKEND),
        ("postgres", DISABLED_RATE_LIMIT_BACKEND),
        ("redis", DISABLED_RATE_LIMIT_BACKEND),
    )

    def build(self, cache_backend, rate_limit_backend):
        return {
            "default": build_default_cache(cache_backend, redis_url=REDIS_URL),
            **build_ratelimit_caches(rate_limit_backend, redis_url=REDIS_URL),
        }

    def test_every_combination_builds(self):
        for cache_backend, rate_limit_backend in self.COMBINATIONS:
            with self.subTest(cache=cache_backend, rate_limit=rate_limit_backend):
                self.assertIsInstance(
                    self.build(cache_backend, rate_limit_backend), dict
                )

    def test_the_rate_limit_alias_ignores_the_cache_backend(self):
        # The same rate-limit selection produces the same alias whatever the
        # cache is doing. This is L1 stated as an invariant rather than a fix.
        for rate_limit_backend in (
            REDIS_RATE_LIMIT_BACKEND,
            POSTGRES_RATE_LIMIT_BACKEND,
        ):
            aliases = [
                self.build(cache_backend, rate_limit_backend)[RATELIMIT_CACHE_ALIAS]
                for cache_backend in ("postgres", "redis", "locmem", "dummy")
            ]
            with self.subTest(rate_limit=rate_limit_backend):
                self.assertEqual(aliases, [aliases[0]] * len(aliases))

    def test_the_cache_backend_ignores_the_rate_limit_selection(self):
        # And the converse, so neither variable can start inferring the other.
        for cache_backend in ("postgres", "redis"):
            defaults = [
                self.build(cache_backend, rate_limit_backend)["default"]
                for rate_limit_backend in SUPPORTED_RATE_LIMIT_BACKENDS
            ]
            with self.subTest(cache=cache_backend):
                self.assertEqual(defaults, [defaults[0]] * len(defaults))

    def test_postgres_rate_limiting_never_reuses_the_default_cache_table(self):
        # Two responsibilities, two tables, even when both are PostgreSQL. A
        # shared table would put ordinary cache entries and security counters
        # under one MAX_ENTRIES cull budget.
        caches_config = self.build("postgres", POSTGRES_RATE_LIMIT_BACKEND)
        self.assertNotEqual(
            caches_config["default"]["LOCATION"],
            caches_config[RATELIMIT_CACHE_ALIAS]["LOCATION"],
        )
        self.assertEqual(
            caches_config[RATELIMIT_CACHE_ALIAS]["LOCATION"], "care_ratelimit_cache"
        )

    def test_disabled_contributes_no_alias_to_any_combination(self):
        for cache_backend in ("postgres", "redis", "locmem", "dummy"):
            with self.subTest(cache=cache_backend):
                built = self.build(cache_backend, DISABLED_RATE_LIMIT_BACKEND)
                self.assertNotIn(RATELIMIT_CACHE_ALIAS, built)


class PostgresAliasShapeTests(SimpleTestCase):
    """
    The two DatabaseCache options that are correctness, not tuning.
    """

    def test_it_is_a_database_cache_on_its_own_table(self):
        alias = build_ratelimit_cache(POSTGRES_RATE_LIMIT_BACKEND)
        self.assertEqual(
            alias["BACKEND"], "django.core.cache.backends.db.DatabaseCache"
        )
        self.assertEqual(alias["LOCATION"], "care_ratelimit_cache")

    def test_the_timeout_outlives_the_longest_window_care_configures(self):
        # `BaseCache.incr` re-sets the key with the *alias* timeout rather than
        # the remaining window, so a timeout shorter than the window expires a
        # live counter and restarts it at zero -- which admits traffic. CARE
        # configures 10/h on the password-reset endpoints, so anything at or
        # below 3600 would be actively harmful; Django's own default is 300.
        alias = build_ratelimit_cache(POSTGRES_RATE_LIMIT_BACKEND)
        self.assertGreater(alias["TIMEOUT"], PERIOD)

    def test_max_entries_is_raised_above_djangos_default(self):
        # DatabaseCache culls a third of the table once the row count passes
        # MAX_ENTRIES, without regard for which rows. At Django's default of 300
        # that would drop live counters under ordinary load.
        alias = build_ratelimit_cache(POSTGRES_RATE_LIMIT_BACKEND)
        self.assertGreater(alias["OPTIONS"]["MAX_ENTRIES"], 300)

    def test_it_needs_no_redis_url(self):
        # The point of the mode: nothing about Redis is consulted or required.
        with NoRedisContact():
            alias = build_ratelimit_cache(
                POSTGRES_RATE_LIMIT_BACKEND, redis_url="", legacy_redis_url=""
            )
        self.assertEqual(
            alias["BACKEND"], "django.core.cache.backends.db.DatabaseCache"
        )


class SystemCheckSuppressionTests(SimpleTestCase):
    """
    RF2 section 5: exactly one check, in exactly one mode.

    The suppression is the part of this change most likely to rot into a blanket
    silence, so both halves are pinned -- what is silenced, and what is not.
    """

    def test_redis_mode_silences_nothing(self):
        self.assertEqual(ratelimit_silenced_checks(REDIS_RATE_LIMIT_BACKEND), [])

    def test_postgres_mode_silences_only_the_atomicity_check(self):
        self.assertEqual(
            ratelimit_silenced_checks(POSTGRES_RATE_LIMIT_BACKEND),
            [RATELIMIT_NON_ATOMIC_CHECK],
        )
        self.assertEqual(RATELIMIT_NON_ATOMIC_CHECK, "django_ratelimit.E003")

    def test_the_unsupported_backend_warning_is_left_to_fire(self):
        # W001 is not silenced anywhere. It is a warning, so `manage.py check`
        # still exits clean, and leaving it audible is the point: whoever runs
        # checks in postgres mode should see that the backend is off the
        # library's supported list.
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            with self.subTest(backend=backend):
                self.assertNotIn(
                    "django_ratelimit.W001", ratelimit_silenced_checks(backend)
                )

    def test_configuration_errors_are_never_silenced(self):
        # E001/E002 mean the limiter is pointed at nothing. Silencing those
        # would hide a rate limiter that is not running.
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            silenced = ratelimit_silenced_checks(backend)
            with self.subTest(backend=backend):
                self.assertNotIn("django_ratelimit.E001", silenced)
                self.assertNotIn("django_ratelimit.E002", silenced)

    def test_disabled_mode_silences_nothing_because_it_installs_nothing(self):
        # Suppression would have been the lazy route here too. There is nothing
        # to suppress: no alias, so no check, so no error.
        self.assertEqual(ratelimit_silenced_checks(DISABLED_RATE_LIMIT_BACKEND), [])
        self.assertEqual(ratelimit_installed_apps(DISABLED_RATE_LIMIT_BACKEND), ())

    def test_the_app_is_installed_in_both_counting_modes(self):
        for backend in (REDIS_RATE_LIMIT_BACKEND, POSTGRES_RATE_LIMIT_BACKEND):
            with self.subTest(backend=backend):
                self.assertEqual(
                    ratelimit_installed_apps(backend), ("django_ratelimit",)
                )

    def test_the_check_still_fires_against_the_postgres_alias(self):
        # The negative control RF2 section 5 asks for, and the reason the
        # suppression is honest: E003 is not disabled, disproved or worked
        # around. It fires, correctly, and SILENCED_SYSTEM_CHECKS is CARE
        # accepting what it says. If this ever stops failing, the suppression
        # has become a claim about atomicity rather than an acceptance of the
        # lack of it.
        with override_settings(
            CACHES={
                "default": build_default_cache("locmem"),
                RATELIMIT_CACHE_ALIAS: postgres_ratelimit_alias(),
            }
        ):
            issues = check_caches(None)
        self.assertIn(RATELIMIT_NON_ATOMIC_CHECK, [issue.id for issue in issues])

    def test_a_non_atomic_backend_outside_postgres_mode_is_still_an_error(self):
        # Someone hand-configuring a LocMem or file-based rate-limit alias gets
        # E003 with nothing silenced, because RF2 accepted PostgreSQL
        # specifically -- not non-atomic increments in general.
        with override_settings(
            CACHES={
                "default": build_default_cache("locmem"),
                RATELIMIT_CACHE_ALIAS: build_default_cache("locmem"),
            }
        ):
            issues = check_caches(None)
        self.assertIn(RATELIMIT_NON_ATOMIC_CHECK, [issue.id for issue in issues])
        self.assertNotIn(RATELIMIT_NON_ATOMIC_CHECK, ratelimit_silenced_checks("redis"))

    def test_the_full_registry_is_clean_in_postgres_mode(self):
        # What `manage.py check` actually does, rather than django_ratelimit's
        # check alone -- and it must come back with no Errors so the init role
        # can run.
        with postgres_mode():
            errors = [
                issue
                for issue in run_checks(include_deployment_checks=False)
                if isinstance(issue, Error)
                and issue.id
                not in ratelimit_silenced_checks(POSTGRES_RATE_LIMIT_BACKEND)
            ]
        self.assertEqual(errors, [])


@override_settings(CARE_RATE_LIMIT_BACKEND=REDIS_RATE_LIMIT_BACKEND)
class RedisStrictModeTests(SimpleTestCase):
    """
    The strict mode, which is the default and the recommendation.

    Its counting behaviour -- increments, sharing across independent
    connections, window expiry, threshold enforcement, caller isolation -- is
    covered against a real Redis in `test_ratelimit_backend.py` and
    `test_ratelimit_semantics.py`, both of which run in this mode. Repeating it
    here would duplicate rather than add. What belongs here is the mode's
    self-description, because that is what a reader compares against postgres.
    """

    def test_it_reports_strict_atomic_semantics(self):
        self.assertEqual(
            rate_limit_semantics(REDIS_RATE_LIMIT_BACKEND), STRICT_ATOMIC_SEMANTICS
        )

    def test_rate_limiting_is_enabled(self):
        with override_settings(DISABLE_RATELIMIT=False):
            self.assertTrue(rate_limiting_enabled())

    def test_the_alias_fails_closed_rather_than_raising(self):
        # IGNORE_EXCEPTIONS turns an outage into an unknown count inside the
        # library; RATELIMIT_FAIL_OPEN=False turns an unknown count into
        # should_limit. The pair is the Redis failure policy, and the postgres
        # mode reproduces its *effect* by a different mechanism below.
        alias = build_ratelimit_cache(REDIS_RATE_LIMIT_BACKEND, redis_url=REDIS_URL)
        self.assertTrue(alias["OPTIONS"]["IGNORE_EXCEPTIONS"])


class PostgresBestEffortSequentialTests(TestCase):
    """
    The postgres mode works. Sequentially, it is exact.

    Every assertion here holds because the requests do not overlap. That is the
    honest scope of the mode's correctness, and stating it this way is what
    makes `PostgresBestEffortConcurrencyTests` a documented limitation rather
    than a surprise.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_ratelimit_table()

    def setUp(self):
        super().setUp()
        with postgres_mode():
            caches[RATELIMIT_CACHE_ALIAS].clear()

    def test_the_counter_advances_one_request_at_a_time(self):
        with postgres_mode():
            for expected in range(1, 5):
                ratelimit(auth_request(), "rf2-pg-count", ["alpha"], RATE)
                self.assertEqual(stored_count("rf2-pg-count-alpha"), expected)

    def test_the_configured_threshold_is_enforced(self):
        with postgres_mode():
            for _ in range(10):
                self.assertFalse(
                    ratelimit(auth_request(), "rf2-pg-limit", ["beta"], RATE)
                )
            with patch("config.ratelimit.validatecaptcha", return_value=False):
                self.assertTrue(
                    ratelimit(auth_request(), "rf2-pg-limit", ["beta"], RATE)
                )

    def exhaust(self, group, key, count=11):
        """Push one caller past the limit.

        The captcha is stubbed throughout. Once the limit trips, `ratelimit()`
        falls through to `validatecaptcha`, and a plain `WSGIRequest` has no
        `.data` for it to read -- that is a DRF attribute. Stubbing keeps these
        assertions about the counter rather than about Google's response.
        """
        with patch("config.ratelimit.validatecaptcha", return_value=False):
            for _ in range(count):
                ratelimit(auth_request(), group, [key], RATE)

    def test_a_limited_caller_can_still_answer_a_captcha(self):
        # Same escape hatch the Redis mode offers. Being over the limit is a
        # challenge, not a lockout.
        with postgres_mode():
            self.exhaust("rf2-pg-captcha", "gamma")
            with patch("config.ratelimit.validatecaptcha", return_value=True):
                self.assertFalse(
                    ratelimit(auth_request(), "rf2-pg-captcha", ["gamma"], RATE)
                )

    def test_callers_are_isolated_from_each_other(self):
        with postgres_mode():
            self.exhaust("rf2-pg-isolation", "noisy")
            # `noisy` is over; `quiet` has made no requests at all.
            self.assertFalse(
                ratelimit(auth_request(), "rf2-pg-isolation", ["quiet"], RATE)
            )

    def test_endpoints_do_not_share_a_bucket(self):
        with postgres_mode():
            self.exhaust("rf2-pg-login", "delta")
            self.assertFalse(ratelimit(auth_request(), "rf2-pg-reset", ["delta"], RATE))

    def test_the_counter_lands_in_the_dedicated_table(self):
        # "DatabaseCache is actually used" as a fact about rows, not about
        # configuration. Nothing else writes to this table.
        with postgres_mode():
            self.assertEqual(counter_rows(), [])
            ratelimit(auth_request(), "rf2-pg-rows", ["epsilon"], RATE)
            self.assertEqual(len(counter_rows()), 1)

    def test_no_redis_connection_is_opened(self):
        # The Redis-free claim, enforced rather than asserted about config.
        with postgres_mode(), NoRedisContact():
            for _ in range(3):
                ratelimit(auth_request(), "rf2-pg-no-redis", ["zeta"], RATE)
            self.assertEqual(stored_count("rf2-pg-no-redis-zeta"), 3)

    def test_the_counter_outlives_its_window(self):
        # The TIMEOUT constant, proven against the stored row rather than the
        # config dict. `add` seeds with the window's TTL and every later `incr`
        # re-sets it with the alias timeout, so it is the second write that
        # decides whether a 10/h counter survives the hour.
        with postgres_mode():
            ratelimit(auth_request(), "rf2-pg-ttl", ["eta"], RATE)
            ratelimit(auth_request(), "rf2-pg-ttl", ["eta"], RATE)
            ((_key, expires),) = counter_rows()

        remaining = (expires - timezone.now()).total_seconds()
        self.assertGreater(remaining, PERIOD)

    def test_djangos_default_timeout_would_expire_the_counter_early(self):
        # The negative control for the constant above. With Django's 300s
        # default, the second request leaves a 10/h counter with less than the
        # window's worth of life -- it would reset mid-window and admit another
        # full allowance. This is why TIMEOUT is set explicitly.
        with postgres_mode(timeout=300):
            ratelimit(auth_request(), "rf2-pg-ttl-bad", ["theta"], RATE)
            ratelimit(auth_request(), "rf2-pg-ttl-bad", ["theta"], RATE)
            ((_key, expires),) = counter_rows()

        remaining = (expires - timezone.now()).total_seconds()
        self.assertLess(remaining, PERIOD)

    def test_separate_windows_get_separate_counters(self):
        # A one-second period makes `_get_window` return the timestamp itself,
        # so waiting past it rolls the bucket -- the same property the Redis
        # mode is tested for, holding here too.
        short_rate = "1/s"
        with postgres_mode():
            self.assertFalse(
                ratelimit(auth_request(), "rf2-pg-window", ["iota"], short_rate)
            )
            with patch("config.ratelimit.validatecaptcha", return_value=False):
                self.assertTrue(
                    ratelimit(auth_request(), "rf2-pg-window", ["iota"], short_rate)
                )

            time.sleep(2)

            self.assertFalse(
                ratelimit(auth_request(), "rf2-pg-window", ["iota"], short_rate)
            )


class PostgresFailurePolicyTests(TestCase):
    """
    RF2 section 6: a database failure must not read as unrestricted traffic.

    Redis reaches the same place through `IGNORE_EXCEPTIONS` plus
    `RATELIMIT_FAIL_OPEN=False`. PostgreSQL cannot -- `DatabaseCache` has no
    such option and raises -- so the wrapper carries the policy instead. The
    endpoints behind it are login, MFA and password reset.
    """

    def missing_table(self):
        # Points at a table that was never created, which is what a deployment
        # that skipped `createcachetable` actually looks like.
        return override_settings(
            CARE_RATE_LIMIT_BACKEND=POSTGRES_RATE_LIMIT_BACKEND,
            DISABLE_RATELIMIT=False,
            CACHES={
                "default": build_default_cache("locmem"),
                RATELIMIT_CACHE_ALIAS: build_ratelimit_cache(
                    POSTGRES_RATE_LIMIT_BACKEND,
                    table="care_ratelimit_cache_absent_on_purpose",
                ),
            },
        )

    def test_a_store_failure_does_not_raise(self):
        with (
            self.missing_table(),
            patch("config.ratelimit.validatecaptcha", return_value=True),
        ):
            ratelimit(auth_request(), "rf2-pg-fail", ["kappa"], RATE)

    def test_a_store_failure_fails_closed(self):
        # Not "under the limit". The caller meets a captcha, and a failing
        # captcha refuses them -- identical to the Redis outage path.
        with (
            self.missing_table(),
            patch("config.ratelimit.validatecaptcha", return_value=False),
        ):
            self.assertTrue(ratelimit(auth_request(), "rf2-pg-fail", ["lambda"], RATE))

    def test_a_valid_captcha_is_still_an_escape(self):
        with (
            self.missing_table(),
            patch("config.ratelimit.validatecaptcha", return_value=True),
        ):
            self.assertFalse(ratelimit(auth_request(), "rf2-pg-fail", ["mu"], RATE))

    def test_the_surrounding_transaction_survives_the_failure(self):
        # The savepoint earning its place. ATOMIC_REQUESTS wraps the whole
        # request, so without it the failed statement would poison every later
        # query -- including the ones the captcha path and the login itself
        # need, turning a fail-closed challenge into a 500.
        with self.missing_table(), transaction.atomic():
            with patch("config.ratelimit.validatecaptcha", return_value=True):
                ratelimit(auth_request(), "rf2-pg-savepoint", ["nu"], RATE)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone(), (1,))

    def test_the_failure_is_logged_without_connection_details(self):
        with (
            self.missing_table(),
            patch("config.ratelimit.validatecaptcha", return_value=False),
            self.assertLogs("config.ratelimit", level="WARNING") as logs,
        ):
            ratelimit(auth_request(), "rf2-pg-log", ["xi"], RATE)

        message = logs.output[0]
        self.assertIn("rf2-pg-log", message)
        for secret in ("password", "://", "postgres://"):
            self.assertNotIn(secret, message.lower())

    def test_only_database_errors_are_absorbed(self):
        # A programming mistake inside the limiter must still surface. The
        # except clause is scoped to DatabaseError for that reason -- a bare
        # `except Exception` here would silently convert every bug on the login
        # path into "limited".
        with (
            postgres_mode(),
            patch(
                "config.ratelimit.is_ratelimited",
                side_effect=TypeError("bug in the limiter"),
            ),
            self.assertRaises(TypeError),
        ):
            ratelimit(auth_request(), "rf2-pg-bug", ["omicron"], RATE)


class PostgresBestEffortConcurrencyTests(TransactionTestCase):
    """
    The limitation, demonstrated. This test documents; it does not accuse.

    `DatabaseCache` inherits `BaseCache.incr`, which is a `get()` followed by a
    `set()` with no row lock between them:

        read current count
                |
        concurrent requests may read the same value
                |
        both write the same incremented value
                |
        actual usage is undercounted

    Two requests that overlap in that gap therefore advance the counter once,
    and the limiter admits a request it would have refused under strict
    counting. That is what "best-effort" means, and it is why `postgres` mode
    must never be described as equivalent to Redis.

    The interleaving is forced with a barrier rather than raced for, so the
    demonstration is deterministic: a scheduling-dependent version of this test
    would pass by luck and rot into a flake. The barrier does not create the
    race -- it selects an ordering the implementation already permits, which is
    exactly the ordering two real concurrent requests can produce.

    Real PostgreSQL, real independent connections. `TransactionTestCase`
    because threads need committed data to see each other's writes.

    Do not "fix" this. Making it pass by making the increment atomic would mean
    RF2's postgres mode had quietly changed its guarantees, and the change would
    be invisible without this test failing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_ratelimit_table()

    @classmethod
    def tearDownClass(cls):
        drop_ratelimit_table()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        with postgres_mode():
            caches[RATELIMIT_CACHE_ALIAS].clear()

    def run_concurrently(self, group, key, rate, threads=2):
        """Run `threads` requests whose reads all land before any of their writes.

        The barrier sits inside `DatabaseCache.get`, which on this path is only
        reached from `BaseCache.incr`. Every thread has read before any thread
        writes -- the precise window the missing row lock leaves open.
        """
        barrier = threading.Barrier(threads, timeout=30)
        original_get = DatabaseCache.get
        results = []
        failures = []

        def synchronized_get(self, key_, default=None, version=None):
            value = original_get(self, key_, default, version)
            barrier.wait()
            return value

        def worker():
            try:
                results.append(ratelimit(auth_request(), group, [key], rate))
            except Exception as exc:
                failures.append(exc)
                # Release whoever is still waiting, so a failure surfaces as
                # this exception rather than as a barrier timeout.
                barrier.abort()
            finally:
                connections.close_all()

        # Every global mutation happens here, on one thread, before any worker
        # starts. `override_settings` and `patch` both swap process-wide state
        # and restore it on exit; entering them per thread would interleave
        # those swaps and could leave the postgres CACHES installed for the rest
        # of the suite. Settings are read by the workers, never changed by them.
        #
        # The captcha is stubbed to fail so that a request which *is* limited
        # reports True rather than raising on `WSGIRequest.data`.
        with (
            postgres_mode(),
            patch.object(DatabaseCache, "get", synchronized_get),
            patch("config.ratelimit.validatecaptcha", return_value=False),
        ):
            workers = [threading.Thread(target=worker) for _ in range(threads)]
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join(timeout=60)

        self.assertEqual(failures, [])
        return results

    def test_concurrent_requests_can_lose_an_increment(self):
        group = "rf2-pg-undercount"
        limit_rate = "2/h"

        # One request first, so the counter exists and the concurrent pair both
        # take the `incr` path rather than racing on `add`.
        with postgres_mode():
            self.assertFalse(ratelimit(auth_request(), group, ["rho"], limit_rate))
            self.assertEqual(stored_count(group + "-rho", rate=limit_rate), 1)

        self.run_concurrently(group, "rho", limit_rate)

        with postgres_mode():
            count = stored_count(group + "-rho", rate=limit_rate)

        # Three requests have now been made. Strict counting would show 3; this
        # shows 2, because the two concurrent increments read the same 1.
        self.assertEqual(count, 2)

    def test_the_undercount_admits_a_request_that_should_have_been_limited(self):
        # The consequence, which is the part that matters to a security reader.
        # With a limit of 2, the third request must be refused. Because the
        # counter lost an increment, it is allowed through instead.
        group = "rf2-pg-overshoot"
        limit_rate = "2/h"

        with postgres_mode():
            ratelimit(auth_request(), group, ["sigma"], limit_rate)

        admitted = self.run_concurrently(group, "sigma", limit_rate)

        # Neither concurrent request was limited -- including the third request
        # overall, which a strict limiter would have refused.
        self.assertEqual(admitted, [False, False])

    def test_the_same_two_requests_do_not_lose_anything_when_they_do_not_overlap(self):
        # The control, and the reason the demonstration above is not vacuous.
        # Same two threads, same independent connections, same code path -- only
        # the overlap removed, by letting each finish before the next starts.
        # The counter reaches 3, so what the barrier selects is an interleaving,
        # not a permanent defect: `postgres` mode is exact until requests
        # overlap, which is precisely what "best-effort" narrows down to.
        group = "rf2-pg-serialized"
        limit_rate = "2/h"

        with postgres_mode():
            ratelimit(auth_request(), group, ["upsilon"], limit_rate)

        for _ in range(2):
            self.run_concurrently(group, "upsilon", limit_rate, threads=1)

        with postgres_mode():
            self.assertEqual(stored_count(group + "-upsilon", rate=limit_rate), 3)

    def test_the_limiter_still_works_once_the_burst_is_over(self):
        # Best-effort, not broken. The mode undercounts a burst; it does not
        # stop counting. A caller that keeps going is still stopped.
        group = "rf2-pg-recovers"
        limit_rate = "2/h"

        with postgres_mode():
            ratelimit(auth_request(), group, ["tau"], limit_rate)
        self.run_concurrently(group, "tau", limit_rate)

        with (
            postgres_mode(),
            patch("config.ratelimit.validatecaptcha", return_value=False),
        ):
            self.assertTrue(ratelimit(auth_request(), group, ["tau"], limit_rate))

    def test_the_race_is_structural_rather_than_incidental(self):
        # Why no amount of retrying or tuning removes it: `DatabaseCache` does
        # not implement `incr` at all. It inherits `BaseCache.incr`, whose body
        # is the read-modify-write the barrier above split -- no row lock, no
        # `UPDATE ... SET value = value + 1`, no `SELECT ... FOR UPDATE`.
        #
        # The Redis contrast is asserted against a real server in
        # `test_ratelimit_backend.WhyPostgresIsBestEffortTests`. It is not
        # repeated here because reaching for `django_redis` would breach the
        # import boundary `test_cache_config` maintains.
        from django.core.cache.backends.base import BaseCache

        self.assertNotIn("incr", DatabaseCache.__dict__)
        self.assertIs(DatabaseCache.incr, BaseCache.incr)


class DisabledModeTests(SimpleTestCase):
    """
    RF2 section 8: off means off, and off is something you ask for.
    """

    def disabled(self):
        return override_settings(
            CARE_RATE_LIMIT_BACKEND=DISABLED_RATE_LIMIT_BACKEND,
            # DISABLE_RATELIMIT stays False so these assertions are about the
            # new mode rather than about the old kill switch.
            DISABLE_RATELIMIT=False,
            CACHES={"default": build_default_cache("locmem")},
        )

    def test_the_wrapper_reports_not_rate_limited(self):
        with self.disabled():
            self.assertFalse(ratelimit(auth_request(), "rf2-off", ["upsilon"], RATE))

    def test_no_caller_is_ever_limited_however_many_requests_they_make(self):
        with self.disabled():
            for _ in range(50):
                self.assertFalse(
                    ratelimit(auth_request(), "rf2-off-many", ["phi"], "1/h")
                )

    def test_django_ratelimit_is_never_called(self):
        # No counter operation. The mock raises rather than returning, so a
        # call would fail the test instead of quietly passing through it.
        with (
            self.disabled(),
            patch(
                "config.ratelimit.is_ratelimited",
                side_effect=AssertionError("django_ratelimit was called"),
            ),
        ):
            self.assertFalse(ratelimit(auth_request(), "rf2-off-lib", ["chi"], RATE))

    def test_no_cache_backend_is_touched(self):
        # Not the rate-limit alias, not `default`, not any alias. Patching the
        # handler's `__getitem__` on the type catches every consumer, including
        # the reference django_ratelimit.core bound at import.
        handler = type(caches)
        original = handler.__getitem__

        def refuse(self, alias):
            msg = f"cache alias {alias!r} was accessed with rate limiting disabled"
            raise AssertionError(msg)

        with self.disabled():
            handler.__getitem__ = refuse
            try:
                self.assertFalse(
                    ratelimit(auth_request(), "rf2-off-cache", ["psi"], RATE)
                )
            finally:
                handler.__getitem__ = original

    def test_no_redis_connection_is_opened(self):
        with self.disabled(), NoRedisContact():
            ratelimit(auth_request(), "rf2-off-redis", ["omega"], RATE)

    def test_no_rate_limit_cache_alias_is_configured(self):
        with self.disabled():
            self.assertNotIn(RATELIMIT_CACHE_ALIAS, self.settings_caches())

    def settings_caches(self):
        from django.conf import settings

        return settings.CACHES

    def test_rate_limiting_reports_itself_disabled(self):
        with self.disabled():
            self.assertFalse(rate_limiting_enabled())

    def test_an_unreachable_redis_does_not_disable_rate_limiting(self):
        # The mode must be chosen, never inferred. A Redis outage trips the
        # failure policy -- fail closed, offer a captcha -- and a deployment
        # that wants no rate limiting has to say so.
        with override_settings(
            CARE_RATE_LIMIT_BACKEND=REDIS_RATE_LIMIT_BACKEND,
            DISABLE_RATELIMIT=False,
            CACHES={
                "default": build_default_cache("locmem"),
                RATELIMIT_CACHE_ALIAS: build_ratelimit_cache(
                    REDIS_RATE_LIMIT_BACKEND, redis_url="redis://127.0.0.1:6399/0"
                ),
            },
        ):
            self.assertTrue(rate_limiting_enabled())
            with patch("config.ratelimit.validatecaptcha", return_value=False):
                self.assertTrue(ratelimit(auth_request(), "rf2-outage", ["aa"], RATE))

    def test_the_legacy_kill_switch_still_works(self):
        # DISABLE_RATELIMIT predates RF2 and local/test settings rely on it.
        with override_settings(
            CARE_RATE_LIMIT_BACKEND=REDIS_RATE_LIMIT_BACKEND, DISABLE_RATELIMIT=True
        ):
            self.assertFalse(rate_limiting_enabled())


class CallersStayBackendAgnosticTests(SimpleTestCase):
    """
    RF2 section 7: the wrapper owns the decision, and keeps owning it.

    A call site that learned to branch on the backend name would spread a
    guarantee difference across six files and make the next backend change a
    six-file change. This reads the sources rather than trusting review.
    """

    CALL_SITES = (
        "config/auth_views.py",
        "care/emr/utils/mfa.py",
        "care/users/reset_password_views.py",
    )

    def source(self, relative_path):
        from django.conf import settings

        return (settings.BASE_DIR / relative_path).read_text(encoding="utf-8")

    def test_no_call_site_names_a_rate_limit_backend(self):
        for path in self.CALL_SITES:
            body = self.source(path)
            with self.subTest(path=path):
                self.assertIn("from config.ratelimit import ratelimit", body)
                self.assertNotIn("CARE_RATE_LIMIT_BACKEND", body)
                self.assertNotIn("RATELIMIT_USE_CACHE", body)

    def test_no_call_site_reaches_for_the_counter_store(self):
        # The alias name is not searched for directly: it is `ratelimit`, which
        # is also the wrapper's own name and appears in every legitimate import
        # here. What matters is that no caller indexes the cache handler or
        # imports the library -- `auth_views` does use the `default` cache for
        # the JWT denylist, which is a different responsibility and stays.
        for path in self.CALL_SITES:
            body = self.source(path)
            with self.subTest(path=path):
                self.assertNotIn("django_ratelimit", body)
                self.assertNotIn("RATELIMIT_CACHE_ALIAS", body)
                self.assertNotIn(f'caches["{RATELIMIT_CACHE_ALIAS}"]', body)
                self.assertNotIn("from django.core.cache import caches", body)

    def test_the_wrapper_signature_is_unchanged(self):
        # Backend selection must not have leaked into the seam's own API
        # either: no `backend=` parameter, no second entry point.
        import inspect

        params = list(inspect.signature(ratelimit).parameters)
        self.assertEqual(params, ["request", "group", "keys", "rate", "increment"])


class StartupDiagnosticsTests(SimpleTestCase):
    """
    RF2 section 14. One line, at startup, that makes the weaker mode visible.
    """

    def summary(self):
        from config.runtime import runtime_summary

        return runtime_summary()

    def test_redis_reports_strict_atomic(self):
        with override_settings(CARE_RATE_LIMIT_BACKEND=REDIS_RATE_LIMIT_BACKEND):
            summary = self.summary()
        self.assertEqual(summary["rate_limit_backend"], REDIS_RATE_LIMIT_BACKEND)
        self.assertEqual(summary["rate_limit_semantics"], STRICT_ATOMIC_SEMANTICS)

    def test_postgres_reports_best_effort_rather_than_just_a_backend_name(self):
        # "postgres" alone would read as a deployment detail. The guarantee is
        # the part an operator needs, so it is stated rather than implied.
        with override_settings(CARE_RATE_LIMIT_BACKEND=POSTGRES_RATE_LIMIT_BACKEND):
            summary = self.summary()
        self.assertEqual(summary["rate_limit_backend"], POSTGRES_RATE_LIMIT_BACKEND)
        self.assertEqual(summary["rate_limit_semantics"], BEST_EFFORT_SEMANTICS)

    def test_disabled_reports_the_backend_and_claims_no_semantics(self):
        with override_settings(CARE_RATE_LIMIT_BACKEND=DISABLED_RATE_LIMIT_BACKEND):
            summary = self.summary()
        self.assertEqual(summary["rate_limit_backend"], DISABLED_RATE_LIMIT_BACKEND)
        self.assertNotIn("rate_limit_semantics", summary)

    def test_the_summary_never_carries_a_connection_string(self):
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            with (
                self.subTest(backend=backend),
                override_settings(CARE_RATE_LIMIT_BACKEND=backend),
            ):
                values = " ".join(self.summary().values()).lower()
                for secret in ("://", "password", "secret", "token"):
                    self.assertNotIn(secret, values)

    def test_it_is_emitted_once(self):
        import config.runtime

        config.runtime._summary_logged = False  # noqa: SLF001
        try:
            with (
                override_settings(CARE_RATE_LIMIT_BACKEND=POSTGRES_RATE_LIMIT_BACKEND),
                self.assertLogs("care.runtime", level="INFO") as logs,
            ):
                config.runtime.log_runtime_summary()
                config.runtime.log_runtime_summary()
        finally:
            config.runtime._summary_logged = True  # noqa: SLF001

        self.assertEqual(len(logs.output), 1)
        self.assertIn(f"rate_limit_semantics={BEST_EFFORT_SEMANTICS}", logs.output[0])


class InitRoleWithoutRedisTests(TestCase):
    """
    RF2 sections 9 and 10: the Redis-free profile initializes.

    `scripts/initialize.sh` rate-limits nothing, so it must run with Redis
    absent entirely. The cache tables it needs come from the `createcachetable`
    step it already calls -- RF2 added no command, no migration and no startup
    hook, because the existing step enumerates DatabaseCache aliases and finds
    the new one on its own.
    """

    def tearDown(self):
        drop_ratelimit_table()
        super().tearDown()

    def redis_free(self, cache_backend="postgres"):
        return override_settings(
            CARE_CACHE_BACKEND=cache_backend,
            CARE_RATE_LIMIT_BACKEND=POSTGRES_RATE_LIMIT_BACKEND,
            SILENCED_SYSTEM_CHECKS=ratelimit_silenced_checks(
                POSTGRES_RATE_LIMIT_BACKEND
            ),
            CACHES={
                "default": build_default_cache(
                    cache_backend, redis_url=REDIS_URL, table="care_cache_rf2_test"
                ),
                RATELIMIT_CACHE_ALIAS: postgres_ratelimit_alias(),
            },
        )

    def table_names(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN (%s, %s) ORDER BY table_name",
                ["care_cache_rf2_test", RATELIMIT_TEST_TABLE],
            )
            return [row[0] for row in cursor.fetchall()]

    def test_createcachetable_creates_both_tables(self):
        # CARE_CACHE_BACKEND=postgres + CARE_RATE_LIMIT_BACKEND=postgres.
        with self.redis_free(), NoRedisContact():
            call_command("createcachetable", verbosity=0)
            self.assertEqual(
                self.table_names(), ["care_cache_rf2_test", RATELIMIT_TEST_TABLE]
            )

    def test_a_redis_default_cache_creates_only_the_rate_limit_table(self):
        # CARE_CACHE_BACKEND=redis + CARE_RATE_LIMIT_BACKEND=postgres. The
        # command acts on DatabaseCache aliases only, so it must not invent a
        # table for the Redis-backed one.
        with self.redis_free(cache_backend="redis"), NoRedisContact():
            call_command("createcachetable", verbosity=0)
            self.assertEqual(self.table_names(), [RATELIMIT_TEST_TABLE])

    def test_disabled_mode_needs_no_rate_limit_table_at_all(self):
        with (
            override_settings(
                CARE_RATE_LIMIT_BACKEND=DISABLED_RATE_LIMIT_BACKEND,
                CACHES={
                    "default": build_default_cache(
                        "postgres", table="care_cache_rf2_test"
                    ),
                },
            ),
            NoRedisContact(),
        ):
            call_command("createcachetable", verbosity=0)
            self.assertEqual(self.table_names(), ["care_cache_rf2_test"])

    def test_checks_pass_with_redis_unreachable(self):
        # The check reads configuration rather than connectivity, and the
        # Redis-free profile configures no Redis to be unreachable in the first
        # place.
        with self.redis_free(), NoRedisContact():
            errors = [
                issue
                for issue in run_checks(include_deployment_checks=False)
                if isinstance(issue, Error)
                and issue.id
                not in ratelimit_silenced_checks(POSTGRES_RATE_LIMIT_BACKEND)
            ]
        self.assertEqual(errors, [])

    def test_rate_limiting_functions_after_the_table_is_created(self):
        # The end of the Redis-free chain: init creates the table, and the API
        # counts into it without ever reaching for a broker.
        with self.redis_free(), NoRedisContact():
            call_command("createcachetable", verbosity=0)
            with override_settings(DISABLE_RATELIMIT=False):
                self.assertFalse(
                    ratelimit(auth_request(), "rf2-init-e2e", ["ab"], RATE)
                )
                self.assertEqual(stored_count("rf2-init-e2e-ab"), 1)


class ManagementCheckPerModeTests(SimpleTestCase):
    """
    RF2 section 19: `manage.py check` is clean in all three modes.

    These run a real process, which is slower than calling `run_checks()` and is
    the only honest way to assert this. Django's check registry is populated
    when `django_ratelimit.checks` is imported and stays populated for the life
    of the process, so an in-process `override_settings(INSTALLED_APPS=...)`
    would leave the check registered and quietly prove nothing about the mode
    that works by not installing the app.

    The exit status is the assertion. `check` fails the process only on Errors,
    so a clean exit with W001 on stdout is exactly the postgres contract:
    startup proceeds, and the weaker guarantee is still said out loud.
    """

    def run_check(self, backend, **extra_env):
        import os
        import subprocess
        import sys

        from django.conf import settings

        # Deliberately not the test profile. `config/settings/test.py` replaces
        # CACHES wholesale and pins the rate-limit alias to Redis, so E003 would
        # never fire there and "1 silenced" would never be reachable -- Django
        # counts messages actually suppressed, not the length of the list.
        # `local` inherits base's CACHES, which is the construction under test.
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.local",
            "CARE_RATE_LIMIT_BACKEND": backend,
            **extra_env,
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, "manage.py", "check"],
            cwd=str(settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        # Django prints a clean run to stdout and a run with any surviving
        # issue to stderr, even when none of them is an Error. The report is
        # one thing to a reader, so it is one thing here.
        return result, result.stdout + result.stderr

    def test_redis_mode_checks_clean_and_silences_nothing(self):
        result, output = self.run_check(REDIS_RATE_LIMIT_BACKEND)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("0 silenced", output)

    def test_postgres_mode_checks_clean_with_one_silenced(self):
        result, output = self.run_check(POSTGRES_RATE_LIMIT_BACKEND)
        # Exit 0: the accepted error is suppressed, so nothing blocks startup.
        self.assertEqual(result.returncode, 0, output)
        # Exactly one, and it is the atomicity check. Anything higher means a
        # second suppression was added without a decision behind it.
        self.assertIn("1 silenced", output)
        # W001 audible: the suppression covers the error, not the disclosure.
        self.assertIn("django_ratelimit.W001", output)

    def test_disabled_mode_checks_clean_with_nothing_silenced(self):
        # No rate-limit alias configured, no Redis, no cache table -- and still
        # a clean check, because the app that would have validated the missing
        # alias is not installed.
        result, output = self.run_check(DISABLED_RATE_LIMIT_BACKEND)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("0 silenced", output)
        self.assertNotIn("django_ratelimit", output)

    def test_an_invalid_backend_stops_the_process(self):
        result, output = self.run_check("memcached")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid CARE_RATE_LIMIT_BACKEND", output)
        for backend in SUPPORTED_RATE_LIMIT_BACKENDS:
            self.assertIn(backend, output)


class DatabaseErrorIsNotSwallowedElsewhereTests(SimpleTestCase):
    """
    The absorbed `DatabaseError` is scoped to the postgres path.

    In `redis` mode the library's own handling applies and a `DatabaseError`
    would be a genuine surprise, so the wrapper must not have made itself the
    place database failures go to die.
    """

    def test_redis_mode_does_not_absorb_database_errors(self):
        with (
            override_settings(
                CARE_RATE_LIMIT_BACKEND=REDIS_RATE_LIMIT_BACKEND,
                DISABLE_RATELIMIT=False,
            ),
            patch(
                "config.ratelimit.is_ratelimited",
                side_effect=DatabaseError("unexpected"),
            ),
            self.assertRaises(DatabaseError),
        ):
            ratelimit(auth_request(), "rf2-redis-db-error", ["ac"], RATE)
