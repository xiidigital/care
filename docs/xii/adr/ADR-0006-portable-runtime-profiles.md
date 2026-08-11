# ADR-0006: Portable Runtime Roles and Deployment Composition

- **Status:** Accepted
- **Date:** 2026-08-09
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None

## Context

The original motivation for this modernization was practical: run CARE economically on Google Cloud Platform without requiring a permanently running application virtual machine.

During the architecture work, a broader requirement became explicit.

CARE must remain usable across multiple deployment environments, including:

- local development;
- Docker Compose;
- traditional virtual machines;
- Kubernetes;
- managed cloud platforms;
- future infrastructure providers.

Google Cloud Platform is the first managed-cloud deployment target.

It is not the application architecture.

Previous modernization decisions intentionally separated application responsibilities from infrastructure implementations:

- ADR-0001: object storage;
- ADR-0002: file transport;
- ADR-0003: asynchronous execution;
- ADR-0004: application cache;
- ADR-0005: distributed locking.

Those decisions make it possible for the same CARE application code to use different infrastructure implementations through explicit configuration.

The remaining architectural concern is process topology.

Historically, CARE coupled several responsibilities to the existence of particular long-running processes.

Examples included:

- Celery Beat running migrations;
- Celery startup performing initialization;
- API and worker processes sharing startup responsibilities;
- worker functionality becoming available as a side effect of imported modules;
- deployment topology implicitly determining application behavior.

This makes serverless and managed execution unnecessarily difficult and makes runtime responsibilities harder to reason about.

CARE therefore needs an explicit model of what each running process is responsible for without making application code aware of the platform on which that process happens to run.

## Decision

CARE SHALL define explicit **runtime roles**.

Every running CARE process SHALL execute one clearly defined runtime role.

Runtime roles describe application responsibilities.

Deployment topology describes how those roles are instantiated.

These concepts SHALL remain separate.

CARE application code SHALL NOT need to know whether it is running on:

- Docker Compose;
- a virtual machine;
- Kubernetes;
- Cloud Run;
- another managed platform.

There SHALL NOT be a `CARE_RUNTIME_PROFILE` or equivalent application setting whose purpose is to tell application code that it is running in `local`, `traditional`, `GCP`, `cloud`, or another deployment topology.

Deployment composition belongs to deployment configuration and infrastructure code.

Application behavior SHALL instead be determined by:

- the runtime role of the current process;
- the independently selected infrastructure backends defined by previous ADRs.

## Runtime Role Selection

The active process role SHALL be selected explicitly through:

```text
CARE_PROCESS_ROLE
```

Initial supported roles are:

```text
api
task_worker
scheduler
init
```

Unknown roles SHALL fail clearly rather than silently falling back to another behavior.

A deployment SHALL explicitly choose the role appropriate for each process.

## Role Independence

A runtime role defines responsibility, not infrastructure.

For example, the `task_worker` role does not imply:

```text
Cloud Run
Cloud Tasks
Redis
Celery
GCP
```

It means only:

```text
this process executes asynchronous CARE work
```

The mechanism used to deliver that work is governed independently by ADR-0003.

Likewise, the `api` role does not determine:

- object-storage provider;
- cache backend;
- lock implementation;
- cloud provider.

Those choices remain independent.

## API Role

The `api` role is responsible for serving CARE's public application API.

Responsibilities include:

- public HTTP API;
- authentication;
- authorization;
- request validation;
- domain operations;
- database persistence;
- file transport;
- asynchronous dispatch;
- API-specific health reporting.

The API role SHALL NOT:

- execute database migrations during normal startup;
- create cache tables during normal startup;
- synchronize permissions during normal startup;
- synchronize valuesets during normal startup;
- run periodic scheduling loops;
- expose task-worker-only execution endpoints;
- assume a permanently running local filesystem;
- depend on Celery Beat for initialization.

API instances SHALL be replaceable and horizontally scalable.

Multiple API instances starting concurrently SHALL not perform deployment-time initialization.

## Task Worker Role

The `task_worker` role is responsible for asynchronous task execution.

Responsibilities include:

- accepting asynchronous work from the selected task backend;
- validating task envelopes;
- executing only registered CARE task handlers;
- reporting execution success or failure;
- task-worker-specific health reporting.

