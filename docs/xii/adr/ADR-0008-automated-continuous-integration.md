# ADR-0008: Automated Continuous Integration, Immutable Artifacts and Controlled Environment Delivery

- **Status:** Accepted
- **Date:** 2026-08-06
- **Last Updated:** 2026-08-19
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None

## Context

The maintained CARE fork must support a repeatable and auditable software delivery process without coupling the application artifact to one deployment environment, one GCP project, or one infrastructure topology.

Previous architecture decisions established that:

- CARE remains locally usable and compatible with traditional deployments;
- application code depends on Django and narrow internal contracts rather than cloud-provider-specific services;
- explicit runtime roles exist for `api`, `task_worker`, `scheduler`, and `init`;
- the same production application image can execute the API, worker, and initialization roles;
- the managed GCP profile uses Cloud Run, Cloud SQL, Cloud Storage, Cloud Tasks, Cloud Scheduler, Cloud Run Jobs, Secret Manager, and Artifact Registry;
- OpenTofu manages the initial GCP infrastructure;
- PostgreSQL can provide the default cache, rate limiting, recent views persistence, and distributed locking required by the Redis-free managed profile;
- Redis remains supported for compatible deployments but is not an architectural requirement of the managed API profile;
- a real staging environment has been deployed and verified on GCP;
- external email delivery is an optional operational capability and is not a prerequisite for deploying CARE;
- secret containers and IAM may be created declaratively, while secret payloads require a separate secure provisioning step.

The next architectural requirement is therefore broader than simply automating deployment to the currently existing GCP environment.

CARE needs a delivery architecture that separates:

1. source validation;
2. artifact construction;
3. infrastructure lifecycle;
4. environment deployment;
5. artifact promotion.

The central requirement is that a CARE release be represented by an immutable application artifact that is independent of its eventual deployment target.

A build produced for staging must not need to be rebuilt merely because the same release is later deployed to production or to another supported runtime.

The initial deployment adapter is GCP, but the delivery architecture must not make GCP the definition of a CARE release.

---

## Decision

CARE SHALL implement automated continuous integration and controlled environment delivery around immutable OCI application artifacts.

GitHub Actions SHALL be the initial CI/CD orchestration platform.

The delivery architecture SHALL distinguish:

```text
source validation
artifact build
artifact publication
infrastructure delivery
application deployment
environment acceptance
artifact promotion
```

These concerns SHALL remain independently executable where practical.

A CARE release SHALL NOT be defined as "the version currently deployed to a particular Cloud Run service."

A CARE release SHALL instead be traceable to an immutable application artifact and its source metadata.

GCP SHALL be the first managed deployment adapter, not the only deployment model supported by the delivery architecture.

---

## 1. Architectural separation

The delivery system SHALL distinguish three primary flows.

### 1.1 Continuous integration and artifact production

```text
source commit
    |
    v
validation
    |
    v
tests
    |
    v
production image build
    |
    v
immutable OCI artifact
    |
    v
artifact registry
```

This flow SHALL NOT require a particular production environment.

### 1.2 Application delivery

```text
immutable artifact digest
    |
    v
selected environment
    |
    v
init role
    |
    v
task worker
    |
    v
API
    |
    v
scheduled jobs
    |
    v
acceptance verification
```

Application delivery consumes an existing artifact.

It SHALL NOT rebuild the application merely because the deployment target changes.

### 1.3 Infrastructure delivery

```text
OpenTofu source
    |
    v
fmt
    |
    v
validate
    |
    v
plan
    |
    v
review / approval
    |
    v
apply
```

Infrastructure delivery SHALL remain distinct from application artifact production.

A new application release SHALL NOT require an infrastructure apply when infrastructure has not changed.

An infrastructure change SHALL NOT require rebuilding the CARE application when application source has not changed.

---

## 2. Continuous integration

CI SHALL validate relevant changes before they become release candidates.

The validation set SHALL include, where applicable:

- formatting;
- linting;
- the upstream-compatible CARE test suite;
- fork-specific architecture tests;
- storage tests;
- file-transport tests;
- task-dispatch tests;
- cache tests;
- rate-limit tests;
- PostgreSQL locking tests;
- recent-views tests;
- runtime-role tests;
- container build verification;
- production static-asset verification;
- OpenTofu formatting;
- OpenTofu validation;
- configuration consistency tests;
- secret or credential scanning where practical.

The exact job partitioning is an implementation detail.

CI SHOULD avoid duplicating expensive work when one verified artifact can be reused by later stages.

---

## 3. Trusted and untrusted CI contexts

Untrusted pull requests SHALL NOT receive deployment credentials or production secrets.

Tests requiring no privileged external resources SHOULD run on ordinary pull requests.

Credentialed or live-environment verification MAY run in trusted contexts such as:

- protected branches;
- explicitly approved workflows;
- staging deployment workflows;
- scheduled verification;
- manually initiated acceptance workflows.

A pull request from an untrusted context SHALL NOT gain access to:

- GCP deployment identity;
- Secret Manager payloads;
- production database credentials;
- SMTP credentials;
- state credentials;
- signing credentials;
- equivalent privileged infrastructure.

