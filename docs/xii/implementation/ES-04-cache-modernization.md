# ES-04: Cache Modernization

- **Status:** Draft
- **Related ADR:** ADR-0004: Configurable Application Cache
- **Depends on:** completed ES-01, ES-02 and ES-03
- **Target branch:** `feature/cache-modernization`

---

# 1. Context

CARE currently uses Redis-backed Django cache for several responsibilities that
are not semantically equivalent.

The verified repository inventory identified at least these categories:

```text
performance cache
shared cache
report progress
rate limiting
distributed locking
direct Redis access
cache-wide invalidation
Celery broker/result backend
health checks
```

ES-03 already separated asynchronous execution from Redis for the initial GCP
task profile.

However, Redis still participates in application behavior outside Celery.

ADR-0004 established that cache modernization must not be implemented as:

```text
replace Redis with PostgreSQL everywhere
```

because Redis currently carries responsibilities beyond ordinary cache.

The target of this phase is narrower:

```text
Identify true cache responsibilities
        ↓
Use Django Cache API
        ↓
Support interchangeable cache backends
        ↓
PostgreSQL / Redis / LocMem / Dummy
```

while explicitly separating:

```text
distributed locks
rate limiting requiring special semantics
durable state
direct Redis coordination
```

from ordinary cache behavior.

Distributed locking remains governed by ADR-0005 and SHALL NOT be solved in
this phase.

---

# 2. Repository State

Before implementation, verify the current repository state.

The active branch MUST be:

```text
feature/cache-modernization
```

The branch MUST be based on the current `gcp` branch containing completed and
merged:

```text
ES-01
ES-02
ES-03
```

Before modifying code, report:

```bash
git status
git branch --show-current
git log -5 --oneline
git remote -v
```

Verify:

- worktree clean;
- `gcp` includes ES-03;
- `upstream` points to `https://github.com/ohcnetwork/care`;
- no unrelated local changes exist.

Do not reset, rebase, merge unrelated branches or push automatically.

---

# 3. Required Documents

Read before implementation:

```text
docs/xii/architecture/00-scope-and-goals.md
docs/xii/architecture/01-current-runtime.md
docs/xii/architecture/02-target-runtime.md
docs/xii/architecture/03-migration-plan.md
docs/xii/architecture/04-testing.md
docs/xii/architecture/06-operations.md
docs/xii/architecture/07-configuration-reference.md

docs/xii/adr/ADR-0004-cache.md
docs/xii/adr/ADR-0005-distributed-locking.md

docs/xii/implementation/ES-03-async-runtime-modernization.md

docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/unresolved-items.md
docs/xii/architecture/inventory/plugin-impact.md
```

If final repository paths differ, locate the committed files and use the actual
paths.

ADR-0004 defines the cache decision.

ADR-0005 defines the boundary this phase SHALL NOT cross.

The current source code remains authoritative for implementation details.

---

# 4. Objective

Modernize CARE's cache usage so that:

- ordinary cache behavior uses Django Cache API;
- cache backend selection is configuration-driven;
- PostgreSQL database cache is supported;
- Redis remains supported and optional;
- LocMem is supported only for process-local disposable values;
- Dummy cache remains available for tests or explicitly non-caching scenarios;
- application code does not depend on Redis-specific cache operations;
- cache failure semantics are explicit;
- report progress is classified correctly;
- cache responsibilities are separated from distributed locking;
- direct Redis cache dependencies are reduced or isolated;
- the default GCP profile can operate without Redis for ordinary caching.

This phase does NOT remove Redis from the project.

It makes Redis optional for cache responsibilities.

---

# 5. Target Architecture

The target cache architecture is:

```text
CARE cache consumer
        |
        v
Django Cache API
        |
        v
CARE_CACHE_BACKEND
        |
        +-------------------+-------------------+------------------+
        |                   |                   |                  |
        v                   v                   v                  v
   DatabaseCache         RedisCache          LocMemCache       DummyCache
        |                   |                   |                  |
        v                   v                   v                  v
   PostgreSQL            Redis-compatible    process-local        no-op
```

