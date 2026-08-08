"""
That distributed-lock semantics cannot be silently faked.

ES-04 sections 19 and 30. This is the one locking-related change ES-04 was
permitted to make, because without it choosing a non-Redis cache backend would
quietly remove mutual exclusion from ~25 call sites while leaving every one of
them looking correct.

The defect being locked out: `config/caches.py` used to define LocMem and Dummy
subclasses whose `set()` accepted an `nx` argument, ignored it, and returned
`True` unconditionally. `Lock.acquire` reads that return value, so under either
backend every acquisition succeeded and no lock ever excluded anything.

ES-04 does **not** implement a replacement lock. ADR-0005 and ES-05 own that.
What it guarantees is the weaker but essential property below: unsupported lock
semantics fail loudly instead of succeeding quietly.
"""

from unittest import skipUnless

from django.conf import settings
from django.core.cache import caches
from django.test import SimpleTestCase, override_settings

from care.utils.lock import Lock, MultipleItemsLock, ObjectLocked
from config import caches as caches_module
from config.caches import (
    LOCK_CACHE_ALIAS,
    build_default_cache,
    build_redis_only_cache,
)


def locks_are_reachable():
    try:
        caches[LOCK_CACHE_ALIAS].set("care_lock_probe", 1, 5)
        return True
    except Exception:
        return False


class FalseLockRegressionTests(SimpleTestCase):
    """The shim is gone and cannot come back unnoticed."""

    def test_no_cache_subclass_pretends_to_support_nx(self):
        # The shim's whole existence was two subclasses in config/caches.py.
        # If either returns, this fails before any behavioural test does.
        self.assertFalse(hasattr(caches_module, "LocMemCache"))
        self.assertFalse(hasattr(caches_module, "DummyCache"))

    def test_locmem_rejects_nx_rather_than_ignoring_it(self):
        # The heart of it. Django's LocMemCache has no `nx` parameter, so
        # passing one is a TypeError. Previously the subclass swallowed it and
        # answered True.
        with (
            override_settings(CACHES={"default": build_default_cache("locmem")}),
            self.assertRaises(TypeError),
        ):
            caches["default"].set("k", value=True, timeout=5, nx=True)

    def test_dummy_rejects_nx_rather_than_ignoring_it(self):
        with (
            override_settings(CACHES={"default": build_default_cache("dummy")}),
            self.assertRaises(TypeError),
        ):
            caches["default"].set("k", value=True, timeout=5, nx=True)

    def test_database_cache_rejects_nx_rather_than_ignoring_it(self):
        # DatabaseCache is the backend the GCP profile selects, so this is the
        # one that would silently disable locking if anyone re-pointed Lock at
        # the default cache.
        with (
            override_settings(
                CACHES={"default": build_default_cache("postgres", table="care_cache")}
            ),
            self.assertRaises(TypeError),
        ):
            caches["default"].set("k", value=True, timeout=5, nx=True)


class LockAliasIsolationTests(SimpleTestCase):
    """
    Locking does not read the ADR-0004 cache (ES-04 section 19, second allowed
    outcome: lock code is prevented from using the generic cache backend).
    """

    def test_lock_uses_its_own_alias_not_the_default_cache(self):
        from care.utils.lock import get_lock_cache

        self.assertIs(get_lock_cache(), caches[LOCK_CACHE_ALIAS])

    def test_selecting_a_non_redis_cache_does_not_change_the_lock_backend(self):
        # The regression this prevents: `CARE_CACHE_BACKEND=postgres` must not
        # silently move locking onto a backend that cannot lock. The default
        # cache below is LocMem and the lock alias stays Redis.
        from care.utils.lock import get_lock_cache

        with override_settings(
            CACHES={
                "default": build_default_cache("locmem"),
                LOCK_CACHE_ALIAS: build_redis_only_cache(
                    settings.REDIS_URL, responsibility=LOCK_CACHE_ALIAS
                ),
            }
        ):
            self.assertEqual(
                get_lock_cache().__class__.__module__, "django_redis.cache"
            )


@skipUnless(locks_are_reachable(), "requires the local Redis from docker compose")
class LockBehaviourTests(SimpleTestCase):
    """
    The lock still excludes. Unchanged behaviour, asserted because ES-04 moved
    which cache it runs against.
    """

    def setUp(self):
        self.cache = caches[LOCK_CACHE_ALIAS]
        self.cache.delete("lock:care-test-lock")
        for i in range(3):
            self.cache.delete(f"lock:care-test-multi-{i}")

    def tearDown(self):
        self.cache.delete("lock:care-test-lock")
        for i in range(3):
            self.cache.delete(f"lock:care-test-multi-{i}")

    def test_second_acquisition_is_refused(self):
        with Lock("care-test-lock"), self.assertRaises(ObjectLocked):
            Lock("care-test-lock").acquire()

    def test_lock_is_released_on_exit(self):
        with Lock("care-test-lock"):
            pass
        # Acquiring again must succeed, which it cannot if release() failed.
        with Lock("care-test-lock"):
            pass

    def test_lock_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ValueError), Lock("care-test-lock"):
            raise ValueError
        with Lock("care-test-lock"):
            pass

    def test_multiple_items_lock_releases_everything_on_partial_failure(self):
        # MultipleItemsLock acquires in order and must not leave earlier keys
        # held when a later one is contended.
        Lock("care-test-multi-1").acquire()
        try:
            with self.assertRaises(ObjectLocked):
                MultipleItemsLock(["care-test-multi-0", "care-test-multi-1"]).acquire()
            # The first key must not still be held.
            self.assertIsNone(self.cache.get("lock:care-test-multi-0"))
        finally:
            Lock("care-test-multi-1").release()
