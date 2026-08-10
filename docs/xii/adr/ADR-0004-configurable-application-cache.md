# ADR-0004: Configurable Application Cache

- **Status:** Accepted
- **Date:** 2026-08-06
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None

## Context

CARE currently configures Redis as its default Django cache.

Repository inspection identified multiple operations that appear related to Redis but do not all represent ordinary cache behavior:

- standard cache reads and writes;
- report-progress values;
- rate-limit counters;
- `cache.set(..., nx=True)` used as lock-like behavior;
- `cache.delete_pattern(...)`;
- direct `get_redis_connection()` access;
- Celery broker and result storage;
- health checks.

A backend swap from Redis to PostgreSQL or LocMem cannot safely replace all these responsibilities.

The existing LocMem shim accepts an `nx` argument while always returning success, silently removing mutual exclusion. This demonstrates that cache configuration and distributed locking must be separated.

The project wants Redis to be optional, while supporting:

- PostgreSQL-backed shared cache;
- LocMem for process-local performance values;
- Redis-compatible shared cache where beneficial;
- the existing local Redis profile.

## Decision

CARE SHALL use Django's cache framework as the abstraction for disposable cached values.

Cache backend selection SHALL be configuration-driven.

Initial supported cache profiles SHALL include:

```text
postgres
redis
locmem
dummy
```

The default local upstream-compatible profile MAY continue using Redis.

The initial low-service-count cloud profile MAY use Django's PostgreSQL database cache.

Cache SHALL NOT be used as a generic substitute for:

- distributed locks;
- durable application state;
- task queues;
- correctness-critical coordination;
- arbitrary Redis commands.

## Cache semantics

Values stored through the cache API SHALL be disposable.

Deleting or losing all cache entries SHALL not destroy durable CARE state.

Correctness-critical or auditable state SHALL use explicit PostgreSQL models or constraints.

## PostgreSQL cache

PostgreSQL cache SHALL use Django's supported database-cache backend.

It is appropriate for:

- moderate shared cache traffic;
- cross-instance disposable values;
- regenerated configuration;
- selected progress values where expiration is acceptable;
- avoiding a separate Redis service in smaller deployments.

It is not assumed to match Redis latency or throughput.

The cache table SHALL be initialized explicitly during environment setup.

## Redis cache

Redis-compatible storage MAY be used for:

- higher-frequency shared cache;
- lower-latency counters;
- deployments that already operate Redis;
- workloads where PostgreSQL cache pressure becomes excessive.

Configuration SHALL remain provider-neutral.

Upstash or another compatible service may be selected through standard Redis URLs.

## LocMem

LocMem MAY be used only for values that do not require cross-process or cross-instance consistency.

It is appropriate for:

- process-local performance optimization;
- regenerated schema data;
- test or development scenarios.

It SHALL NOT be used for:

- distributed locking;
- globally enforced rate limits;
- shared task progress;
- correctness-sensitive state.

## Report progress

Report progress SHALL be classified separately.

It may use:

- the configured shared cache when disposable progress is sufficient;
- an explicit PostgreSQL model when durability, auditability or failure history is required.

Progress values SHALL not be described or implemented as locks.

## Nonportable cache operations

Operations such as:

```text
delete_pattern
get_redis_connection
backend-specific command execution
```

SHALL not appear in provider-neutral cache consumers.

Each existing use SHALL be:

- eliminated;
- replaced with explicit key tracking;
- moved to a responsibility-specific implementation;
- retained only inside a Redis-specific optional component.

## Failure behavior

Every cache use SHALL define whether cache failure:

- becomes a cache miss;
- produces a controlled degraded response;
- blocks the operation.

Performance caches may fail open as misses.

Correctness-sensitive behavior must not rely on ignored cache exceptions.

## Consequences

### Positive

- Redis becomes optional for ordinary caching.
- PostgreSQL can provide moderate shared cache without another service.
- Cache consumers align with Django.
- Provider-specific Redis operations are isolated.
- Locks and durable state are no longer confused with caching.