The worker SHALL NOT:

- expose the normal public CARE REST API where route isolation is practical;
- perform database migrations;
- create cache tables;
- synchronize permissions;
- synchronize valuesets;
- execute deployment initialization;
- schedule periodic work.

Worker-only HTTP endpoints SHALL only be routed when the process is configured as `task_worker`.

The explicit Cloud Tasks execution endpoint introduced by ADR-0003 belongs to this role.

The same role MAY be implemented in different deployment environments.

For example:

```text
GCP:
Cloud Tasks
    ->
Cloud Run task_worker
```

A traditional deployment may instead execute reusable task operations through Celery workers without requiring the HTTP worker transport.

The role definition remains independent of the transport.

## Scheduler Role

The `scheduler` role represents periodic scheduling responsibility.

Its responsibility is:

```text
decide when periodic work should be triggered
```

It SHALL NOT contain the actual business logic of scheduled operations.

Periodic business operations SHALL remain callable independently of the scheduler.

Examples include:

- cleanup commands;
- maintenance commands;
- periodic task dispatch;
- other bounded recurring operations.

A deployment MAY implement scheduling using:

```text
Celery Beat
Cloud Scheduler
Kubernetes CronJob
another scheduler
```

without changing the underlying CARE operation.

The scheduler role MAY not exist as a CARE application process in deployments where the platform provides scheduling externally.

For example, the initial GCP deployment is expected to use Cloud Scheduler rather than a continuously running CARE scheduler container.

This does not change the scheduler responsibility model.

## Init Role

The `init` role owns deployment-time application initialization.

Initialization currently includes:

```text
python manage.py migrate --noinput
python manage.py createcachetable
python manage.py compilemessages
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

The exact implementation may remain encapsulated by the existing:

```text
scripts/initialize.sh
```

or its future equivalent.

The `init` role is ephemeral.

It SHALL:

1. execute required initialization;
2. fail immediately when an initialization step fails;
3. return a non-zero process status on failure;
4. terminate successfully after initialization completes.

It SHALL NOT:

- serve HTTP traffic;
- remain permanently running;
- execute Celery workers;
- execute scheduling loops.

Initialization SHALL be safe to invoke independently from the API, task worker and scheduler.

Database-backed coordination defined by ADR-0005 SHALL protect initialization operations where concurrent execution would be unsafe.

## Initialization Is a Deployment Operation

Initialization SHALL NOT occur as a side effect of normal API or worker startup.

Normal API startup SHALL NOT run:

```text
migrate
createcachetable
sync_permissions_roles
sync_valueset
```

Normal task-worker startup SHALL NOT run those operations either.

Traditional/local compatibility MAY temporarily invoke the shared initialization entrypoint from an existing deployment script such as Celery Beat startup, but this is deployment compatibility behavior, not an architectural dependency.

The managed-cloud target SHALL execute initialization explicitly before deploying or promoting application revisions.

## Deployment Composition

A deployment is a composition of runtime roles and infrastructure backends.

The application does not need an application-level runtime profile flag to represent that composition.

### Local / traditional example

A local deployment may compose:

```text
api
Celery worker
Celery Beat / scheduler
init during startup

PostgreSQL
MinIO through Django Storage
Redis
Celery
```

This preserves the existing developer and traditional deployment experience.

### Initial GCP example

The first managed-cloud deployment is expected to compose:

```text
Cloud Run API
    ->
api role

Cloud Run private task service
    ->
task_worker role

Cloud Run Job
    ->
init role

Cloud Scheduler
    ->
periodic triggers

Cloud Tasks
    ->
asynchronous dispatch

Cloud SQL
    ->
PostgreSQL

Cloud Storage
    ->
Django Storage