Consumers must not know which backend is selected.

The following are explicitly NOT part of this abstraction:

```text
distributed locks
Celery broker
Cloud Tasks
transactional state
durable progress
arbitrary Redis commands
```

---

# 6. Scope

This phase includes:

- re-verifying cache and Redis call sites;
- classifying every cache use;
- introducing/finalizing `CARE_CACHE_BACKEND`;
- PostgreSQL `DatabaseCache`;
- optional Redis-backed cache;
- LocMem profile;
- Dummy/test profile;
- removal of Redis-specific behavior from ordinary cache consumers;
- removal/replacement of `delete_pattern` where used as generic cache behavior;
- replacing direct Redis reads used only for cache semantics;
- explicit cache-key namespaces and prefixes;
- explicit cache timeouts;
- report-progress classification;
- cache health checks;
- PostgreSQL cache-table initialization requirements;
- cache-focused tests;
- documentation updates.

This phase MAY refactor rate-limiting cache plumbing only where necessary to
separate it cleanly from ordinary cache.

This phase SHALL NOT redesign rate-limiting policy unless required to prevent
incorrect generic cache assumptions.

---

# 7. Out of Scope

Do not:

- implement distributed locking;
- fix lock semantics;
- replace `cache.set(..., nx=True)` with fake compatibility;
- choose PostgreSQL advisory locks;
- add Redis locks;
- change Cloud Tasks;
- change Celery architecture;
- change file storage;
- change file transport;
- add Terraform;
- deploy Cloud Run;
- configure Memorystore;
- configure Upstash;
- implement PostgreSQL task queues;
- redesign domain models;
- redesign authentication.

ADR-0005 / ES-05 owns distributed locking.

---

# 8. Re-verify Cache and Redis Inventory

Before changing code, repeat the current search for:

```text
REDIS_URL
django_redis
django.core.cache
from django.core.cache import cache
cache.
get_redis_connection
Redis(
redis.
delete_pattern
nx=
CELERY_BROKER_URL
CELERY_RESULT_BACKEND
django_ratelimit
```

For every non-test use, record:

```text
file
symbol
line
responsibility
required semantics
shared across processes?
shared across instances?
must be durable?
requires atomicity?
requires pattern deletion?
requires TTL?
requires locking?
can safely disappear?
```

Classify each use into exactly one primary category:

```text
performance_cache
shared_cache
report_progress
rate_limit
distributed_lock
transient_state
celery_broker
celery_result_backend
health_check
direct_redis
unknown
```

If a use spans responsibilities, document that explicitly.

Do not migrate `distributed_lock` entries in this phase.

---

# 9. Cache Backend Selection

Implement or finalize:

```text
CARE_CACHE_BACKEND
```

Supported values:

```text
postgres
redis
locmem
dummy
```

Recommended GCP profile:

```text
CARE_CACHE_BACKEND=postgres
```

Local upstream-compatible profile may remain:

```text
CARE_CACHE_BACKEND=redis
```

Tests may use:

```text
CARE_CACHE_BACKEND=locmem
```

or:

```text
CARE_CACHE_BACKEND=dummy
```

depending on test semantics.

Invalid values SHALL raise a clear configuration error listing supported values.

---

# 10. Backend-Specific Requirements

## 10.1 PostgreSQL

When:

```text
CARE_CACHE_BACKEND=postgres
```

configure:

```text
django.core.cache.backends.db.DatabaseCache
```

The cache table SHALL be explicit.

Recommended setting:

```text
CARE_CACHE_TABLE=care_cache
```

The table SHALL NOT be created automatically on every application startup.

Initialization SHALL occur through:

```bash
python manage.py createcachetable
```

or an explicit initialization job.

The implementation SHALL document this requirement.

## 10.2 Redis

When:

```text
CARE_CACHE_BACKEND=redis
```

use Django's supported Redis cache backend or the project's current compatible
Redis cache implementation.

Redis configuration SHALL be URL-driven and provider-neutral.

Recommended:

```text
REDIS_CACHE_URL
```

Legacy `REDIS_URL` may remain as a fallback when unambiguous.

