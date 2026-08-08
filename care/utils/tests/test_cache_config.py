"""
Cache configuration: backend selection, validation and the Redis boundary.

Covers ES-04 sections 23, 27 and 32. These tests exercise the settings-level
functions directly rather than reloading settings modules, so they assert what
CARE will actually construct without needing four processes to do it.
"""

import ast
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.caches import (
    DUMMY_CACHE_BACKEND,
    LOCK_CACHE_ALIAS,
    LOCMEM_CACHE_BACKEND,
    POSTGRES_CACHE_BACKEND,
    RECENT_VIEWS_CACHE_ALIAS,
    REDIS_CACHE_BACKEND,
    SUPPORTED_CACHE_BACKENDS,
    build_default_cache,
    build_redis_only_cache,
    resolve_redis_cache_url,
    validate_cache_backend,
)

REDIS_URL = "redis://localhost:6379"
CACHE_URL = "redis://cache.example:6379/2"


class CacheBackendValidationTests(SimpleTestCase):
    def test_every_documented_backend_is_accepted(self):
        for backend in ("postgres", "redis", "locmem", "dummy"):
            with self.subTest(backend=backend):
                self.assertEqual(validate_cache_backend(backend), backend)

    def test_supported_backends_are_exactly_the_four_in_adr_0004(self):
        self.assertEqual(
            set(SUPPORTED_CACHE_BACKENDS),
            {"postgres", "redis", "locmem", "dummy"},
        )

    def test_invalid_backend_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_cache_backend("memcached")
        self.assertIn("memcached", str(ctx.exception))

    def test_rejection_lists_the_supported_values(self):
        # A configuration error that does not say what is allowed just moves the
        # guessing to the operator.
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_cache_backend("")
        message = str(ctx.exception)
        for backend in SUPPORTED_CACHE_BACKENDS:
            self.assertIn(backend, message)


class CachesConstructionTests(SimpleTestCase):
    """The generated Django CACHES entry for each backend (ES-04 section 27)."""

    def test_postgres_selects_the_database_cache_and_names_its_table(self):
        config = build_default_cache(
            POSTGRES_CACHE_BACKEND, table="care_cache", key_prefix="care", timeout=300
        )
        self.assertEqual(
            config["BACKEND"], "django.core.cache.backends.db.DatabaseCache"
        )
        self.assertEqual(config["LOCATION"], "care_cache")
        self.assertEqual(config["KEY_PREFIX"], "care")
        self.assertEqual(config["TIMEOUT"], 300)

    def test_redis_selects_the_redis_cache(self):
        config = build_default_cache(REDIS_CACHE_BACKEND, redis_url=CACHE_URL)
        self.assertEqual(config["BACKEND"], "django_redis.cache.RedisCache")
        self.assertEqual(config["LOCATION"], CACHE_URL)

    def test_locmem_selects_the_locmem_cache(self):
        config = build_default_cache(LOCMEM_CACHE_BACKEND)
        self.assertEqual(
            config["BACKEND"], "django.core.cache.backends.locmem.LocMemCache"
        )

    def test_dummy_selects_the_dummy_cache(self):
        config = build_default_cache(DUMMY_CACHE_BACKEND)
        self.assertEqual(
            config["BACKEND"], "django.core.cache.backends.dummy.DummyCache"
        )

    def test_explicit_timeout_is_only_a_default_not_a_ceiling(self):
        # ES-04 section 12: CARE_CACHE_TIMEOUT is the default entry lifetime.
        # Call sites with their own lifetime pass an explicit TTL.
        config = build_default_cache(LOCMEM_CACHE_BACKEND, timeout=42)
        self.assertEqual(config["TIMEOUT"], 42)


class RedisNotRequiredTests(SimpleTestCase):
    """
    ES-04 section 23: only the selected backend's variables are validated.

    The point of ADR-0004 is that a PostgreSQL-cache deployment does not need a
    Redis URL. If any of these raised, Redis would still be mandatory in
    practice while the documentation claimed otherwise.
    """

    def test_postgres_does_not_require_a_redis_url(self):
        config = build_default_cache(
            POSTGRES_CACHE_BACKEND, redis_url=None, legacy_redis_url=None
        )
        self.assertEqual(
            config["BACKEND"], "django.core.cache.backends.db.DatabaseCache"
        )

    def test_locmem_does_not_require_a_redis_url(self):
        build_default_cache(LOCMEM_CACHE_BACKEND, redis_url=None, legacy_redis_url=None)

    def test_dummy_does_not_require_a_redis_url(self):
        build_default_cache(DUMMY_CACHE_BACKEND, redis_url=None, legacy_redis_url=None)

    def test_redis_does_require_a_redis_url(self):
        with self.assertRaises(ImproperlyConfigured):
            build_default_cache(
                REDIS_CACHE_BACKEND, redis_url=None, legacy_redis_url=None
            )


