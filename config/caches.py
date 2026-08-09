"""
Validation and construction of CARE's cache configuration.

ADR-0004 makes the cache backend a configuration choice. This module is the
settings-level half of that: it validates the selected backend name, checks that
the variables *that backend* needs are present, and builds the ``CACHES`` entries
Django consumes. It deliberately imports no application code and opens no
connection, so importing settings never touches Redis or PostgreSQL.

Only the selected backend is validated. Choosing ``postgres`` must not require a
Redis URL, and choosing ``locmem`` or ``dummy`` must not require either.

Two separate concerns live here, and keeping them apart is the point:

``default``
    The provider-neutral cache described by ADR-0004. Consumers reach it through
    ``django.core.cache`` and may use only the portable Django cache API.

Distributed locking is not configured here: ES-05 uses PostgreSQL
transaction-scoped advisory locks directly.
"""

from django.core.exceptions import ImproperlyConfigured

POSTGRES_CACHE_BACKEND = "postgres"
REDIS_CACHE_BACKEND = "redis"
LOCMEM_CACHE_BACKEND = "locmem"
DUMMY_CACHE_BACKEND = "dummy"

#: Values accepted by ``CARE_CACHE_BACKEND``.
SUPPORTED_CACHE_BACKENDS = (
    POSTGRES_CACHE_BACKEND,
    REDIS_CACHE_BACKEND,
    LOCMEM_CACHE_BACKEND,
    DUMMY_CACHE_BACKEND,
)

#: Aliases for responsibilities that are *not* ADR-0004 cache and are therefore
#: never selected by ``CARE_CACHE_BACKEND``. Each still requires Redis, and each
#: is listed here so the remaining Redis dependencies can be enumerated rather
#: than discovered at runtime.
#:
#: ``recent_views``
#:     ``care.emr.utils.recent_views`` -- needs ``LPUSH``/``LTRIM``/``LREM``.
RECENT_VIEWS_CACHE_ALIAS = "recent_views"

DEFAULT_CACHE_TABLE = "care_cache"
DEFAULT_CACHE_KEY_PREFIX = "care"
DEFAULT_CACHE_TIMEOUT = 300

_DB_BACKEND = "django.core.cache.backends.db.DatabaseCache"
_REDIS_BACKEND = "django_redis.cache.RedisCache"
_LOCMEM_BACKEND = "django.core.cache.backends.locmem.LocMemCache"
_DUMMY_BACKEND = "django.core.cache.backends.dummy.DummyCache"
_REDIS_CLIENT_CLASS = "django_redis.client.DefaultClient"


def worker_scoped_key(key, key_prefix, version):
    """
    Namespace a cache key by parallel test worker. Test settings only.

    ``manage.py test --parallel`` runs each worker in its own process against
    the *same* Redis, and the aliases that must stay on Redis (``locks``,
    ``recent_views``) would otherwise share every key. Lock keys are the sharp
    edge: several are constants -- ``PatientCreateLock`` has no per-object
    component -- so two workers would contend for one lock and one would see a
    spurious 423.

    Django sets ``_worker_id`` in each worker after settings are loaded, and on
    a forking platform the parent's value is already fixed by then, so this is
    read per operation rather than baked into ``KEY_PREFIX``. Outside a parallel
    run it is 0 and the key is stable.
    """
    from django.test.runner import _worker_id

    return f"{key_prefix}:w{_worker_id}:{version}:{key}"


def validate_cache_backend(backend: str) -> str:
    """Return ``backend`` if supported, otherwise raise ``ImproperlyConfigured``."""
    if backend not in SUPPORTED_CACHE_BACKENDS:
        supported = ", ".join(SUPPORTED_CACHE_BACKENDS)
        msg = (
            f"Invalid CARE_CACHE_BACKEND: {backend!r}. "
            f"Supported values are: {supported}."
        )
        raise ImproperlyConfigured(msg)
    return backend


def resolve_redis_cache_url(
    redis_cache_url: str | None, legacy_redis_url: str | None
) -> str:
    """
    Resolve the Redis URL for cache use, newest variable first.

    Precedence is ``REDIS_CACHE_URL`` then the legacy ``REDIS_URL`` (ES-04
    section 24). ``REDIS_URL`` is kept because Celery still reads it; this
    function only decides which value the *cache* uses.

    The URL is never logged. It routinely carries a password.
    """
    url = (redis_cache_url or "").strip() or (legacy_redis_url or "").strip()
    if not url:
        msg = (
            f"CARE_CACHE_BACKEND={REDIS_CACHE_BACKEND!r} requires REDIS_CACHE_URL "
            "(or the legacy REDIS_URL) to be set."
        )
        raise ImproperlyConfigured(msg)
    return url


