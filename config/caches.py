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
    Not cache. It counts, and counting wants an atomic ``INCR`` that the
    portable API does not offer. It is therefore never selected by
    ``CARE_CACHE_BACKEND``; RF2 gives it its own variable,
    ``CARE_RATE_LIMIT_BACKEND``, with its own supported values and its own
    documented guarantees. The two selections are orthogonal and every
    combination of them is valid.

Distributed locking is not configured here: ES-05 uses PostgreSQL
transaction-scoped advisory locks directly. Recent views are not configured
here either: RF1 moved them to a PostgreSQL model
(``care/emr/utils/recent_views.py``), so they need no cache alias at all.

After RF2 no alias here requires Redis. Redis remains fully supported and is
still the strongest option for rate limiting, but it is now selected rather than
assumed.
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
#: therefore never selected by ``CARE_CACHE_BACKEND``. ``CARE_RATE_LIMIT_BACKEND``
#: selects it instead, so that rate limiting keeps its own store and its own
#: failure policy even when both selections name the same technology.
RATELIMIT_CACHE_ALIAS = "ratelimit"

POSTGRES_RATE_LIMIT_BACKEND = "postgres"
REDIS_RATE_LIMIT_BACKEND = "redis"
DISABLED_RATE_LIMIT_BACKEND = "disabled"

#: Values accepted by ``CARE_RATE_LIMIT_BACKEND``, strongest first.
SUPPORTED_RATE_LIMIT_BACKENDS = (
    REDIS_RATE_LIMIT_BACKEND,
    POSTGRES_RATE_LIMIT_BACKEND,
    DISABLED_RATE_LIMIT_BACKEND,
)

#: What each mode actually promises, for the startup summary. These strings are
#: the honest short form of section 15 of the RF2 brief and are deliberately not
#: interchangeable: ``postgres`` must never be reported as strict.
STRICT_ATOMIC_SEMANTICS = "strict_atomic"
BEST_EFFORT_SEMANTICS = "best_effort_non_atomic"

#: ``django_ratelimit``'s check that a cache backend cannot increment atomically.
#: Suppressed under -- and only under -- ``CARE_RATE_LIMIT_BACKEND=postgres``.
#: See :func:`ratelimit_silenced_checks`.
RATELIMIT_NON_ATOMIC_CHECK = "django_ratelimit.E003"

DEFAULT_CACHE_TABLE = "care_cache"
DEFAULT_CACHE_KEY_PREFIX = "care"
DEFAULT_CACHE_TIMEOUT = 300
DEFAULT_RATELIMIT_KEY_PREFIX = "care-ratelimit"

#: Its own table, never ``DEFAULT_CACHE_TABLE``. Rate limiting is a distinct
#: responsibility with a distinct retention profile, and sharing a table would
#: put ordinary cache entries and security counters under one ``MAX_ENTRIES``
#: cull budget.
DEFAULT_RATELIMIT_CACHE_TABLE = "care_ratelimit_cache"

#: Entry lifetime for the PostgreSQL rate-limit alias, and not a cosmetic
#: default. ``django_ratelimit`` seeds a counter with the window's own TTL, but
#: every subsequent increment goes through ``BaseCache.incr``, which re-``set``s
#: the key using the *alias* timeout instead of the remaining window. Leaving
#: Django's 300s default would silently expire the counter for any window longer
#: than five minutes -- CARE's own limits include ``10/h`` -- and an expired
#: counter restarts at zero, which admits traffic. A day comfortably outlives
#: every window CARE configures; keys are namespaced by window, so an entry that
#: outlives its window is never read again, only culled.
DEFAULT_RATELIMIT_CACHE_TIMEOUT = 86400

#: ``DatabaseCache`` culls a third of the table whenever the row count passes
#: ``MAX_ENTRIES``, and it does not care which rows. At Django's default of 300
#: that would drop live rate-limit counters under ordinary load, which is a
#: sharper failure than the non-atomic increment RF2 does accept. Raised so that
#: culling stays a housekeeping event rather than a limiter defect.
DEFAULT_RATELIMIT_MAX_ENTRIES = 10000

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