### Negative

- PostgreSQL cache adds database queries and table growth.
- Different profiles have different latency characteristics.
- Existing backend-specific cache operations require refactoring.
- Some values may need dedicated models instead of cache.

## Alternatives Considered

### Replace Redis globally with DatabaseCache

Rejected.

Redis currently performs responsibilities beyond caching.

### Keep Redis mandatory

Rejected.

Smaller cloud-native deployments should not require it for ordinary caching.

### Use LocMem as the default cloud cache

Rejected for shared values.

Cloud Run instances do not share LocMem state.

### Create a custom generic cache API

Rejected.

Django already provides the required abstraction for cache semantics.

## Out of Scope

This ADR does not define:

- distributed locks;
- Celery broker selection;
- task queues;
- detailed rate-limit implementation;
- exact report-progress model;
- database sizing;
- Upstash-specific features.

## Related Documents

- Cache and Redis inventory
- ADR-0003: Configurable Asynchronous Execution
- ADR-0005: Distributed Locking
- IS-04: Cache Modernization

## Implementation Status

Delivered by ES-04 on `feature/cache-modernization`, 2026-08-09.

- [x] Decision accepted.
- [x] Cache responsibilities classified.
- [x] PostgreSQL cache implemented.
- [x] Redis cache retained as optional.
- [x] LocMem use restricted.
- [x] Backend-specific operations removed from generic consumers.
- [x] Report progress assigned to an appropriate backend.

### What that means concretely

`CARE_CACHE_BACKEND` selects `postgres`, `redis`, `locmem` or `dummy`, built and
validated in `config/caches.py`. Only the selected backend's variables are
required, so a PostgreSQL-cache deployment needs no Redis URL. The default
remains `redis` so an unconfigured checkout behaves as it did upstream.

`delete_pattern` and `get_redis_connection` no longer appear in any
provider-neutral cache consumer. Model-cache invalidation deletes explicit keys
from a registry the `@cacheable` decorator fills; the recent-views list, which
needed Redis list commands with no portable equivalent, moved to
`care/emr/utils/recent_views.py` and read its own alias.

