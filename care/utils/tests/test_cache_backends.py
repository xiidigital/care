"""
Behaviour of each configurable cache backend against the real services.

Covers ES-04 sections 28 (PostgreSQL), 29 (Redis) and 30 (LocMem). The suite's
own cache is LocMem, so these tests select a backend explicitly rather than
relying on whatever the test profile happens to use.

Only the operations CARE actually performs are asserted. The inventory in
`docs/xii/architecture/inventory/cache-and-redis.md` section 5 lists them:
`get`, `set`, `add`, `delete`, `delete_many`, `get_or_set` and `clear`, plus
`add`/`incr` reached indirectly through django_ratelimit.
"""

from unittest import skipUnless

from django.conf import settings
from django.core.cache import caches
from django.core.management import call_command
from django.db import ProgrammingError, connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from config.caches import build_default_cache

POSTGRES_TEST_TABLE = "care_cache_test"


def redis_url_with_database(url: str, database: int) -> str:
    """Point ``url`` at a specific Redis database, replacing any existing index."""
    base = url.rstrip("/")
    head, _, tail = base.rpartition("/")
    if tail.isdigit():
        base = head
    return f"{base}/{database}"


#: A Redis database of its own for the cache tests below.
#:
#: They exercise `clear()`, and django_redis implements that as FLUSHDB, which
#: empties the entire database and ignores KEY_PREFIX -- the same mechanism that
#: caused E7. Sharing database 0 with the `ratelimit` alias would let this
#: module flush counters a concurrent worker is asserting on, so it gets a
#: database the rest of the suite never touches. Per-worker key prefixes cannot
#: help here: FLUSHDB does not look at keys at all.
REDIS_LOCATION = redis_url_with_database(settings.REDIS_URL, 15)

POSTGRES_CACHES = {
    "default": build_default_cache(
        "postgres", table=POSTGRES_TEST_TABLE, key_prefix="care-test", timeout=300
    )
}
LOCMEM_CACHES = {"default": build_default_cache("locmem", key_prefix="care-test")}
DUMMY_CACHES = {"default": build_default_cache("dummy", key_prefix="care-test")}
REDIS_CACHES = {
    "default": build_default_cache(
        "redis", redis_url=REDIS_LOCATION, key_prefix="care-cache-backend-test"
    )
}


class CacheOperationsMixin:
    """
    The portable operations CARE relies on.

    Reused across backends deliberately: ADR-0004's claim is that consumers do
    not need to know which backend is configured, and that is only true if the
    same assertions hold against each one.
    """

    def cache(self):
        return caches["default"]

    def test_set_then_get(self):
        self.cache().set("alpha", {"value": 1})
        self.assertEqual(self.cache().get("alpha"), {"value": 1})

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.cache().get("absent"))

    def test_get_missing_returns_the_supplied_default(self):
        self.assertEqual(self.cache().get("absent", "fallback"), "fallback")

    def test_add_does_not_overwrite_an_existing_key(self):
        self.cache().set("beta", "first")
        self.assertFalse(self.cache().add("beta", "second"))
        self.assertEqual(self.cache().get("beta"), "first")

    def test_add_writes_when_the_key_is_absent(self):
        self.assertTrue(self.cache().add("gamma", "written"))
        self.assertEqual(self.cache().get("gamma"), "written")

    def test_delete_removes_the_key(self):
        self.cache().set("delta", "x")
        self.cache().delete("delta")
        self.assertIsNone(self.cache().get("delta"))

    def test_delete_many_removes_every_named_key(self):
        # This is what replaced delete_pattern in
        # care/emr/resources/tag/cache_invalidation.py and resources/base.py.
        self.cache().set("epsilon:1", "a")
        self.cache().set("epsilon:2", "b")
        self.cache().delete_many(["epsilon:1", "epsilon:2"])
        self.assertIsNone(self.cache().get("epsilon:1"))
        self.assertIsNone(self.cache().get("epsilon:2"))

    def test_get_or_set_computes_and_stores(self):
        # care/utils/models/base.py relies on this.
        self.assertEqual(
            self.cache().get_or_set("zeta", lambda: "computed"), "computed"
        )
        self.assertEqual(self.cache().get("zeta"), "computed")

    def test_zero_timeout_expires_immediately(self):
        self.cache().set("eta", "value", 0)
        self.assertIsNone(self.cache().get("eta"))

    def test_explicit_timeout_is_honoured_over_the_default(self):
        self.cache().set("theta", "value", 300)
        self.assertEqual(self.cache().get("theta"), "value")

    def test_incr_counts_up(self):
        # django_ratelimit increments a counter it created with add().
        self.cache().add("iota", 1, 60)
        self.assertEqual(self.cache().incr("iota"), 2)

    def test_clear_empties_the_cache(self):
        self.cache().set("kappa", "value")
        self.cache().clear()
        self.assertIsNone(self.cache().get("kappa"))


