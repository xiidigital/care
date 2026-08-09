# ES-06: Runtime Roles and Process Isolation

- **Status:** Draft
- **Related ADR:** ADR-0006: Portable Runtime Roles and Deployment Composition
- **Depends on:** completed ES-01 through ES-05
- **Target branch:** `feature/runtime-roles`

---

## 1. Context

ADR-0006 establishes that CARE application behavior SHALL depend on explicit
runtime roles, not on deployment topology.

The application SHALL NOT need to know whether it is running on:

- Docker Compose;
- a virtual machine;
- Kubernetes;
- Cloud Run;
- another managed platform.

Deployment composition remains an infrastructure concern.

Application process responsibility is expressed through:

```text
CARE_PROCESS_ROLE
```

The initial supported runtime roles are:

```text
api
task_worker
scheduler
init
```

Previous modernization phases already established independent configuration for:

```text
CARE_STORAGE_BACKEND
CARE_TASK_BACKEND
CARE_CACHE_BACKEND
```

and PostgreSQL-backed distributed locking.

ES-06 SHALL complete the process-responsibility separation without introducing a
high-level runtime-profile setting.

There SHALL NOT be:

```text
CARE_RUNTIME_PROFILE
IS_GCP
USE_CLOUD_RUN
CLOUD_MODE
```

or equivalent application switches representing deployment topology.

---

## 2. Repository State

Before implementation, verify the current repository state.

The active branch MUST be:

```text
feature/runtime-roles
```

The branch MUST be based on the current `gcp` branch containing completed and
merged:

```text
ES-01
ES-02
ES-03
ES-04
ES-05
```

Before modifying code, report:

```bash
git status
git branch --show-current
git log -5 --oneline
git remote -v
```

Verify:

- worktree is clean except user-local ignored/untracked state explicitly known;
- ES-05 is merged;
- ADR-0006 exists;
- `upstream` points to `https://github.com/ohcnetwork/care`;
- no unrelated tracked changes exist.

Do not reset, rebase, merge unrelated branches or push automatically.

---

## 3. Required Documents

Read before implementation:

```text
docs/xii/architecture/00-scope-and-goals.md
docs/xii/architecture/01-current-runtime.md
docs/xii/architecture/02-target-runtime.md
docs/xii/architecture/03-migration-plan.md
docs/xii/architecture/04-testing.md
docs/xii/architecture/06-operations.md
docs/xii/architecture/07-configuration-reference.md

docs/xii/adr/ADR-0006-runtime-roles-and-deployment-composition.md

docs/xii/implementation/ES-03-async-runtime-modernization.md
docs/xii/implementation/ES-04-cache-modernization.md
docs/xii/implementation/ES-05-distributed-locking-modernization.md

docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/task-call-sites.md
docs/xii/architecture/inventory/cache-and-redis.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

If final repository paths differ, locate the actual committed documents and use
those paths.

The ADR defines runtime-role architecture.

This ES defines implementation requirements.

The current repository remains authoritative for concrete startup behavior.

---

## 4. Objective

Implement explicit CARE runtime roles so that:

- every application process has one declared responsibility;
- runtime behavior does not depend on cloud-provider labels;
- API and task-worker route surfaces are isolated;
- deployment-time initialization is separated from long-running processes;
- scheduler responsibility is explicit;
- startup commands reflect runtime responsibility;
- health/readiness checks reflect process responsibility;
- backend selections remain independent;
- local and traditional deployment remain compatible;
- the same application image can serve multiple roles;
- the application is ready to be composed into the first managed-cloud
  deployment without containing GCP-specific runtime branching.

This phase prepares runtime behavior.

It does NOT deploy GCP resources.

---

## 5. Target Runtime Model

The target application model is:

```text
same CARE code/image
        |
        +--------------------+---------------------+-------------------+
        |                    |                     |                   |
        v                    v                     v                   v
       api              task_worker             scheduler             init
        |                    |                     |                   |
        v                    v                     v                   v
 public HTTP           async execution       periodic trigger    initialization
