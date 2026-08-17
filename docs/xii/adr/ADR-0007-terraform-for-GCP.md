# ADR-0007: Terraform for GCP Infrastructure as Code

- **Status:** Accepted, amended 2026-08-11
- **Date:** 2026-08-06
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None

> **Amendment, 2026-08-11 — the tool is OpenTofu.**
>
> This ADR originally selected Terraform. During ES-07 the maintainers chose
> OpenTofu instead: it is a fork of Terraform under the Linux Foundation, with
> an open source licence rather than the BUSL, and it consumes the same HCL and
> the same providers.
>
> The change is narrow and the rest of this document stands unaltered. The
> configuration language, the resource model, the `hashicorp/google` provider
> and every requirement stated below are identical. What differs is the CLI
> (`tofu` rather than `terraform`), the registry the provider is fetched from,
> and the `required_version` constraint.
>
> Read every "Terraform SHALL" below as "OpenTofu SHALL". The word is retained
> throughout because the requirements are about infrastructure as code and not
> about a vendor, and rewriting them would create churn without changing
> meaning. The file name is likewise unchanged, so existing references still
> resolve.
>
> Recorded as the sole deviation in the ES-07 final report.

## Context

ADR-0006 defines a portable runtime architecture and establishes GCP as the
first managed-cloud target.

The application architecture implemented by ADR-0001 through ADR-0006,
including RF1 and RF2, has deliberately separated application responsibilities
from infrastructure-specific implementations.

The initial managed GCP profile requires multiple coordinated resources:

- required GCP APIs;
- Artifact Registry;
- IAM identities and bindings;
- Cloud Run services;
- Cloud Run Jobs;
- Cloud SQL for PostgreSQL;
- Cloud Storage;
- Cloud Tasks;
- Cloud Scheduler;
- Secret Manager;
- networking and connectivity;
- logging, monitoring and alerting foundations.

Manual creation of these resources would make environments difficult to
reproduce, audit, review, evolve and destroy safely.

The deployment is greenfield and can therefore be created directly from
declared infrastructure.

The infrastructure definition must preserve the portability boundaries already
established by the application architecture. Terraform must configure a
supported runtime profile; it must not become another place where application
behavior is implemented.

## Decision

A Terraform-compatible infrastructure-as-code tool SHALL manage the initial GCP
profile. **As amended on 2026-08-11, that tool is OpenTofu**; the original
decision named Terraform, and the two are interchangeable at the level every
requirement in this document is written.

Terraform SHALL manage the lifecycle and configuration of supported GCP
infrastructure.

Application source code SHALL NOT create production infrastructure at runtime.

Terraform SHALL configure the application through the public configuration
contracts established by the application architecture rather than introducing
GCP-specific branches into CARE domain logic.

## Initial GCP deployment composition

The primary Redis-free GCP composition SHALL be deployable as:

    Artifact Registry
            |
            v
    single immutable CARE image
            |
            +-- Cloud Run API
            |      CARE_PROCESS_ROLE=api
            |
            +-- Cloud Run private task worker
            |      CARE_PROCESS_ROLE=task_worker
            |
            +-- Cloud Run Job
                   CARE_PROCESS_ROLE=init

    Cloud SQL PostgreSQL
            +-- application database
            +-- Django PostgreSQL cache
            +-- PostgreSQL best-effort rate limiting
            +-- Recent Views
            +-- PostgreSQL advisory locks

    Cloud Storage
            +-- Django Storage GCS backend

    Cloud Tasks
            +-- authenticated invocation of private task worker

    Cloud Scheduler
            +-- scheduled operations

    Secret Manager
            +-- runtime secrets

    Cloud Logging / Monitoring

The corresponding application configuration is conceptually:

    CARE_CACHE_BACKEND=postgres
    CARE_RATE_LIMIT_BACKEND=postgres
    CARE_TASK_BACKEND=cloud_tasks
    CARE_STORAGE_BACKEND=gcs

Exact environment-variable names SHALL follow the application's implemented
configuration reference.

This composition SHALL NOT require Redis.

## Redis-compatible GCP composition

Redis support SHALL remain available for compatibility and deployments that
choose to operate Redis.