---

## 4. Immutable application artifacts

The production pipeline SHALL build an OCI-compatible container image.

A built image SHALL be treated as immutable.

Release identity SHALL include, at minimum:

- repository commit SHA;
- image digest.

Release metadata SHOULD additionally record, where applicable:

- release tag;
- upstream CARE base commit;
- build timestamp;
- CI workflow/run identifier;
- source repository;
- relevant infrastructure revision.

Mutable tags MAY exist for human convenience.

Deployment and promotion SHALL use immutable image digests as the authoritative artifact identity.

A mutable tag SHALL NOT be sufficient evidence that two environments run the same artifact.

---

## 5. Build once, promote the same artifact

An artifact that has passed staging acceptance SHOULD be promoted to production by deploying the exact same immutable digest.

The normal release path SHALL therefore be:

```text
commit
  |
  v
CI
  |
  v
build once
  |
  v
OCI digest
  |
  v
staging deployment
  |
  v
staging acceptance
  |
  v
approval
  |
  v
production deployment
  |
  v
same OCI digest
```

Production promotion SHALL NOT rebuild the application from the same source commit.

This prevents differences caused by:

- changed dependencies;
- changed build tooling;
- mutable package repositories;
- different build arguments;
- accidental local files;
- different generated static assets;
- different timestamps or build environments.

If a rebuild is required, the result SHALL be considered a new artifact and SHALL pass the appropriate acceptance path again.

---

## 5a. Workflow definitions are a control plane

*Added after ES-08 finding D10.*

GitHub registers a `workflow_dispatch` entry point only for a workflow file that
exists on the repository's **default branch**. A delivery workflow that lives
only on a release lineage therefore cannot be started by an operator at all; the
API answers `HTTP 404: workflow ... not found on the default branch`. Pushing the
branch is not sufficient — the definitions must be merged to the default branch.

Delivery workflow definitions are therefore repository-level control-plane
configuration, and they SHALL be permitted to live on the repository default
branch independently of the application revision they build or deploy.

Three things which were previously conflated SHALL be treated as separate:

| concept | decided by |
| --- | --- |
| workflow definition location | the default branch |
| source revision | an explicit, **verified** input |
| deployed artifact | an immutable digest |

The following consequences are normative.

**The presence of a workflow on the default branch SHALL NOT make the default
branch a deployment target.** A control-plane workflow SHALL NOT build or deploy
its own branch merely because it is defined there. It SHALL identify the source
revision explicitly.

**Source trust SHALL be evaluated independently of workflow-definition
location.** Once the built revision is a workflow input it is
attacker-reachable: anyone who may dispatch a workflow may type a ref. A
revision SHALL be treated as trusted if, and only if, it is already reachable
from the managed-cloud release lineage, determined by commit ancestry rather
than by comparing the input string. A branch that merely *contains* the release
lineage SHALL NOT qualify.

**Trust SHALL be established before a credential exists.** The job that decides
source trust SHALL hold no deployment credential and SHALL declare no GitHub
Environment; a gate able to authorize itself is not a gate. Every job requesting
`id-token: write` SHALL depend on it.

**Credentialed jobs SHALL check out the resolved commit SHA**, not the ref that
was supplied. Re-resolving a ref after checking it is a
time-of-check/time-of-use gap: a branch may move between the two reads.

**Application delivery SHALL remain by immutable digest.** A control-plane
deployment entry point SHALL accept a digest and SHALL NOT accept a branch or a
tag as the artifact selector.

**Environment protection SHALL NOT be relaxed to accommodate a control plane.**
A shim on the default branch obtains no identity of its own: the workload
identity bindings accept only the matching GitHub Environment claim, so a
workflow that does not run in the protected environment cannot become the
protected identity.

Three practical constraints were established by running this, not by reading the
documentation, and they shape any control plane of this kind:

- **Reusable-workflow nesting is shallower than it looks.** A chain three
  workflows deep runs; four does not, and the failure is a startup failure with
  no log. The trust gate is therefore a composite action, which costs no nesting
  level, and a build does not chain a deployment onto itself.
- **`./.github/workflows/x.yml` resolves against the calling run's commit**, not
  against the file that contains it. A workflow reachable from the control plane
  SHALL name its dependencies at an explicit ref.
- **A called workflow cannot exceed its caller's permissions**, and an explicit
  `permissions:` block defaults everything it does not name to `none`. A calling
  job SHALL grant every permission its callee requests, including read.

These properties are machine-checked by
`care.utils.delivery.invariants`, so a workflow edited to skip the gate, to
check out an unverified ref, or to give the gate a credential fails CI rather
than review.

---

## 6. Artifact portability

The application image SHALL NOT encode a specific deployment instance.

The production artifact SHALL NOT hardcode:

- GCP project IDs;
- Cloud Run service names;
- environment names;
- database hosts;
- database credentials;
- bucket names;
- queue names;
- domains;
- SMTP credentials;
- environment-specific secret values.

Environment-specific configuration SHALL be supplied at deployment or runtime.