@override_settings(CACHES=POSTGRES_CACHES)
class PostgresCacheTests(CacheOperationsMixin, TestCase):
    """ES-04 section 28, against the real PostgreSQL test database."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # DDL inside the class-level atomic block: the table exists for these
        # tests and is rolled back afterwards, so no state leaks into --keepdb.
        with override_settings(CACHES=POSTGRES_CACHES):
            call_command("createcachetable", verbosity=0)

    def setUp(self):
        super().setUp()
        caches["default"].clear()

    def test_createcachetable_created_the_table(self):
        self.assertIn(POSTGRES_TEST_TABLE, connection.introspection.table_names())

    def test_values_live_in_the_table_not_in_the_process(self):
        # The property that matters for Cloud Run: unlike LocMem, a write is
        # visible to anything reading the same database, not just this process.
        caches["default"].set("shared", "value")
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {POSTGRES_TEST_TABLE}")  # noqa: S608
            self.assertGreater(cursor.fetchone()[0], 0)


@override_settings(
    CACHES={
        "default": build_default_cache(
            "postgres", table="care_cache_does_not_exist", key_prefix="care-test"
        )
    }
)
class PostgresMissingTableTests(TestCase):
    """
    ES-04 section 28: a missing cache table must produce a clear diagnostic
    rather than a silent fallback.

    Silently behaving like a cache miss would be the dangerous outcome -- every
    read would recompute, the service would look merely slow, and the missing
    initialization step would never be noticed.
    """

    def test_reading_a_missing_cache_table_raises(self):
        # atomic() wraps the call rather than the assertion so the failed
        # statement rolls back to a savepoint; otherwise the surrounding test
        # transaction stays aborted.
        with self.assertRaises(ProgrammingError) as ctx, transaction.atomic():
            caches["default"].get("anything")
        self.assertIn("care_cache_does_not_exist", str(ctx.exception))


@override_settings(CACHES=LOCMEM_CACHES)
class LocMemCacheTests(CacheOperationsMixin, SimpleTestCase):
    """ES-04 section 30."""

    def setUp(self):
        super().setUp()
        caches["default"].clear()

    def test_requires_no_external_service(self):
        # Nothing to assert about a connection; the point is that the whole
        # mixin above passes with neither Redis nor a cache table present.
        self.assertEqual(
            caches["default"].__class__.__module__,
            "django.core.cache.backends.locmem",
        )

    def test_state_is_process_local(self):
        # Two aliases pointing at different LOCATIONs do not share storage,
        # which is the same mechanism that makes two processes not share it.
        with override_settings(
            CACHES={
                "default": build_default_cache("locmem", key_prefix="one"),
                "other": build_default_cache("locmem", key_prefix="two"),
            }
        ):
            caches["default"].set("shared", "value")
            self.assertIsNone(caches["other"].get("shared"))


def redis_is_reachable():
    """Whether the configured Redis answers, so a skip says why rather than erroring."""
    try:
        with override_settings(CACHES=REDIS_CACHES):
            caches["default"].set("care_redis_probe", 1)
            return caches["default"].get("care_redis_probe") == 1
    except Exception:
        return False


@skipUnless(redis_is_reachable(), "requires the local Redis from docker compose")
@override_settings(CACHES=REDIS_CACHES)
class RedisCacheTests(CacheOperationsMixin, SimpleTestCase):
    """
    ES-04 section 29, against the local Redis.

    Redis stays a supported cache backend; ADR-0004 makes it optional, not
    removed. Celery is deliberately not exercised here -- it is a separate
    responsibility that merely happens to share a URL.
    """

    def setUp(self):
        super().setUp()
        caches["default"].clear()

    def tearDown(self):
        caches["default"].clear()
        super().tearDown()

    def test_legacy_redis_url_alone_configures_the_cache(self):
        # ES-04 section 24: the existing local Docker Compose profile sets only
        # REDIS_URL and must keep working without anyone adding a new variable.
        config = build_default_cache(
            "redis", redis_url=None, legacy_redis_url=settings.REDIS_URL
        )
        with override_settings(CACHES={"default": config}):
            caches["default"].set("legacy", "value")
            self.assertEqual(caches["default"].get("legacy"), "value")
            caches["default"].delete("legacy")

    def test_writes_are_visible_to_a_second_client(self):
        # The property LocMem cannot provide: another process reading the same
        # Redis sees the write.
        caches["default"].set("cross", "value")
        with override_settings(
            CACHES={
                "second": build_default_cache(
                    "redis",
                    redis_url=REDIS_LOCATION,
                    key_prefix="care-cache-backend-test",
                )
            }
        ):
            self.assertEqual(caches["second"].get("cross"), "value")


@override_settings(CACHES=DUMMY_CACHES)
class DummyCacheTests(SimpleTestCase):
    """
    ES-04 section 10.4: Dummy is valid for tests and explicitly non-caching
    environments. It must be honest about storing nothing.
    """

    def test_writes_are_discarded(self):
        caches["default"].set("key", "value")
        self.assertIsNone(caches["default"].get("key"))

    def test_add_reports_success_but_stores_nothing(self):
        self.assertTrue(caches["default"].add("key", "value"))
        self.assertIsNone(caches["default"].get("key"))