Terraform MAY support a composition using a managed Redis-compatible service
when required by selected application backends, including:

    CARE_CACHE_BACKEND=redis
    CARE_RATE_LIMIT_BACKEND=redis
    CARE_TASK_BACKEND=celery

The presence of Redis SHALL be determined by the selected runtime composition.

Terraform SHALL NOT provision Redis merely because CARE historically depended
on it.

The Redis-free profile is a first-class supported deployment, not a degraded
fallback.

## Runtime roles

Infrastructure SHALL preserve the runtime-role model defined by ADR-0006.

The same immutable application image SHOULD be used for the API, task worker
and initialization job.

Infrastructure SHALL distinguish roles through:

- command or entrypoint;
- CARE_PROCESS_ROLE;
- environment configuration;
- IAM identity;
- IAM permissions;
- ingress policy;
- scaling configuration.

Separate application images SHALL NOT be required merely to represent runtime
roles.

## Cloud Run API

Terraform SHALL provision a Cloud Run service for the public CARE API.

The API service SHALL:

- run with CARE_PROCESS_ROLE=api;
- expose the public CARE HTTP surface;
- not expose /internal/tasks/execute/;
- not run migrations or initialization commands during instance startup;
- not run Celery Beat;
- not require Redis when the Redis-free composition is selected;
- connect to Cloud SQL;
- access only the secrets and storage resources required by the API role.

Whether the API permits unauthenticated Cloud Run invocation is a deployment
decision separate from CARE application authentication.

If public API access is required, Cloud Run MAY permit public invocation while
CARE continues to enforce application-level authentication on protected API
routes.

## Cloud Run task worker

Terraform SHALL provision a distinct Cloud Run service for HTTP task execution.

The worker SHALL:

- run with CARE_PROCESS_ROLE=task_worker;
- use the same application image where practical;
- expose the internal task execution route required by Cloud Tasks;
- not expose the public CARE API surface;
- not run initialization;
- not run scheduling loops.

### Worker invocation security

The task worker SHALL NOT permit unauthenticated public invocation.

This is a production security requirement, not optional hardening.

Terraform SHALL enforce the worker's invocation policy.

Cloud Tasks SHALL invoke the worker using an OIDC identity.

The worker invoker identity SHALL receive only the IAM permission required to
invoke the worker, normally through:

    roles/run.invoker

Broad identities such as:

    allUsers
    allAuthenticatedUsers

SHALL NOT receive worker invocation permission.

The architecture SHALL NOT add a shared application secret, static bearer
token or equivalent compensating mechanism merely to duplicate Cloud Run IAM.

The application-level worker endpoint intentionally relies on platform IAM for
request authentication.

A deployment that exposes the worker without the required IAM boundary SHALL
be considered invalid.

## Cloud Tasks

Terraform SHALL provision the queues required by the Cloud Tasks backend.

Queue configuration SHALL explicitly define operational policies including, as
applicable:

- region;
- retry behavior;
- maximum attempts;
- maximum retry duration;
- backoff;
- dispatch rate;
- concurrent dispatch limits.

Queue retry policy SHALL account for application behavior such as maintenance
mode returning a retryable response.

Cloud Tasks requests SHALL use OIDC authentication for the private worker.

Queue configuration SHALL NOT require application-managed service-account JSON
keys.

## Initialization

Terraform SHALL provision or support a Cloud Run Job for application
initialization.

The initialization job SHALL run the explicit initialization sequence defined
by the application, including:

    migrate
    createcachetable
    compilemessages
    sync_permissions_roles
    sync_valueset

The job SHALL use:

    CARE_PROCESS_ROLE=init

Normal API and worker startup SHALL NOT perform these operations.

Initialization SHALL be an explicit deployment step.

A failed initialization job SHALL fail the deployment procedure rather than
silently allowing a partially initialized application rollout.

Terraform SHALL NOT reproduce the initialization sequence as infrastructure
logic. It SHALL invoke the application-provided initialization command or
entrypoint.

## PostgreSQL and Cloud SQL

Cloud SQL for PostgreSQL SHALL provide the durable relational database for the
initial GCP profile.