```

Each role has explicit responsibilities.

Backend choices remain independent:

```text
storage  -> CARE_STORAGE_BACKEND
tasks    -> CARE_TASK_BACKEND
cache    -> CARE_CACHE_BACKEND
locking  -> PostgreSQL per ADR-0005
```

There is no application-level deployment-profile switch.

---

## 6. Scope

This phase includes:

- runtime-role parsing and validation;
- role constants/enums if appropriate;
- API role routing;
- task-worker role routing;
- scheduler-role behavior;
- init-role execution;
- startup command/script organization;
- role-specific health behavior;
- role-specific configuration validation;
- same-image role verification;
- local Docker Compose compatibility;
- traditional Celery compatibility;
- explicit initialization flow;
- documentation updates;
- runtime-focused tests.

This phase MAY remove startup coupling left intentionally for local/traditional
compatibility if it can now be removed safely.

---

## 7. Out of Scope

Do not:

- add Terraform;
- create Cloud Run resources;
- create Cloud SQL resources;
- create Cloud Scheduler resources;
- create Cloud Tasks queues;
- create IAM bindings;
- configure Secret Manager resources;
- configure Artifact Registry;
- configure networking;
- change storage architecture;
- change file transport;
- redesign async dispatcher;
- redesign cache;
- redesign locking;
- redesign JWT denylist;
- redesign recent views;
- add AWS/Azure deployment support;
- add Kubernetes manifests;
- implement CI/CD.

Those belong to later phases.

---

## 8. Re-verify Current Process Topology

Before modifying code, inventory the current process entrypoints.

Search for:

```text
start.sh
start-dev.sh
celery_worker.sh
celery_beat.sh
celery-dev.sh
initialize.sh
Procfile
gunicorn
uvicorn
manage.py
CARE_PROCESS_ROLE
CARE_TASK_HANDLER_ENDPOINT_ENABLED
```

For every startup path, record:

```text
entrypoint
intended process
long-running?
routes exposed
initialization performed
collectstatic?
compilemessages?
migrate?
createcachetable?
sync_permissions_roles?
sync_valueset?
Celery worker?
Celery Beat?
```

Update `runtime-and-deployment.md` if the current source differs from the existing
inventory.

Do not change startup scripts until the inventory is complete.

---

## 9. CARE_PROCESS_ROLE

Implement or finalize:

```text
CARE_PROCESS_ROLE
```

Supported values:

```text
api
task_worker
scheduler
init
```

Use a narrow configuration module, conceptually:

```text
config/runtime.py
```

if a dedicated runtime config module does not already exist.

The exact module location should follow established repository conventions.

Invalid values SHALL raise:

```text
django.core.exceptions.ImproperlyConfigured
```

and list every valid role.

Do not silently accept unknown values.

Do not map unknown values to `api`.

---

## 10. Default Role

Default:

```text
api
```

MAY be preserved for backward compatibility if current local startup relies on
an unset `CARE_PROCESS_ROLE`.

However:

- the default must be documented;
- production deployment definitions SHOULD set the role explicitly;
- tests must verify the default;
- the application must not infer role from command name, hostname or environment
  provider.

If the current repository already sets role explicitly in all supported
entrypoints, no default is required.

Verify before deciding.

---

## 11. Runtime Role API

Expose one simple application-level way to inspect role.

Conceptually:

```python
current_process_role()
is_api_process()
is_task_worker()
is_scheduler()
is_init_process()
```

or equivalent.

Do not introduce an object-heavy runtime framework.

The purpose is to avoid scattered environment-string comparisons.

Do not allow domain logic to branch arbitrarily on role.

Role checks should remain concentrated around:

- routing;
- startup;
- health;
- configuration validation;
- process boundaries.

---

## 12. API Role

When:

```text
CARE_PROCESS_ROLE=api
```

CARE SHALL expose the normal public application routes.

The role SHALL support:

- authentication;
- authorization;
- public REST/API routes;
- file transport;
- normal domain operations;
- asynchronous dispatch;
- API health/readiness.

The API role SHALL NOT expose:

```text
/internal/tasks/execute/
```

or any task-worker-only route.

The API role SHALL NOT perform:

```text
migrate
createcachetable
sync_permissions_roles
sync_valueset
```

during normal startup.

The API role SHALL NOT start:

```text
Celery Beat
scheduler loops
```

as part of the API process.

---

## 13. Task Worker Role

When:

```text
CARE_PROCESS_ROLE=task_worker
```

CARE SHALL expose only the route surface required to execute registered
asynchronous tasks, plus explicitly required diagnostics/health routes.

The existing worker endpoint:

```text
POST /internal/tasks/execute/
```

belongs to this role.

The worker SHALL NOT expose the public CARE application API where clean routing
is practical.

At minimum, verify that:

- standard API router URLs are not registered;
- admin/public API views are not unintentionally reachable;
- task endpoint is registered;
- health route is available if required operationally.

Do not duplicate Django projects/settings modules purely for route isolation if
conditional routing is sufficient.

---

## 14. Worker Security Preconditions

ES-03 established that the internal task endpoint has no application-layer shared
secret and intentionally relies on platform IAM/OIDC.

ES-06 SHALL preserve that decision.

Do not add:

```text
X-Worker-Secret
static bearer token
HMAC shared secret
```

to compensate for undeployed IAM.

Document and validate that the worker role is not safe for public unauthenticated
deployment.

Carry the production requirement forward:

```text
task_worker service must be authenticated-only at the platform boundary
```

Actual IAM enforcement belongs to ES-07.

---

## 15. Scheduler Responsibility

The `scheduler` role represents scheduling responsibility.

It SHALL NOT contain business logic.

Re-verify current periodic work and Beat configuration.

For local/traditional deployments, scheduler responsibility may remain
implemented through Celery Beat.

The scheduler role SHOULD therefore map cleanly to existing Beat operation.

Do not introduce a new scheduler framework.

Do not implement Cloud Scheduler in this phase.

---

## 16. Celery Beat and Scheduler Role

If Celery Beat remains the scheduler implementation for local/traditional use,
ensure its process is explicitly treated as:

```text
CARE_PROCESS_ROLE=scheduler
```

where practical.

Beat SHOULD:

- register/trigger periodic work;
- not own application initialization architecturally.

If current scripts still invoke `initialize.sh` before Beat for compatibility,
analyze whether ES-06 can remove that coupling safely.

Preferred final state:

```text
init
  -> initialization