Secret Manager
Artifact Registry
Cloud Logging
```

Two valid managed-GCP compositions exist. They differ only in whether a
Redis-compatible service is present.

#### Managed GCP with Redis — available now

```text
Cloud SQL
Cloud Storage
Cloud Tasks
Redis-compatible service
```

Advantages:

- it is the existing implementation;
- it requires the least engineering effort;
- it gives the best rate-limit performance, because counters use a single
  server-side atomic command.

A deployment that already operates a Redis-compatible service SHOULD choose this
composition.

#### Managed GCP Redis-free — future

```text
Cloud SQL
Cloud Storage
Cloud Tasks
no Redis
```

**This profile does not exist yet.** It requires RF2 (see "Compatibility,
portability and the Redis-free target" below); RF1 is delivered. It SHALL NOT be
presented as an available option, configured, or used as a cost baseline until
RF2 is delivered too.

Until then, a managed GCP deployment needs a Redis-compatible service for rate
limiting, even when `CARE_CACHE_BACKEND=postgres` and
`CARE_TASK_BACKEND=cloud_tasks` remove every other use of it. Recent views no
longer contribute to that requirement.

The GCP deployment is therefore a composition of already-defined capabilities, not a separate version of CARE.

## Same Application Image

The managed-cloud deployment SHOULD use the same immutable CARE application image for multiple runtime roles where practical.

Differences between roles SHOULD be expressed through:

- command;
- environment configuration;
- route composition;
- identity;
- scaling policy;
- infrastructure permissions.

Separate application images SHOULD NOT be created merely because API and task worker processes have different responsibilities.

Role-specific images MAY be introduced later only when a concrete technical or security requirement justifies them.

## Django and PostgreSQL

Django remains an intentional architectural choice.

Django ORM remains the primary persistence abstraction.

PostgreSQL remains CARE's durable system of record.

This runtime architecture SHALL NOT introduce:

- repository abstractions over Django ORM;
- cloud-specific persistence APIs;
- alternative application data models merely for deployment portability.

Runtime portability SHALL be achieved by isolating infrastructure responsibilities, not by hiding Django.

## Storage Independence

Runtime roles SHALL use the storage abstraction established by ADR-0001.

The active object-storage implementation remains selected independently.

Examples include:

```text
MinIO through S3Storage
AWS S3 through S3Storage
Google Cloud Storage through GoogleCloudStorage
```

No runtime role SHALL branch based on storage provider.

## File Transport Independence

Runtime roles SHALL preserve ADR-0002.

Clients communicate with CARE.

CARE communicates with Django Storage.

A deployment SHALL NOT require frontend changes when changing storage provider.

## Asynchronous Execution Independence

Runtime roles SHALL use ADR-0003.

The asynchronous backend is selected independently from runtime topology.

For example:

```text
local:
CARE_TASK_BACKEND=celery