The same artifact SHOULD therefore be usable, where supported, by:

```text
GCP Cloud Run
traditional Docker deployments
VM-based deployments
container orchestration platforms
future managed-cloud profiles
```

Support for a runtime is not implied merely because the artifact is OCI-compatible.

Each supported deployment profile still requires its own operational validation.

---

## 7. Deployment adapters

Environment deployment SHALL be treated as an adapter around the common application artifact.

The initial managed deployment adapter is GCP.

A GCP deployment adapter MAY know about:

- Cloud Run;
- Cloud Run Jobs;
- Cloud Tasks;
- Cloud Scheduler;
- Cloud SQL;
- Cloud Storage;
- Secret Manager;
- Artifact Registry;
- GCP IAM.

The common application build SHALL NOT need to know those details.

Future adapters MAY support other runtimes without redefining the CARE application artifact.

Conceptually:

```text
                     immutable OCI artifact
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
           GCP            Docker / VM      future runtime
        deployment         deployment         adapter
          adapter            adapter
```

Provider-specific deployment logic SHALL remain outside CARE domain logic.

---

## 8. GCP as the first managed deployment adapter

The initial managed-cloud delivery implementation SHALL target the GCP profile defined by ADR-0006 and ADR-0007.

The current GCP environment composition includes, as applicable:

- Cloud Run API;
- private Cloud Run task worker;
- Cloud Run initialization Job;
- scheduled Cloud Run Jobs;
- Cloud Tasks;
- Cloud Scheduler;
- Cloud SQL;
- Cloud Storage;
- Secret Manager;
- Artifact Registry.

These resources are deployment concerns.

Their names and identifiers SHALL NOT define the application artifact.

---

## 9. Runtime roles and the shared image

The production application image SHOULD remain shared across the runtime roles established by ADR-0006.

The same immutable digest SHOULD execute:

```text
api
task_worker
init
```

Scheduled management operations SHOULD also use the same artifact where their dependencies permit it.

Role selection SHALL occur through runtime configuration and commands, not through separately compiled application variants.

Role-specific images MAY be introduced only when a concrete dependency or operational requirement justifies them.

A role-specific image SHALL NOT be introduced merely for organizational convenience.

Development-only tooling or fixture images do not redefine the production artifact when they are not part of the runtime release.

---

## 10. Initialization is a deployment operation

The delivery process SHALL treat initialization as an explicit deployment operation.

The `init` role currently executes the established initialization sequence:

```text
migrate
createcachetable
compilemessages
sync_permissions_roles
sync_valueset
```

The deployment system SHALL invoke the repository-defined initialization contract rather than independently reimplementing those commands in CI/CD configuration.

API and worker startup SHALL NOT perform this initialization sequence.

Long-running services SHALL NOT become the migration mechanism.

---

## 11. Initialization failure semantics

Initialization SHALL fail the deployment when a required initialization step fails.

The delivery sequence SHALL NOT continue to a new application revision after a failed initialization operation.

A failed initialization SHALL NOT be hidden through retries that make an unsuccessful deployment appear successful.

Where initialization is implemented as a managed Job, its execution result SHALL be observed explicitly.

---

## 12. Application deployment order

For a normal managed deployment, the default sequence SHALL be:

1. select an already-built immutable artifact;
2. validate required environment configuration;
3. update the initialization role to that artifact;
4. execute initialization;
5. stop if initialization fails;
6. deploy or update the task worker;
7. verify worker readiness and access policy;
8. deploy or update the API;
9. update scheduled Jobs where required;
10. verify Scheduler or task integration where relevant;
11. run environment smoke or acceptance tests;
12. record deployment metadata.

The task worker SHOULD normally be deployed before an API revision capable of producing new task payloads.

A specification MAY define a different ordering for a particular backward-compatible migration or release strategy.

---

## 13. Database migrations

Database migrations SHALL remain explicit deployment operations.

The delivery system SHALL NOT automatically reverse database migrations during application rollback.

Schema evolution SHOULD preserve compatibility across the deployment window when worker and API revisions may temporarily differ.

Expand-and-contract migration techniques SHOULD be used when a schema change cannot safely support both old and new application revisions simultaneously.

Destructive migrations require explicit engineering judgment.

A successful application rollback does not imply that database state has been rolled back.

---

## 14. Environment promotion

Deployment and promotion are distinct operations.

Deployment means installing an artifact into an environment.

Promotion means selecting an artifact that has already satisfied the required acceptance level and making it eligible for the next environment.

The initial managed promotion path SHOULD be:

```text
build
  ->
staging
  ->
acceptance
  ->
production approval
  ->
production
```

The architecture SHALL NOT require all installations to use that exact environment sequence.

For example, a local or independently operated CARE deployment may consume a published release artifact without using the maintainers' GCP staging environment.

---

## 15. Staging

Staging SHALL be the primary managed environment for validating release candidates before production.

Staging acceptance SHOULD verify the critical architecture paths established by previous ADRs, including where applicable:

- API health;
- authentication;
- worker IAM isolation;
- task dispatch;
- route isolation;
- PostgreSQL connectivity;
- GCS transport;
- cache operation;
- rate limiting;
- Recent Views;
- PostgreSQL advisory locking;
- initialization;
- scheduled Jobs;
- Redis-free operation for the Redis-free profile;
- runtime-role isolation.

Not every acceptance check must run for every documentation-only or infrastructure-neutral change.

The implementation specification SHALL define when acceptance is required.

---

## 16. Production is a deployment target, not a build target

Production SHALL consume an existing release artifact.

Production SHALL NOT have a special application build that embeds production configuration.

The production deployment workflow MAY be configured for a specific operational environment, but the common CI/build architecture SHALL NOT assume that environment is the only possible production installation.

A maintainer MAY operate:

- one GCP production environment;
- multiple GCP environments;
- another cloud profile;
- a traditional deployment;
- another supported OCI runtime.

Those environments consume release artifacts; they do not define them.

---

## 17. Production approval

Production deployment SHALL require an explicit trusted action.

The initial implementation SHOULD use a protected GitHub environment, manual workflow approval, or equivalent repository-supported gate.

The exact human approval model may depend on the operating organization.

The architecture requires that production promotion be:

- intentional;
- authenticated;
- auditable.

It does not prescribe a particular organizational hierarchy.

---

## 18. Infrastructure delivery

OpenTofu infrastructure changes SHALL use a separate validation and delivery path from ordinary application releases.

Infrastructure CI SHALL include:

```text
tofu fmt -check
tofu validate
tofu plan
```

where appropriate.

Production infrastructure applies SHOULD require review of the generated plan.

Special attention SHALL be given to changes involving:

- Cloud SQL replacement;
- storage deletion;
- secret deletion;
- IAM broadening;
- service-account replacement;
- queue replacement;
- networking changes;
- deletion protection;
- backup configuration.

An application release with no infrastructure change SHOULD NOT run a production infrastructure apply merely as ceremony.

---

## 19. Infrastructure and application coordination

Some releases may require both infrastructure and application changes.

When they do, the release specification SHALL identify the dependency ordering.

Examples include:

```text
infrastructure first -> application
```

when the application requires a new managed resource, or:

```text
application first -> infrastructure cleanup
```

during an expand-and-contract transition.

The pipeline SHALL NOT assume that infrastructure and application changes always have the same lifecycle.

---

## 20. Greenfield environment bootstrap

Creating a new managed environment has a different lifecycle from deploying a new application revision to an existing environment.

The bootstrap sequence SHALL distinguish:

```text
infrastructure creation
        |
        v
secret containers and IAM
        |
        v
secure secret-value provisioning
        |
        v
environment configuration complete
        |
        v
application initialization
        |
        v
runtime deployment
```

A greenfield environment SHALL NOT be assumed deployable merely because OpenTofu successfully created its infrastructure.

---

## 21. Secret containers and secret payloads

OpenTofu MAY create:

- Secret Manager resources;
- IAM bindings;
- references required by runtime services.

Secret values SHALL NOT be committed in:

- OpenTofu source;
- `.tfvars`;
- backend configuration;
- GitHub workflow source;
- Dockerfiles;
- repository configuration;
- test fixtures intended for publication.

Secret payload provisioning SHALL be a separate secure operational step unless a future approved secret-management mechanism provides an equally safe automated path.

The absence of an external SMTP credential SHALL NOT by itself prevent deployment when the selected environment intentionally uses a non-delivery email backend.

---

## 22. Email configuration

The delivery architecture SHALL remain provider-neutral with respect to email.

CI/CD SHALL NOT require or hardcode a specific SMTP or transactional-email provider.

An environment MAY use:

- console email;
- SMTP;
- another supported Django email backend.

External email delivery is an operational capability.

Its absence SHALL NOT make a generic CARE artifact invalid.

Production MAY temporarily use a non-delivery backend when explicitly selected by the operator.

A future operational policy MAY impose stricter production requirements without redefining the common application artifact.

No email credentials or provider-specific production values SHALL be committed to the public repository.

---

## 23. Deployment identity

CI/CD SHALL use protected deployment identities.

Long-lived downloaded service-account keys SHOULD NOT be used.

For GCP, Workload Identity Federation or an equivalent short-lived credential mechanism SHOULD be preferred.

Deployment identities SHALL follow least privilege.

Where practical, separate permissions SHOULD exist for:

- artifact publication;
- staging deployment;
- production deployment;
- infrastructure planning;
- infrastructure application.

CI identity SHALL NOT automatically receive every runtime secret available to the application.

---

## 24. Artifact publication

The initial managed artifact registry is GCP Artifact Registry.

Artifact Registry is an implementation of the artifact publication boundary, not part of CARE domain logic.

Release metadata SHALL preserve enough information to retrieve the exact artifact digest.

Future artifact registries MAY be supported without changing the application build contract.

---

## 25. Release metadata

A managed deployment SHALL record enough metadata to reconstruct what was deployed.

At minimum:

```text
application commit
image digest
environment
deployment timestamp
```