Do not add provider-specific switches such as:

```text
USE_UPSTASH
USE_MEMORYSTORE
```

## 10.3 LocMem

LocMem SHALL be supported only for process-local disposable cache.

Configuration must clearly document:

```text
not shared across processes
not shared across Cloud Run instances
not suitable for global rate limits
not suitable for distributed locks
not suitable for cross-instance progress
```

## 10.4 Dummy

Dummy cache SHALL be valid only for tests or explicitly non-caching
environments.

Production usage SHOULD be rejected or loudly warned unless intentionally
configured.

---

# 11. Cache Keys

Cache keys SHALL be predictable and namespaced.

Use a configurable prefix:

```text
CARE_CACHE_KEY_PREFIX
```

Recommended conceptual value:

```text
care:<environment>
```

Do not embed:

```text
provider name
Redis host
bucket
cloud project
```

in generic cache keys.

Where existing code already uses stable keys, preserve compatibility unless the
current key is incorrect.

---

# 12. Cache Timeouts

Explicitly classify cache entries by timeout.

Do not rely on one undocumented global TTL where semantics differ.

At minimum review:

```text
performance cache
report progress
schema/config cache
temporary derived values
rate-limit-adjacent values
```

Use the global:

```text
CARE_CACHE_TIMEOUT
```

only as a default.

Call sites with domain-specific lifetime requirements may use explicit TTLs.

Avoid `None` / infinite cache lifetime unless verified and intentional.

---

# 13. Standard Cache Operations

Provider-neutral consumers MAY use ordinary Django cache operations such as:

```python
cache.get(...)
cache.set(...)
cache.add(...)
cache.delete(...)
cache.get_many(...)
cache.set_many(...)
cache.clear(...)
```

subject to verified backend support.

Do not assume backend-specific keyword arguments are portable.

Specifically:

```python
cache.set(..., nx=True)
```

is NOT a generic cache operation.

Every such use SHALL remain classified under distributed locking and deferred to
ES-05.

Do not emulate `nx=True` in generic cache code.

---

# 14. `delete_pattern`

`delete_pattern()` is not part of Django's portable cache API.

Locate every use.

For each, choose the smallest correct replacement.

Preferred order:

1. delete known explicit keys;
2. maintain a small deterministic key list when necessary;
3. bump a cache version/prefix;
4. restructure invalidation around explicit domain identifiers;
5. retain Redis-specific `delete_pattern` only inside a clearly Redis-only
   optional component if there is no provider-neutral requirement.

Do not build an abstract pattern-deletion API.

Do not scan the PostgreSQL cache table manually from application consumers.

---

# 15. Direct Redis Access

Locate every:

```python
get_redis_connection(...)
```

or direct Redis client use.

For each:

- identify actual semantics;
- classify it;
- migrate it only if it is genuinely cache behavior.

If the call is:

```text
lock
rate-limit atomic counter
Redis-specific maintenance
Celery
```

do not disguise it as generic cache.

Leave it for the appropriate responsibility or isolate it behind that
responsibility's later implementation.

The end state of ES-04 should contain no direct Redis client use in ordinary
cache consumers.

---

# 16. Report Progress

Re-evaluate report-progress behavior.

Determine:

```text
is progress disposable?
must it survive cache loss?
must it survive process restart?
must it be queryable after failure?
is it user-visible?
```

Two acceptable target states are:

```text
shared cache
```

or:

```text
explicit PostgreSQL model
```

ADR-0004 allows either depending on semantics.

Do not introduce a new model unless the current requirements justify durability.

If existing progress is clearly disposable and short-lived, use the configured
shared cache.

If progress is already durable elsewhere, remove redundant cache state where
safe.

Document the decision explicitly.

---

# 17. Rate Limiting Boundary

Rate limiting is not identical to ordinary cache.

The current repository has known E7 behavior around shared Redis cache and test
workers.

ES-04 SHALL:

- identify which rate-limit implementation depends on cache;
- identify required atomicity;
- identify key dimensions;
- identify cross-instance consistency requirements;
- separate rate-limit configuration from generic cache configuration if
  necessary.

