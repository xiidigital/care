"""
Advisory distributed locks.

These are **not** cache, even though they are currently implemented on a cache
client. ADR-0005 and ES-05 own the replacement; ES-04 only moved them off the
provider-neutral ``default`` cache so that selecting a PostgreSQL, LocMem or
Dummy cache cannot silently remove mutual exclusion.

The mechanism is Redis ``SET key value NX EX ttl``: it succeeds only if the key
does not already exist, which is what makes it a lock. No portable Django cache
backend offers that -- ``DatabaseCache.add()`` is a ``SELECT`` followed by an
``INSERT`` inside a transaction, not a single atomic statement -- so this module
deliberately talks to the dedicated ``locks`` alias rather than to
``django.core.cache.cache``.

Before ES-04 a LocMem subclass in ``config/caches.py`` accepted ``nx`` and
returned ``True`` unconditionally. Every lock appeared to be acquired and none
was. That shim is gone; the regression test
``care/utils/tests/test_lock.py::LockBackendSafetyTests`` keeps it gone.
"""

from django.conf import settings
from django.core.cache import caches
from rest_framework.exceptions import APIException

from config.caches import LOCK_CACHE_ALIAS


def get_lock_cache():
    """
    Return the cache backing distributed locks.

    Resolved per call rather than at import so ``override_settings`` in tests
    takes effect, matching how ``django.core.cache.cache`` behaves.
    """
    return caches[LOCK_CACHE_ALIAS]


class ObjectLocked(APIException):
    status_code = 423
    default_detail = "The resource you are trying to access is locked"
    default_code = "object_locked"


class Lock:
    def __init__(self, key, timeout=settings.LOCK_TIMEOUT):
        self.key = f"lock:{key}"
        self.timeout = timeout

    def acquire(self):
        if not get_lock_cache().set(
            self.key, value=True, timeout=self.timeout, nx=True
        ):
            raise ObjectLocked

    def release(self):
        return get_lock_cache().delete(self.key)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


class MultipleItemsLock:
    def get_key(self, key):
        return f"lock:{key}"

    def __init__(self, keys, timeout=settings.LOCK_TIMEOUT):
        self.keys = [self.get_key(key) for key in keys]
        self.aquired_keys = []
        self.timeout = timeout

    def acquire(self):
        cache = get_lock_cache()
        for key in self.keys:
            if not cache.set(key, value=True, timeout=self.timeout, nx=True):
                self.release()
                raise ObjectLocked
            self.aquired_keys.append(key)

    def release(self):
        cache = get_lock_cache()
        for key in self.aquired_keys:
            cache.delete(key)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False
