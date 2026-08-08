# ES-05: Distributed Locking Modernization

- **Status:** Draft
- **Related ADR:** ADR-0005: Distributed Locking as a Separate Responsibility
- **Depends on:** completed ES-01, ES-02, ES-03 and ES-04
- **Target branch:** `feature/distributed-locking-modernization`

---

# 1. Context

CARE historically implemented distributed locking through cache semantics.

The verified implementation used patterns such as:

```python
cache.set(key, value, timeout=..., nx=True)
```

This worked only because the Redis-backed cache accepted Redis-specific
semantics.

The repository also previously contained compatibility behavior in which
non-Redis cache backends accepted the `nx` argument without providing actual
distributed exclusion.

ES-04 removed that unsafe behavior.

The current architecture therefore makes the problem explicit:

```text
cache
    !=
distributed lock
```

ADR-0005 establishes that distributed locking is an independent responsibility.

This phase SHALL determine what each current lock is actually protecting before
choosing an implementation.

The goal is NOT:

```text
Redis locks
    ↓
PostgreSQL advisory locks everywhere
```

The goal is:

```text
existing lock call site
        ↓
identify protected invariant
        ↓
choose correct concurrency mechanism
        ↓
implement only what is actually required
```

Possible outcomes include:

```text
database constraint
conditional database update
row-level lock
transaction-level PostgreSQL advisory lock
Redis-backed distributed lock
process-local lock
no lock required
```

Different call sites MAY use different mechanisms.

---

# 2. Repository State

Before implementation, verify the current repository state.

The active branch MUST be:

```text
feature/distributed-locking-modernization
```

The branch MUST be based on the current `gcp` branch containing completed and
merged ES-01 through ES-04.

Before modifying code, report:

```bash
git status
git branch --show-current
git log -5 --oneline
git remote -v
```

Verify:

- worktree clean;
- ES-04 merged;
- ADR-0005 exists;
- `upstream` points to `https://github.com/ohcnetwork/care`;
- no unrelated changes exist.

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

docs/xii/adr/ADR-0005-distributed-locking.md

docs/xii/implementation/ES-03-async-runtime-modernization.md
docs/xii/implementation/ES-04-cache-modernization.md

docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/unresolved-items.md
docs/xii/architecture/inventory/plugin-impact.md
```

If final repository paths differ, locate the actual committed documents.

The ADR defines the decision.

This ES defines the implementation requirements.

The current source code remains authoritative for call-site behavior.

---

# 4. Objective

Modernize CARE's locking behavior so that:

- distributed locking is separate from Django cache;
- no lock implementation depends on generic cache extensions;
- each lock call site has explicit concurrency semantics;
- database invariants replace locks where appropriate;
- PostgreSQL-backed locking is supported where appropriate;
- Redis-backed locking remains optional where appropriate;
- non-distributed environments cannot silently impersonate distributed locking;
- lock ownership and release semantics are explicit;
- lock timeouts are explicit;
- failure behavior is explicit;
- initialization can run correctly without requiring Redis when locking can be
  provided by PostgreSQL;
- the initial GCP runtime can avoid Redis for locking if verified call-site
  semantics permit it.

This phase does NOT require eliminating Redis from the project.

---

# 5. Target Architecture

The desired architectural shape is:

```text
CARE operation
      |
      v
Concurrency requirement
      |
      +--------------------+----------------------+-------------------+
      |                    |                      |                   |
      v                    v                      v                   v
DB invariant         row/transaction lock   advisory lock       distributed lock
      |                    |                      |                   |
      v                    v                      v                   v