Terraform SHALL manage infrastructure concerns such as:

- Cloud SQL instance;
- PostgreSQL version;
- database creation where appropriate;
- database users or IAM database configuration where appropriate;
- backups;
- deletion protection;
- connectivity;
- availability configuration;
- storage sizing and growth policy;
- environment-specific sizing.

Terraform SHALL NOT contain application schema SQL.

In particular, Terraform SHALL NOT create:

- Django model tables;
- care_cache;
- care_ratelimit_cache;
- Recent Views tables;
- application indexes or constraints;
- migration-managed application objects.

Those belong to Django migrations and management commands.

## PostgreSQL-backed transient responsibilities

In the Redis-free profile PostgreSQL also supports selected non-authoritative or
coordination responsibilities implemented by CARE:

- Django DatabaseCache;
- best-effort PostgreSQL rate limiting;
- Recent Views persistence;
- PostgreSQL advisory locks.

Terraform SHALL provide the database infrastructure required by those
responsibilities but SHALL NOT reimplement their application semantics.

The cache and rate-limit tables SHALL remain distinct as required by the
application configuration.

## Cloud Storage

Terraform SHALL provision private Cloud Storage buckets required by the GCS
storage backend.

Buckets SHALL NOT require public object access for normal CARE operation.

The frontend SHALL NOT receive direct storage credentials.

The application SHALL access objects through Django Storage and the CARE HTTP
transport established by ADR-0001.

Terraform SHALL NOT reintroduce signed-URL transport as a deployment
requirement.

Bucket IAM SHALL use least privilege and SHOULD distinguish application
identities where their required permissions differ.

Production bucket deletion and retention behavior SHALL be explicitly decided
rather than inherited accidentally from Terraform defaults.

## Cloud Scheduler

Cloud Scheduler SHALL replace the need for a continuously running Celery Beat
process in the managed GCP profile.

Scheduled operations SHALL invoke explicit application operations or jobs.

Terraform SHALL declare scheduler resources and their invocation identities.

Scheduler authentication SHALL use managed GCP identity mechanisms where
supported.

The GCP profile SHALL NOT require a permanently running scheduler container.

## Artifact Registry

Terraform SHALL provision the Artifact Registry repositories required for CARE
container images.

Runtime services and jobs SHALL reference immutable image versions where
practical.

Infrastructure code SHALL NOT require separate images for each CARE process
role.

Image build and publication MAY remain a CI/CD responsibility rather than a
Terraform responsibility.

## Service accounts and least privilege

Terraform SHALL create or manage explicit runtime identities where practical.

At minimum, responsibilities SHOULD be separable for:

    API runtime
    task worker runtime
    Cloud Tasks invocation
    initialization job
    Cloud Scheduler invocation
    deployment automation

Identities MAY be combined only when their effective privileges remain
appropriately narrow and the simplification is justified.

Terraform SHALL avoid using broad project-level roles when narrower
resource-level permissions are sufficient.

Runtime service accounts SHALL NOT use committed JSON keys.

## Secret Manager

Terraform SHALL manage Secret Manager resources and IAM access bindings where
appropriate.

Secret values SHOULD be injected through a secure operational or deployment
process.

Plaintext production secret values SHALL NOT be committed to Terraform source,
.tfvars, repository configuration or application source.

Each runtime identity SHOULD receive access only to the secrets it requires.

Sensitive Terraform outputs SHALL be minimized.

## Networking

Terraform SHALL declare networking required for the supported deployment.

Networking decisions SHALL be driven by actual service requirements rather than
by recreating a traditional VM network topology.

Cloud SQL connectivity SHALL use a supported secure GCP mechanism.

Public exposure SHALL be limited to services intentionally designed for public
access.

The task worker SHALL remain protected by IAM regardless of network topology.

A Serverless VPC Access connector SHALL NOT be introduced unless required by a
selected dependency or connectivity design.

## Scale to zero

Infrastructure SHALL preserve the scale-to-zero goals of ADR-0006 where the GCP
service supports them.

The API MAY scale to zero when operational requirements permit.

The HTTP task worker SHOULD scale to zero when idle.

Cloud Run Jobs consume compute only while executing.