*(RF1 has since removed that alias entirely: recent views are a PostgreSQL
model, not a cache of any kind. See "Redis compatibility, portability and the
Redis-free target" below.)*

Report progress is **shared cache**, not a model: it is a percentage whose loss
costs a duplicate render, while the durable artefacts are written independently.
It was also renamed off its misleading `set_lock`/`clear_lock` names.

### Locking, deliberately unfinished

The LocMem shim that accepted `nx` and always returned success is gone.
`LocMemCache`, `DummyCache` and `DatabaseCache` now all raise `TypeError` when
handed `nx`, so unsupported lock semantics fail loudly instead of silently.

Locking itself was **not** replaced -- ADR-0005 and ES-05 own that. It moved to a
dedicated Redis-backed `locks` alias so that selecting a non-Redis cache cannot
be mistaken for a working lock. Verified end to end: with Redis stopped and
`CARE_CACHE_BACKEND=postgres`, ordinary caching works and `Lock` raises
`ConnectionError` rather than succeeding.

Consequently CARE was **not** Redis-free at the end of ES-04: Redis remained
required for distributed locking, recent views, and the Celery profile.
ADR-0004's claim -- that Redis is optional *for ordinary caching* -- holds.

*(Locking left this list when ES-05 replaced it with PostgreSQL advisory locks,
and recent views left it when RF1 replaced them with a PostgreSQL model. The
current list is in "Redis compatibility, portability and the Redis-free target"
below.)*

### Follow-up: rate limiting decoupled from the default cache (2026-08-09)

ES-06 found that this ADR's headline selection, `CARE_CACHE_BACKEND=postgres`,
could not actually be deployed: `django_ratelimit` read the `default` cache and
its `E003` system check rejects any backend without an atomic `incr`, so every
management command aborted -- including the whole `init` role, which rate-limits
nothing. Recorded as `inventory/unresolved-items.md` L1.

That was a coupling defect, not a cache-configuration defect, and it is now
fixed. `settings.RATELIMIT_USE_CACHE` -- a seam `django-ratelimit` has always
supported and CARE had simply never set -- names a dedicated `ratelimit` alias
built by `config.caches.build_ratelimit_cache`. `CARE_CACHE_BACKEND` no longer
has any bearing on whether CARE starts.

The alias is Redis in every profile. This was the same shape as the `locks` and
`recent_views` decisions above and for the same kind of reason: no portable
Django cache backend provides the primitive. `DatabaseCache` does not implement
`incr` at all, inheriting `BaseCache`'s unlocked `get()`-then-`set()`, so two
concurrent requests lose an increment (`unresolved-items.md` K4, now with tests).
Redis `INCR` is a single server-side command.

So the list gains one entry and, since ES-05 and RF1, loses two: Redis is
required for the Celery profile **and rate limiting** -- and not for locking,
and not for recent views. `ratelimit` is now the only alias in `CACHES` that is
Redis in every profile, and `build_redis_only_cache` has been removed for want
of a caller. This ADR's claim is unchanged, and is worth restating precisely --
Redis is optional *for ordinary caching*, and this ADR governs nothing else.

`django_ratelimit.E003` is no longer silenced in any settings module.

## Redis compatibility, portability and the Redis-free target

Added 2026-08-09. Clarification only; no decision above is changed and no
implementation follows from this section.

Three statements were being conflated across the architecture set. They are
distinct and all three are true.

### 1. Compatibility

Redis remains **fully supported** and is a first-class deployment choice, not a
legacy one.

A deployment MAY deliberately select Redis for:

- the `default` cache (`CARE_CACHE_BACKEND=redis`, which is also the default);
- rate limiting;
- the Celery broker in traditional and local runtimes.

Recent views left this list with RF1. There is no configuration that puts them
back on Redis, and none is offered.

That is a supported deployment profile. Nothing in this ADR discourages Redis
where it already exists, is already operated, or is already paid for. A
deployment with a healthy Redis SHOULD use it: it is the lowest-effort
composition and the best-performing one for counters.

### 2. Portability

CARE SHALL NOT depend **architecturally** on Redis.

Redis-dependent capabilities SHALL remain isolated behind explicit seams. The
current seams are:

```text
cache alias            config/caches.py, selected by CARE_CACHE_BACKEND
rate limit wrapper     config/ratelimit.py
recent views service   care/emr/utils/recent_views.py (PostgreSQL since RF1)
async dispatcher       ADR-0003 task backend selection
```

Business code SHALL NOT know whether Redis exists. It calls the seam; the seam
resolves the backend. This is what makes the Redis choice reversible in either
direction. The recent views seam is the demonstration: callers were changed from
a Redis-keyed list to a PostgreSQL model without a single caller learning that
either existed.

### 3. Redis-free target

A fully Redis-free deployment is a **future supported profile**. It is not
implemented, and it SHALL NOT be described as if it were.

Exactly one capability still requires redesign to reach it:

- **Rate Limiting** -- replace `django-ratelimit` with a provider-neutral
  implementation supporting PostgreSQL atomic counters.

It is recorded as roadmap item **RF2** in `inventory/unresolved-items.md`,
Part RF. It belongs to no current phase; it is future modernization work and is
not designed here.

**Recent Views** was the other, recorded as **RF1**. It is done: the Redis list
operations are gone, replaced by `emr.UserValueSetRecentView` and an ordinary
recency query. Recent views is PostgreSQL-only, with no backend selector, no
fallback and no dual write.

Redis-free is therefore **not impossible** and **not already delivered**. It is
now one bounded piece of work away, and reaching it changes that one capability
rather than the application architecture -- which is the point of the seams
above.