def build_default_cache(
    backend: str,
    *,
    redis_url: str | None = None,
    legacy_redis_url: str | None = None,
    table: str = DEFAULT_CACHE_TABLE,
    key_prefix: str = DEFAULT_CACHE_KEY_PREFIX,
    timeout: int = DEFAULT_CACHE_TIMEOUT,
    ignore_exceptions: bool = True,
) -> dict:
    """
    Build the ``CACHES["default"]`` entry for ``backend``.

    ``timeout`` is the *default* entry lifetime only. Call sites whose data has
    its own lifetime pass an explicit TTL and are unaffected by it
    (ES-04 section 12).
    """
    validate_cache_backend(backend)

    if backend == POSTGRES_CACHE_BACKEND:
        # LOCATION is the table name. The table is created by an explicit
        # `createcachetable` step, never on startup -- see scripts/initialize.sh
        # and ES-04 section 22.
        return {
            "BACKEND": _DB_BACKEND,
            "LOCATION": table,
            "KEY_PREFIX": key_prefix,
            "TIMEOUT": timeout,
        }

    if backend == REDIS_CACHE_BACKEND:
        return {
            "BACKEND": _REDIS_BACKEND,
            "LOCATION": resolve_redis_cache_url(redis_url, legacy_redis_url),
            "KEY_PREFIX": key_prefix,
            "TIMEOUT": timeout,
            "OPTIONS": {
                "CLIENT_CLASS": _REDIS_CLIENT_CLASS,
                # Mimics memcached behaviour: a Redis outage degrades to a cache
                # miss rather than an exception. That is the right default for
                # the performance and shared caches, and it is why the `locks`
                # alias below sets it to False -- a lock must not fail open.
                # The JWT denylist also reads through this cache and therefore
                # fails open; that is a pre-existing hazard recorded in
                # unresolved-items.md, not a decision made here.
                "IGNORE_EXCEPTIONS": ignore_exceptions,
            },
        }

    if backend == LOCMEM_CACHE_BACKEND:
        # Process-local. Not shared across processes or Cloud Run instances,
        # and therefore not valid for global rate limits, distributed locks or
        # cross-instance progress (ES-04 section 10.3).
        return {
            "BACKEND": _LOCMEM_BACKEND,
            "LOCATION": key_prefix,
            "KEY_PREFIX": key_prefix,
            "TIMEOUT": timeout,
        }

    return {
        "BACKEND": _DUMMY_BACKEND,
        "KEY_PREFIX": key_prefix,
        "TIMEOUT": timeout,
    }


def build_redis_only_cache(redis_url: str | None, *, responsibility: str) -> dict:
    """
    Build a Redis-backed alias for a responsibility that is not ADR-0004 cache.

    Always Redis, independent of ``CARE_CACHE_BACKEND``, because each caller
    needs a Redis command no portable Django cache backend offers --
    ``SET ... NX`` for locking, list operations for recent views.

    These read ``REDIS_URL``, not ``REDIS_CACHE_URL``: they are different
    responsibilities from caching, and a deployment may point the cache at a
    managed Redis while these stay elsewhere.

    ``IGNORE_EXCEPTIONS`` is False. A swallowed exception would turn a failed
    lock acquisition into an apparent success -- precisely the silent failure
    ES-04 section 19 exists to eliminate -- and would silently drop recent-view
    writes.

    Nothing here *implements* locking. It is the existing Redis mechanism given
    its own alias, so that a PostgreSQL, LocMem or Dummy ``default`` cache
    cannot be mistaken for one. ES-05 replaces the locking half.
    """
    url = (redis_url or "").strip()
    if not url:
        msg = (
            f"{responsibility} requires REDIS_URL to be set. It is not part of "
            "CARE_CACHE_BACKEND and has no portable backend yet."
        )
        raise ImproperlyConfigured(msg)
    return {
        "BACKEND": _REDIS_BACKEND,
        "LOCATION": url,
        "KEY_PREFIX": f"care-{responsibility}",
        "OPTIONS": {
            "CLIENT_CLASS": _REDIS_CLIENT_CLASS,
            "IGNORE_EXCEPTIONS": False,
        },
    }