scheduler
  -> scheduling only
```

If removing the compatibility invocation would break the existing local
developer workflow, preserve it only in the deployment wrapper and document it
as compatibility behavior, not scheduler responsibility.

---

## 17. Init Role

When:

```text
CARE_PROCESS_ROLE=init
```

execute the existing explicit initialization sequence.

Current sequence:

```text
python manage.py migrate --noinput
python manage.py createcachetable
python manage.py compilemessages -v 0
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

Prefer reusing:

```text
scripts/initialize.sh
```

rather than duplicating commands.

The init process SHALL:

- run once;
- fail on first failed step;
- return non-zero on failure;
- return zero on success;
- terminate after completion.

It SHALL NOT:

- launch HTTP server;
- launch Celery;
- launch Beat;
- remain running.

---

## 18. Init Role Entrypoint

Provide a clear role entrypoint.

Possible implementations include:

```text
scripts/start-init.sh
```

or using `scripts/initialize.sh` directly.

Avoid unnecessary wrapper layers.

The same application image must be capable of running:

```text
api
task_worker
scheduler
init
```

through command/environment differences.

---

## 19. Initialization Independence

Prove that initialization is independent from long-running processes.

The following must be true:

```text
API can start without running init
task worker can start without running init
scheduler can start without owning init
init can execute without API/worker/Beat
```

This does not mean the application can function correctly against an
uninitialized database.

It means initialization is an explicit deployment prerequisite rather than a
startup side effect.

---

## 20. Same Image Requirement

Verify that the same built CARE application image can execute each runtime role.

Do not build separate images for:

```text
api
task_worker
scheduler
init
```

unless a concrete existing dependency makes same-image execution impossible.

If any role requires a distinct image, stop and report why before implementing
that divergence.

---

## 21. Process Commands

Each role SHALL have one clear command or documented entrypoint.

Conceptually:

```text
api
  -> gunicorn / existing API server

task_worker
  -> gunicorn / HTTP server with worker routing

scheduler
  -> Celery Beat

init
  -> initialize.sh
```

Use actual repository commands.

Do not replace working process managers/frameworks without need.

---

## 22. Route Composition

Refactor route registration so role boundaries are explicit.

Preferred model:

```text
api
    -> public CARE URLs
    -> health

task_worker
    -> internal task URL
    -> worker health
```

Do not rely solely on view-level denial after exposing routes.

Route absence is preferred for worker-only endpoints on API and vice versa.

Add tests using URL resolution/reverse where appropriate.

---

## 23. Public API Isolation on Worker

The worker role SHOULD NOT expose normal API routes.

At minimum test representative routes such as:

```text
/ping/
/api/v1/...
/admin/
```

according to the actual repository routing design.

Health/diagnostic endpoints may be intentionally shared.

If complete route isolation would require invasive Django restructuring,
implement the smallest safe boundary and document exact remaining routes.

Do not silently claim full isolation if some routes remain shared.

---

## 24. Worker Route Isolation on API