class RedisUrlPrecedenceTests(SimpleTestCase):
    """ES-04 section 24: REDIS_CACHE_URL, then legacy REDIS_URL, then error."""

    def test_redis_cache_url_wins(self):
        self.assertEqual(resolve_redis_cache_url(CACHE_URL, REDIS_URL), CACHE_URL)

    def test_legacy_redis_url_is_the_fallback(self):
        # This is what keeps the existing local Docker Compose profile working
        # without anyone having to add a new variable.
        self.assertEqual(resolve_redis_cache_url(None, REDIS_URL), REDIS_URL)

    def test_blank_redis_cache_url_falls_through_to_the_legacy_variable(self):
        self.assertEqual(resolve_redis_cache_url("   ", REDIS_URL), REDIS_URL)

    def test_neither_set_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_redis_cache_url(None, None)

    def test_error_does_not_leak_a_url(self):
        # Redis URLs routinely carry a password.
        with self.assertRaises(ImproperlyConfigured) as ctx:
            resolve_redis_cache_url("", "")
        self.assertNotIn("redis://", str(ctx.exception))


class RedisOnlyAliasTests(SimpleTestCase):
    """
    Locking and recent views are not cache and are not selected by
    CARE_CACHE_BACKEND (ES-04 sections 15 and 19).
    """

    def test_lock_alias_is_redis_regardless_of_cache_backend(self):
        config = build_redis_only_cache(REDIS_URL, responsibility=LOCK_CACHE_ALIAS)
        self.assertEqual(config["BACKEND"], "django_redis.cache.RedisCache")

    def test_lock_alias_does_not_swallow_exceptions(self):
        # A swallowed error would turn a failed acquisition into an apparent
        # success -- the exact silent failure ES-04 section 19 forbids.
        config = build_redis_only_cache(REDIS_URL, responsibility=LOCK_CACHE_ALIAS)
        self.assertFalse(config["OPTIONS"]["IGNORE_EXCEPTIONS"])

    def test_aliases_do_not_share_a_key_namespace(self):
        lock = build_redis_only_cache(REDIS_URL, responsibility=LOCK_CACHE_ALIAS)
        views = build_redis_only_cache(
            REDIS_URL, responsibility=RECENT_VIEWS_CACHE_ALIAS
        )
        self.assertNotEqual(lock["KEY_PREFIX"], views["KEY_PREFIX"])

    def test_missing_redis_url_fails_loudly(self):
        # ES-04 section 25: the application fails clearly if a selected
        # responsibility still requires Redis.
        with self.assertRaises(ImproperlyConfigured) as ctx:
            build_redis_only_cache(None, responsibility=LOCK_CACHE_ALIAS)
        self.assertIn("REDIS_URL", str(ctx.exception))


class GenericCacheKeyTests(SimpleTestCase):
    """ES-04 section 11: generic cache keys carry no provider detail."""

    def test_key_prefix_carries_no_provider_or_infrastructure_detail(self):
        config = build_default_cache(
            REDIS_CACHE_BACKEND, redis_url=CACHE_URL, key_prefix="care"
        )
        prefix = config["KEY_PREFIX"]
        for forbidden in ("redis", "upstash", "gcs", "s3", "cache.example", "6379"):
            self.assertNotIn(forbidden, prefix.lower())


class DirectRedisBoundaryTests(SimpleTestCase):
    """
    ES-04 section 32: ordinary cache consumers must not reach for Redis.

    Deliberately an allowlist rather than a blanket ban. Responsibilities that
    genuinely still need Redis -- locking until ES-05, the recent-views list --
    are permitted and named, so this test documents the remaining surface
    instead of pretending it is empty.
    """

    REPO_ROOT = Path(__file__).resolve().parents[3]

    #: Modules allowed to name django_redis, and why.
    ALLOWED = {
        # Builds the CACHES entries; names the backend as a string.
        "config/caches.py",
        # The isolated Redis-only recent-views component.
        "care/emr/utils/recent_views.py",
        # Test settings construct the Redis-only aliases.
        "config/settings/test.py",
        # This test.
        "care/utils/tests/test_cache_config.py",
    }

    def _python_files(self):
        for directory in ("care", "config"):
            yield from (self.REPO_ROOT / directory).rglob("*.py")

    def test_no_ordinary_consumer_imports_django_redis(self):
        offenders = []
        for path in self._python_files():
            relative = path.relative_to(self.REPO_ROOT).as_posix()
            if relative in self.ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "django_redis"
                ):
                    offenders.append(f"{relative}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("django_redis"):
                            offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [], f"django_redis imported outside {self.ALLOWED}")

    def test_no_consumer_calls_delete_pattern(self):
        # delete_pattern is a django_redis extension, not part of Django's
        # portable cache API. ES-04 section 14 removed the only two call sites.
        offenders = []
        for path in self._python_files():
            relative = path.relative_to(self.REPO_ROOT).as_posix()
            if relative in self.ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "delete_pattern":
                    offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_models_package_does_not_depend_on_redis_at_import_time(self):
        # care/emr/models/valueset.py used to import get_redis_connection at
        # module level, which made the whole models package fail to import
        # without django_redis installed.
        source = (self.REPO_ROOT / "care/emr/models/valueset.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("django_redis", source)
        self.assertNotIn("get_redis_connection", source)