Do NOT redesign the complete rate-limit policy unless required for cache
portability.

Do NOT silently use LocMem for production-global rate limiting.

Do NOT treat DatabaseCache as automatically safe for atomic rate-limit updates
without verifying implementation semantics.

If correct PostgreSQL rate limiting requires dedicated code beyond cache, record
it as a separate unresolved item instead of implementing an unsafe approximation.

---

# 18. E7

The known E7 defect class MUST be revisited in this phase because it is directly
related to cache isolation and test correctness.

However, do not "fix" E7 by hiding shared-state behavior.

First reproduce and explain:

```text
parallel test workers
shared Redis cache
cache.clear()
global or insufficiently isolated keys
```

Then determine the smallest correct test-isolation fix.

Preferred solutions include:

```text
per-worker cache prefix
per-worker cache namespace
isolated test cache databases where supported
```

Do not change production semantics solely to make tests pass.

Separately evaluate the production rate-limit key bug already identified.

If production uses a constant or globally shared key where caller-specific
dimensions are required, fix that as a correctness issue only if the intended
key semantics are unambiguous from code/tests.

If intent is unclear, document and defer policy redesign.

---

# 19. LocMem False-Lock Behavior

The current LocMem compatibility behavior that accepts:

```python
cache.set(..., nx=True)
```

and always returns success is dangerous.

ES-04 SHALL remove the possibility that a generic cache backend silently
pretends to implement distributed locking.

Allowed outcomes:

- generic cache adapter rejects unsupported `nx`;
- lock code is prevented from using generic cache backend;
- lock behavior is isolated for ES-05.

Do not implement the real replacement lock mechanism here.

The requirement is:

```text
unsupported distributed-lock semantics must fail explicitly,
not silently succeed.
```

This is the one locking-related change permitted in ES-04 because it is required
to make cache backend selection safe.

---

# 20. Cache Failure Semantics

Every cache category SHALL define what happens when cache access fails.

For performance cache:

```text
cache failure -> miss -> recompute
```

may be acceptable.

For shared progress:

```text
cache failure -> controlled unavailable state
```

may be appropriate.

For rate limiting:

failure policy must be explicit.

Do not globally set:

```text
IGNORE_EXCEPTIONS=True
```

without considering responsibility.

Backend failure behavior may differ by selected profile.

Document intentional behavior.

---

# 21. Health Checks

Current health checks may assume Redis exists.

Refactor cache health checks so they reflect the selected cache backend.

Examples:

```text
CARE_CACHE_BACKEND=postgres
→ verify cache table / basic set-get path

CARE_CACHE_BACKEND=redis
→ verify Redis-backed cache

CARE_CACHE_BACKEND=locmem
→ no external dependency check

CARE_CACHE_BACKEND=dummy
→ report disabled/non-operational cache intentionally
```

Do not keep Redis health as a universal application requirement.

Celery health remains separate.

The known ES-03 finding that Celery queue-length health is invalid under Cloud
Tasks SHALL remain separate.

---

# 22. Initialization

When PostgreSQL database cache is selected, initialization must explicitly
create the cache table.

Integrate this into the existing explicit initialization architecture created by
ES-03.

Conceptually:

```text
initialize
  ├── migrate
  ├── createcachetable   [only when postgres cache selected]
  ├── sync_permissions_roles
  └── sync_valueset
```

Do not execute `createcachetable` on every API startup.

Do not make Redis startup a prerequisite for PostgreSQL-cache initialization.

---

# 23. Configuration Validation

Update settings validation.

Required conditional behavior:

## `postgres`

Require:

```text
database configuration
CARE_CACHE_TABLE or safe default
```

Do not require Redis URL.

## `redis`

Require:

```text
REDIS_CACHE_URL
```

or explicit supported legacy fallback.

## `locmem`

Require no external cache service.

## `dummy`

Require no external cache service.

Do not validate unused backend variables.

---

# 24. Legacy Redis Configuration

Preserve compatibility with current local Docker Compose.

If the repository currently defines:

```text
REDIS_URL
```

the local profile SHOULD continue working.

