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

Redis-compatible infrastructure MAY remain optional for responsibilities that still explicitly require it.

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

Current examples include:

- Celery in traditional/local deployments;
- `recent_views`;
- external plugins or future optional capabilities.

The presence of Redis is therefore capability-specific.

The absence of Redis SHALL NOT prevent:

- object storage;
- PostgreSQL locking;
- explicit initialization;
- Cloud Tasks execution;
- ordinary PostgreSQL-backed cache;

when the selected configuration does not otherwise require it.

CARE SHALL NOT claim to be universally Redis-free while Redis-specific capabilities remain enabled.

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

Optional Redis-compatible services may also represent persistent cost if enabled.

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

- [x] Decision accepted.
- [ ] `api` role finalized.
- [ ] `task_worker` role finalized.
- [ ] `scheduler` responsibility finalized.
- [ ] `init` role finalized.
- [ ] Runtime-role validation implemented.
- [ ] Role-specific route isolation verified.
- [ ] Role-specific startup commands verified.
- [ ] Role-specific health behavior implemented.
- [ ] Initialization fully independent from long-running processes.
- [ ] Local/traditional runtime compatibility verified.
- [ ] Managed-cloud-ready runtime composition verified.
- [ ] Same-image multi-role execution verified.
- [ ] Production worker IAM requirement carried into infrastructure phase.