This is mandatory.

Under:

```text
CARE_PROCESS_ROLE=api
```

the internal task execution URL must not resolve.

This requirement already exists conceptually from ES-03 and must remain covered.

---

## 25. Role-Specific Configuration Validation

Runtime-role validation SHALL validate only capabilities required by the active
role.

Do not require configuration for inactive responsibilities.

Examples:

### api

Validate:

- database;
- storage;
- selected cache where needed;
- task dispatcher configuration.

Do not require worker-only HTTP configuration unless needed by the dispatcher.

### task_worker

Validate:

- database;
- task registry;
- worker endpoint configuration;
- selected storage/cache where registered handlers require them.

Do not require public-API-only configuration solely because it exists in base
settings.

### scheduler

Validate:

- selected scheduler implementation;
- dependencies required by that scheduler.

### init

Validate:

- database;
- initialization dependencies.

Avoid speculative validation.

Use actual requirements from the repository.

---

## 26. Backend Orthogonality

Do not create logic such as:

```python
if process_role == "api":
    force_task_backend("celery")
```

or:

```python
if process_role == "task_worker":
    force_storage_backend("gcs")
```

Runtime role and backend selections remain independent.

Valid combinations are determined by capability requirements, not platform
assumptions.

---

## 27. No CARE_RUNTIME_PROFILE

Search for existing or proposed references to:

```text
CARE_RUNTIME_PROFILE
RUNTIME_PROFILE
IS_GCP
USE_GCP
CLOUD_MODE
```

Do not introduce them.

If documentation currently proposes them, correct the documentation.

If unrelated historical configuration exists, classify it before removing it.

---

## 28. Health Model

Re-verify current health endpoints and checks.

Separate:

```text
liveness
readiness
dependency diagnostics
```

where the current architecture supports it cleanly.

Do not create a complex health framework if unnecessary.

At minimum, health behavior SHALL not require dependencies belonging only to
another role.

---

## 29. API Health

For the API role, health/readiness SHOULD reflect dependencies required to serve
normal API traffic.

Candidates include:

- PostgreSQL;
- selected storage;
- selected cache if required;
- async dispatcher configuration/availability where appropriate.

Do not automatically require Redis when:

```text
CARE_CACHE_BACKEND=postgres
CARE_TASK_BACKEND=cloud_tasks
```

unless another API capability explicitly requires Redis.

If recent views still create a Redis service dependency for specific endpoints,
document that distinction honestly.

---

## 30. Task Worker Health

Worker health SHOULD reflect:

- PostgreSQL;
- task registry validity;
- execution dependencies;
- worker transport configuration.

Do not require:

- public API router;
- frontend assets;
- scheduler health.

Cloud Tasks service availability itself may not need a worker-side connectivity
check because delivery is inbound.

Use actual runtime semantics.

---

## 31. Scheduler Health

For Celery Beat scheduler:

- verify scheduler startup;
- verify broker dependency;
- avoid reporting API/storage dependencies unless periodic scheduling actually
  needs them.

Do not implement Cloud Scheduler health.

---

## 32. Init Health

Init is a finite process.

Its health is:

```text
exit 0 -> success
non-zero -> failure
```

Do not create a persistent HTTP health endpoint for init.

---

## 33. Startup Logging

Each long-running CARE process SHOULD log once, at startup:

```text
process_role
storage_backend
task_backend
cache_backend
```

only where those values are applicable and available.

Do not log:

- passwords;
- Redis URLs with credentials;
- database URLs with credentials;
- service-account tokens;
- secrets.

Use existing logging infrastructure.

Do not add a new logging framework.

---

## 34. Local Docker Compose

Preserve the existing local developer experience.

Developers SHALL NOT need:

```text
GCP credentials
Cloud Run
Cloud Tasks
GCS
Secret Manager
```

to run CARE locally.

Local Compose may continue using:

```text
PostgreSQL
MinIO
Redis
Celery
Celery Beat
```

Map local containers explicitly to runtime responsibilities where appropriate.

Do not require developers to manually launch four new containers if the current
topology can express roles through existing services.

---

## 35. Traditional Deployment Compatibility

Preserve compatibility with:

```text
API
Celery worker
Celery Beat
PostgreSQL
Redis
S3-compatible storage
```

The modernization must not force traditional users onto Cloud Tasks.

Celery remains supported.

---

## 36. Managed-Cloud Readiness

Without deploying GCP resources, verify that the application can be configured
conceptually as:

