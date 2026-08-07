# ES-03: Async Runtime Modernization

- **Status:** Draft
- **Related ADR:** ADR-0003: Configurable Asynchronous Execution
- **Depends on:** completed ES-01 and ES-02
- **Target branch:** `feature/async-runtime-modernization`

---

# 1. Context

CARE currently uses Celery and Redis for asynchronous execution.

The verified runtime inventory established that task decoration does not imply
asynchronous intent.

The repository contains a limited number of task definitions, but several
task-decorated functions are called synchronously.

The inventory established approximately:

```text
8 task definitions
4 actual asynchronous dispatch call sites
16 non-test synchronous calls to task-decorated functions
```

The exact current counts SHALL be re-verified against the current `gcp` branch
before implementation.

CARE also currently uses Celery Beat startup for operational work including:

- database migrations;
- permission synchronization;
- value-set synchronization;
- periodic cleanup registration.

This creates an undesirable coupling between:

```text
application initialization
Celery
Redis
periodic scheduling
asynchronous execution
```

ADR-0003 established that these responsibilities must be separated.

The target architecture is portable:

```text
Reusable CARE operation
        |
        +-- direct synchronous call
        |
        +-- Celery wrapper
        |
        +-- configurable async dispatcher
                    |
                    +-- Celery backend
                    |
                    +-- Cloud Tasks backend
```

Cloud Tasks is the initial GCP asynchronous backend.

It is not the application architecture.

---

# 2. Repository State

Before implementation, verify the current repository state.

The active branch MUST be:

```text
feature/async-runtime-modernization
```

The branch MUST be based on the current `gcp` branch containing completed and
merged ES-01 and ES-02.

Before modifying code, report:

```bash
git status
git branch --show-current
git log -5 --oneline
git remote -v
```

Verify:

- the worktree is clean;
- ES-01 is merged;
- ES-02 is merged;
- `upstream` points to `https://github.com/ohcnetwork/care`;
- the branch contains no unrelated changes.

Do not reset, rebase, merge unrelated work or push automatically.

---

# 3. Required Documents

Read before implementation:

```text
docs/architecture/00-scope.md
docs/architecture/01-current-runtime.md
docs/architecture/02-target-runtime.md
docs/architecture/03-migration-plan.md
docs/architecture/04-testing.md
docs/architecture/06-operations.md
docs/architecture/07-configuration.md

docs/adr/ADR-0003-asynchronous-execution.md

docs/specifications/ES-01-storage-modernization.md
docs/specifications/ES-02-file-transport-modernization.md

docs/xii/architecture/inventory/task-call-sites.md
docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

If final repository paths differ, locate the committed files and use the actual
paths.

The ADR defines the architectural decision.

This ES defines the implementation requirements.

The current source code remains authoritative for implementation details.

---

# 4. Objective

Modernize CARE's asynchronous execution model so that:

- reusable task behavior is independent of Celery;
- synchronous call sites remain synchronous;
- only verified asynchronous call sites use async dispatch;
- local and traditional deployments may continue using Celery;
- the initial GCP profile may use Cloud Tasks;
- Cloud Tasks does not appear in CARE domain logic;
- task payloads are explicit and JSON-serializable;
- retry semantics are provider-neutral where possible;
- initialization does not depend on Celery Beat;
- periodic work is exposed as explicit reusable commands;
- the implementation remains portable.

This phase does NOT remove Celery from CARE.

It removes Celery as the definition of CARE task behavior.

---

# 5. Target Architecture

The target structure is:

```text
CARE request / command / domain caller
                |
                +-------------------------------+
                |                               |
                v                               v
        synchronous call               async dispatch
                |                               |
                v                               v
        reusable operation            CARE task dispatcher
                                                |
                                +---------------+---------------+
                                |                               |
                                v                               v
                         Celery backend                  Cloud Tasks backend
                                |                               |
                                v                               v
                         Celery worker                  private HTTP worker
                                |                               |
                                +---------------+---------------+
                                                |
                                                v
                                      reusable operation
