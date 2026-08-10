"""
Validation and construction of CARE's cache configuration.

ADR-0004 makes the cache backend a configuration choice. This module is the
settings-level half of that: it validates the selected backend name, checks that
the variables *that backend* needs are present, and builds the ``CACHES`` entries
Django consumes. It deliberately imports no application code and opens no
connection, so importing settings never touches Redis or PostgreSQL.

Only the selected backend is validated. Choosing ``postgres`` must not require a
Redis URL, and choosing ``locmem`` or ``dummy`` must not require either.

Separate concerns live here, and keeping them apart is the point:

``default``
    The provider-neutral cache described by ADR-0004. Consumers reach it through
    ``django.core.cache`` and may use only the portable Django cache API.

``ratelimit``
    Not cache. It needs an atomic ``INCR``, which the portable API does not
    offer, so it is not selected by ``CARE_CACHE_BACKEND`` and is listed
    explicitly -- the remaining Redis dependencies should be enumerable rather
    than discovered when something breaks. It is now the only such alias.

Distributed locking is not configured here: ES-05 uses PostgreSQL
transaction-scoped advisory locks directly. Recent views are not configured
here either: RF1 moved them to a PostgreSQL model
(``care/emr/utils/recent_views.py``), so they need no cache alias at all.
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

#: The one alias for a responsibility that is *not* ADR-0004 cache and is
#: therefore never selected by ``CARE_CACHE_BACKEND``. It still requires Redis,
#: and is named here so the remaining Redis dependency can be enumerated rather
#: than discovered at runtime.
#:
#: ``ratelimit``
#:     ``django_ratelimit`` -- needs an atomic ``INCR``. See
#:     :func:`build_ratelimit_cache` for why no portable backend qualifies.
RATELIMIT_CACHE_ALIAS = "ratelimit"

DEFAULT_CACHE_TABLE = "care_cache"
DEFAULT_CACHE_KEY_PREFIX = "care"
DEFAULT_CACHE_TIMEOUT = 300
DEFAULT_RATELIMIT_KEY_PREFIX = "care-ratelimit"

_DB_BACKEND = "django.core.cache.backends.db.DatabaseCache"
_REDIS_BACKEND = "django_redis.cache.RedisCache"
_LOCMEM_BACKEND = "django.core.cache.backends.locmem.LocMemCache"
_DUMMY_BACKEND = "django.core.cache.backends.dummy.DummyCache"
_REDIS_CLIENT_CLASS = "django_redis.client.DefaultClient"


def worker_scoped_key(key, key_prefix, version):
    """
    Namespace a cache key by parallel test worker. Test settings only.

    ``manage.py test --parallel`` runs each worker in its own process against
    the *same* Redis, and the one alias that must stay on Redis (``ratelimit``)
    would otherwise share every key: its buckets are keyed on the test client's
    fixed 127.0.0.1, so two workers would fill one bucket and one would see a
    spurious 429.

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


def build_ratelimit_cache(
    redis_url: str | None = None,
    legacy_redis_url: str | None = None,
    *,
    key_prefix: str = DEFAULT_RATELIMIT_KEY_PREFIX,
) -> dict:
    """
    Build the ``CACHES["ratelimit"]`` entry. Always Redis, never ``default``.

    **Why this alias exists.** ``django_ratelimit`` counts with ``cache.add()``
    followed by ``cache.incr()``, and its ``E003`` system check rejects any
    backend whose ``incr`` is not atomic. Before this alias it read ``default``,
    so ``CARE_CACHE_BACKEND=postgres`` made *every management command* abort --
    including the whole `init` role, which never rate-limits anything. Rate
    limiting now names its own alias through ``RATELIMIT_USE_CACHE``, so the
    ADR-0004 cache choice no longer decides whether CARE can start
    (`unresolved-items.md` L1).

    **Why not PostgreSQL.** ``DatabaseCache`` does not override ``incr``; it
    inherits ``BaseCache.incr``, which is a ``get()`` then a ``set()`` on two
    separate statements with no row lock. Two concurrent requests both read *n*
    and both write *n+1*, so the limit overshoots. ``E003`` is stating a fact,
    not being cautious, and silencing it would convert a startup error into a
    silent limiter defect (`unresolved-items.md` K4). Redis ``INCR`` is a single
    atomic command, which is the guarantee actually required.

    So Redis stays mandatory *for rate limiting* even though ADR-0004 made it
    optional for ordinary cache. CARE is not Redis-free; it is Redis-optional
    for the `default` cache and Redis-required here.

    **Failure policy.** ``IGNORE_EXCEPTIONS`` is True here, deliberately.
    An unreachable Redis makes ``add()`` and ``incr()`` return None,
    which ``django_ratelimit`` reads as an unknown count; with
    ``RATELIMIT_FAIL_OPEN`` left False it then reports ``should_limit`` and CARE
    falls through to captcha validation. That is fail-closed with a captcha
    escape, which is what 07-configuration-reference.md §26.4 asks for. Letting
    the exception propagate instead would fail closed as an unhandled 500 on the
    login and password-reset paths, which is a worse way to say the same thing.

    Opens no connection: this only builds a dict.
    """
    url = (redis_url or "").strip() or (legacy_redis_url or "").strip()
    if not url:
        msg = (
            "Rate limiting requires REDIS_RATE_LIMIT_URL (or the legacy "
            "REDIS_URL). It is not part of CARE_CACHE_BACKEND: django_ratelimit "
            "needs an atomic INCR, which no portable cache backend provides."
        )
        raise ImproperlyConfigured(msg)
    return {
        "BACKEND": _REDIS_BACKEND,
        "LOCATION": url,
        "KEY_PREFIX": key_prefix,
        "OPTIONS": {
            "CLIENT_CLASS": _REDIS_CLIENT_CLASS,
            "IGNORE_EXCEPTIONS": True,
        },
    }


# ``build_redis_only_cache`` used to live here, building a Redis alias for
# responsibilities that were not ADR-0004 cache. Its last two consumers are
# gone: locking became PostgreSQL advisory locking (ES-05) and recent views
# became a PostgreSQL model (RF1). Rate limiting has its own builder above,
# with a different failure policy, so nothing generic remains to keep.