```text
API:
CARE_PROCESS_ROLE=api
CARE_TASK_BACKEND=cloud_tasks
CARE_STORAGE_BACKEND=gcs
CARE_CACHE_BACKEND=postgres

Worker:
CARE_PROCESS_ROLE=task_worker
CARE_STORAGE_BACKEND=gcs
CARE_CACHE_BACKEND=postgres
```

with required Cloud Tasks variables supplied.

Do not require Redis for:

- core locking;
- PostgreSQL cache;
- explicit initialization;
- Cloud Tasks worker transport.

If `recent_views` still makes Redis required for particular API behavior,
document that capability dependency.

Do not hide it.

---

## 37. Scheduler in Managed GCP

The initial managed GCP deployment is expected to use an external platform
scheduler.

Therefore ES-06 SHALL NOT require a long-running `scheduler` CARE process for the
managed composition.

Periodic operations must already be callable independently.

Verify this remains true after ES-03.

Actual Cloud Scheduler triggers belong to ES-07.

---

## 38. Init in Managed GCP

Verify `init` can run as an ephemeral process using only the application image,
database access and required configuration.

It SHALL not require:

```text
API server
task worker
Celery Beat
Redis for locking
```

Initialization may still require Redis only if an unrelated selected capability
explicitly uses Redis during one of the initialization commands.

If so, identify and document it.

---

## 39. Plugin Impact

Review plugin assumptions around:

```text
URL registration
startup
Celery
process role
AppConfig.ready()
autodiscover
```

Plugins may assume all CARE URLs exist in every process.

Document:

```text
compatible
API-only assumption
worker-incompatible
scheduler-incompatible
unknown
```

Do not build a generic plugin runtime SDK.

Preserve existing plugin behavior where practical.

---

## 40. Tests — Runtime Config

Test:

```text
api accepted
task_worker accepted
scheduler accepted
init accepted
invalid role rejected
default behavior if supported
```

No test should depend on deployment provider.

---

## 41. Tests — API Routing

Under `api`:

verify:

- representative public API route resolves;
- file routes resolve;
- internal task execution route does NOT resolve.

Use isolated settings/process tests where URLconf import caching would otherwise
hide role differences.

---

## 42. Tests — Worker Routing

Under `task_worker`:

verify:

- internal task route resolves;
- representative public API routes do not resolve where full isolation is
  implemented;
- health route behaves as designed.

If some public routes remain intentionally shared, assert/document exact scope.

---

## 43. Tests — Scheduler Role

Verify local scheduler command can start with:

```text
CARE_PROCESS_ROLE=scheduler
```

without triggering initialization unexpectedly.

Test configuration validation.

Do not run an indefinitely blocking scheduler process inside unit tests.

Use command/config/import-level verification plus bounded integration where
appropriate.

---

## 44. Tests — Init Role

Run init in the real Docker runtime.

Verify:

```text
migrate
createcachetable
compilemessages
sync_permissions_roles
sync_valueset
```

complete successfully.

Verify process exits zero.

Inject or simulate one failing initialization command where practical and prove
non-zero propagation.

Do not alter management command semantics only to make this test easy.

---

## 45. Tests — Initialization Separation

Prove API startup does not execute migrations.

Prove task-worker startup does not execute migrations.

Prove scheduler startup does not own initialization in the target role model.

Use static script checks and/or controlled runtime tests.

Do not infer from documentation.

---

## 46. Tests — Same Image

Using the built application image, execute bounded verification for:

```text
api
task_worker
scheduler
init
```

Prove no role requires rebuilding the application image.

For long-running roles, startup/health verification is sufficient.

---

## 47. Tests — Backend Orthogonality

Test representative unusual but valid combinations.

Examples:

```text
api + celery + s3 + redis
api + cloud_tasks + gcs + postgres
task_worker + cloud_tasks + gcs + postgres
```

No code should reject combinations merely because they do not correspond to a
hard-coded deployment profile.

Do not require live GCP for configuration tests.

---

## 48. Tests — No Runtime Profile Coupling

Add static or configuration tests proving:

```text
CARE_RUNTIME_PROFILE
```

is not required and not consulted.

Do not merely grep documentation; verify application settings/runtime modules do
not branch on a deployment-profile concept.

---

## 49. Tests — Health Isolation

Test at least:

### api with Redis unavailable

Where selected API capabilities do not require Redis, API health should not fail
solely because Redis is absent.

### task_worker

Worker health must not fail because public API routing is absent.

### init

No HTTP health requirement.