Where available, metadata SHOULD also include:

```text
release tag
upstream CARE base commit
CI workflow/run
infrastructure commit
initialization execution
acceptance result
```

Metadata MAY be recorded through:

- GitHub deployment records;
- release artifacts;
- environment metadata;
- deployment manifests;
- equivalent auditable mechanisms.

No sensitive value SHALL be stored as release metadata.

---

## 26. Upstream traceability

The maintained fork SHALL preserve traceability to its upstream CARE base.

Release metadata SHOULD record the upstream base commit used by the fork release.

Upstream synchronization changes SHALL pass the same relevant CI validation as other application changes.

Synchronization SHALL NOT bypass:

- application tests;
- architecture-specific regression tests;
- container build verification;
- infrastructure validation when infrastructure is affected.

---

## 27. Rollback

Application rollback SHALL normally mean deploying a previously known immutable artifact.

Conceptually:

```text
current digest
     |
     X
previous accepted digest
     |
     v
redeploy
```

Rollback SHALL NOT rebuild an old source commit and assume the result is identical.

Database rollback is a separate operation.

The delivery system SHALL NOT automatically reverse migrations as part of application rollback.

Operators SHALL consider schema compatibility before rolling an application revision backward.

---

## 28. Rollback metadata

The delivery system SHOULD make it possible to determine:

- which digest preceded the current deployment;
- which application commit produced it;
- which upstream base it used;
- whether initialization ran;
- which schema state the deployment expects;
- which infrastructure revision was active.

This information SHALL be sufficient to make rollback a controlled operational decision rather than an attempt to reconstruct history from mutable tags.

---

## 29. Failure boundaries

The delivery system SHALL distinguish failures in:

```text
CI
artifact build
artifact publication
infrastructure plan
infrastructure apply
secret provisioning
initialization
worker deployment
API deployment
acceptance
promotion
```

A failure in one phase SHALL NOT be represented as success merely because another phase completed.

For example:

- a successful image build is not a successful deployment;
- a successful OpenTofu apply is not proof that CARE works;
- a successful initialization Job is not proof that the API is healthy;
- a successful staging deployment is not production approval.

---

## 30. Live-cloud verification

A successful infrastructure plan or apply SHALL NOT be considered sufficient evidence for application behaviour.

Managed-environment acceptance SHOULD verify behaviour against the running environment where the relevant property cannot be proven locally.

Examples include:

- Cloud Run IAM enforcement;
- Cloud Tasks OIDC invocation;
- Cloud Storage transport;
- Scheduler execution;
- runtime scale behaviour;
- environment-specific secret access.

Live-cloud tests SHALL be limited to trusted contexts.

---

## 31. Redis compatibility and Redis-free delivery

CI SHALL preserve both architectural properties:

1. Redis-backed configurations remain supported where selected.
2. Redis-free managed configurations remain deployable where selected.

A release SHALL NOT silently reintroduce Redis as an unconditional dependency of:

- API initialization;
- PostgreSQL locking;
- Recent Views;
- PostgreSQL rate limiting;
- Cloud Tasks operation.

Redis MAY remain required when the operator explicitly selects capabilities such as:

```text
CARE_TASK_BACKEND=celery
CARE_RATE_LIMIT_BACKEND=redis
CARE_CACHE_BACKEND=redis
```

CI SHOULD include enough profile coverage to detect accidental coupling.

---

## 32. Local and traditional compatibility

Automated managed-cloud delivery SHALL NOT redefine the local development contract.

Local and traditional deployments MAY continue using:

- Docker Compose;
- PostgreSQL;
- MinIO;
- Redis;
- Celery;
- Celery Beat;
- other already-supported local components.

GitHub Actions and GCP SHALL NOT become runtime dependencies of CARE.

The repository SHALL remain usable without access to the maintainers' CI/CD environment.

---

## 33. Branch and event policy

The implementation MAY use different workflow triggers for:

- pull requests;
- trusted branch updates;
- release candidates;
- staging deployments;
- production promotions;
- infrastructure changes;
- manual operations.

The exact branching policy is an implementation concern.

However, branch names SHALL NOT substitute for artifact identity.

The authoritative deployed application identity remains the immutable image digest.

---

## 34. Public repository constraints

The maintained repository is expected to be publishable.

CI/CD design SHALL assume that repository contents may be publicly visible.

The repository SHALL therefore contain architecture and configuration interfaces, not private operational credentials.

The following SHALL NOT be committed:

- service-account private keys;
- SMTP passwords;
- database passwords;
- Django secret values;
- private JWKS material;
- secret payloads;
- private backend credentials;
- environment-specific authentication tokens.

Public examples SHALL use placeholders or documented variable names.

---

## 35. Security scanning

CI SHOULD perform automated checks for accidentally committed secrets where practical.

Such checks supplement but do not replace:

- `.gitignore`;
- protected GitHub environments;
- Secret Manager;
- short-lived deployment identity;
- code review.

A secret-scanning failure SHOULD block artifact publication when the finding represents a credible committed credential.

---

## 36. Reproducible build boundary