Preferred precedence:

```text
REDIS_CACHE_URL
    ↓
legacy REDIS_URL
    ↓
configuration error if Redis selected
```

Do not remove `REDIS_URL` if Celery or another unresolved responsibility still
uses it.

---

# 25. Redis Optionality

At completion, CARE under a non-Celery task profile and non-Redis cache profile
must be able to start without a Redis cache dependency.

This does NOT mean all Redis usages in the repository must disappear.

Redis may remain required for:

```text
local Celery
distributed locking until ES-05
optional rate limiting
plugin functionality
```

The application SHALL fail clearly if a selected responsibility still requires
Redis.

Do not claim "Redis-free" for a profile that still selects a Redis-dependent
lock backend.

---

# 26. Plugins

Review plugin cache assumptions.

Plugins may:

- use `django.core.cache`;
- import `django_redis`;
- call `get_redis_connection`;
- use Redis directly.

Document:

```text
provider-neutral plugin
Redis-required plugin
unknown
```

Do not break plugin Celery behavior.

Do not introduce a generic plugin cache SDK.

---

# 27. Tests — Backend Selection

Test:

```text
postgres selected
redis selected
locmem selected
dummy selected
invalid backend rejected
Redis variables not required under postgres
Redis variables not required under locmem
Redis variables not required under dummy
```

Verify generated Django `CACHES` configuration.

---

# 28. Tests — PostgreSQL Cache

Using a real PostgreSQL test database, verify:

```text
set
get
add
delete
timeout
expiration
get_many
set_many
clear
```

only where CARE actually depends on those methods.

Test cross-connection/process visibility where practical.

Verify cache table creation.

Verify missing cache table produces a clear diagnostic rather than silent
fallback.

---

# 29. Tests — Redis Cache

Using existing local Redis:

```text
set
get
delete
expiration
shared visibility
```

Verify legacy local configuration still works.

Do not test unrelated Celery behavior in cache-focused tests.

---

# 30. Tests — LocMem

Verify:

```text
basic cache works
state is process-local
no external dependency required
```

Most importantly:

```text
distributed-lock semantics are not silently accepted
```

A test SHALL prove the previous false-lock behavior cannot recur.

---

# 31. Tests — `delete_pattern`

For every removed `delete_pattern` call, add regression tests for the replacement
invalidation behavior.

Do not merely assert that `delete_pattern` disappeared.

Assert that stale cached values are actually invalidated as intended.

---

# 32. Tests — Direct Redis Removal

Add static or focused tests proving ordinary cache consumers no longer import:

```text
django_redis
get_redis_connection
redis client
```

where migrated.

Do not prohibit those imports globally while unresolved responsibilities still
legitimately use them.

---

# 33. Tests — Report Progress

Test:

```text
initial progress
update
read from another request/process where shared semantics are required
expiration
missing progress
cache failure behavior
```

If report progress remains cache-backed, verify behavior under:

```text
postgres
redis
```

where practical.

Do not require LocMem cross-instance semantics.

---

# 34. Tests — Rate Limiting / E7

Reproduce E7 before changing test isolation if possible.

Add deterministic tests for the chosen isolation fix.

Verify:

- parallel workers do not clear each other's cache namespace;
- cache clearing in one worker does not affect another worker's rate-limit state;
- production cache key semantics are not changed accidentally.

If the production constant-key bug is fixed, add caller-isolation tests.

Record the exact E7 disposition in the final report.

---

# 35. Tests — Health Checks

Test health behavior under:

```text
postgres
redis
locmem
dummy
```

No Redis health call should execute when Redis cache is not selected.

No PostgreSQL cache-table check should execute when PostgreSQL cache is not
selected.

---

# 36. Full Regression

After focused tests pass:

- rebuild if dependency/settings/container behavior changed;
- restart official local stack;
- verify services;
- run initialization;
- run focused cache tests;
- run full serial suite;
- run full parallel suite.

Because E7 belongs to this phase, the target outcome is:

```text
parallel suite reliably green
```

Do not accept the previous E7 exception procedure as the final state of ES-04.