Cloud Scheduler and Cloud Tasks replace continuously running application
processes.

Cloud SQL remains a persistent baseline resource and therefore the complete
deployment SHALL NOT be described as zero-cost or entirely scale-to-zero.

Redis SHALL NOT become another persistent baseline cost in the Redis-free
profile.

## Health and readiness

Terraform SHALL configure health behavior consistently with the role-aware
health architecture implemented by ES-06.

Infrastructure probes SHALL NOT assume:

- Celery exists when Cloud Tasks is selected;
- Redis exists when PostgreSQL cache/rate limiting is selected;
- public API routes exist on the task worker.

A deployment SHALL be considered unhealthy based on dependencies relevant to
the selected runtime role and backend composition.

## Logging and observability

The GCP profile SHALL integrate with Cloud Logging through normal container
stdout/stderr unless a stronger requirement is identified.

Terraform SHOULD establish the foundations for:

- service health monitoring;
- Cloud Run errors;
- Cloud Run Job failures;
- Cloud Tasks failures or excessive retries;
- Cloud SQL health;
- storage errors;
- scheduler failures.

Alert policies SHALL be introduced only for concrete operational conditions;
the infrastructure SHALL NOT create speculative monitoring merely to increase
resource coverage.

Existing application logging defects SHALL be tracked separately when they are
not infrastructure defects.

## Environments

Infrastructure SHALL support separate environment compositions for:

    dev
    staging
    prod

Environment separation SHALL include stateful resources and secrets.

Environment differences MAY include:

- Cloud SQL sizing;
- minimum and maximum Cloud Run instances;
- deletion protection;
- backup retention;
- storage lifecycle;
- task queue limits;
- logging or monitoring thresholds.

Application architecture SHALL remain the same across environments.

Module reuse is encouraged, but modules SHALL NOT be introduced solely for
aesthetic abstraction.

## Terraform structure

The Terraform implementation SHOULD prefer understandable composition over deep
module hierarchies.

Modules SHOULD represent meaningful reusable infrastructure boundaries.

A module SHALL NOT be introduced merely because several resources can
technically be grouped together.

Environment-specific configuration SHOULD remain visibly reviewable.

Provider and Terraform versions SHALL be constrained.

## State

Terraform state SHALL be remote and protected.

State storage SHALL:

- restrict access;
- support recovery or versioning;
- separate environments;
- be treated as sensitive infrastructure metadata.

Application runtime service accounts SHALL NOT require access to Terraform
state.

Terraform state SHALL NOT intentionally contain application secret values when
an alternative operational mechanism is available.

## Bootstrap

Resources required to hold Terraform's own remote state create a bootstrap
dependency.

The implementation SHALL document explicitly how the initial state backend is
created.

Bootstrap SHALL be minimal.

The project SHALL NOT hide an undocumented manual prerequisite behind an
otherwise declarative Terraform deployment.

## Plans and review

Production applies SHOULD use reviewed Terraform plans.

Plans SHALL be inspected for destructive or privilege-expanding changes,
especially:

- Cloud SQL replacement;
- database deletion;
- bucket deletion;
- secret deletion;
- IAM broadening;
- service-account replacement;
- queue deletion;
- networking changes;
- disabling deletion protection;
- accidental public worker access.

## Resource protection

Stateful production resources SHOULD use appropriate protections.

Cloud SQL production instances SHOULD use deletion protection.

Storage buckets SHALL have an explicit deletion/retention policy.

Terraform lifecycle rules MAY be used where they protect resources from
accidental destruction, but SHALL NOT be used to conceal unmanaged drift.

terraform destroy is not an application rollback mechanism.

## Application schemas

Terraform SHALL NOT contain embedded application SQL for creating or modifying:

- Django tables;
- database-cache tables;
- rate-limit tables;
- Recent Views tables;
- application models;
- application indexes or constraints.

Those SHALL be created through:

- Django migrations;
- Django management commands;
- explicitly approved application schema tooling.

## Deployment sequencing

A managed GCP deployment SHOULD conceptually follow:

    1. Provision or update infrastructure
    2. Publish/select immutable application image
    3. Update initialization job to that image
    4. Run initialization job
    5. Require successful initialization
    6. Deploy/update API and task worker
    7. Configure/activate asynchronous and scheduled invocation
    8. Verify health and IAM boundaries