managed GCP:
CARE_TASK_BACKEND=cloud_tasks
```

The application operation remains identical.

The runtime-role architecture SHALL NOT make Cloud Tasks mandatory for all deployments.

## Cache Independence

Runtime roles SHALL use ADR-0004.

Cache implementation is selected independently through the approved cache configuration.

Examples include:

```text
PostgreSQL DatabaseCache
Redis
LocMem
Dummy
```

A runtime role SHALL not infer cache behavior from deployment platform.

## Locking Independence

Distributed locking follows ADR-0005.

Core locking currently uses PostgreSQL transaction-scoped advisory locks.

A runtime role SHALL not introduce a Redis dependency merely because a previous deployment topology used Redis.

## Redis

Redis SHALL NOT be considered a universal CARE runtime requirement.

Redis MAY remain required by selected capabilities.

Capabilities that require Redis today:

- Celery as the dispatcher, in traditional and local deployments;
- **strict** rate limiting — `CARE_RATE_LIMIT_BACKEND=redis`, the only mode
  whose counters are atomic and therefore hold under concurrent bursts;
- external plugins or future optional capabilities.

Capabilities that no longer require Redis:

- distributed locking — PostgreSQL advisory locks (ADR-0005);
- `recent_views` — a PostgreSQL model, in every profile (RF1);
- the ordinary application cache — configurable (ADR-0004);
- rate limiting — configurable since RF2 (2026-08-11). `postgres` counts into a
  dedicated `DatabaseCache` table with no Redis at all, at the cost of
  best-effort rather than strict semantics; `disabled` counts nothing. The
  library is unchanged in both counting modes;
- asynchronous dispatch — configurable (ADR-0003).

The presence of Redis is therefore capability-specific.

The absence of Redis SHALL NOT prevent:

- object storage;
- PostgreSQL locking;
- explicit initialization;
- Cloud Tasks execution;
- ordinary PostgreSQL-backed cache;
- PostgreSQL-backed rate limiting, with its weaker guarantee stated;

when the selected configuration does not otherwise require it.

CARE SHALL NOT claim to be universally Redis-free while Redis-specific capabilities remain enabled.

### Compatibility, portability and the Redis-free target

Added 2026-08-09. Clarification only. No decision in this ADR changes and no
implementation follows from this section.

Three statements are distinct and all three are true.

**Compatibility.** Redis is fully supported and is a first-class deployment
choice. A deployment MAY deliberately select Redis for the default cache, rate
limiting and the Celery broker. Nothing here discourages Redis where it already
exists — a deployment that operates Redis SHOULD use it, and it remains the only
way to get strict rate limiting. Recent views is the one capability where Redis
is *not* offered: RF1 made it PostgreSQL-only, with no backend selector and no
fallback.

**Portability.** CARE SHALL NOT depend architecturally on Redis. Redis-dependent
capabilities SHALL remain isolated behind explicit seams — the cache alias, the
rate limit wrapper, the recent views service and the async dispatcher. Business
code SHALL NOT know whether Redis exists. RF1 exercised that property: the
recent views seam changed storage engines without any caller changing. RF2
exercised it again, and harder: the rate-limit wrapper gained three backends
with three different guarantees, and none of its six call sites changed a line.

**Redis-free target.** Delivered for the API by RF2 (2026-08-11). The profile is
`CARE_CACHE_BACKEND=postgres` + `CARE_RATE_LIMIT_BACKEND=postgres` +
`CARE_TASK_BACKEND=cloud_tasks`, verified with Redis unreachable: checks pass,
init completes, the API serves, and login rate limiting returns 429 without a
Redis connection being attempted.

The claim stops there, and the boundary is the point of this ADR's
capability-specific position. **Celery still requires Redis** — a Redis-free
deployment runs `cloud_tasks`. **Strict rate limiting still requires Redis** —
the PostgreSQL mode is best-effort and says so.

RF2 deliberately did *not* do what was planned for it — replacing
`django-ratelimit` with a provider-neutral atomic counter. That would have meant
writing a bespoke limiter on the login path to recover a guarantee Redis already
provides. Naming the weaker guarantee costs less and hides less. The replacement
remains available as future work and no caller would change.

The original roadmap entry is **RF2** in `inventory/unresolved-items.md`,
Part RF, and is now marked complete there.

**Recent Views** was the second item, **RF1**, and it is delivered: Redis list
operations replaced by `emr.UserValueSetRecentView`.

Redis-free is therefore neither impossible nor already delivered. Because the
seams exist, reaching it is a change to that one remaining capability rather
than a redesign of the application.

## Process Startup

Each runtime role SHALL have a clear startup path.

Startup behavior SHALL be minimal.

Normal long-running process startup SHOULD perform only actions required to start that role.

For example:

```text
api startup
    ->
prepare process
    ->
serve API
```

and:

```text
task_worker startup
    ->
prepare process
    ->