```

The same operation may therefore be:

- called synchronously;
- invoked by Celery;
- invoked through Cloud Tasks.

No operation SHALL require Celery merely to be callable.

---

# 6. Scope

This phase includes:

- re-verifying task definitions and call sites;
- separating reusable logic from Celery wrappers where required;
- preserving synchronous invocation semantics;
- introducing a narrow async dispatch API;
- implementing a Celery dispatch backend;
- implementing a Cloud Tasks dispatch backend;
- implementing a private task HTTP execution endpoint;
- explicit task registration;
- JSON task payload validation;
- transaction-aware dispatch;
- provider-neutral retry classification where required;
- fixing the report-generation retry issue tracked as S2;
- separating migrations/setup from Celery Beat startup;
- exposing periodic task logic through management commands;
- preserving local Celery and Celery Beat compatibility;
- task-focused tests;
- documentation updates.

---

# 7. Out of Scope

Do not:

- replace Redis cache;
- fix E7;
- redesign rate limiting;
- redesign distributed locks;
- implement PostgreSQL cache;
- implement PostgreSQL task queue;
- add Terraform;
- deploy to GCP;
- configure Cloud Scheduler infrastructure;
- configure Cloud Run infrastructure;
- change storage architecture;
- change file transport;
- redesign unrelated domain services;
- add a general workflow engine.

---

# 8. Re-verify the Task Inventory

Before changing code, repeat the current search for:

```text
@shared_task
@app.task
.delay(
.apply_async(
send_task(
AsyncResult
CELERY_RESULT_BACKEND
add_periodic_task
crontab
```

For every current task record:

- source path;
- task name;
- synchronous call sites;
- asynchronous call sites;
- payload arguments;
- return value;
- caller use of result;
- caller use of task ID;
- retries;
- autoretry settings;
- countdown/delay;
- expiry;
- database writes;
- storage writes;
- external calls;
- idempotency concerns;
- periodic registration.

Update `task-call-sites.md` before or alongside implementation if upstream or
previous phases changed the inventory.

Do not convert a call site merely because the target function is decorated.

---

# 9. Preserve Synchronous Semantics

Every currently synchronous call SHALL remain synchronous unless this ES
explicitly identifies a reason to change it.

For example, if current code does:

```python
rebalance_account_task(...)
```

and does not call:

```python
rebalance_account_task.delay(...)
```

the target implementation SHALL continue to perform the operation synchronously.

Preferred result:

```python
rebalance_account(...)
```

for reusable logic, with:

```python
@shared_task(...)
def rebalance_account_task(...):
    return rebalance_account(...)
```

where a Celery compatibility wrapper is still needed.

Do not silently introduce eventual consistency into synchronous request flows.

---

# 10. Extract Reusable Operations

Where a Celery task contains the actual application logic, extract that logic
into an ordinary callable.

Preferred conceptual form:

```python
def generate_report(...):
    ...
```

with:

```python
@shared_task(...)
def generate_report_task(...):
    return generate_report(...)
```

and a Cloud Tasks handler calling:

```python
generate_report(...)
```

Requirements:

- ordinary callable;
- no Celery task request dependency;
- no Cloud Tasks dependency;
- explicit arguments;
- explicit return behavior;
- testable directly.

Do not extract functions merely for aesthetic consistency if the task wrapper is
already a trivial delegator.

---

# 11. Narrow Async Dispatch Contract

Introduce a small internal dispatch boundary.

Conceptually:

```python
enqueue_task(
    task_name,
    payload,
    *,
    delay_seconds=None,
    task_id=None,
)
```

The exact names may follow existing repository conventions.

The contract SHALL support only currently verified needs.

Do not implement speculative concepts such as:

```text
chains
groups
chords
workflow DAGs
arbitrary callbacks
arbitrary import paths
dynamic Python callables
```

The dispatcher SHALL select an implementation through configuration.

---

# 12. Backend Selection

Introduce or finalize:

```text
CARE_TASK_BACKEND
```

Initial supported values:

```text
celery
cloud_tasks
```

Default local/traditional behavior SHALL remain:

```text
celery
```

The GCP profile SHALL be able to select:

```text
cloud_tasks
```

Invalid values SHALL fail clearly.

Do not make Cloud Tasks settings mandatory when Celery is selected.

Do not make Celery/Redis settings mandatory when Cloud Tasks is selected,
except for unrelated cache responsibilities that remain outside this ES.

---

# 13. Task Registry

Cloud Tasks requests SHALL not contain arbitrary Python import paths.

Implement an explicit task registry.

Conceptually:

```python
TASK_HANDLERS = {
    "send_totp_enabled_email": send_totp_enabled_email,
    "send_totp_disabled_email": send_totp_disabled_email,
    "generate_report": generate_report,
}
```

Use actual verified task names.

Requirements:

- stable external task name;
- explicit handler mapping;
- no `eval`;
- no `import_string` from user payload;
- no arbitrary callable resolution;
- deterministic registration.

Unknown task names SHALL be rejected.

---

# 14. Task Payloads

Cloud-compatible task payloads SHALL be JSON serializable.

Allowed examples:

```text
UUID strings
integer IDs
strings
booleans
small dictionaries
timestamps represented explicitly
```

Do not dispatch:

- model instances;
- querysets;
- uploaded files;
- open streams;
- exception objects;
- provider clients;
- credentials;
- complete clinical objects where IDs suffice.

Handlers SHALL reload current application state from PostgreSQL.

Document payload schemas for each migrated async task.

---

# 15. Transaction-Aware Dispatch

Where an asynchronous task depends on newly committed database state, dispatch
SHALL occur after successful transaction commit.

Use Django transaction facilities, typically:

```python
transaction.on_commit(...)
```

Test:

- commit → task dispatched;
- rollback → task not dispatched.

Do not enqueue a Cloud Task that can race ahead of an uncommitted CARE record.

---

# 16. Celery Backend

Preserve Celery for:

- local Docker Compose;
- upstream-compatible development;
- traditional installations;
- explicit Celery deployments.

The Celery dispatcher SHALL call the existing thin task wrappers.

Preserve existing:

- task names;
- queues where meaningful;
- delay/countdown behavior;
- expiry where meaningful;
- retry configuration where still applicable.

Do not break current local workers.

---

# 17. Cloud Tasks Backend

Implement the initial GCP asynchronous backend using the official Google Cloud
Tasks client library.

Use Application Default Credentials.

Do not require service-account JSON files.

The dispatcher SHALL construct authenticated HTTP tasks targeted at CARE's
private task worker.

Required configuration includes, according to the configuration reference:

```text
GCP_PROJECT_ID
GCP_TASKS_LOCATION
GCP_TASKS_QUEUE
GCP_WORKER_URL
GCP_TASKS_SERVICE_ACCOUNT
GCP_TASKS_OIDC_AUDIENCE
```

Only selected-backend validation should require these.

---

# 18. Cloud Tasks HTTP Request

Use:

```text
POST
Content-Type: application/json
```

The request body SHALL include an explicit task envelope.

Conceptually:

```json
{
  "task": "generate_report",
  "payload": {
    "report_id": "..."
  },
  "version": 1
}
```

Use the smallest shape required.

Do not include credentials or entire domain records.

Support delayed execution only where an existing verified call site uses
countdown/delay behavior.

---

# 19. Private Task Worker Endpoint

Add a dedicated internal HTTP endpoint.

It SHALL:

- accept POST only;
- parse JSON;
- validate the envelope;
- validate task name;
- validate payload;
- execute a registered handler;
- return 2xx only on successful execution;
- return an appropriate retryable response for transient failures;
- avoid leaking exception internals;
- not log complete task payloads.

The endpoint SHALL be designed to be protected by Cloud Run IAM.

Application-level checks MAY add defense in depth but SHALL not pretend to
replace platform IAM.

Do not expose this as a normal public user API operation.

---

# 20. Worker Role Separation

If the current deployment structure allows route-level separation, support a
worker process role such as:

```text
CARE_PROCESS_ROLE=task_worker
```

The public API role SHOULD not expose the internal task execution route where
cleanly avoidable.

If the current Django routing structure makes that unnecessarily invasive, use
the smallest safe implementation and document the limitation for ES-06.

Do not add Cloud Run infrastructure here.

---

# 21. Retry Semantics

Retry policy SHALL be based on operation failure semantics, not storage-provider
exception names.

This specifically resolves S2.

Current code contains behavior similar to:

```python
autoretry_for=(botocore.exceptions.ClientError,)
```

which works under S3 but not GCS.

Refactor retry classification so report generation can distinguish:

```text
transient/retryable failure
permanent/non-retryable failure
```

without depending directly on:

```text
botocore.ClientError
google.api_core exception types
```

inside the Celery task definition.

Preferred architecture:

```text
provider/library exception
        ↓
operation boundary
        ↓
provider-neutral retryable exception
        ↓
Celery / Cloud Tasks retry behavior
```

Do not build a large exception hierarchy.

One or a few narrowly scoped application exceptions are sufficient.

---

# 22. Cloud Tasks Retry Behavior

Cloud Tasks retries based primarily on HTTP status.

The worker SHALL map execution outcomes intentionally.

Conceptually:

```text
success
→ 2xx

transient/retryable failure
→ non-2xx allowing retry

permanent validation/business failure
→ terminal/non-retry behavior according to queue policy
```

Do not return success after a failed operation merely to prevent retries.

Do not retry malformed task payloads indefinitely.

Infrastructure retry limits remain an ES-06/ES-07 concern, but application
behavior must support correct classification.

---

# 23. Idempotency

Assume at-least-once execution.

Analyze every migrated asynchronous task.

For each, document whether duplicate execution can cause:

- duplicate email;
- duplicate report;
- duplicate DB record;
- repeated storage write;
- repeated cleanup;
- harmless repetition.

Use the smallest reliable idempotency mechanism.

Prefer:

- unique constraints;
- existing status fields;
- conditional updates;
- existing generated object identity;
- explicit execution/idempotency record only when needed.

Do not use Redis as the sole idempotency mechanism.

---

# 24. Email Tasks

Inspect TOTP and other asynchronously dispatched email tasks.

Preserve current email behavior.

Move reusable email behavior outside Celery only where necessary.

Test:

- Celery dispatch;
- Cloud Tasks dispatch;
- worker execution;
- retry behavior;
- duplicate-execution behavior.

Do not redesign email templates or providers.

---

# 25. Report Generation

Report generation requires special attention because:

- it performs storage operations;
- it currently contains provider-specific retry behavior;
- it may be longer-running than simple notification tasks;
- callers may inspect progress or task state.

This ES SHALL:

- extract reusable report-generation execution if required;
- remove storage-provider-specific retry classification;
- preserve current report generation behavior;
- preserve progress semantics;
- migrate asynchronous dispatch through the selected backend.

Do not move report progress to a new cache/model architecture unless required
for task correctness.

That broader decision belongs to later cache/state work.

If current progress depends on Celery result objects, document and replace only
the minimum necessary result dependency.

---

# 26. Task Results

Search for use of:

```text
AsyncResult
task.id
result.get()
CELERY_RESULT_BACKEND
```

For every actual result consumer, decide whether the value represents:

```text
transport state
application state
user-visible progress
durable result
```

Do not reproduce Celery Result Backend semantics in Cloud Tasks.

Where application state matters, use existing database state.

Where no caller uses results, do not add result persistence.

---

# 27. Periodic Work

Periodic scheduling is separate from asynchronous dispatch.

Identify all current Beat registrations.

Expose underlying periodic operations as ordinary Django management commands or
reusable functions.

Expected examples include:

```text
cleanup_expired_token_slots
cleanup_incomplete_file_uploads
```

The commands SHALL:

- run directly;
- be idempotent;
- return appropriate exit status;
- work on empty state;
- not require a Celery worker.

Preserve Celery Beat wrappers/registration for local compatibility where
needed.

Do not implement Cloud Scheduler Terraform here.

---

# 28. Migrations and Initialization

The verified runtime currently performs important setup during Celery/Beat
startup.

This coupling must be removed.

Identify exact startup execution of:

```text
python manage.py migrate
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

Refactor so these operations can be run explicitly and independently.

Normal API startup SHALL not depend on Celery Beat performing them.

Normal task-worker startup SHALL not perform migrations automatically in the
target architecture.

Local Docker compatibility may temporarily preserve current startup sequencing
if removing it would unnecessarily break upstream development, but the code
must expose explicit independent commands for the future cloud runtime.

Document exactly what remains local-only.

---

# 29. Celery Beat Compatibility

Do not remove Celery Beat from local Docker Compose in ES-03 unless doing so is
both trivial and fully compatible.

Local periodic behavior may remain:

```text
Celery Beat
→ reusable operation / management command
```

The important requirement is that production runtime is no longer dependent on
Beat-specific implementation.

Do not add Cloud Scheduler here.

---

# 30. Plugins

Re-run plugin-impact analysis for task behavior.

Plugins may introduce tasks through:

```python
autodiscover_tasks()
```

The core explicit Cloud Tasks registry cannot automatically support arbitrary
third-party plugin Celery tasks.

Document:

- current core support;
- plugin task compatibility;
- how a plugin may register an async handler in the future;
- which plugin scenarios remain Celery-only.

Do not invent a large plugin SDK.

If a tiny explicit registration mechanism naturally supports plugins, it may be
added.

---

# 31. Configuration

Update configuration according to ADR-0003.

At minimum support:

```text
CARE_TASK_BACKEND=celery|cloud_tasks
```

Cloud Tasks variables should be conditionally required.

Celery variables remain active only for Celery profile.

Do not remove Redis configuration globally because Redis still serves unrelated
responsibilities.

Do not change cache configuration.

---

# 32. Dependency Management

Add only required GCP task dependencies.

Use the official Google Cloud Tasks Python client.

Use the repository dependency manager and lockfile workflow.

Do not upgrade unrelated direct dependencies.

Do not remove Celery.

Do not remove Redis client dependencies.

Record dependency changes in the final report.

---

# 33. Tests — Dispatcher

Test:

```text
celery backend selection
cloud_tasks backend selection
invalid backend
task name
payload serialization
delay
task ID if supported
backend error
unknown task
```

Mock the Cloud Tasks client for unit tests.

Ordinary tests must not require GCP credentials.

---

# 34. Tests — Cloud Tasks Request Construction

Verify:

- correct queue parent;
- worker URL;
- POST method;
- JSON content;
- content type;
- OIDC service account;
- OIDC audience;
- delay/schedule time where used;
- task name when explicitly provided.

Do not assert unnecessary internal protobuf formatting.

---

# 35. Tests — Worker

Test:

- POST succeeds for registered task;
- GET rejected;
- malformed JSON rejected;
- unknown task rejected;
- invalid payload rejected;
- successful execution returns 2xx;
- retryable execution failure returns retryable non-2xx;
- permanent invalid request does not masquerade as retriable work;
- no arbitrary import/callable execution exists.

Platform IAM itself is tested later in deployed runtime.

---

# 36. Tests — Synchronous Calls

This is mandatory.

For every task-derived function currently called synchronously, verify that the
updated caller still executes synchronously.

Do not allow the async modernization to change these semantics accidentally.

At least one explicit regression test SHALL prove the most important current
synchronous call path remains inline.

---

# 37. Tests — Transaction Dispatch

Test:

```text
commit -> enqueue
rollback -> no enqueue
```

for any call site migrated to `transaction.on_commit`.

---

# 38. Tests — Retry Portability

Add tests proving report-generation retry classification does not depend on S3.

At minimum simulate:

```text
retryable storage/provider failure
non-retryable application failure
```

The tests must pass regardless of whether:

```text
CARE_STORAGE_BACKEND=s3
CARE_STORAGE_BACKEND=gcs
```

No task retry definition should import `botocore.ClientError` merely to support
object storage.

The unrelated AWS SNS SMS integration remains untouched.

---

# 39. Tests — Idempotency

Test duplicate invocation for each migrated async task where duplicate execution
could be harmful.

At minimum review:

```text
emails
reports
cleanup
```

Do not create artificial idempotency infrastructure for harmless tasks.

---

# 40. Tests — Periodic Commands

Test management commands directly.

Verify:

- empty state succeeds;
- expected matching records are processed;
- repeated execution succeeds;
- exit status is correct.

Preserve any existing Celery Beat tests.

---

# 41. Full Regression

After focused tests pass:

- rebuild the application image if dependencies changed;
- restart the official local stack;
- verify backend/celery/db/redis/minio health;
- verify migrations;
- verify sync commands;
- run focused async tests;
- run full serial suite;
- run documented parallel suite.

Record:

```text
seed
test count
passed
failed
skipped
duration
```

Serial must be green.

E7 remains out of scope and may be interpreted only according to its documented
procedure.

No deterministic async-runtime regression is acceptable.

---

# 42. Documentation Updates

Update:

```text
docs/xii/architecture/inventory/task-call-sites.md
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

Update:

```text
docs/adr/ADR-0003-asynchronous-execution.md
```

implementation checklist.

Update configuration reference for:

```text
CARE_TASK_BACKEND
Cloud Tasks settings
process role where implemented
```

Do not modify ADR-0004/0005 decisions.

---

# 43. Allowed Modifications

Claude MAY modify:

- task modules;
- task call sites;
- narrow dispatcher modules;
- Cloud Tasks backend module;
- internal task worker API;
- task tests;
- management commands for current periodic operations;
- Celery wrappers;
- startup scripts where migration/setup coupling is removed;
- task-related settings;
- dependency files;
- task/runtime inventories;
- ADR-0003 status.

---

# 44. Forbidden Modifications

Claude SHALL NOT modify:

- Redis cache behavior;
- rate limiting;
- E7;
- distributed locking;
- PostgreSQL cache;
- storage architecture;
- file transport;
- Terraform;
- Cloud Run resource definitions;
- Cloud SQL infrastructure;
- GitHub Actions;
- unrelated domain APIs;
- SNS SMS boto3 behavior.

---

# 45. Commit Strategy

Use focused commits.

Suggested sequence:

```text
refactor(tasks): separate reusable operations from celery wrappers

feat(tasks): add configurable async dispatcher

feat(tasks): add cloud tasks backend and private worker

refactor(tasks): make retry semantics provider-neutral

refactor(runtime): expose periodic and initialization operations explicitly

test(tasks): cover celery and cloud tasks execution

docs(tasks): complete async runtime modernization
```

Exact grouping may differ when smaller logical commits are clearer.

Do not squash.

Do not push.

---

# 46. Acceptance Criteria

ES-03 is complete only when:

- current task inventory is re-verified;
- synchronous callers remain synchronous;
- reusable task logic does not require Celery;
- async callers use the narrow dispatcher;
- `CARE_TASK_BACKEND=celery` works;
- `CARE_TASK_BACKEND=cloud_tasks` works at unit/integration-contract level;
- local Docker Celery remains functional;
- Cloud Tasks dispatcher uses official client and ADC;
- worker executes only registered handlers;
- task payloads are JSON serializable;
- transaction-dependent tasks enqueue after commit;
- report retry behavior is provider-neutral;
- S2 is resolved;
- periodic logic can execute independently of Beat;
- migrations/setup no longer conceptually depend on Beat for the target runtime;
- no Redis/cache/lock behavior was changed;
- full serial regression is green;
- no deterministic async-runtime regression remains;
- ADR-0003 implementation checklist is updated.

---

# 47. Final Report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. current task count;
8. synchronous call-site count;
9. async dispatch call-site count;
10. reusable operations extracted;
11. Celery compatibility status;
12. dispatcher contract;
13. Cloud Tasks backend implementation;
14. worker implementation;
15. task registry;
16. payload schemas;
17. transaction-on-commit changes;
18. retry portability / S2 resolution;
19. idempotency findings;
20. periodic-work changes;
21. migration/setup startup changes;
22. plugin compatibility findings;
23. dependencies changed;
24. focused test results;
25. serial full-suite result;
26. parallel result and E7 occurrences;
27. documentation updated;
28. unresolved async items;
29. deviations from ADR-0003 or ES-03;
30. final verdict:

```text
READY TO MERGE
```

or:

```text
NOT READY TO MERGE
```

Stop after ES-03.

Do not begin ES-04.