PostgreSQL           PostgreSQL             PostgreSQL          backend-specific
```

An application-level lock abstraction MAY exist only for call sites that truly
need an explicit lock.

The abstraction SHALL NOT hide database constraints or row-level locking behind
a fake generic API.

---

# 6. Scope

This phase includes:

- re-verifying every lock call site;
- documenting what invariant each lock protects;
- identifying whether each lock is actually required;
- replacing cache-based lock semantics;
- implementing PostgreSQL locking where appropriate;
- retaining Redis locking only where justified;
- lock ownership and release semantics;
- timeout behavior;
- failure behavior;
- concurrency tests;
- initialization-lock behavior;
- lock-specific health behavior where necessary;
- configuration where a true interchangeable lock backend is justified;
- documentation updates.

---

# 7. Out of Scope

Do not:

- redesign ordinary cache;
- change Cloud Tasks architecture;
- change Celery architecture;
- redesign file storage;
- redesign file transport;
- add Terraform;
- deploy Cloud Run;
- configure Memorystore;
- redesign rate limiting;
- redesign JWT denylist;
- redesign recent views;
- implement a general distributed coordination framework;
- introduce leader election;
- implement workflow orchestration.

---

# 8. Re-verify Lock Inventory

Before changing code, search for:

```text
Lock(
MultipleItemsLock(
cache.set(... nx=
nx=True
acquire(
release(
redis.lock
LockError
advisory
select_for_update
```

Also inspect management commands and initialization paths.

For every lock call site, record:

```text
file
symbol
line
lock key
timeout
protected operation
protected database rows
storage/external side effects
whether duplicates are harmful
whether duplicates are merely inefficient
whether the invariant can be encoded in PostgreSQL
whether all contenders share PostgreSQL
expected lock duration
possible process crash behavior
possible retry behavior
```

Classify each call site into exactly one primary category:

```text
database_constraint
conditional_update
row_lock
postgres_advisory_lock
redis_distributed_lock
process_local_lock
idempotency_record
lock_not_required
requires_analysis
```

Do not implement until classification is complete.

---

# 9. Locking Decision Rule

For each call site, choose the simplest mechanism that preserves correctness.

Preferred order:

```text
1. database invariant / constraint
2. conditional update
3. row-level transaction lock
4. transaction-level advisory lock
5. explicit distributed lock
```

An explicit distributed lock SHOULD be the last choice, not the default.

---

# 10. Database Constraints

If the real requirement is:

```text
only one durable record may exist
```

use a database constraint where practical.

Examples include:

```text
UNIQUE
UniqueConstraint
conditional UniqueConstraint
```

Do not use an external lock to compensate for a missing durable invariant.

Any migration introduced for correctness must be:

- minimal;
- explicitly justified;
- tested for existing-data compatibility.

Do not add constraints speculatively.

---

# 11. Conditional Updates

If the operation can be guarded through a state transition such as:

```text
pending -> processing
```

prefer an atomic conditional update.

Conceptually:

```python
updated = Model.objects.filter(
    pk=...,
    status="pending",
).update(status="processing")
```

Only the caller that updates one row proceeds.

This is preferable to an external lock when it directly encodes ownership in
durable state.

---

# 12. Row-Level Locking

Use:

```python
select_for_update()
```

where:

- a concrete existing row represents the protected resource;
- work occurs inside a database transaction;
- lock duration is short;
- external/network work is not held unnecessarily inside the transaction.

Do not hold database row locks while:

- uploading large files;
- waiting on remote APIs;
- generating long reports;
- sleeping/retrying externally.

---

# 13. PostgreSQL Advisory Locks

PostgreSQL advisory locks MAY be used where:

- all contenders share the same PostgreSQL database;
- no natural row exists to lock;
- the protected operation is bounded;
- coordination is required across processes/instances;
- loss of the database means CARE cannot meaningfully proceed anyway.

Prefer transaction-scoped advisory locks where possible:

```text
pg_try_advisory_xact_lock
```

or equivalent.

Transaction-scoped locks automatically release when the transaction ends.

Session-scoped advisory locks require more careful connection ownership and
SHOULD be avoided unless verified requirements demand them.

---

# 14. Advisory Lock Key Generation

If advisory locks are used, lock keys SHALL be deterministic.

Do not use Python's built-in:

```python
hash()
```

because it is process-randomized.

Use a stable algorithm.

The mapping must:

- be deterministic across processes;
- avoid obvious collisions;
- use namespace separation by lock category;
- avoid leaking sensitive identifiers in logs.

Document the chosen key derivation.

Add deterministic test vectors.

---

# 15. Connection Pooling

Advisory lock behavior must be compatible with Django database connection
management.

If transaction-scoped advisory locks are used:

- acquisition must occur inside `transaction.atomic()`;
- the protected operation must use the same database connection;
- release occurs automatically on transaction end.

Do not return a lock object whose lifetime accidentally exceeds the transaction.

If session-scoped locks are retained anywhere, explicitly test:

- connection reuse;
- release on exception;
- release on connection close;
- process termination assumptions.

---

# 16. Non-Blocking vs Blocking Acquisition

Every explicit lock call site SHALL define whether it expects:

```text
try once
```

or:

```text
wait for lock
```

Do not preserve Redis behavior blindly.

Preferred behavior for web requests is usually bounded/non-blocking acquisition.

Possible outcomes:

```text
acquired
busy
timeout
backend unavailable
```

Do not wait indefinitely.

---

# 17. Lock Timeout

Every explicit distributed lock SHALL have a documented timeout or transaction
scope.

Timeout must reflect the protected operation.

Do not use arbitrarily large defaults such as 15 minutes everywhere unless
verified.

For Redis-style lease locks, distinguish:

```text
wait timeout
lease expiration
```

These are not the same value.

For transaction-scoped PostgreSQL locks, transaction lifetime defines the lock
lifetime.

---

# 18. Lock Ownership

If an explicit lease-based backend such as Redis is retained, release SHALL
verify ownership.

A process must not be able to release another process's lock simply because it
knows the key.

Use a random ownership token or backend-native safe lock primitive.

Do not implement:

```text
GET key
DELETE key
```

as two unprotected operations.

---

# 19. Redis Lock Backend

Redis MAY remain supported where actual semantics justify it.

If retained:

- use a dedicated `locks` alias;
- do not use the configurable default cache alias;
- do not use `IGNORE_EXCEPTIONS=True`;
- use ownership-safe lock behavior;
- define lease expiry;
- define acquisition timeout;
- fail loudly when Redis is unavailable.

Do not require Redis merely because historical code did.

---

# 20. PostgreSQL Lock Backend

If multiple call sites genuinely share a generic advisory-lock semantic, a
narrow PostgreSQL lock backend MAY be introduced.

Conceptually:

```python
with lock("sync_permissions_roles", backend="postgres"):
    ...
```

However, backend selection SHALL NOT become an excuse to force fundamentally
different concurrency mechanisms through one interface.

If one operation should use `select_for_update` and another advisory locking,
implement them explicitly.

---

# 21. CARE_LOCK_BACKEND

Introduce:

```text
CARE_LOCK_BACKEND
```

ONLY if the final inventory demonstrates that the same explicit lock semantic
must support interchangeable Redis and PostgreSQL implementations.

Initial supported values MAY be:

```text
postgres
redis
```

If all current explicit locks can be implemented correctly with PostgreSQL and
Redis compatibility only needs a transitional wrapper, do NOT introduce a
configuration abstraction solely for symmetry with cache/tasks/storage.

The final decision must follow the inventory, not aesthetics.

Document whichever outcome is chosen.

---

# 22. Initialization Lock — sync_permissions_roles

This call site requires special attention.

Current initialization includes:

```text
sync_permissions_roles
```

and historically protects it with:

```text
Lock("sync_permissions_roles", 900)
```

The operation begins with behavior equivalent to marking all permissions as
temporarily deleted before rebuilding state.

Concurrent execution may therefore be unsafe.

Analyze whether the correct mechanism is:

```text
PostgreSQL advisory lock
row/table lock
transactional rewrite
single deployment-job guarantee
combination of infrastructure and DB protection
```

Do not rely solely on "Cloud Run Job should only run once".

Application/database-level protection is still desirable against accidental
concurrent invocations.

This is a likely PostgreSQL advisory-lock candidate because:

- all contenders require PostgreSQL;
- the protected state is database state;
- initialization already requires PostgreSQL;
- Redis should not be required merely to run database initialization.

But verify before implementing.

---

# 23. Other Lock Call Sites

For every remaining lock, document the actual invariant.

Examples of questions:

```text
Is the lock preventing duplicate records?
Is it preventing duplicate calculation?
Is it protecting a batch job?
Is it serializing writes to the same entity?
Is it only an optimization?
Does a database row already represent ownership?
Could the operation be idempotent instead?
```

Do not preserve locks whose only purpose disappears after a better database
invariant is implemented.

---

# 24. MultipleItemsLock

Inspect current semantics carefully.

Determine whether it means:

```text
acquire many independent resources atomically
```

or merely:

```text
sequentially acquire several locks
```

Sequential acquisition can deadlock if different callers acquire keys in
different orders.

If multi-lock semantics remain necessary:

- normalize ordering deterministically;
- define partial acquisition cleanup;
- test contention;
- test reversed-input order.

Do not implement distributed multi-lock protocols unless actual call sites
require them.

---

# 25. Deadlock Avoidance

Any code acquiring more than one lock SHALL use deterministic ordering.

For database row locks, order rows consistently.

For advisory locks, sort stable lock identifiers.

For Redis locks, sort lock names.

Add concurrency tests for opposite input order when multi-lock behavior exists.

---

# 26. Failure Semantics

Explicitly define behavior for:

```text
backend unavailable
lock busy
lock timeout
operation raises
process terminates
transaction rolls back
```

Correctness-sensitive operations SHALL NOT interpret backend failure as lock
acquisition success.

Fail-open locking is prohibited.

---

# 27. Observability

Lock operations SHOULD expose structured diagnostics without leaking sensitive
data.

Useful fields:

```text
lock category
opaque resource key/hash
backend
acquired
contended
wait duration
timeout
process role
```

Do not log patient-identifying lock names directly if avoidable.

Do not add a heavy metrics framework in this phase.

---

# 28. Async Runtime Interaction

ES-03 assumes at-least-once execution.

Locking SHALL not be used as a substitute for task idempotency.

The unresolved ES-03 findings:

```text
S5 report duplication
S6 TOTP duplicate notifications
```

must not automatically be "fixed" by wrapping tasks in locks.

Those may require durable execution records or domain-specific idempotency.

Only use locks when they protect the correct invariant.

---

# 29. Recent Views

`recent_views` remains a Redis-specific data structure from ES-04.

Do not modify it in ES-05 unless lock code still shares infrastructure with it.

Recent views are NOT distributed locking.

Do not treat removal of Redis locking as permission to remove Redis globally.

---

# 30. JWT Denylist

K6 is explicitly outside ES-05.

Do not move or redesign JWT denylist storage in this phase.

It is security state, not distributed locking.

---

# 31. Health Checks

If locking remains a required runtime dependency for a selected profile, health
reporting must make that visible.

Examples:

```text
PostgreSQL lock backend
→ database health already covers transport availability

Redis lock backend
→ explicit Redis lock dependency health
```

Do not report Redis as universally required if the lock implementation no longer
uses it.

Avoid performing destructive lock operations in public liveness checks.

A diagnostic acquire/release against a reserved lock namespace MAY be used only
if safe and necessary.

---

# 32. Configuration Validation

If a configurable lock backend exists:

## postgres

Require a working database configuration.

Do not require Redis.

## redis

Require the dedicated Redis lock configuration.

Prefer:

```text
REDIS_LOCK_URL
```

with legacy fallback where appropriate.

Do not reuse `REDIS_CACHE_URL` implicitly unless documentation deliberately
allows a shared physical instance.

Logical aliases should remain distinct even when URLs are equal.

---

# 33. Plugins

Review plugin imports/use of:

```text
care.utils.lock
Lock
MultipleItemsLock
cache.set(nx=True)
redis locks
```

Classify:

```text
compatible with modern lock API
requires Redis
uses removed behavior
unknown
```

Preserve import compatibility where practical.

Do not invent a plugin concurrency SDK.

---

# 34. Tests — Inventory and Classification

Add tests or static checks where appropriate to ensure:

- no generic cache alias is used for lock acquisition;
- no `nx=True` exists outside the approved Redis lock implementation;
- no fake lock shim exists.

Update the lock inventory with the final classification.

---

# 35. Tests — PostgreSQL Advisory Lock

If implemented, use real PostgreSQL.

Test:

```text
first transaction acquires
second concurrent transaction cannot acquire
release on commit
release on rollback
release on exception
different lock names do not contend
same lock name across separate connections contends
stable key generation
```

Do not simulate concurrency only with mocks.

Use separate DB connections/processes/threads as required by Django's test
environment.

---

# 36. Tests — Row Locks / Conditional Updates

Where those mechanisms replace locks, add real concurrency tests.

Prove the protected invariant, not merely implementation calls.

Examples:

```text
only one claimant enters processing state
concurrent update is serialized
duplicate durable record cannot be created
```

---

# 37. Tests — Redis Locks

If Redis lock support remains:

Using the existing real local Redis service, verify:

```text
first owner acquires
second owner contends
owner releases
non-owner cannot release
lease expiration behaves as documented
backend failure fails loudly
timeout works
```

Do not rely only on mocked Redis.

---

# 38. Tests — Initialization

Run two concurrent invocations of the protected initialization operation where
practical.

At minimum test the concurrency mechanism around:

```text
sync_permissions_roles
```

Prove that two contenders cannot execute the unsafe critical section
simultaneously.

Then verify normal:

```text
scripts/initialize.sh
```

still works.

---

# 39. Tests — MultipleItemsLock

If retained:

Test:

```text
same set in different input order
partial contention
cleanup after failed acquisition
exception cleanup
no deadlock under reversed caller ordering
```

If no call site requires it, remove it rather than preserving dead generic API.

---

# 40. Tests — No Redis Lock Dependency Under PostgreSQL Profile

If PostgreSQL replaces all core explicit locks, verify:

```text
Redis stopped
CARE_CACHE_BACKEND=postgres
task backend not requiring Redis
initialization
lock-protected core operation
```

works successfully.

Do not claim CARE is globally Redis-free if `recent_views` or local Celery still
require Redis in selected workflows.

The report must distinguish:

```text
Redis-free locking
```

from:

```text
Redis-free application
```

---

# 41. Full Regression

After focused tests pass:

- rebuild if dependencies/settings changed;
- restart local stack;
- verify services;
- run initialization;
- run lock concurrency tests;
- run full serial suite;
- run full parallel suite.

Record:

```text
seed
test count
passed
failed
skipped
duration
parallel workers
```

Both serial and parallel suites should be green unless a newly identified
unrelated flake is independently demonstrated.

Do not reintroduce the old E7 exception.

---

# 42. Documentation Updates

Update:

```text
docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

Update:

```text
docs/xii/adr/ADR-0005-distributed-locking.md
```

implementation checklist.

Update configuration reference only for settings actually implemented.

Update operations documentation with:

- lock backend behavior;
- initialization concurrency behavior;
- failure semantics.

---

# 43. Allowed Modifications

Claude MAY modify:

- `care/utils/lock.py` or its replacement;
- lock consumers;
- relevant database transaction logic;
- management commands protected by locks;
- lock settings;
- lock health diagnostics;
- lock tests;
- migrations only if a database invariant demonstrably requires one;
- documentation.

Claude MAY remove:

- obsolete cache-lock compatibility;
- unused generic lock helpers;
- unused multi-lock APIs.

---

# 44. Forbidden Modifications

Claude SHALL NOT:

- redesign ordinary cache;
- redesign rate limiting;
- modify JWT denylist architecture;
- modify recent views architecture;
- change Cloud Tasks;
- change Celery architecture;
- change file storage;
- change file transport;
- add Terraform;
- deploy GCP resources;
- configure Memorystore;
- introduce a general distributed systems framework.

---

# 45. Commit Strategy

Use focused commits.

Suggested sequence:

```text
docs(locking): classify existing lock semantics

refactor(locking): separate locks from django cache

feat(locking): implement postgres concurrency mechanisms

fix(runtime): protect initialization with database coordination

test(locking): cover real contention and failure semantics

docs(locking): complete distributed locking modernization
```

If Redis compatibility remains as a separate implementation, it may deserve:

```text
feat(locking): preserve optional redis lock backend
```

Do not squash.

Do not push.

---

# 46. Acceptance Criteria

ES-05 is complete only when:

- every core lock call site is re-verified;
- every lock is classified by protected invariant;
- generic Django cache is not used for distributed locking;
- no fake `nx=True` compatibility exists;
- database constraints/transactions replace locks where appropriate;
- explicit locks remain only where justified;
- PostgreSQL advisory locking is used only where justified;
- Redis locking, if retained, is explicit and optional;
- lock acquisition failures never silently succeed;
- ownership/release semantics are safe;
- multi-lock deadlock behavior is resolved or the API removed;
- `sync_permissions_roles` concurrency is safe without relying on Redis where
  the selected architecture permits it;
- initialization semantics are documented;
- real concurrency tests pass;
- serial regression is green;
- parallel regression is green;
- no unrelated cache/async/storage/runtime redesign occurred;
- ADR-0005 implementation checklist is updated.

---

# 47. Final Report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. re-verified lock call-site count;
8. classification of every lock;
9. locks removed;
10. locks replaced by constraints;
11. locks replaced by conditional updates;
12. row-lock implementations;
13. PostgreSQL advisory-lock implementations;
14. Redis lock implementations retained;
15. whether `CARE_LOCK_BACKEND` was introduced and why;
16. lock key derivation;
17. ownership/release semantics;
18. timeout semantics;
19. `MultipleItemsLock` disposition;
20. initialization / `sync_permissions_roles` disposition;
21. Redis dependency before vs after;
22. recent-views impact;
23. plugin findings;
24. migrations created, if any;
25. focused concurrency test results;
26. Redis lock integration result, if applicable;
27. PostgreSQL lock integration result, if applicable;
28. serial full-suite result;
29. parallel full-suite result;
30. documentation updated;
31. unresolved locking items;
32. items explicitly deferred;
33. deviations from ADR-0005 or ES-05;
34. final verdict:

```text
READY TO MERGE
```

or:

```text
NOT READY TO MERGE
```

Stop after ES-05.

Do not begin ES-06.