If another unrelated known flake appears, classify it separately.

Record:

```text
seed
test count
passed
failed
skipped
duration
parallel worker count
```

---

# 37. Documentation Updates

Update:

```text
docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

Update:

```text
docs/xii/adr/ADR-0004-cache.md
```

implementation checklist.

Update configuration reference for:

```text
CARE_CACHE_BACKEND
CARE_CACHE_TABLE
CARE_CACHE_TIMEOUT
CARE_CACHE_KEY_PREFIX
REDIS_CACHE_URL
LocMem behavior
Dummy behavior
```

Update operations documentation for:

```text
createcachetable
cache health
cache maintenance
```

Do not modify ADR-0005 implementation decisions.

---

# 38. Allowed Modifications

Claude MAY modify:

- cache settings;
- cache helper modules;
- ordinary cache consumers;
- report-progress cache consumers;
- rate-limit cache plumbing where needed for portability/test correctness;
- cache health checks;
- initialization scripts for cache-table creation;
- test settings;
- cache tests;
- rate-limit tests directly related to E7;
- documentation.

Claude MAY remove:

- generic `delete_pattern` usage;
- generic direct Redis cache access;
- unsafe LocMem compatibility behavior.

---

# 39. Forbidden Modifications

Claude SHALL NOT:

- implement final distributed-lock backend;
- introduce PostgreSQL advisory locking;
- introduce Redis lock implementation;
- change Cloud Tasks;
- change Celery architecture;
- change storage;
- change file transport;
- add Terraform;
- add Memorystore;
- add Upstash-specific logic;
- implement PostgreSQL queues;
- redesign unrelated domain APIs.

---

# 40. Commit Strategy

Use focused commits.

Suggested sequence:

```text
refactor(cache): classify and isolate cache responsibilities

feat(cache): add configurable django cache backends

refactor(cache): remove backend-specific cache operations

fix(cache): isolate parallel test cache namespaces

refactor(runtime): initialize postgres cache explicitly

test(cache): cover postgres redis and local cache profiles

docs(cache): complete cache modernization
```

Exact grouping may differ if smaller logical commits are clearer.

Do not squash.

Do not push.

---

# 41. Acceptance Criteria

ES-04 is complete only when:

- cache/Redis inventory is re-verified;
- ordinary cache consumers use Django Cache API only;
- `CARE_CACHE_BACKEND` supports `postgres`, `redis`, `locmem`, `dummy`;
- PostgreSQL DatabaseCache works;
- Redis cache remains supported;
- LocMem behavior is explicitly process-local;
- Dummy profile works for tests;
- no generic cache consumer relies on `delete_pattern`;
- no generic cache consumer relies on direct Redis access;
- distributed-lock semantics cannot silently succeed through LocMem;
- report progress has an explicit backend decision;
- cache health reflects selected backend;
- PostgreSQL cache initialization is explicit;
- Redis cache is optional when not selected;
- E7 parallel-test interference is resolved;
- serial full regression is green;
- parallel full regression is reliably green;
- no distributed-lock implementation was introduced;
- ADR-0004 implementation checklist is updated.

---

# 42. Final Report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. re-verified cache/Redis call-site counts;
8. responsibility classification;
9. `CARE_CACHE_BACKEND` implementation;
10. PostgreSQL cache behavior;
11. Redis compatibility;
12. LocMem behavior;
13. Dummy behavior;
14. `delete_pattern` disposition;
15. direct Redis disposition;
16. report-progress decision;
17. rate-limit boundary decision;
18. E7 root cause and final fix;
19. LocMem false-lock disposition;
20. health-check changes;
21. initialization changes;
22. plugin findings;
23. dependencies changed;
24. focused test results;
25. serial full-suite result;
26. parallel full-suite result and seeds;
27. documentation updated;
28. unresolved cache items;
29. items explicitly deferred to ADR-0005 / ES-05;
30. deviations from ADR-0004 or ES-04;
31. final verdict:

```text
READY TO MERGE
```

or:

```text
NOT READY TO MERGE
```

Stop after ES-04.

Do not begin ES-05.