def validate_rate_limit_backend(backend: str) -> str:
    """Return ``backend`` if supported, otherwise raise ``ImproperlyConfigured``.

    Deliberately independent of :func:`validate_cache_backend`. The rate-limit
    store is not inferred from ``CARE_CACHE_BACKEND`` and never has been: a
    deployment may want PostgreSQL caching with strict Redis counters, or Redis
    caching with best-effort PostgreSQL counters, and both are valid.
    """
    if backend not in SUPPORTED_RATE_LIMIT_BACKENDS:
        supported = ", ".join(SUPPORTED_RATE_LIMIT_BACKENDS)
        msg = (
            f"Invalid CARE_RATE_LIMIT_BACKEND: {backend!r}. "
            f"Supported values are: {supported}."
        )
        raise ImproperlyConfigured(msg)
    return backend


def rate_limit_semantics(backend: str) -> str | None:
    """The guarantee ``backend`` provides, for the startup summary.

    ``None`` for ``disabled``: there is no counter, so there is nothing to
    characterise.
    """
    validate_rate_limit_backend(backend)
    if backend == REDIS_RATE_LIMIT_BACKEND:
        return STRICT_ATOMIC_SEMANTICS
    if backend == POSTGRES_RATE_LIMIT_BACKEND:
        return BEST_EFFORT_SEMANTICS
    return None


def ratelimit_installed_apps(backend: str) -> tuple[str, ...]:
    """The ``django_ratelimit`` entry for ``INSTALLED_APPS``, if it belongs there.

    The app contributes no models, no URLs and no middleware CARE uses. Its
    entire effect is ``ready()`` registering ``check_caches``, which validates
    the *rate-limit cache alias*. Under ``disabled`` there is no such alias --
    that is the whole point of the mode -- so the check would report ``E002``
    against a configuration that is exactly right.

    Not installing it is the only resolution that keeps three things true at
    once: ``manage.py check`` is clean, nothing is silenced, and disabled mode
    requires neither Redis nor a cache table. Silencing the check instead would
    have spent a suppression on a mode that has nothing to suppress, and RF2
    section 5 confines suppression to ``postgres``.

    ``django_ratelimit`` itself stays installed in the project and imported by
    the wrapper; only the app registration is conditional.
    """
    validate_rate_limit_backend(backend)
    if backend == DISABLED_RATE_LIMIT_BACKEND:
        return ()
    return ("django_ratelimit",)