Production images SHOULD be built from repository-controlled source rather than an arbitrary developer working tree.

The build process SHALL avoid including unrelated untracked local files.

The production build context SHOULD be explicitly constrained.

The previously observed difference between a working-tree build and a clean source export SHALL be treated as a delivery concern.

ES-08 SHOULD close the existing `.dockerignore` / build-context finding so CI artifacts are derived from known repository content.

---

## 37. Artifact provenance

Where practical, the delivery system SHOULD preserve provenance connecting:

```text
source commit
     |
     v
CI run
     |
     v
image digest
     |
     v
environment deployment
```

A future implementation MAY add artifact signing or formal supply-chain attestations.

Such mechanisms are not required to establish the initial delivery architecture.

---

## 38. Environment configuration

Environment-specific values SHALL remain deployment inputs.

Examples include:

- project identifier;
- region;
- service names;
- database URL;
- bucket names;
- queue identifiers;
- public URL;
- scaling limits;
- email backend;
- secret references.

The application artifact SHALL consume these values through its supported configuration interfaces.

Changing an environment value SHALL NOT inherently require rebuilding CARE.

---

## 39. Environment independence

The delivery architecture SHALL NOT assume that `dev`, `staging`, and `prod` necessarily live:

- in the same GCP project;
- in different GCP projects;
- in GCP at all.

Those are deployment-topology decisions.

The initial OpenTofu implementation may define concrete GCP environment roots, but the common CI/build contract remains independent of them.

---

## 40. Production environment optionality

The repository SHALL NOT require maintainers to operate a particular production environment merely to produce a release.

It SHALL be possible to:

```text
validate
build
publish
```

without deploying to the maintainers' production environment.

Likewise, another operator MAY consume an accepted artifact and deploy it independently using a supported deployment profile.

---

## 41. No automatic production from arbitrary source changes

Ordinary pull requests SHALL NOT deploy production.

Ordinary branch pushes SHALL NOT implicitly become production releases unless an explicit future policy intentionally defines such a protected release branch.

Production promotion SHALL remain an explicit trusted action.

---

## 42. Infrastructure state

OpenTofu state SHALL remain protected according to ADR-0007.

CI logs SHALL NOT expose sensitive state content.

Infrastructure state SHALL NOT be embedded in application release artifacts.

Application release metadata MAY reference an infrastructure commit or deployment revision but SHALL NOT contain the infrastructure state itself.

---

## 43. Destructive infrastructure operations

Automated delivery SHALL NOT use `tofu destroy` as an application rollback mechanism.

Destructive production infrastructure changes require explicit review.

Existing lifecycle and deletion protections established by ADR-0007 SHALL remain effective when infrastructure delivery becomes automated.

CI/CD SHALL NOT weaken those protections merely to simplify automation.

---

## 44. Acceptance versus health checks

Runtime health checks and release acceptance serve different purposes.

Health checks answer whether a running process is operational.

Acceptance verifies whether a release satisfies critical environment behaviour.

The delivery system SHALL NOT substitute `/ping/` or `/health/` alone for staging acceptance where the release affects:

- task dispatch;
- storage;
- database behaviour;
- IAM;
- scheduling;
- initialization;
- other cross-service functionality.

---

## 45. Production acceptance

After production deployment, the delivery system SHOULD perform non-destructive smoke verification.

Production smoke tests SHALL avoid creating unnecessary persistent data.

Tests requiring destructive or synthetic state SHOULD normally remain in staging unless a safe production-specific strategy exists.

---

## 46. Deployment concurrency

The delivery system SHOULD prevent conflicting deployments to the same managed environment from executing simultaneously.

A second production promotion SHOULD NOT race an already-running production deployment.

Environment-level workflow concurrency or an equivalent mechanism SHOULD be used.

This coordination mechanism is a deployment concern and SHALL NOT be implemented as an application Redis lock.

---

## 47. Cancellation semantics

Cancelling a workflow after initialization or migration has begun SHALL NOT be treated as an automatic rollback.

The implementation SHOULD define non-cancellable or carefully controlled boundaries around state-changing deployment operations where appropriate.

Operators SHALL be able to determine which deployment stages completed before cancellation.

---

## 48. Infrastructure drift

Managed infrastructure workflows SHOULD detect drift through OpenTofu plans.

A clean application deployment does not prove infrastructure has no drift.

Infrastructure drift detection and application acceptance are complementary checks.

---

## 49. Cost awareness

CI/CD automation SHALL NOT require permanently running compute merely to orchestrate deployments.

The delivery system SHOULD use request-driven or ephemeral CI/CD execution.

Environment baseline costs, such as persistent Cloud SQL instances, belong to the deployed environment and SHALL NOT be misrepresented as CI/CD costs.

---

## 50. Observability of deployment

Deployment workflows SHOULD produce enough non-sensitive output for operators to identify:

- artifact selected;
- environment targeted;
- initialization result;
- worker deployment result;
- API deployment result;
- acceptance result.

Secrets SHALL be masked or omitted.

A deployment failure SHOULD identify the failing phase without requiring access to a developer's local machine.