The exact CI/CD implementation is outside this ADR, but infrastructure design
SHALL permit this ordering.

A rollout SHALL NOT depend on every Cloud Run instance independently attempting
database initialization.

## Verification requirements

The Terraform implementation SHALL be verifiable beyond successful
terraform apply.

At minimum, the implementation specification SHALL require evidence that:

1. the API runs with the API role;
2. the worker runs with the task-worker role;
3. the worker cannot be invoked anonymously;
4. the intended Cloud Tasks identity can invoke the worker;
5. an unrelated identity cannot invoke the worker;
6. Cloud Tasks can execute a registered task end to end;
7. the initialization job completes successfully;
8. API and worker startup do not perform initialization;
9. the application can upload and retrieve an object through the GCS-backed
   Django Storage path;
10. the Redis-free profile starts and serves requests without Redis;
11. PostgreSQL cache and rate-limit tables are created by application
    initialization rather than Terraform;
12. scheduled operations can be invoked without Celery Beat;
13. secrets are not embedded in the image or committed Terraform values;
14. stateful production resources have the intended destruction protections.

## Consequences

### Positive

- Environments are reproducible.
- Infrastructure changes are reviewable.
- IAM and networking are versioned.
- Greenfield environments can be recreated.
- Drift becomes easier to detect.
- Deployment documentation can reference concrete code.
- Worker isolation becomes an enforceable infrastructure property.
- The Redis-free managed profile does not acquire an unnecessary persistent
  Redis cost.
- Runtime roles remain aligned with the same-image architecture.
- Initialization becomes explicit and observable.

### Negative

- Terraform state must be protected.
- Provider and module versions require maintenance.
- Stateful-resource changes require careful planning.
- Some operational secret workflows remain outside Terraform.
- Multiple runtime identities increase IAM configuration.
- Supporting both Redis-free and Redis-compatible compositions increases the
  Terraform test matrix.
- Cloud SQL remains a persistent baseline cost.

## Alternatives Considered

### Manual GCP configuration

Rejected.

It is not reproducible or sufficiently auditable.

### Terraform

Originally selected; superseded by the 2026-08-11 amendment.

It remains entirely viable, and the configuration in this repository would run
under it unmodified — that is the point of choosing a fork rather than a
different tool. What decided it against Terraform was the licence: HashiCorp
moved Terraform to the Business Source License, and CARE is a Digital Public
Good whose infrastructure definition should be reproducible by anyone under an
open source licence.

Reverting is a one-line change to `required_version` and a different CLI.

### OpenTofu

Selected, 2026-08-11.

A Linux Foundation fork of Terraform under MPL-2.0. It reads the same HCL, uses
the same `hashicorp/google` provider, and has the same state format, so nothing
in this ADR needed to be reconsidered on technical grounds.

The trade-off is that it is a smaller ecosystem with less third-party
documentation, and provider releases reach its registry slightly later. Neither
affects this deployment, which uses one provider and no third-party modules.

### Pulumi

Not selected.

The HCL ecosystem has broad GCP support and matches the current project plan.
Pulumi would also put infrastructure definition in a general-purpose language,
which weakens the boundary this ADR is drawing between infrastructure lifecycle
and application behaviour.

### Kubernetes manifests

Rejected.

Kubernetes is not the selected runtime.

### Application-created infrastructure

Rejected.

Infrastructure lifecycle must remain outside application execution.

### Recreate the Docker Compose topology on a VM

Rejected for the managed GCP profile.

It preserves continuously running infrastructure and defeats the purpose of the
managed runtime architecture.

### Require Redis in every GCP deployment

Rejected.

CARE now has a supported Redis-free application composition. Redis remains a
compatibility and performance option, not an architectural requirement.

### Put application schema creation in Terraform

Rejected.

Terraform owns infrastructure lifecycle; Django owns application schema
lifecycle.

### Protect the worker with an application shared secret

Rejected.

Cloud Run IAM and Cloud Tasks OIDC provide managed identity without introducing
another secret lifecycle.