serve internal worker endpoint
```

Deployment-time database mutation belongs to the `init` role instead.

## Route Isolation

Routes SHALL reflect process responsibility where practical.

The `api` role SHALL expose public application routes.

The `task_worker` role SHALL expose internal worker routes required by the selected asynchronous implementation.

Worker-only routes SHALL NOT be registered on the public API role.

Public CARE APIs SHOULD NOT be registered on a dedicated task-worker service when clean route isolation is practical.

The goals are:

- reduced accidental exposure;
- clear process responsibility.

## Security Boundary

Runtime-role separation does not replace infrastructure security.

In managed deployments, private process-to-process invocation SHALL use the platform's identity and authorization mechanisms.

For the initial GCP deployment:

- the task worker SHALL reject unauthenticated invocation at the Cloud Run IAM boundary;
- the Cloud Tasks service account SHALL receive only the required invocation permission;
- the worker SHALL NOT be configured for unauthenticated public invocation.

Application shared secrets SHALL NOT be introduced merely to compensate for missing deployment IAM.

This requirement is a production deployment blocker for subsequent runtime and infrastructure implementation.

## Secrets

Runtime roles SHALL receive secrets through the deployment environment.

Application source code SHALL NOT contain:

- cloud service-account keys;
- static infrastructure credentials;
- production secrets.

The initial GCP profile is expected to use managed identities and Secret Manager.

Application code SHALL prefer ambient credentials where supported.

## Health and Readiness

Health checks SHALL reflect process responsibilities.

A health endpoint SHALL NOT declare a process unhealthy because an unrelated runtime role's dependency is unavailable.

### API health

Relevant dependencies may include:

- PostgreSQL;
- selected storage;
- selected cache when required for request serving;
- asynchronous dispatcher availability where appropriate.

### Task worker health

Relevant dependencies may include:

- PostgreSQL;
- dependencies required by registered task execution;
- worker transport configuration.

It SHALL NOT require public API routing.

### Scheduler health

Relevant dependencies depend on the selected scheduling implementation.

### Init

The init role does not require a permanent liveness endpoint.

Its health is represented by process exit status.

Health semantics SHALL distinguish where practical between:

```text
liveness
readiness
dependency diagnostics
```

Detailed implementation belongs to ES-06.

## Scale to Zero

The initial managed-cloud deployment SHOULD exploit scale-to-zero where operationally appropriate.

Candidate components include:

- API instances;
- HTTP task workers;
- initialization jobs when not executing.

Cloud Run Jobs naturally have no continuously running instance outside job execution.

Cloud Scheduler and Cloud Tasks are managed services.

PostgreSQL/Cloud SQL remains a persistent managed dependency and therefore represents a baseline cost.

A Redis-compatible service represented a baseline cost wherever rate limiting
was used, which was every profile serving the API. That cost was the practical
motivation for RF1 and RF2, and both have now removed their share of it: recent
views became a PostgreSQL model, and rate limiting became selectable. A
managed-cloud deployment on `postgres` + `postgres` + `cloud_tasks` carries no
Redis line at all.

It remains a cost for deployments that select Redis — for the Celery broker, or
for strict rate limiting, which is the only mode whose counters hold under
concurrent bursts. Choosing `postgres` to avoid the cost is a legitimate trade
with a stated consequence; it is still not a reason to disable rate limiting.

The architecture SHALL NOT describe the entire deployment as:

```text
zero-cost when idle
```

or:

```text
fully serverless
```

when persistent infrastructure still exists.

## Local Development

The modernization SHALL preserve an effective local development environment.

Developers SHALL NOT need:

- GCP credentials;
- Cloud Tasks;
- GCS;
- Cloud Run;
- Secret Manager;

to run CARE locally.

The existing Docker-based profile SHOULD continue using practical local dependencies such as:

```text
PostgreSQL
MinIO
Redis
Celery
Celery Beat
```

when appropriate.

Managed-cloud support is additive.

It SHALL NOT replace local development with cloud dependencies.

## Traditional Deployments

Traditional deployments remain supported.

A VM or Kubernetes deployment MAY continue running:

```text
API
Celery worker
Celery Beat
PostgreSQL
Redis
S3-compatible storage
```

provided the selected CARE configuration supports those capabilities.

The runtime-role model does not require traditional deployments to adopt Cloud Tasks or managed-cloud services.

## GCP Is the First Managed Target

The first managed-cloud implementation SHALL target Google Cloud Platform.

Expected services include:

```text
Cloud Run
Cloud Run Jobs
Cloud SQL
Cloud Storage
Cloud Tasks
Cloud Scheduler
Secret Manager
Artifact Registry
Cloud Logging
```

These are deployment choices.

CARE domain and application logic SHALL not depend directly on them except through the narrowly defined infrastructure backends established by previous ADRs.

Future managed-cloud implementations may compose different services.

## Portability Rule

A deployment-provider change SHOULD require changes primarily to:

- infrastructure definitions;
- environment configuration;
- deployment commands;
- backend configuration.

It SHOULD NOT require changes to:

- CARE domain models;
- business logic;
- public file API;
- task handler implementations;
- cache consumers;
- locking consumers.

Provider-specific application integration SHALL be introduced only where a concrete requirement cannot be satisfied through existing abstractions.

## Configuration

Runtime-role configuration SHALL remain orthogonal to backend configuration.

Conceptually:

```text
CARE_PROCESS_ROLE
CARE_STORAGE_BACKEND
CARE_TASK_BACKEND
CARE_CACHE_BACKEND
```

describe independent dimensions.

No `CARE_RUNTIME_PROFILE` setting SHALL be introduced merely to group these values.

Deployment definitions MAY provide convenient bundles of environment variables, but the application SHALL validate the concrete capabilities rather than a high-level platform label.

## Invalid Combinations

The application MAY reject combinations that are intrinsically invalid.

For example, a `task_worker` role using a worker transport that lacks required worker configuration should fail fast.

However, combinations SHALL NOT be rejected simply because they are unusual for a named deployment platform.

For example, the application SHALL NOT encode assumptions such as:

```text
if GCP then Cloud Tasks
if local then Celery
if cloud then GCS
```

Those are deployment defaults, not application invariants.

## Observability

Each runtime process SHOULD identify its active role in logs and diagnostic metadata.

Relevant backend selections MAY also be logged in a non-sensitive form.

Examples:

```text
process_role=api
task_backend=cloud_tasks
storage_backend=gcs
cache_backend=postgres
```

Secrets, credentials and sensitive connection strings SHALL NOT be logged.

The runtime role SHOULD be visible enough to diagnose accidental deployment misconfiguration.

## Consequences

### Positive

- Process responsibilities become explicit.
- Deployment provider is removed from application behavior.
- Local and traditional deployments remain supported.
- Managed-cloud execution becomes practical.
- Runtime components can scale independently.
- Initialization is no longer coupled to long-running workers.
- The same application image can serve multiple roles.
- GCP remains a deployment target rather than becoming the application architecture.
- Future cloud providers can be added primarily through deployment composition.
- Invalid infrastructure dependencies become easier to detect.
- Operational reasoning and testing become simpler.

### Negative

- Deployment configuration becomes more explicit.
- More than one process command must be maintained.
- Route isolation requires additional tests.
- Role-specific health behavior must be maintained.
- Multiple supported deployment compositions increase the regression surface.
- Operational documentation must distinguish runtime role from deployment topology.

## Alternatives Considered

### Introduce `CARE_RUNTIME_PROFILE=local|traditional|cloud`

Rejected.

A runtime-profile variable would duplicate information already represented by:

- process role;
- storage backend;
- task backend;
- cache backend;
- deployment configuration.

It would also allow contradictory configurations such as:

```text
CARE_RUNTIME_PROFILE=cloud
CARE_TASK_BACKEND=celery
```

without adding useful application semantics.

Deployment profiles therefore remain operational compositions, not application-level configuration.

### GCP-specific application mode

Rejected.

Configuration such as:

```text
CARE_PROCESS_ROLE=gcp_api
IS_GCP=true
USE_CLOUD_RUN=true
```

would couple application behavior to a provider.

### Preserve topology-driven startup behavior

Rejected.

API, Celery worker and Celery Beat startup should not implicitly determine initialization or unrelated application responsibilities.

### Preserve current Docker Compose topology inside a GCP VM

Rejected as the primary managed-cloud strategy.

It would retain permanently running application infrastructure and much of the operational burden this modernization intends to avoid.

Traditional VM deployment remains supported as a separate deployment choice.

### Kubernetes as the universal runtime abstraction

Rejected.

Kubernetes could host CARE but is not necessary to define application runtime responsibilities.

Making it mandatory would add substantial operational complexity.

### Separate codebases or images for every runtime role

Rejected as the default.

Roles share the same CARE application and business logic.

A common immutable image reduces drift.

Separate images require a concrete justification.

### Abstract every future cloud provider immediately

Rejected.

Portability SHALL be achieved through narrow application abstractions and deployment composition.

AWS, Azure and other profiles SHALL be implemented only when concrete requirements exist.

## Out of Scope

This ADR does not define:

- Terraform module structure;
- actual Cloud Run resources;
- Cloud Run CPU or memory sizing;
- Cloud Run min/max instances;
- Cloud SQL tier or sizing;
- VPC configuration;
- DNS;
- domains;
- TLS;
- Secret Manager resource definitions;
- Artifact Registry repositories;
- detailed Cloud Tasks queue policy;
- Cloud Scheduler schedules;
- AWS runtime profile;
- Azure runtime profile;
- Kubernetes manifests;
- monitoring dashboards;
- alert policies;
- CI/CD workflows;
- deployment promotion;
- production rollback procedure;
- multi-region architecture.

Those belong to subsequent Engineering Specifications and infrastructure decisions.

## Related Documents

- ADR-0001: Portable Object Storage
- ADR-0002: Server-Mediated File Transport
- ADR-0003: Configurable Asynchronous Execution
- ADR-0004: Configurable Application Cache
- ADR-0005: Distributed Locking as a Separate Responsibility
- ES-06: Runtime Roles
- Operations Guide
- Configuration Reference
- Runtime and Deployment Inventory

## Implementation Status

Implemented by ES-06 on `feature/runtime-roles`, 2026-08-09.

- [x] Decision accepted.
- [x] `api` role finalized. Serves the public application and the shared
      diagnostics; registers no worker route; performs no initialization.
- [x] `task_worker` role finalized. One role, two transports: the HTTP endpoint
      (`scripts/start-worker.sh`) and a Celery worker (`scripts/celery_worker.sh`).
- [x] `scheduler` responsibility finalized. Celery Beat carries the role and no
      longer owns initialization. Periodic operations remain independently
      callable, so a platform scheduler can replace the process.
- [x] `init` role finalized. `scripts/initialize.sh` is ephemeral, stops at the
      first failed step, exits non-zero on failure and zero on success, and its
      health is that exit status.
- [x] Runtime-role validation implemented. `config/runtime.py`;
      `CARE_PROCESS_ROLE` accepts `api`, `task_worker`, `scheduler`, `init`, and
      anything else raises `ImproperlyConfigured` naming all four. The ES-03
      values `job` and `celery_worker` are removed.
- [x] Role-specific route isolation verified. Asserted by URL resolution for
      every role and confirmed live: the internal task route returns 404 on the
      API, and every public route returns 404 on the worker.
- [x] Role-specific startup commands verified. Eight entrypoints, each declaring
      its role; recorded in `inventory/runtime-and-deployment.md` §15.1.
- [x] Role-specific health behavior implemented. `HEALTHY_DJANGO` is composed
      from the role and the selected backends; the Celery queue probe is absent
      under Cloud Tasks instead of permanently failing, and `init` has none.
- [x] Initialization fully independent from long-running processes. No
      long-running entrypoint runs `migrate`, `createcachetable`,
      `sync_permissions_roles`, `sync_valueset` or `initialize.sh`.
- [x] Local/traditional runtime compatibility verified. `make up` unchanged;
      four role services start and reach healthy; Celery worker and beat run as
      before. Traditional deployments must now run initialization explicitly —
      see `inventory/unresolved-items.md` L6.
- [x] Managed-cloud-ready runtime composition verified. `api` and `task_worker`
      compose correctly with `cloud_tasks` + `gcs` + `postgres`. The one blocker
      found here — `CARE_CACHE_BACKEND=postgres` preventing every management
      command through `django_ratelimit.E003` — was outside this architecture and
      has since been resolved by the ES-04 follow-up: rate limiting reads a
      dedicated `ratelimit` cache alias instead of `default`. See
      `unresolved-items.md` L1. Redis remained required for rate limiting at the
      time, which was consistent with this ADR's position that Redis is
      capability-specific rather than a blanket runtime dependency. RF2 has
      since made that dependency a selection too.
- [x] Same-image multi-role execution verified. All four roles run from
      `care_local`; no role needs a distinct image.
- [x] Production worker IAM requirement carried into infrastructure phase.
      Stated in `scripts/start-worker.sh`, in the operations guide §112.6, and as
      `unresolved-items.md` L4.

Not addressed, and deliberately so:

- the production image still declares no `CMD`; four roles share one image and
  the orchestrator selects the role by command;
- the Celery Beat container probe remains a start marker (`unresolved-items.md`
  L3);
- the rate-limiting Redis capability dependency remained at the time of writing;
  it is gone as of RF2, which made the counter store selectable rather than
  replacing the library. The `recent_views` dependency
  (`unresolved-items.md` L5) is gone too — RF1 closed it. Both were closed after
  this ADR was written and neither was ES-07 work.