Use real dependencies where practical.

---

## 50. Docker Runtime Verification

After focused tests pass:

- rebuild if runtime scripts/settings changed;
- restart official Docker Compose stack;
- verify services;
- verify API role;
- verify Celery worker compatibility;
- verify scheduler/Beat compatibility;
- run init independently.

Do not delete volumes.

Do not reset user-local data unnecessarily.

---

## 51. Full Regression

Run the full serial suite.

Run the full parallel suite.

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

ES-04 resolved E7.

Do not use the old E7 exemption.

Any deterministic runtime-role regression must be fixed.

---

## 52. Documentation Updates

Update:

```text
docs/xii/architecture/inventory/runtime-and-deployment.md
docs/xii/architecture/inventory/plugin-impact.md
docs/xii/architecture/inventory/unresolved-items.md
```

Update:

```text
docs/xii/adr/ADR-0006-runtime-roles-and-deployment-composition.md
```

implementation checklist.

Update configuration reference for:

```text
CARE_PROCESS_ROLE
```

only.

Do NOT add:

```text
CARE_RUNTIME_PROFILE
```

to configuration documentation.

Update operations documentation for:

- process responsibilities;
- role startup;
- initialization;
- route isolation;
- role health behavior.

---

## 53. Allowed Modifications

Claude MAY modify:

- runtime configuration modules;
- URL composition;
- health checks;
- startup scripts;
- Docker Compose role environment where necessary;
- init entrypoint;
- scheduler/Beat startup wrapper;
- runtime tests;
- documentation.

Claude MAY remove:

- runtime-topology inference;
- startup-time initialization coupling;
- route registrations that violate role boundaries.

---

## 54. Forbidden Modifications

Claude SHALL NOT:

- add Terraform;
- deploy GCP resources;
- change task dispatcher architecture;
- change storage architecture;
- change file transport;
- redesign cache;
- redesign locking;
- redesign recent views;
- redesign JWT denylist;
- add a deployment-profile application abstraction;
- add provider-specific process roles;
- add CI/CD.

---

## 55. Commit Strategy

Use focused commits.

Suggested sequence:

```text
refactor(runtime): formalize process roles

refactor(runtime): isolate api and worker routing

refactor(runtime): separate init and scheduler responsibilities

refactor(health): make diagnostics role-aware

test(runtime): verify runtime role isolation

docs(runtime): complete runtime roles architecture
```

Exact grouping may differ if smaller logical commits are clearer.

Do not squash.

Do not push.

---

## 56. Acceptance Criteria

ES-06 is complete only when:

- `CARE_PROCESS_ROLE` is the sole application-level runtime-role selector;
- supported roles are `api`, `task_worker`, `scheduler`, `init`;
- invalid roles fail clearly;
- no `CARE_RUNTIME_PROFILE` or deployment-provider application mode exists;
- API role exposes public routes and excludes worker-only routes;
- task_worker exposes worker routes and isolates public API routes where
  practical;
- scheduler responsibility is explicit;
- init responsibility is explicit and ephemeral;
- long-running process startup does not perform deployment initialization;
- initialization can run independently;
- initialization uses PostgreSQL locking from ADR-0005;
- the same application image supports all roles;
- backend configuration remains orthogonal to role selection;
- local Docker Compose remains functional;
- traditional Celery deployment remains functional;
- managed-cloud composition is application-ready without GCP deployment code;
- role-specific health behavior is implemented;
- worker IAM requirement remains explicitly documented for ES-07;
- serial full regression is green;
- parallel full regression is green;
- ADR-0006 implementation checklist is updated.

---

## 57. Final Report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. re-verified startup/process inventory;
8. `CARE_PROCESS_ROLE` implementation;
9. default-role decision;
10. API role behavior;
11. task_worker behavior;
12. scheduler behavior;
13. init behavior;
14. route isolation result;
15. initialization separation result;
16. same-image verification;
17. health/readiness changes;
18. backend-orthogonality result;
19. local Docker Compose result;
20. traditional Celery compatibility;
21. managed-cloud readiness result;
22. Redis dependency observations;
23. plugin findings;
24. focused test results;
25. serial full-suite result;
26. parallel full-suite result;
27. documentation updated;
28. unresolved runtime items;
29. items explicitly deferred to ES-07;
30. deviations from ADR-0006 or ES-06;
31. final verdict:

```text
READY TO MERGE
```

or:

```text
NOT READY TO MERGE
```

Stop after ES-06.

Do not begin ES-07.