## Out of Scope

This ADR does not define:

- CI/CD implementation;
- organization-wide GCP landing zones;
- multi-cloud infrastructure code;
- AWS or Azure profiles;
- multi-region application architecture;
- application database migrations;
- a plugin deployment SDK;
- exact production sizing before measurements exist.

## Related Documents

- ADR-0001: Storage
- ADR-0003: Async Runtime
- ADR-0004: Cache
- ADR-0005: Distributed Locking
- ADR-0006: Portable Runtime Profiles
- ES-07: GCP Infrastructure and Deployment
- GCP Operations Guide
- Configuration Reference
- Runtime and Deployment Inventory
- Unresolved Items Inventory

## Implementation Status

- [x] Decision accepted.
- [x] OpenTofu implementation created (amendment above).
- [x] Remote state bootstrap documented and verified.
- [x] Environment layout implemented.
- [x] Required GCP APIs declared.
- [x] Artifact Registry declared.
- [x] Runtime service accounts and IAM declared.
- [x] Cloud SQL declared and protected.
- [x] Cloud Storage declared and protected.
- [x] Cloud Tasks queues declared.
- [x] Cloud Run API declared.
- [x] Private Cloud Run task worker declared.
- [x] Worker IAM/OIDC boundary verified.
- [x] Initialization Cloud Run Job declared and executed successfully.
- [x] Cloud Scheduler resources declared.
- [x] Secret Manager resources and bindings declared.
- [x] Role-aware health behavior verified.
- [x] Redis-free GCP composition verified through infrastructure, initialization and runtime deployment.
- [x] GCS application transport verified end to end.
- [x] Cloud Tasks application dispatch verified end to end.
- [x] Cloud Scheduler operation verified end to end.
- [x] Development environment applied successfully.
- [x] Destructive-change protections tested.

**Post-ES-07 note, 2026-08-16 — evidence only; the decision is unchanged.**

Three findings this deployment produced have since been closed on the
pre-staging hardening branch, and two figures recorded above as characteristic
of the environment are no longer characteristic of it:

- Cloud Run cold start is now **4.3s** for the API and **5.4s** for the worker,
  against 38.7s and 37.5s when this ADR was verified. `collectstatic` and
  `compilemessages` moved into the image build (`unresolved-items.md` L8); the
  services, probes, scaling limits and startup budget declared here are
  unchanged.
- Deployed processes emit complete exception logs, so the "Logging and
  observability" section's assumption — that stdout and stderr reaching Cloud
  Logging is sufficient — now holds in practice as well as in configuration
  (L2). It was not holding: the settings module was disabling `django.request`.
- A permanently unsendable email task is no longer redelivered by Cloud Tasks
  (N2). The queue configuration declared here is unchanged; the worker's status
  mapping is what changed.

**N1 remains open and still blocks the first staging or production deployment.**
Neither environment can send email. Nothing above changes that.

**Amended 2026-08-17 — N1 is reclassified; the decision in this ADR is
unchanged.** It is an operational capability and deployment follow-up, not a
staging or production blocker.

This ADR's *Environments* section already says that application architecture
SHALL remain the same across environments and that environments MAY differ in
operational configuration. Email delivery is such a difference. CARE separates
**application email generation** — rendering, dispatch, execution, failure
reporting, all implemented and portable — from **external email delivery**,
which is an environment-specific operational choice.

The console email backend is therefore a valid runtime configuration in dev,
staging and production. Under it the full asynchronous path is exercised and
observable in Cloud Logging; only the relay hop is absent. No infrastructure
declared by this ADR requires a mail provider, no environment guard rejects the
console backend, and **this repository names no provider**.

An operator who later wants external delivery clears `django_email_backend`,
supplies the generic SMTP settings through `extra_env`, and declares
`EMAIL_PASSWORD` through `optional_secrets` — a configuration change, using the
Secret Manager mechanism this ADR already specifies, with no change to the
infrastructure architecture. Credentials are written directly to Secret Manager
and never reach OpenTofu state, exactly as the *Secret Manager* section requires.

External mailbox delivery has not been verified in any environment, and this
amendment does not claim it has.