---

## 51. Initial implementation boundaries

The first ES-08 implementation SHALL focus on establishing the reusable delivery architecture and the already-supported GCP adapter.

It SHALL NOT use ES-08 as justification to redesign:

- application domain logic;
- cache architecture;
- rate limiting;
- Recent Views;
- distributed locking;
- task semantics;
- runtime roles;
- email-provider policy;
- OpenTofu resource topology unrelated to delivery.

Defects discovered during implementation SHALL be recorded and classified before unrelated redesign is attempted.

---

## 52. Consequences

### Positive

- Releases become repeatable and auditable.
- CI is separated from any particular production installation.
- The application is built once and promoted as an immutable artifact.
- Staging and production can be proven to run the same bytes.
- GCP-specific deployment logic does not leak into the application artifact.
- Future deployment adapters can consume the same OCI release.
- Infrastructure and application changes can evolve independently.
- Initialization failures stop deployment explicitly.
- Rollback can reference known immutable artifacts.
- Upstream synchronization remains traceable.
- Public-repository operation does not require committing private configuration.
- Redis-free and Redis-compatible profiles can both remain testable.

### Negative

- Multiple workflows and trust boundaries require maintenance.
- Live-cloud acceptance requires protected credentials.
- Environment promotion introduces release metadata and coordination requirements.
- Database migrations still require engineering judgment.
- Infrastructure and application changes sometimes require coordinated ordering.
- Supporting multiple deployment profiles increases the testing matrix.
- Secret provisioning remains a separate operational concern.
- Production rollback cannot safely imply automatic database rollback.

---

## 53. Alternatives Considered

### Make the current GCP production environment the CI/CD target

Rejected.

A particular Cloud Run installation is a deployment target, not the definition of a CARE release.

### Build separately for staging and production

Rejected.

Separate builds weaken the evidence that production runs the artifact accepted in staging.

### Build separate images for API, worker and init

Rejected as the default.

The existing runtime architecture intentionally uses one immutable application image with explicit process roles.

### Automatically deploy every branch to production

Rejected.

Production promotion must remain explicit, trusted and auditable.

### Run migrations during API or worker startup

Rejected.

Initialization is an explicit deployment operation.

### Run OpenTofu apply on every application release

Rejected.

Application and infrastructure lifecycle are distinct.

### Put environment configuration into the container image

Rejected.

It would couple artifacts to particular deployments and prevent true artifact promotion.

### Require external email delivery before production deployment

Rejected as an architectural requirement.

External email delivery is an environment capability. An operator may temporarily select a non-delivery backend.

### Commit secret values so CI can deploy automatically

Rejected.

The repository is expected to be publishable and must not contain operational credentials.

### Use long-lived GCP service-account keys in GitHub

Rejected as the preferred model.

Short-lived federated identity provides a safer trust boundary.

### Rebuild an old commit when rollback is needed

Rejected.

Rollback should use a previously known immutable artifact.

---

## 54. Out of Scope

This ADR does not define:

- the exact GitHub Actions YAML implementation;
- a specific email provider;
- organization-wide release governance;
- automatic database rollback;
- multi-cloud infrastructure modules;
- Kubernetes deployment manifests;
- AWS or Azure deployment adapters;
- mandatory artifact signing;
- mandatory SLSA level;
- organization-wide GCP landing-zone policy;
- a requirement that every CARE operator use GitHub Actions;
- a requirement that every CARE deployment use the maintainers' staging environment.

These may be addressed by later implementation specifications or ADRs.

---

## 55. Related Documents

- ADR-0004: Cache architecture
- ADR-0005: Distributed locking
- ADR-0006: Portable Runtime Profiles with GCP as the First Managed Target
- ADR-0007: Infrastructure as Code for the Initial GCP Profile
- ES-06: Runtime Roles and Process Isolation
- ES-07: GCP Infrastructure and Deployment
- ES-08: Continuous Integration and Controlled Delivery
- Operations Guide
- Configuration Reference
- Runtime and Deployment Inventory
- GCP Configuration Inventory
- Unresolved Items Inventory

---

## 56. Implementation Status

### Architecture and deployment prerequisites already established

- [x] Explicit runtime roles exist.
- [x] Production image can execute API, task-worker and init roles.
- [x] Initialization is separated from long-running startup.
- [x] Production static assets are generated at image build time.
- [x] GCP infrastructure is declared in OpenTofu.
- [x] Artifact Registry is available.
- [x] Managed GCP staging environment exists.
- [x] Staging has been verified without Redis.
- [x] Cloud Tasks worker IAM isolation has been verified.
- [x] Cloud Storage transport has been verified.
- [x] PostgreSQL rate limiting has been verified.
- [x] PostgreSQL Recent Views has been verified.
- [x] PostgreSQL advisory locking has been verified.
- [x] Cloud Scheduler replacement for Beat has been verified.
- [x] External email delivery is explicitly non-blocking.
- [x] Secret containers and secret payload provisioning are architecturally separated.

### ADR-0008 implementation