def ratelimit_silenced_checks(backend: str) -> list[str]:
    """System checks CARE deliberately accepts, for ``SILENCED_SYSTEM_CHECKS``.

    Exactly one entry, in exactly one mode.

    ``django_ratelimit.E003`` is correct: ``DatabaseCache`` inherits
    ``BaseCache.incr``, a ``get()`` then a ``set()`` with no row lock, so two
    concurrent requests can both read *n* and both write *n+1*. The check is
    stating that fact, and RF2 does not dispute it -- ``postgres`` mode accepts
    the resulting undercount as the price of a Redis-free deployment, documents
    it as best-effort, and proves it in
    ``care/utils/tests/test_ratelimit_modes.py``.

    What is *not* silenced, in any mode:

    ``django_ratelimit.W001``
        "not officially supported". Left to fire under ``postgres`` on purpose.
        It is a warning, so ``manage.py check`` still exits clean, and it keeps
        the weaker guarantee visible to whoever runs it -- which is the same
        thing the startup summary does, from the other end.
    ``django_ratelimit.E001``/``E002``
        Genuine misconfiguration. Silencing these would hide a rate limiter
        pointed at nothing.

    Redis mode silences nothing and must keep passing the check on its merits.
    """
    validate_rate_limit_backend(backend)
    if backend == POSTGRES_RATE_LIMIT_BACKEND:
        return [RATELIMIT_NON_ATOMIC_CHECK]
    return []


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
    backend: str,
    *,
    redis_url: str | None = None,
    legacy_redis_url: str | None = None,
    key_prefix: str = DEFAULT_RATELIMIT_KEY_PREFIX,
    table: str = DEFAULT_RATELIMIT_CACHE_TABLE,
    timeout: int = DEFAULT_RATELIMIT_CACHE_TIMEOUT,
    max_entries: int = DEFAULT_RATELIMIT_MAX_ENTRIES,
) -> dict | None:
    """
    Build the ``CACHES["ratelimit"]`` entry for ``backend``, or ``None``.

    Never ``default``, in any mode. ``django_ratelimit`` counts with
    ``cache.add()`` followed by ``cache.incr()``, and its ``E003`` check rejects
    any backend whose ``incr`` is not atomic. While rate limiting read
    ``default``, ``CARE_CACHE_BACKEND=postgres`` therefore aborted *every*
    management command -- including the whole `init` role, which rate-limits
    nothing (`unresolved-items.md` L1). Naming its own alias through
    ``RATELIMIT_USE_CACHE`` decoupled the two, and RF2 keeps that separation
    even when both selections happen to name PostgreSQL: the responsibilities
    differ, and so do the table, the retention and the failure policy.

    **redis** -- strict. ``INCR`` is one command executed by Redis rather than a
    read-modify-write in the caller, so concurrent requests cannot lose an
    increment. This is the mode to choose when the limit is meant to be an
    actual limit, and it is the default.

    ``IGNORE_EXCEPTIONS`` is True here, deliberately. An unreachable Redis makes
    ``add()`` and ``incr()`` return None, which ``django_ratelimit`` reads as an
    unknown count; with ``RATELIMIT_FAIL_OPEN`` left False it then reports
    ``should_limit`` and CARE falls through to captcha validation. That is
    fail-closed with a captcha escape (07-configuration-reference.md §26.4).
    Letting the exception propagate would fail closed as an unhandled 500 on the
    login and password-reset paths, which is a worse way to say the same thing.

    **postgres** -- best-effort, and labelled that way everywhere. ``E003`` is
    right about ``DatabaseCache``: two concurrent requests can both read *n* and
    both write *n+1*, so a burst is undercounted and more requests get through
    than the configured rate allows. RF2 accepts that in exchange for a
    deployment that needs no Redis at all, and accepts it *explicitly* -- the
    check is suppressed only in this mode, the startup summary says
    ``best_effort_non_atomic``, and a test demonstrates the undercount rather
    than papering over it. It is not equivalent to Redis and must not be
    described as though it were.

    Two options carry real weight here rather than being tuning. ``TIMEOUT``
    outlives the longest window because ``BaseCache.incr`` re-``set``s the key
    with the alias timeout instead of the remaining window, and ``MAX_ENTRIES``
    is raised because ``DatabaseCache`` culls a third of the table at the
    threshold without regard for what it drops. See the constants above.

    **disabled** -- ``None``. No alias is configured, because none is needed:
    the wrapper returns "not limited" without reaching a backend. A missing
    alias is the honest representation of that, and it is why
    :func:`ratelimit_installed_apps` also drops the app.

    Opens no connection in any mode: this only builds a dict.
    """
    validate_rate_limit_backend(backend)

    if backend == DISABLED_RATE_LIMIT_BACKEND:
        return None

    if backend == POSTGRES_RATE_LIMIT_BACKEND:
        # LOCATION is the table name, created by the explicit `createcachetable`
        # step in scripts/initialize.sh -- which walks CACHES and picks up every
        # DatabaseCache alias, so this one needs no command of its own.
        return {
            "BACKEND": _DB_BACKEND,
            "LOCATION": table,
            "KEY_PREFIX": key_prefix,
            "TIMEOUT": timeout,
            "OPTIONS": {"MAX_ENTRIES": max_entries},
        }

    url = (redis_url or "").strip() or (legacy_redis_url or "").strip()
    if not url:
        msg = (
            f"CARE_RATE_LIMIT_BACKEND={REDIS_RATE_LIMIT_BACKEND!r} requires "
            "REDIS_RATE_LIMIT_URL (or the legacy REDIS_URL). Set "
            f"CARE_RATE_LIMIT_BACKEND={POSTGRES_RATE_LIMIT_BACKEND!r} for "
            "best-effort rate limiting without Redis, or "
            f"{DISABLED_RATE_LIMIT_BACKEND!r} to turn rate limiting off."
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


def build_ratelimit_caches(backend: str, **kwargs) -> dict:
    """The ``CACHES`` fragment for ``backend``: one alias, or none at all.

    Spread into ``CACHES`` so that ``disabled`` contributes nothing rather than
    contributing a placeholder. ``createcachetable``, ``check_caches`` and the
    wrapper all read ``CACHES``, and all three behave correctly given an absent
    alias -- provided the app is not installed either.
    """
    config = build_ratelimit_cache(backend, **kwargs)
    if config is None:
        return {}
    return {RATELIMIT_CACHE_ALIAS: config}


# ``build_redis_only_cache`` used to live here, building a Redis alias for
# responsibilities that were not ADR-0004 cache. Its last two consumers are
# gone: locking became PostgreSQL advisory locking (ES-05) and recent views
# became a PostgreSQL model (RF1). Rate limiting has its own builder above,
# with a different failure policy, so nothing generic remains to keep.