Implemented on `feature/ci-controlled-delivery` (ES-08), 2026-08-19. A box is
checked when the thing exists in the repository and its behaviour was verified;
where verification required a GitHub Actions run, the box says so, because a
workflow GitHub has never seen has never run (ES-08 sections 133, 134, 197).

- [x] CI workflow implemented. `.github/workflows/ci.yml`.
- [x] Pull-request validation implemented. No credentials; `pull_request` rather than `pull_request_target`.
- [x] Full application regression gate implemented. The Makefile targets, plus the backend-sensitive modules again under the Redis-free selections.
- [x] OpenTofu validation workflow implemented. `infra-check.yml`; fmt and validate need no credentials and run on pull requests.
- [x] Clean production build context enforced. Allowlist in `docker/prod.Dockerfile.dockerignore`; P2 closed, with byte-identical inventories from a dirty tree and a clean export.
- [x] Immutable OCI image build implemented in CI. `build-image.yml`, from the repository's production Dockerfile.
- [x] Artifact publication implemented. Artifact Registry, digest read back from the registry.
- [x] Commit-to-digest provenance recorded. `release-metadata.json`, plus `APP_VERSION` reported by `/app_version/`.
- [x] Upstream base commit recorded in release metadata. From the committed `UPSTREAM_BASE`.
- [x] GCP staging deployment adapter implemented in CI/CD. `infrastructure/scripts/gcp/`, called by `deploy-app.yml`.
- [x] Explicit init execution gate implemented. Verified against staging: the deployment stops on init failure and the window is bounded in the log.
- [x] Worker-before-API deployment ordering implemented. In the script, not in YAML, and each deployed digest is read back.
- [x] Staging acceptance workflow implemented. `acceptance.sh`, executed against the real staging environment.
- [x] Same-digest promotion semantics verified. Deployment and promotion accept a digest and refuse a tag; promotion has no build step, asserted by a test.
- [x] Production deployment workflow implemented. `promote-production.yml`; no production environment was created (ES-08 section 67).
- [x] Production approval gate implemented. Protected `production` environment, after an eligibility job that shows the digest. **Not exercised:** requires the GitHub environment to exist.
- [x] Environment deployment concurrency protection implemented. One group per environment, cancellation disabled.
- [x] Production smoke verification implemented. `smoke.sh`; non-destructive, no synthetic state.
- [x] Application rollback to a previous immutable digest implemented or operationally documented. `rollback.sh`, `rollback.yml`, and the previous digest recorded from the platform at every deployment.
- [x] Infrastructure plan workflow implemented. `infra-check.yml`; plan output deliberately not published.
- [x] Production infrastructure apply gate implemented. `infra-apply.yml`, protected, refuses a destructive plan unless asked, and never destroys.
- [x] Greenfield bootstrap and secret-provisioning sequence operationally documented and verified. `08-continuous-delivery.md` section 11; N4 remains true and is stated as expected behaviour.
- [x] Secret/credential scanning implemented where practical. gitleaks, pinned to a commit.
- [x] Release/deployment metadata recorded. Build, deployment and acceptance records, none committed to the branch.
- [x] Public-repository credential boundary verified. No key material, no secret payload, no tfvars and no state in the repository; asserted by the delivery-invariants test.

Open, and tracked in `inventory/unresolved-items.md` Part D8:

- [ ] A real GitHub Actions run of the CI/build path (D1). Blocked: the branch has not been pushed.
- [ ] A real trusted workflow deploying a CI-built digest to staging (D1). Same blocker; the same scripts were executed manually against staging instead, which proves the environment behaves and does not prove the platform wiring.
- [ ] Workload Identity Federation applied, and the GitHub environments and variables configured (D2). Declared and validated; requires an operator apply and repository-admin configuration.
- [ ] Recent Views covered by automated acceptance (D3). Manually verified in ES-07; automating it would require broadening the deployment identity.

---

## 57. Acceptance Direction for ES-08

ES-08 SHALL demonstrate that the architecture above exists in operation rather than only in workflow source.

At minimum, acceptance SHALL prove:

1. a clean source revision passes CI;
2. a production OCI image is built from controlled repository content;
3. the image is published and identified by digest;
4. the digest is traceable to the source commit;
5. the GCP staging adapter can deploy that existing digest without rebuilding it;
6. initialization executes explicitly and blocks deployment on failure;
7. the worker is deployed before the API;
8. staging acceptance executes against the running environment;
9. no Redis dependency is introduced into the Redis-free staging profile;
10. environment-specific configuration is absent from the application artifact;
11. a production promotion can select the already-accepted digest rather than rebuild it;
12. production deployment requires an explicit trusted gate;
13. CI/CD contains no committed environment credentials;
14. OpenTofu validation is independent of ordinary application deployment;
15. conflicting deployments to the same environment cannot race;
16. release metadata identifies the artifact and source revision;
17. a previously accepted immutable digest can be identified for rollback;
18. the common build remains usable independently of the maintainers' GCP production environment.

A successful GitHub Actions run alone SHALL NOT satisfy these criteria when the property being tested requires inspection of the resulting artifact or running environment.
