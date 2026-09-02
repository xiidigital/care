# ES-08: Continuous Integration, Immutable Artifact Build and Controlled Delivery

- **Status:** Implemented; superseded for production activation by ES-09
- **Related ADR:** ADR-0008: Automated Continuous Integration, Immutable Artifacts and Controlled Environment Delivery
- **Depends on:** completed ES-01 through ES-07, RF1, RF2, pre-staging hardening, staging readiness
- **Target branch:** `feature/ci-controlled-delivery`
- **Initial CI/CD platform:** GitHub Actions
- **Initial managed deployment adapter:** GCP
- **Primary managed profile:** Redis-free

## 1. Context

ADR-0008 establishes a delivery architecture that separates:

- source validation;
- artifact build;
- artifact publication;
- infrastructure delivery;
- application deployment;
- environment acceptance;
- artifact promotion.

The application and infrastructure architecture required by previous phases is
already implemented.

The repository currently supports:

- portable runtime roles;
- one immutable production application image for API, worker and init;
- provider-neutral storage;
- provider-neutral asynchronous dispatch;
- PostgreSQL cache;
- PostgreSQL best-effort rate limiting;
- PostgreSQL Recent Views;
- PostgreSQL advisory locking;
- Redis-compatible alternative profiles;
- OpenTofu-managed GCP infrastructure;
- an applied and verified GCP dev environment;
- an applied and verified GCP staging environment;
- Artifact Registry;
- Cloud Run API;
- private Cloud Run task worker;
- Cloud Run init and scheduled Jobs;
- Cloud Tasks;
- Cloud Scheduler;
- Cloud SQL;
- Cloud Storage;
- Secret Manager.

The current deployment process has been proven manually and operationally.

ES-08 SHALL automate and formalize that process without redefining the
application architecture or making the resulting release artifact dependent on
one specific GCP environment.

## 2. Objective

Implement automated continuous integration and controlled delivery so that:

- pull requests are validated without privileged deployment credentials;
- trusted source revisions can produce an immutable OCI application image;
- the image is built once;
- the image is published and identified by digest;
- source commit and image digest are traceable;
- GCP staging can deploy that existing digest without rebuilding;
- initialization is explicit and blocks rollout on failure;
- worker deployment precedes API deployment;
- staging acceptance verifies the running environment;
- production promotion selects an already-built digest;
- production deployment requires explicit trusted approval;
- infrastructure delivery remains distinct from application delivery;
- rollback identifies and redeploys an existing immutable artifact;
- the public repository contains no operational credentials.

The implementation SHALL preserve the possibility of deploying the same OCI
artifact through future non-GCP deployment adapters.

## 3. Branch preconditions

Before modifying anything:

1. switch to the current `gcp` integration branch;
2. verify ADR-0008 is committed;
3. verify all previous accepted work is merged;
4. verify tracked worktree is clean;
5. create:

    feature/ci-controlled-delivery

6. perform all ES-08 work only on that branch.

Report before modification:

    git status
    git branch --show-current
    git log -15 --oneline
    git remote -v

Do not work directly on `gcp`.

Do not push unless explicitly instructed.

Do not squash.

Do not begin ES-09.

## 4. Required documents

Read before implementation:

    docs/xii/architecture/00-scope-and-goals.md
    docs/xii/architecture/01-current-runtime.md
    docs/xii/architecture/02-target-runtime.md
    docs/xii/architecture/03-migration-plan.md
    docs/xii/architecture/04-testing.md
    docs/xii/architecture/06-operations.md
    docs/xii/architecture/07-configuration-reference.md

    docs/xii/adr/ADR-0001-*.md
    docs/xii/adr/ADR-0002-*.md
    docs/xii/adr/ADR-0003-*.md
    docs/xii/adr/ADR-0004-*.md
    docs/xii/adr/ADR-0005-*.md
    docs/xii/adr/ADR-0006-*.md
    docs/xii/adr/ADR-0007-*.md
    docs/xii/adr/ADR-0008-*.md

    docs/xii/implementation/ES-01-*.md
    docs/xii/implementation/ES-02-*.md
    docs/xii/implementation/ES-03-*.md
    docs/xii/implementation/ES-04-*.md
    docs/xii/implementation/ES-05-*.md
    docs/xii/implementation/ES-06-*.md
    docs/xii/implementation/ES-07-*.md

    docs/xii/architecture/inventory/runtime-and-deployment.md
    docs/xii/architecture/inventory/cache-and-redis.md
    docs/xii/architecture/inventory/plugin-impact.md
    docs/xii/architecture/inventory/unresolved-items.md
    docs/xii/architecture/inventory/gcp-configuration.md

    infrastructure/README.md

If actual committed paths differ, locate and use the real paths.

The current code, workflows, infrastructure and configuration reference remain
authoritative for concrete implementation details.

## 5. Inventory existing CI/CD

Before creating workflows, inspect the repository for existing:

    .github/workflows/
    GitHub Actions
    Docker build scripts
    image publication scripts
    release scripts
    deployment scripts
    OpenTofu scripts
    GitHub environments
    repository secrets references
    branch protection assumptions
    release metadata files
    tagging conventions

Produce an inventory with:

    existing workflow
    trigger
    trust level
    credentials used
    build performed?
    image published?
    infrastructure touched?
    environment deployed?
    reusable?
    keep / replace / merge / remove

Do not create duplicate workflows before understanding existing behavior.

## 6. Workflow architecture

The GitHub Actions implementation SHALL separate at least these concerns:

    CI
    build/publish
    staging deploy
    production promote/deploy
    infrastructure validate/plan
    infrastructure apply

These MAY be implemented as:

- reusable workflows;
- callable workflows;
- separate top-level workflows;
- a small number of composed workflows.

Avoid one giant workflow that does everything.

The workflow boundaries should reflect ADR-0008 responsibilities.

## 7. Workflow naming

Use clear names.

Conceptually:

    ci.yml
    build-image.yml
    deploy-staging.yml
    promote-production.yml
    infra-check.yml
    infra-apply.yml

Exact names may differ.

Do not encode mutable project-specific values into workflow filenames.

## 8. Pull-request CI

Pull requests SHALL run without deployment credentials.

PR CI SHALL not require:

- GCP service-account credentials;
- Secret Manager payloads;
- production database access;
- staging database access;
- SMTP credentials;
- OpenTofu state write access.

PR CI SHOULD include relevant:

- formatting;
- linting;
- application tests;
- architecture-specific tests;
- container build verification;
- production static-asset verification;
- OpenTofu fmt;
- OpenTofu validate;
- configuration consistency checks.

Live-cloud acceptance SHALL NOT run automatically on untrusted PRs.

## 9. Full application test gate

The maintained application regression suite SHALL be a release gate.

Use the repository's real test commands.

At minimum verify:

- serial suite;
- parallel suite where practical and stable.

Record counts and seeds where supported.

Do not silently exclude fork-specific tests.

Do not use stale upstream test commands if the repository has evolved.

## 10. Test matrix

CI SHOULD cover the important portable configurations without multiplying the
matrix unnecessarily.

At minimum preserve evidence for:

- traditional/local-compatible configuration;
- Redis-free managed configuration;
- Redis-enabled compatibility configuration where current tests already support
  it.

Do not run every possible backend cross-product.

Select representative combinations that detect architectural coupling.

## 11. OpenTofu validation

CI SHALL validate infrastructure separately from application deployment.

For all relevant roots:

    tofu fmt -check
    tofu validate

Where credentials or project access are not required, these checks SHALL run on
pull requests.

Environment-specific plans requiring authenticated state/project access belong
to trusted contexts.

## 12. Infrastructure plan workflow

Implement a trusted infrastructure plan workflow.

It SHOULD support selecting:

    dev
    staging
    prod

The workflow SHALL:

- authenticate using short-lived identity;
- initialize the correct backend;
- run `tofu plan`;
- preserve the plan or human-readable summary where useful;
- identify destructive and privilege-expanding changes.

Production plan output SHALL be reviewable before apply.

Do not automatically apply production from an ordinary branch push.

## 13. Infrastructure apply workflow

Infrastructure apply SHALL be separate from ordinary application release.

Production apply requires explicit trusted invocation or protected environment
approval.

The workflow SHALL not weaken existing:

- deletion protections;
- prevent_destroy behavior;
- bucket protections;
- Cloud SQL protections;
- IAM boundaries.

The workflow SHALL not execute `tofu destroy` as rollback.

## 14. OpenTofu version

Use the version range and tool established by ADR-0007/ES-07.

Do not silently switch back to Terraform.

CI SHALL install a deterministic supported OpenTofu version.

Record the actual version used by acceptance.

## 15. GCP authentication

GitHub Actions SHALL prefer short-lived federated identity.

Use GitHub OIDC -> GCP Workload Identity Federation or the current GCP-supported
equivalent.

Do not create or commit service-account JSON keys.

Do not require maintainers to paste long-lived GCP keys into GitHub secrets.

If Workload Identity Federation does not yet exist, implement the required
infrastructure through OpenTofu where appropriate.

Document the trust relationship explicitly.

## 16. GitHub identity trust

The GCP trust configuration SHALL restrict which GitHub workflows/repository
contexts may impersonate deployment identities.

Do not grant arbitrary GitHub repositories access.

Where practical, constrain by:

- repository;
- branch/ref;
- environment;
- workflow;
- subject claims.

Use the narrowest practical trust expression without making normal operation
unmaintainable.

## 17. Deployment service accounts

Evaluate separate automation identities for:

    artifact publication
    staging application deployment
    production application deployment
    infrastructure plan/apply

Do not automatically use one broad CI service account for all operations.

Identity sharing is acceptable only when its effective permissions remain
appropriately narrow and the simplification is justified.

## 18. GitHub environments

Use GitHub Environments where they materially improve:

- deployment approval;
- environment-specific variables;
- environment-specific secret references;
- deployment concurrency;
- auditability.

At minimum evaluate:

    staging
    production

Production SHALL require an explicit protected gate.

Staging may deploy automatically from a trusted integration/release event if
that policy is documented.

## 19. No hardcoded deployment instance in common build

The common CI/build workflow SHALL NOT hardcode:

- project ID;
- Cloud Run service;
- staging environment;
- production environment;
- Cloud SQL instance;
- GCS bucket;
- queue;
- domain.

The common build workflow produces an artifact.

Deployment workflows consume environment configuration separately.

## 20. Build context hardening — P2

Close the existing P2 build-context finding.

The production image build SHALL use controlled repository content.

Untracked local files SHALL NOT silently enter the production image.

Preferred approaches include:

- correct `.dockerignore`;
- build from a clean Git checkout in CI;
- both.

The implementation SHALL prove that unrelated untracked files do not affect:

- image size;
- image digest inputs;
- application contents.

Do not depend on a developer manually creating a git archive as the normal CI
build path unless that is deliberately chosen and documented.

## 21. .dockerignore

Review `.dockerignore`.

Exclude at minimum categories that should not enter production images, where
applicable:

    .git
    .github local artifacts if not needed
    .claude
    IDE metadata
    test results
    caches
    local virtualenvs
    local build artifacts
    Terraform/OpenTofu state
    local tfvars/backend files
    documentation build output
    scratch files
    local database dumps
    secrets

Do not exclude source/runtime files required by the application.

Verify against a real production build.

## 22. Production image build

CI SHALL build the existing production image.

Do not create a separate "CI production image" Dockerfile.

The build SHALL include:

- production dependencies;
- build-time collectstatic;
- build-time compilemessages;
- static assets;
- runtime scripts;
- application code.

Do not inject environment-specific production secrets at build time.

## 23. Build without runtime infrastructure

The production image build SHALL not require:

- Cloud SQL;
- Redis;
- GCS;
- Cloud Tasks;
- Secret Manager runtime access;
- SMTP.

Placeholder values used solely for settings parsing must not become runtime
configuration baked into the image.

## 24. Build cache

GitHub Actions MAY use build cache.

Cache use SHALL not change artifact correctness.

The final image digest remains authoritative.

Do not make successful builds depend on a private developer-local cache.

## 25. Image tagging

Published images MAY receive useful tags such as:

    commit SHA
    release candidate
    semantic release tag

But promotion and deployment SHALL use the digest.

Do not make `latest` authoritative.

## 26. Artifact Registry publication

The initial CI implementation SHALL publish production OCI images to the
Artifact Registry created by ADR-0007.

The build workflow SHALL output:

    image repository
    image tag(s)
    immutable digest
    full digest reference

These outputs SHALL be reusable by deployment workflows.

## 27. Artifact provenance metadata

Record the relationship:

    source commit
      ->
    GitHub Actions run
      ->
    image digest

At minimum preserve:

- repository;
- commit SHA;
- image digest;
- workflow run URL or run ID.

Where practical also preserve:

- upstream CARE base;
- build timestamp;
- release tag.

Do not include secrets.

## 28. Upstream base detection

Determine the fork's upstream CARE base using the current documented fork
strategy.

Do not guess based only on branch names.

Record the upstream base commit in release metadata.

If the repository already stores this relationship another way, reuse it.

## 29. Reusable build workflow

Prefer a reusable build workflow or equivalent mechanism so staging and
production do not each rebuild.

The workflow output SHALL include the immutable digest.

A downstream deployment workflow SHALL be able to accept:

    image_digest

as an explicit input.

## 30. Staging deployment input

The staging deployment workflow SHALL consume an existing immutable image
reference.

It SHALL NOT perform a new production image build.

Staging deployment MAY be invoked:

- automatically from an accepted trusted branch event;
- manually with a digest;
- from the build workflow after trusted CI succeeds.

Whichever policy is chosen must preserve digest identity.

## 31. Staging infrastructure independence

Application deployment to staging SHALL NOT automatically run OpenTofu apply
unless a deliberate infrastructure change is part of the deployment.

The staging deploy workflow MAY verify infrastructure outputs/configuration.

Do not couple every release to an infrastructure apply.

## 32. Staging environment configuration

Use environment configuration already established by ES-07/staging readiness.

Do not embed staging configuration into the image.

Obtain needed deployment values from:

- GitHub environment variables;
- repository variables;
- OpenTofu outputs;
- GCP resource discovery;
- other documented non-secret configuration.

Sensitive runtime values remain in Secret Manager.

## 33. Greenfield bootstrap — N4

Formalize N4.

A new environment requires:

1. state/bootstrap infrastructure;
2. environment infrastructure;
3. secret containers;
4. secure secret payload provisioning;
5. application image;
6. init;
7. runtime deployment.

The CI/CD implementation SHALL document this explicitly.

Do not pretend a greenfield environment can complete automatically before its
required secret values exist.

## 34. Secret provisioning

Secret payload provisioning MAY remain manual or external to the main CI/CD
pipeline.

That is acceptable.

The requirement is that it be:

- explicit;
- documented;
- secure;
- separate from repository content.

Do not force secret values into OpenTofu state to achieve "full automation."

## 35. Staging initialization gate

The staging deployment workflow SHALL:

1. configure/update the init Job to the selected digest;
2. execute the init Job;
3. wait for completion;
4. stop deployment if init fails.

Do not deploy a new API revision after failed init.

Record the Job execution identifier/result.

## 36. Worker-before-API ordering

After successful init:

1. deploy/update worker to selected digest;
2. wait for worker readiness;
3. verify worker IAM boundary remains correct;
4. deploy/update API to selected digest;
5. wait for API readiness.

This order SHALL be explicit in workflow logic.

Do not rely solely on OpenTofu resource graph ordering for application release
semantics.

## 37. Scheduled Jobs

Update application Cloud Run Jobs to the selected digest as part of application
delivery where they are part of the same release.

Do not require OpenTofu apply merely to change the application image digest if
the existing deployment architecture supports a dedicated image rollout path.

If OpenTofu currently owns image references, implement the smallest clean
mechanism that preserves infrastructure/application lifecycle separation.

Document the chosen strategy.

## 38. Scheduler configuration

Cloud Scheduler definitions normally belong to infrastructure delivery.

Ordinary application release SHALL not recreate Scheduler resources.

If scheduled Jobs need a new image, update the Job target/runtime artifact, not
the Scheduler schedule unless schedule infrastructure changed.

## 39. Staging acceptance workflow

Implement reusable staging acceptance.

It SHALL run against the deployed staging environment.

At minimum verify critical non-destructive paths:

- API ping/health;
- application version/digest;
- worker IAM rejects anonymous invocation;
- worker/API route isolation;
- Cloud Tasks application dispatch;
- GCS upload/download;
- PostgreSQL cache;
- PostgreSQL rate limiting;
- Recent Views;
- PostgreSQL locking through a safe path;
- one controlled scheduled Job;
- console email path;
- Redis-free composition.

Reuse existing ES-07/staging verification scripts where practical.

Do not duplicate all acceptance logic in YAML.

## 40. Acceptance data cleanup

Acceptance tests SHALL clean up synthetic data where practical.

Do not leave:

- synthetic patients;
- acceptance files;
- temporary users;
- test MFA state;
- temporary Jobs;
- temporary IAM grants.

If a check intentionally leaves state, document it.

## 41. Staging acceptance result

Acceptance SHALL produce a machine-observable success/failure result.

A failed required acceptance test SHALL block promotion eligibility.

Do not merely print warnings and continue.

## 42. Accepted artifact record

When staging acceptance succeeds, record that the specific image digest passed.

The record MAY be:

- a GitHub artifact;
- release metadata file;
- deployment environment metadata;
- GitHub deployment status;
- another auditable mechanism.

The accepted status must identify the exact digest.

## 43. Promotion input

Production promotion SHALL accept an existing image digest.

The promotion workflow SHALL NOT:

- rebuild source;
- resolve a mutable tag and assume it is unchanged;
- silently select HEAD;
- silently select latest.

The user/operator must be able to see the digest being promoted.

## 44. Promotion eligibility

By default, production promotion SHOULD require evidence that the digest has
passed staging acceptance.

Allowing emergency override MAY be considered, but if implemented it must be:

- explicit;
- privileged;
- auditable;
- clearly marked as bypassing normal promotion policy.

Do not silently bypass staging evidence.

## 45. Production environment gate

Production deployment SHALL use an explicit trusted gate.

Use GitHub protected environment approval or an equivalent supported mechanism.

The workflow must clearly display:

- target environment;
- image digest;
- source commit if known;
- staging acceptance status.

No ordinary pull request or arbitrary branch push may deploy production.

## 46. Production deployment configuration

Production deployment SHALL consume runtime/environment configuration separately
from the image.

Do not require:

- real SMTP;
- a specific email provider;
- Redis.

The currently valid console backend may remain selected operationally.

The workflow SHALL not reject production merely because external email delivery
is unconfigured.

## 47. Production init gate

Production deployment SHALL execute the same explicit init contract.

The workflow SHALL stop after init failure.

Do not automatically roll back database migrations.

Do not deploy new API/worker revisions after failed init.

## 48. Production worker-before-API

Preserve the same deployment ordering:

    init
    worker
    API
    scheduled Jobs where applicable
    smoke verification

Production shall not invent a separate release ordering without a concrete
reason.

## 49. Same digest verification

After staging deployment, verify running:

- API;
- worker;
- init Job definition;
- scheduled application Jobs where applicable

reference the selected digest.

After production promotion, perform the equivalent verification.

Production acceptance SHALL prove the digest is the same artifact accepted in
staging.

## 50. Production smoke verification

Implement a non-destructive production smoke stage.

At minimum verify:

- API health;
- application version/digest;
- worker IAM boundary;
- role route isolation.

Additional checks may run if they can be performed safely without synthetic
persistent data.

Do not run destructive staging-style acceptance automatically in production.

## 51. Deployment concurrency

Use GitHub Actions concurrency or an equivalent mechanism.

Deployments targeting the same environment SHALL not run concurrently.

Conceptually:

    staging deployment group
    production deployment group

A newer deployment MAY cancel an older one only if cancellation is safe for the
phase in which it occurs.

## 52. Cancellation safety

Do not make migration/init execution casually cancellable in a way that obscures
whether it completed.

Where possible, structure workflows so state-changing steps are clearly bounded.

After cancellation, operators must be able to determine:

- whether init started;
- whether init succeeded;
- whether worker changed;
- whether API changed.

## 53. Release metadata

Create a durable non-secret release/deployment record.

At minimum record:

    application commit
    upstream base commit
    image digest
    staging deployment result
    staging acceptance result
    production deployment result if applicable
    infrastructure commit/revision
    deployment timestamp

This need not be a database.

Prefer repository/GitHub-native mechanisms where practical.

## 54. Release tag policy

A Git tag/release MAY be used to label an accepted artifact.

Do not make tagging itself rebuild the image unless the tag intentionally
represents a new artifact.

If semantic versioning is introduced, it must map to an existing digest.

Do not invent a versioning strategy unless one already exists or is required for
the implementation.

## 55. Artifact retention

Do not automatically delete the immediately previous accepted production image.

Keep enough immutable history to support rollback.

Artifact lifecycle cleanup MAY be added later with explicit retention rules.

Do not let aggressive cleanup destroy rollback candidates.

## 56. Rollback workflow

Implement or document an operational rollback path.

Rollback SHALL accept a previously published immutable digest.

The rollback flow SHALL:

1. identify the prior accepted digest;
2. verify it still exists;
3. consider database compatibility;
4. update worker;
5. update API;
6. update scheduled Jobs if applicable;
7. perform smoke verification.

Do not rebuild old source.

Do not automatically reverse migrations.

## 57. Database rollback warning

The rollback workflow/documentation SHALL explicitly state:

    application rollback != database rollback

If the current schema is incompatible with an older application artifact,
rollback may be unsafe.

The workflow SHALL not pretend otherwise.

## 58. Infrastructure workflow independence

Application rollback SHALL not run OpenTofu apply unless infrastructure
actually needs to change.

Infrastructure rollback is a separate operation.

## 59. Security scanning

Add practical secret scanning.

Prefer native or well-maintained tooling.

At minimum detect obvious committed:

- private keys;
- GCP service-account credentials;
- high-confidence tokens;
- known secret patterns.

Do not invent a bespoke secret scanner unless necessary.

Do not require live deployment credentials for scanning.

## 60. Dependency/security scanning

Evaluate existing repository tooling before adding new scanners.

A minimal dependency or container vulnerability check MAY be included where
practical.

Do not make ES-08 fail because an unrelated scanning ecosystem requires major
repository refactoring.

Record findings honestly.

## 61. Workflow permissions

Every GitHub Actions workflow SHALL declare minimal permissions where practical.

Avoid default broad write permissions.

Explicitly review need for:

    contents
    packages
    id-token
    deployments
    actions
    security-events

Do not grant write permissions to untrusted PR workflows unnecessarily.

## 62. Fork safety

Because the repository is public/forkable, workflows must be safe when run from
fork pull requests.

A fork PR SHALL not gain privileged secrets through:

    pull_request_target
    unsafe checkout
    untrusted workflow execution

Do not use `pull_request_target` for code execution unless the trust model is
proven safe and necessary.

## 63. GitHub cache safety

Build/test caches SHALL not be used as a secret storage mechanism.

Do not cache:

- credentials;
- `.env`;
- tfvars with secrets;
- secret payloads;
- Terraform/OpenTofu state.

## 64. OpenTofu plan artifacts

If plan files are persisted between workflow jobs, treat them as sensitive
infrastructure metadata.

Do not publish them publicly by default.

Human-readable summaries may be safer.

Production apply SHALL use a reviewed trustworthy plan or regenerate and
revalidate under the approved workflow, according to the implementation design.

## 65. Infrastructure production gate

Production infrastructure apply SHALL require explicit approval.

It SHALL be separate from production application promotion.

An application release must not receive infrastructure-admin privileges merely
because it can deploy a Cloud Run revision.

## 66. GCP staging adapter

Implement the first managed deployment adapter around the existing GCP
infrastructure.

The adapter SHALL know how to:

- authenticate;
- select staging resources;
- update image references;
- execute init;
- update worker;
- update API;
- update Jobs;
- run acceptance.

The common build workflow SHALL not contain these GCP-specific deployment
details.

## 67. GCP production adapter

Implement production deployment logic structurally, but do not deploy production
unless explicitly instructed.

The workflow/configuration must validate and be reviewable.

Production environment may not currently exist.

Do not create it merely to satisfy ES-08.

## 68. Future adapters

Do not implement Kubernetes/AWS/Azure deployment in ES-08.

However, avoid designing the common artifact/build contract in a way that would
make those impossible.

The boundary should remain:

    common OCI artifact
        ->
    deployment adapter

## 69. Local release consumption

Document how another operator can consume a published image independently.

Conceptually:

    docker pull <repository>@sha256:<digest>

Do not require access to the maintainers' staging or production environment to
consume an artifact.

If Artifact Registry access makes public consumption impossible, document the
current limitation rather than redesigning registry distribution in this phase.

## 70. Artifact registry visibility

Do not automatically make Artifact Registry public.

Registry publication policy is an operational/security decision.

The release architecture remains portable even if the initial registry is
private.

Future mirroring to another registry may be added later.

## 71. Public repository configuration

Workflow files may contain:

- generic variable names;
- non-secret resource naming conventions;
- environment names;
- reusable action references.

They SHALL NOT contain:

- secret payloads;
- private service-account keys;
- SMTP credentials;
- database passwords;
- JWKS private material;
- access tokens.

Use GitHub environment/repository configuration for deployment metadata where
appropriate.

## 72. GitHub variables vs secrets

Use variables for non-sensitive deployment metadata.

Use secrets only where values are actually secret.

Do not classify project IDs or region names as secrets merely out of habit.

Do not store runtime application secrets in GitHub when the runtime already
reads them from GCP Secret Manager unless the workflow itself genuinely needs
them.

## 73. Workload identity configuration

Prefer storing only non-secret identifiers in GitHub, such as:

    workload identity provider resource name
    service account email
    project ID
    region

OIDC token exchange shall provide authentication.

No private key material should exist in GitHub for GCP deployment.

## 74. Infrastructure bootstrap documentation

Document the one-time steps required before GitHub Actions can manage GCP,
including:

- state bootstrap;
- Workload Identity Federation bootstrap;
- GitHub trust configuration;
- GitHub environment/variables setup;
- required manual secret payload provisioning.

Clearly distinguish:

    repository setup
    environment bootstrap
    ordinary release

## 75. Bootstrap chicken-and-egg

If Workload Identity Federation itself must be created before CI can use it,
document the initial operator authentication path.

It is acceptable for one initial OpenTofu apply to use an authenticated operator.

Do not hide this prerequisite.

## 76. Staging secrets

The staging workflow SHALL not need to know application secret values.

Runtime services should reference Secret Manager containers/versions.

Deployment automation only needs permission to update runtime resources, not to
read every secret payload.

Verify this boundary.

## 77. Production secrets

The production workflow SHALL follow the same principle.

Production deployment identity should not automatically gain secretAccessor on
all runtime secrets unless required by the deployment mechanism.

## 78. Environment metadata

Maintain a clear mapping from logical environment to deployment configuration.

For GCP this may include:

    project
    region
    API service name
    worker service name
    init Job name
    queue
    scheduled Jobs
    artifact repository

Do not put this mapping into the application image.

## 79. Staging and dev same-project finding N3

Current dev and staging share a GCP project.

ES-08 SHALL not require fixing N3.

However, CI/CD must not assume environments always share a project.

Environment configuration should permit a future staging/project separation
without workflow redesign.

## 80. Email provider neutrality

CI/CD SHALL not select or require an email provider.

Console email remains a valid configured backend.

Future SMTP/API credentials may be provisioned separately.

No production promotion gate in ES-08 may require external mailbox delivery.

## 81. Acceptance scripts

Prefer executable repository scripts over large inline YAML command blocks for
complex acceptance logic.

Scripts should:

- be runnable locally by an authenticated operator where practical;
- accept environment inputs;
- avoid secret output;
- return meaningful exit codes.

Do not move application business logic into shell scripts.

## 82. Acceptance portability

Separate environment-independent checks from GCP-specific checks where
practical.

For example:

    API contract checks
    digest/version checks

may be reusable, while:

    Cloud Run IAM
    Cloud Tasks
    Cloud Scheduler

belong to the GCP adapter.

Do not over-abstract prematurely.

## 83. Workflow observability

A reviewer should be able to see:

- which commit is being built;
- which digest was produced;
- which environment is targeted;
- whether init passed;
- whether worker deployment passed;
- whether API deployment passed;
- whether acceptance passed.

Do not bury core deployment state in hundreds of unstructured log lines.

Use GitHub job summaries where useful.

## 84. Error handling

Workflow failure SHALL be explicit.

Do not use broad:

    continue-on-error: true

for required deployment stages.

Optional diagnostics may continue after failure only if the primary result
remains failed.

## 85. Retry behavior

Use retries only for operations known to be transient.

Do not automatically retry:

- failed migrations;
- invalid configuration;
- IAM denial;
- missing secrets;
- deterministic acceptance failures.

Short retries for propagation/readiness may be appropriate.

## 86. Deployment readiness waits

After updating Cloud Run services, wait for the new revision to become ready.

Do not immediately run acceptance against an old revision.

Verify the active revision/digest.

## 87. Cloud Run digest verification

Query deployed revision metadata after rollout.

Compare the deployed image digest with the requested digest.

Do this for:

- worker;
- API;
- relevant Jobs.

A successful CLI update alone is not enough.

## 88. Worker IAM verification during staging delivery

Staging acceptance SHALL preserve the existing critical IAM test:

- unauthenticated worker invocation is rejected before Django;
- intended task invocation succeeds.

Do not accidentally weaken worker IAM as part of release automation.

## 89. Redis-free verification during staging delivery

Staging acceptance SHALL verify the selected Redis-free profile remains active.

At minimum inspect configured backends and runtime behavior.

Do not provision Redis as a workaround for CI/CD failures.

## 90. Database cache/rate-limit verification

Staging acceptance SHOULD verify:

- `care_cache`;
- `care_ratelimit_cache`;

remain operational and distinct.

Do not create those tables from CI directly.

They remain the init role's responsibility.

## 91. Migration state metadata

Record the init execution associated with a release.

Do not attempt to serialize the full Django migration table into release
metadata.

The goal is operational traceability, not duplicating the database schema state.

## 92. Production rollback candidate

After successful production deployment, preserve the previous production digest
as a known rollback candidate.

Do not assume "previous tag" equals previous deployed digest.

Read actual deployment metadata.

## 93. Roll-forward preference

When a failure involves irreversible or forward-only database changes, operators
may need to fix forward instead of roll back.

Document this.

CI/CD shall not promise rollback is always safe.

## 94. Build reproducibility verification

Build the same source revision through the controlled CI build more than once
where practical.

Container digests may differ for legitimate build-metadata reasons unless the
build is fully reproducible.

The important requirement is controlled source input and immutable published
output.

Do not claim bit-for-bit reproducibility without proof.

## 95. Image contents verification

Add checks proving production image does not contain obvious local-only
artifacts.

Examples:

    .git
    .claude
    test-results
    terraform.tfstate
    local env files
    editor metadata
    scratch directories

Do not expose image filesystem contents containing secrets in logs.

## 96. Build-time secret verification

Inspect Docker build configuration for secret leakage.

Do not pass production secrets as ordinary Docker build args.

If build secrets are ever required, use a mechanism that does not persist them
in image history/layers.

Current production build should not need application runtime secrets.

## 97. Static assets

CI image verification SHALL prove production static assets exist before
publication.

Do not reintroduce runtime collectstatic.

## 98. Compilemessages

Likewise verify compiled localization catalogs exist in the production image.

Do not reintroduce compilemessages into long-running production startup.

## 99. Container startup verification

Before publication or staging deployment, run a bounded container startup
verification where practical.

Do not require live GCP merely to prove the image starts.

Use safe build-time/test configuration.

## 100. Artifact publication gate

Do not publish a release-candidate production image until required CI gates pass.

A failed test run SHALL not produce an artifact treated as accepted for
promotion.

Diagnostic images may be published separately only if clearly distinguished.

## 101. Workflow reuse

Avoid copy/pasting the same auth/build/deploy logic across multiple workflows.

Use reusable workflows/composite actions/scripts where they form a stable
boundary.

Do not create abstraction for every three shell commands.

## 102. Third-party GitHub Actions

Pin third-party actions to stable versions, preferably commit SHAs for
security-sensitive workflows where practical.

At minimum avoid floating:

    @main
    @master

Review permissions required by each action.

Do not add unnecessary marketplace dependencies.

## 103. GitHub Actions version maintenance

Document that pinned action versions require periodic maintenance.

Do not create custom replacements merely to avoid version upgrades.

## 104. Release event

Choose and document the initial trusted event that produces/publishes release
artifacts.

Possibilities include:

- merge/push to `gcp`;
- manually dispatched build;
- release tag.

The implementation SHALL not assume that every commit on every branch is a
production release.

The chosen policy should support upstream synchronization work without
accidental production deployment.

## 105. Staging deployment policy

Choose an initial staging policy.

A reasonable default is:

    trusted build from gcp
        ->
    publish digest
        ->
    deploy to staging
        ->
    acceptance

But implementation may choose manual staging deployment if that better fits the
current operating model.

Document the choice.

## 106. Production promotion policy

Production promotion SHALL remain manual/approved.

It SHALL consume the accepted digest.

Do not automatically promote every green staging deployment to production.

## 107. Production environment absence

If no real production environment exists yet, the production workflow SHALL be
implemented and statically/configurationally verified without creating
production resources.

Do not invent a production project or deploy one.

Acceptance for this part may prove:

- workflow inputs;
- approval gate;
- environment separation;
- same-digest semantics;
- no rebuild;
- deployment command construction.

## 108. Dry-run capability

Where practical, deployment workflows SHOULD support a safe preview/dry-run
mode.

For infrastructure this is OpenTofu plan.

For application deployment, a summary of intended:

    environment
    digest
    services/jobs

may be sufficient.

Do not pretend Cloud Run update has a universal no-op dry-run if it does not.

## 109. Manual operator path

CI/CD automation SHALL not make manual recovery impossible.

Document the underlying commands for:

- deploy a digest;
- run init;
- inspect worker;
- inspect API;
- run acceptance;
- rollback digest.

Automation should orchestrate understandable operations rather than hide them
behind opaque custom machinery.

## 110. Local compatibility

ES-08 SHALL not break:

- local Docker Compose;
- traditional Celery;
- Redis-backed configuration;
- MinIO local storage;
- direct developer test execution.

If shared scripts or application configuration change, rerun local regression.

## 111. Application source modifications

Application source changes are allowed only when CI/CD implementation exposes a
real blocker.

Before changing application code:

1. record the blocker;
2. prove it;
3. make the smallest correction;
4. add regression coverage.

Do not use ES-08 for unrelated cleanup.

## 112. Infrastructure modifications

Infrastructure may be modified when required for:

- Workload Identity Federation;
- deployment IAM;
- GitHub automation identity;
- release metadata integration;
- CI/CD-safe image rollout.

Do not redesign unrelated runtime infrastructure.

## 113. Workload Identity Federation infrastructure

If not already present, declare:

- workload identity pool;
- provider;
- service-account impersonation bindings;
- claim restrictions.

Use OpenTofu.

Do not create this manually without declaring it unless required for the first
bootstrap; if bootstrapped manually, import/declare it appropriately afterward.

## 114. GitHub-to-GCP permissions

Verify actual effective permissions.

At minimum demonstrate:

- CI without deployment identity cannot modify GCP;
- staging deploy identity can perform required staging deployment;
- it cannot perform unrelated broad project administration;
- production identity/gate remains separate where implemented.

Do not grant Editor to solve permission errors.

## 115. Artifact publication permission

The build/publish identity needs only the relevant Artifact Registry
permissions plus authentication prerequisites.

It does not need Cloud SQL or runtime secret access.

## 116. Deployment permission

Application deployment identity SHOULD be scoped to updating:

- target Cloud Run services;
- target Jobs;
- reading required resource metadata;
- invoking init;
- required acceptance inspection.

It should not need:

- database passwords;
- object contents;
- secret payload access;
- project IAM administration.

Document unavoidable exceptions.

## 117. Acceptance permission

Acceptance jobs may need application authentication or limited GCP inspection.

Do not give acceptance the infrastructure-admin identity by default.

Separate identities may be used where practical.

## 118. Synthetic application identity

If acceptance requires an application user, use a controlled staging-only
mechanism.

Do not commit real user credentials.

Do not depend on production user data.

Clean up synthetic state where practical.

## 119. Fixture policy

Staging currently refuses the dev fixture loader.

Preserve that separation.

Do not enable broad fixture loading in staging merely to simplify CI.

Use targeted acceptance setup.

## 120. OpenTofu state access

Only infrastructure workflows that genuinely require state access should receive
it.

Build and ordinary application deploy workflows should not need direct state
write permission.

Reading outputs may use safer alternatives where practical.

## 121. Infrastructure outputs

If deployment workflows consume OpenTofu outputs, choose a safe mechanism.

Possible strategies include:

- environment/repository variables generated operationally;
- authenticated `tofu output`;
- GCP resource discovery;
- generated non-secret deployment metadata.

Do not publish sensitive state.

## 122. Environment manifest

Consider creating a non-secret environment/deployment manifest containing
resource identifiers required by the GCP deployment adapter.

If introduced, it SHALL contain no secret payloads.

It should reduce hardcoded resource-name duplication.

Do not create one if existing configuration already provides the same boundary
cleanly.

## 123. Production environment portability

Do not name common workflow inputs:

    care-prod-project-123
    care-prod-api

Use generic:

    environment
    project_id
    region
    api_service
    worker_service
    init_job

or equivalent environment configuration.

## 124. Build workflow portability

The build workflow should remain usable even if Artifact Registry publication is
later complemented by another OCI registry.

Do not make GCP project discovery part of the Docker build itself.

Publication is a downstream concern.

## 125. Registry abstraction level

Do not build a custom registry abstraction framework.

It is enough to keep:

    build artifact
        ->
    publish to configured OCI registry

as a conceptual boundary.

GCP Artifact Registry is the only required implementation in ES-08.

## 126. Release metadata format

Choose a simple machine-readable format if a file artifact is used.

JSON is acceptable.

Include only stable metadata.

Do not invent a database or service solely for release records.

## 127. Metadata integrity

Release metadata should be generated by the trusted workflow from actual values,
not manually typed where avoidable.

The image digest must come from the registry/build result.

Do not accept an arbitrary user-supplied digest as "staging accepted" without
verifying it was actually deployed and tested.

## 128. Promotion verification

Before production deployment, verify:

- digest exists;
- digest matches accepted staging record;
- source metadata resolves;
- operator explicitly selected production.

Failure of any required check must stop promotion.

## 129. Emergency promotion

Do not implement emergency bypass unless needed.

If no real production environment exists, defer bypass design.

Normal path remains accepted-digest promotion.

## 130. GitHub release integration

Optional.

A GitHub Release may reference:

- release tag;
- digest;
- upstream base;
- acceptance result.

Do not require GitHub Release creation for every staging build unless the
existing release process benefits from it.

## 131. Status checks

Document which CI jobs should eventually be required branch-protection checks.

Do not attempt to configure organization/repository branch protection through
GitHub APIs unless explicitly required and authorized.

Workflow implementation is enough for ES-08 unless repository settings are
already managed as code.

## 132. Workflow testing

Use tools or methods appropriate for validating workflow syntax and behavior.

At minimum:

- inspect YAML;
- validate action references;
- run workflows in the real GitHub repository for trusted acceptance where
  possible.

Local workflow simulators may supplement but not replace real GitHub Actions for
OIDC/deployment behavior.

## 133. Real CI acceptance

At least one real GitHub Actions run SHALL prove the common CI/build path where
repository permissions allow.

Do not consider workflow source alone sufficient.

Record run ID/URL where appropriate.

## 134. Real staging delivery acceptance

At least one real trusted workflow SHALL deploy an existing CI-built digest to
the real staging environment and execute staging acceptance.

If GitHub repository/environment administration prevents this, report the exact
external blocker and do not fake success locally.

## 135. Same-digest proof

Acceptance evidence SHALL show:

    CI build digest == staging deployed digest

and, if production is not deployed, production workflow configuration SHALL show
that it consumes a digest input rather than rebuilding.

If production is later deployed, require:

    staging accepted digest == production deployed digest

unless an explicit emergency bypass is invoked.

## 136. CI build versus local build

Do not use a locally pushed image as the final ES-08 artifact acceptance.

The point of ES-08 is to prove CI-produced artifact delivery.

Local builds may be used during development/debugging only.

## 137. OpenTofu CI acceptance

Run real GitHub Actions validation for OpenTofu.

Where safe/authorized, run a staging plan through GitHub Actions.

Do not apply infrastructure merely to prove the workflow unless an actual
infrastructure change exists.

## 138. Production workflow acceptance without production

If production does not exist:

- validate workflow syntax;
- validate required inputs;
- validate protected-environment configuration where accessible;
- prove it has no build step;
- prove it consumes an immutable digest;
- prove it calls the shared deployment logic;
- prove production is gated.

Do not create production infrastructure.

## 139. Staging drift after CI deployment

After staging application deployment, verify infrastructure has no unexplained
OpenTofu drift.

Changing runtime image through a separate application-delivery mechanism may
create apparent drift if OpenTofu owns the image field.

This must be resolved architecturally.

Do not accept permanent intentional drift.

Choose a mechanism where:

- OpenTofu intentionally ignores application image rollout fields; or
- deployment updates the source of truth consumed by OpenTofu; or
- another clean ownership boundary is established.

Document the decision.

## 140. Image ownership boundary

Explicitly decide whether application image references in Cloud Run/Jobs are
owned by:

    application delivery
or
    OpenTofu

They SHALL NOT be actively owned by both in conflicting ways.

Preferred:

- OpenTofu creates/configures infrastructure;
- application delivery owns image revision updates.

Implement the appropriate lifecycle/ignore mechanism only for the exact image
fields required.

Do not broadly ignore Cloud Run configuration drift.

## 141. Drift verification

After implementing the image ownership boundary:

1. deploy staging through CI/CD;
2. run OpenTofu plan;
3. expect no unexplained image rollback/update.

This is mandatory.

## 142. Infrastructure changes requiring image fields

OpenTofu must still be able to create a new greenfield service that needs an
initial image.

Document how the initial image is supplied during environment creation.

After creation, ordinary application delivery owns subsequent image promotion.

Do not create a chicken-and-egg ambiguity.

## 143. Initial image input

The environment root may accept an initial image digest/reference.

Greenfield creation uses it.

Ordinary releases must not require OpenTofu apply to update it.

Document this distinction.

## 144. Scheduled Job image ownership

Apply the same ownership rule to application Jobs.

Scheduler definitions stay infrastructure-owned.

Job application image revisions belong to application delivery.

## 145. Init Job image ownership

Init Job definition remains infrastructure-owned except for the application
image revision selected during deployment.

The deployment workflow must be able to update the init image without creating
OpenTofu drift.

## 146. Environment promotion record

Record staging deployment and acceptance before marking a digest promotable.

A digest may have multiple staging deployments.

Promotion status should refer to a successful acceptance run.

## 147. Failed staging digest

A digest that fails staging acceptance SHALL not be promoted by the normal
production workflow.

A later code fix produces a new digest.

Do not mark the failed digest accepted merely because infrastructure was
corrected around it unless acceptance is rerun successfully.

## 148. Environment-specific failure

If staging fails because the environment itself is broken rather than the
artifact, fix the environment and rerun acceptance against the same digest.

The accepted record should represent the successful rerun.

## 149. Application release versus infrastructure release

Document examples:

Application-only:

    code change
    -> CI
    -> image
    -> staging
    -> production

Infrastructure-only:

    OpenTofu change
    -> plan
    -> apply

Coordinated:

    OpenTofu adds resource
    -> apply
    -> new application digest
    -> deploy

This distinction should be clear to operators.

## 150. Deployment script reuse

If operational scripts already exist from ES-07, reuse/refactor them.

Do not replace proven scripts with YAML-only reimplementations merely because
GitHub Actions is being added.

Scripts should remain understandable outside CI.

## 151. Shell safety

New deployment scripts SHALL use appropriate shell safety:

    set -e
    set -u
    pipefail

where compatible.

Handle command failures explicitly where they carry semantic meaning.

Do not swallow failures through chained `|| true` except for truly optional
diagnostics.

## 152. Cross-platform scope

CI/deploy shell scripts may target Linux runners.

Do not require them to work on Windows unless they are also documented as local
developer tooling.

Application development remains cross-platform where currently supported.

## 153. GitHub Actions runner

Use GitHub-hosted Linux runners initially unless a requirement for self-hosted
runners exists.

Do not introduce permanently running CI VMs.

## 154. Runner trust

Privileged deployment jobs SHALL only execute repository-controlled code from a
trusted revision.

Do not checkout arbitrary PR head code into a job holding deployment
credentials.

This is especially important for reusable workflows and `workflow_run`.

## 155. Workflow chaining

If a staging deploy is triggered after CI completion, verify that the commit/digest
being deployed is the exact artifact produced by the successful trusted CI run.

Do not blindly deploy current branch HEAD if it has changed since the build.

## 156. Artifact handoff

Use workflow outputs, GitHub artifacts, release metadata or registry inspection
to hand off the digest.

Do not scrape mutable console text where a structured output can be used.

## 157. CI artifact retention

GitHub workflow artifacts used only for metadata may use modest retention.

The OCI image in the registry remains the authoritative deployable artifact.

Do not depend on short-lived GitHub artifacts as the only copy of the image.

## 158. Environment approval metadata

Production approval should occur after the operator can see the selected digest
and source commit.

Do not request approval before the workflow knows what artifact will be
deployed.

## 159. Approval and secret release

If GitHub Environments gate access to deployment variables/secrets, preserve
that trust boundary.

Do not pass protected production credentials through unprotected upstream jobs.

## 160. No runtime secret exposure

Deployment workflows should reference Secret Manager resource bindings, not
print secret values.

Do not use:

    gcloud secrets versions access

unless the workflow genuinely needs the plaintext.

Runtime services should retrieve secrets through Cloud Run secret references.

## 161. Runtime env verification

After deployment, verify required non-secret backend selections:

    CARE_STORAGE_BACKEND
    CARE_TASK_BACKEND
    CARE_CACHE_BACKEND
    CARE_RATE_LIMIT_BACKEND
    CARE_PROCESS_ROLE

without printing sensitive variables.

## 162. Public repository secret examples

Documentation should use placeholders:

    example-project
    example-region
    secret-name

Do not accidentally commit the staging synthetic user's credentials or other
acceptance secrets.

## 163. Workflow documentation

Document:

- what triggers each workflow;
- required GitHub variables;
- required GitHub environments;
- GCP identities;
- required approvals;
- inputs;
- outputs;
- manual invocation;
- failure recovery.

The repository should be understandable by another operator.

## 164. CI/CD setup documentation

Provide a setup guide for a new operator/repository fork.

It should explain:

1. run application locally;
2. bootstrap OpenTofu state if using GCP;
3. configure GCP Workload Identity Federation;
4. configure GitHub variables/environments;
5. provision required secret payloads securely;
6. run CI;
7. build/publish artifact;
8. deploy staging;
9. accept/promote artifact.

Do not require access to the original maintainer's GCP project.

## 165. GCP-specific variables

Document GCP deployment metadata required by the GCP adapter.

Do not make these global application configuration.

Examples may include:

    GCP_PROJECT_ID
    GCP_REGION
    ARTIFACT_REGISTRY_REPOSITORY
    API_SERVICE
    WORKER_SERVICE
    INIT_JOB

Use actual implemented names.

## 166. Generic build outputs

The common build workflow output should be generic where practical:

    image_ref
    image_digest
    source_sha

not:

    cloud_run_image

## 167. Infrastructure plan comments

Optional.

If adding PR plan summaries, do so safely.

Do not expose sensitive state or secrets.

Do not make comment-posting permissions necessary for basic CI.

## 168. Action pinning

For identity/auth/deployment actions, strongly prefer immutable action commit
pins.

Document the upstream action/version in comments or dependency tooling if useful.

Do not use unknown/unmaintained actions for privileged workflows.

## 169. Dependency updates

Do not implement a full Dependabot/Renovate strategy unless already present.

But ensure pinned actions/providers can be maintained.

Record follow-up if necessary.

## 170. Acceptance timeout

Set bounded timeouts for:

- init;
- Cloud Run readiness;
- task completion;
- acceptance Jobs.

Do not allow a stuck deployment to consume CI indefinitely.

Timeout values should reflect existing observed behavior.

## 171. Task acceptance

Cloud Tasks acceptance should use an existing harmless application path.

Do not create a production-only debug task.

Staging synthetic state is acceptable.

## 172. Scheduled acceptance

The workflow may manually execute a scheduled Job instead of waiting for its
cron.

The acceptance goal is to prove the Job artifact/permissions work.

Do not change production schedules for CI.

## 173. GCS acceptance

Use a bounded small test object.

Delete it afterward.

Verify round-trip bytes.

Do not expose bucket contents in logs.

## 174. Rate-limit acceptance

Use sequential requests only for PostgreSQL best-effort rate limiting.

Do not fail the deployment because it lacks Redis-like concurrency guarantees.

The semantic difference is intentional and documented.

## 175. Recent Views acceptance

Use staging-only synthetic data.

Verify persistence through different instances where practical.

Clean up afterward.

## 176. Lock acceptance

Use a safe PostgreSQL advisory-lock path.

Do not intentionally deadlock the staging database.

## 177. CI runtime cost

Keep ordinary pull-request CI reasonably bounded.

Do not run live GCP acceptance for every PR.

Expensive live tests belong to trusted release/staging workflows.

## 178. Staging standing cost

ES-08 does not need to change staging sizing.

The existing standing-cost finding remains operational.

Do not redesign Cloud SQL solely to reduce CI/CD implementation cost.

## 179. Production deployment optionality

A release can be built and accepted without a production deployment.

The repository SHALL support:

    release artifact created
    staging accepted
    no production promotion yet

This is a valid state.

## 180. External operator consumption

Document enough metadata for another operator to identify an artifact.

Do not promise that the initial private Artifact Registry is universally
accessible.

A future public/mirrored registry can address distribution.

## 181. Build from public fork

The build workflow should function in another fork after configuring its own
registry/authentication.

Do not depend on hardcoded `xiidigital` repository ownership beyond GitHub trust
configuration used by the maintainer deployment identities.

## 182. Workflow repository checks

Where deployment identity trust is tied to one GitHub repository, that is
expected for the maintainer's deployment.

The common CI/build design still remains reusable.

Document the distinction.

## 183. Production service naming

Do not implement or validate a production workflow using made-up production
service names if no production environment exists.

Use environment configuration inputs.

The workflow must fail clearly when required target metadata is absent.

## 184. Error messages

Deployment scripts/workflows should report actionable failures.

Examples:

    missing staging configuration
    digest not found
    init failed
    worker revision not ready
    worker unexpectedly public
    API digest mismatch
    staging acceptance failed

Avoid generic "command failed" where the semantic context is known.

## 185. Deployment summary

At the end of a successful staging deployment, emit a summary containing:

    source commit
    image digest
    init execution
    worker revision
    API revision
    acceptance status

At the end of production promotion, emit:

    same metadata
    staging acceptance reference
    approval environment

No secrets.

## 186. Release record storage

Prefer GitHub-native release/deployment metadata before introducing new storage.

A repository artifact committed back by CI is not required and may create noisy
automation commits.

Do not automatically commit deployment records to the source branch unless
there is a strong reason.

## 187. Git tags

If tags are created by the workflow, they must be intentional and protected.

Do not create a new tag for every staging deployment unless that is the desired
release policy.

## 188. Deployment auditability

GitHub Actions run history plus environment/deployment metadata should allow an
operator to answer:

- who initiated deployment;
- what digest was deployed;
- where;
- when;
- whether acceptance passed.

## 189. Manual deployment parity

The automated workflow should use the same underlying deployment semantics as
the documented manual path.

Do not maintain two divergent deployment implementations.

## 190. CLI tooling

Use standard supported tools where practical:

    docker/buildx
    gcloud
    tofu
    gh where appropriate

Do not introduce a custom deployment binary unless shell/workflow composition
becomes demonstrably inadequate.

## 191. GitHub CLI

Do not make `gh` mandatory inside workflows if native Actions APIs cover the
requirement.

It may be useful for operational/manual commands.

## 192. Artifact signing

Out of scope for mandatory ES-08 acceptance.

If easy and supported by the chosen registry/identity, it may be explored, but
do not block controlled delivery on signing.

Record it as future supply-chain hardening if not implemented.

## 193. SBOM

Generating an SBOM may be useful but is not mandatory unless existing tooling
already provides it easily.

Do not derail ES-08 into a full software supply-chain project.

## 194. Container vulnerability findings

If vulnerability scanning is enabled and reports findings, distinguish:

- critical exploitable runtime issue;
- base image/dependency advisory;
- informational issue.

Do not automatically suppress everything.

Do not redesign the application solely because a scanner produces a long generic
report.

## 195. Deployment approval absence

If GitHub plan/account does not support the desired environment approval
feature, implement the safest available explicit manual gate and document the
limitation.

Do not silently remove the production approval requirement.

## 196. External prerequisites

Clearly report repository/GCP settings that cannot be created from code due to
permissions or platform boundaries.

Examples:

- GitHub environment protection requiring repository admin;
- organization policy;
- external DNS;
- secret payloads.

Do not fake completion.

## 197. No asynchronous promises

All ES-08 verification must be completed during the implementation session or
reported as externally blocked.

Do not describe future workflow runs as if they have already passed.

## 198. Documentation updates

Update:

    ADR-0008 implementation checklist
    06-operations.md
    07-configuration-reference.md where relevant
    runtime-and-deployment.md
    gcp-configuration.md
    unresolved-items.md

Add CI/CD documentation covering:

    workflow architecture
    GitHub setup
    Workload Identity Federation
    build/publish
    staging deployment
    staging acceptance
    production promotion
    rollback
    infrastructure delivery
    greenfield bootstrap
    secret provisioning boundary
    deployment metadata
    manual recovery

Do not rewrite unrelated ADRs.

## 199. Commit strategy

Use focused commits.

Suggested sequence:

    ci: add pull request validation

    ci: build and publish immutable application images

    ci: validate opentofu infrastructure

    ci: add gcp workload identity federation

    ci: deploy immutable images to staging

    ci: run staging acceptance

    ci: add controlled production promotion

    ci: add infrastructure plan and apply workflows

    test(ci): verify delivery invariants

    docs(ci): complete controlled delivery architecture

Different grouping is acceptable if reviewability improves.

Do not create one giant workflow commit.

Do not squash.

Do not push.

## 200. Allowed modifications

May create/modify:

- `.github/workflows/`;
- `.github/actions/` if justified;
- CI/deployment scripts;
- `.dockerignore`;
- production build support;
- OpenTofu required for GitHub/GCP identity;
- deployment IAM;
- non-secret environment/deployment metadata;
- CI tests;
- infrastructure tests;
- documentation;
- ADR-0008 implementation checklist;
- application code only for proven CI/CD blockers.

## 201. Forbidden modifications

Do not:

- redesign storage;
- redesign file transport;
- redesign async runtime;
- redesign cache;
- redesign rate limiting;
- redesign Recent Views;
- redesign locking;
- reintroduce Redis as mandatory;
- select an email provider;
- commit credentials;
- deploy production unless explicitly instructed;
- create production infrastructure merely for testing;
- add Kubernetes/AWS/Azure delivery adapters;
- begin ES-09.

## 202. Acceptance criteria

ES-08 is complete only when:

- work occurs on `feature/ci-controlled-delivery`;
- existing CI/CD is inventoried;
- PR CI runs without privileged deployment credentials;
- application regression is gated;
- OpenTofu fmt/validate is gated;
- production build context excludes unrelated local files;
- P2 is closed;
- production image builds without runtime infrastructure/secrets;
- static assets are present in CI-built image;
- compiled messages are present;
- image is published by trusted CI;
- immutable image digest is captured;
- source commit -> image digest provenance is recorded;
- upstream base commit is recorded;
- staging deploy consumes an existing digest;
- staging deploy does not rebuild;
- init runs explicitly and blocks on failure;
- worker deploys before API;
- deployed worker/API/Jobs match requested digest;
- worker IAM remains private;
- staging acceptance runs against the real environment;
- staging acceptance verifies Redis-free behavior;
- staging accepted digest is recorded;
- normal production promotion accepts a digest rather than source;
- production workflow contains no image build;
- production promotion requires explicit trusted approval;
- production is not deployed unless explicitly requested;
- rollback identifies a previous immutable digest;
- application rollback does not imply database rollback;
- infrastructure validation is separate from application delivery;
- production infrastructure apply is explicitly gated;
- image fields have one clear ownership boundary;
- application image rollout does not produce unexplained OpenTofu drift;
- greenfield bootstrap/secret sequence is documented;
- secret payloads are not committed or written into OpenTofu state by the normal
  workflow;
- GCP authentication uses short-lived identity rather than service-account JSON
  keys;
- workflow permissions are least privilege where practical;
- fork/untrusted PR execution cannot access deployment credentials;
- deployment concurrency is controlled per environment;
- release/deployment metadata is recorded;
- repository contains no hardcoded production deployment instance in the common
  build path;
- another operator can understand how to consume the OCI artifact independently;
- staging remains healthy after CI/CD rollout;
- ADR-0008 checklist is updated;
- ES-09 has not started.

## 203. Final report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. existing CI/CD inventory;
8. workflow architecture;
9. PR CI triggers;
10. trusted build trigger;
11. staging deploy trigger;
12. production promotion trigger;
13. infrastructure workflow triggers;
14. GitHub Actions runner;
15. OpenTofu version;
16. GCP authentication mechanism;
17. Workload Identity Federation resources;
18. GitHub trust restrictions;
19. automation service accounts;
20. workflow permissions;
21. `.dockerignore` / P2 resolution;
22. controlled build context proof;
23. CI production image build result;
24. static-assets verification;
25. compilemessages verification;
26. image repository;
27. image digest;
28. source commit;
29. upstream base commit;
30. provenance record;
31. staging deployment input digest;
32. proof staging did not rebuild;
33. init execution result;
34. worker deployment result;
35. API deployment result;
36. Job image update result;
37. deployed-digest verification;
38. worker IAM verification;
39. staging acceptance result;
40. Redis-free staging verification;
41. GCS acceptance result;
42. Cloud Tasks acceptance result;
43. PostgreSQL cache result;
44. PostgreSQL rate-limit result;
45. Recent Views result;
46. locking result;
47. Scheduler result;
48. console-email result;
49. acceptance cleanup result;
50. accepted-digest record;
51. OpenTofu image ownership decision;
52. post-deployment OpenTofu drift result;
53. production workflow implementation;
54. production approval mechanism;
55. proof production workflow does not rebuild;
56. production deployment performed? yes/no;
57. rollback mechanism/documentation;
58. previous-digest identification;
59. infrastructure plan workflow;
60. infrastructure apply gate;
61. GitHub/GCP secret boundary;
62. greenfield bootstrap/N4 documentation;
63. secret provisioning workflow;
64. secret scanning result;
65. fork/untrusted PR safety;
66. deployment concurrency behavior;
67. cancellation semantics;
68. release metadata;
69. workflow summaries/auditability;
70. manual deployment parity;
71. local compatibility if affected;
72. application test results if application code changed;
73. OpenTofu validation results;
74. real GitHub Actions run evidence;
75. real staging CI/CD deployment evidence;
76. remaining CI/CD findings;
77. remaining production prerequisites;
78. deviations from ADR-0008 / ES-08;
79. final verdict:

    READY FOR PRODUCTION OPERATIONS

or:

    NOT READY

Stop after ES-08.

Do not begin ES-09.

---

## 204. Real GitHub Actions evidence (2026-08-20)

Recorded after the branch was published and the workflows were executed on
GitHub. This section states what actually ran, and what did not.

### 204.1 Repository configuration

| | |
|---|---|
| remote | `https://github.com/xiidigital/care.git` |
| repository | `xiidigital/care`, public, `viewerPermission: ADMIN` |
| default branch | `develop` |
| branch published | `feature/ci-controlled-delivery` |

GitHub Environments, all five present, names matching the workflows exactly:

| environment | protection |
|---|---|
| `artifact-publication` | none — gates a credential, not a human |
| `staging` | none |
| `infrastructure-plan` | none |
| `infrastructure-apply` | required reviewer |
| `production` | required reviewer |

Repository variables (five, non-secret identifiers only) and **zero repository
secrets**: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`ARTIFACT_REGISTRY_REPOSITORY`, `IMAGE_NAME`. No runtime secret payload is held
by GitHub; application secrets remain in Secret Manager.

### 204.2 What ran

`CARE CI` is green on a GitHub-hosted `ubuntu-24.04` runner:

| run | commit | result |
|---|---|---|
| [32301845452](https://github.com/xiidigital/care/actions/runs/32301845452) | `60b7bf02` | failure |
| [32303955622](https://github.com/xiidigital/care/actions/runs/32303955622) | `03958953` | failure |
| [32305734815](https://github.com/xiidigital/care/actions/runs/32305734815) | `9f280a9a` | failure — production image, 6/6 startup checks red |
| [32340865564](https://github.com/xiidigital/care/actions/runs/32340865564) | `efa1d552` | **success — 6/6 jobs** |

The green run covers sections 133's CI half: workflow syntax resolves, the job
graph runs, `contents: read` holds at workflow level with no id-token on any
untrusted-PR job, the pinned gitleaks gate passes on both the tree and the
introduced commits, and the production image builds, is inventoried, excludes a
planted build context, and **starts and serves** — init exits zero, api and
task_worker both answer `/ping/`, route isolation holds in both directions, and
the api reports the Redis-free path.

The defects that made the earlier runs red, and their guards, are D11.

### 204.3 What did not run, and why

No image has been published by GitHub Actions. `build-image.yml`,
`deploy-staging.yml`, `promote-production.yml`, `rollback.yml`,
`infra-apply.yml` and the plan half of `infra-check.yml` are reachable only by
`workflow_dispatch` or by a push to `gcp`, and GitHub registers a
`workflow_dispatch` only for a workflow present on the **default** branch —
`develop`, which carries none of them. See D10 for the exact API responses and
the two ways to close it.

Consequently sections 134 and everything downstream remain unproven: no
CI-built digest, no OIDC exchange, no staging deployment, no same-digest proof,
no CI-run acceptance, no accepted-digest record, no post-deployment drift check.
None of these were weakened or worked around to produce a result.

### 204.4 Verdict

**NOT READY.** Section 133 is satisfied for the CI/build validation path.
Section 134 is not, and the blocker is repository configuration (D10), not
workflow correctness.

---

## 205. Superseding closeout evidence (2026-08-30)

Section 204 is retained as the contemporaneous report from the first CI run. It
is no longer the current verdict.

The later ES-08 chain completed through GitHub Actions:

| stage | run | result |
|---|---|---|
| build and publish | `32406259063` | success |
| staging deployment and acceptance | `32411111409` | success |
| promotion eligibility | `32412389243` | eligible; held at production gate |

D1, D2, D10, D11 and D13 record the corresponding evidence. Production was
later provisioned and smoke-tested manually; that work belongs to ADR-0009 and
ES-09 and does not retroactively count as a GitHub production deployment.

**ES-08 verdict: IMPLEMENTED.** Automated production deployment remains
unexercised because the protected environment's production variables have not
been configured. Clinical activation is explicitly outside ES-08.

## 206. ES-08 closure (2026-09-02)

The one blocker that survived section 205 was D12: `care-infra` could
authenticate through WIF but held no project roles, so an infrastructure plan in
CI failed on 403 before reading any resource. That is now done.

| check | evidence | result |
|---|---|---|
| bootstrap state reconciles with reality | `tofu plan -detailed-exitcode` | exit 0, `No changes` |
| IAM activation is additive only | plan with `grant_infrastructure_roles=true` | 12 to add, 0 to change, 0 to destroy |
| roles granted, scope | live project IAM policy | 12 enumerated roles; no Owner, no Editor |
| OpenTofu plans through WIF as `care-infra` | run [33585439698](https://github.com/xiidigital/care/actions/runs/33585439698) | success |
| staging infrastructure drift | staging plan with the real image | exit 0, `No changes` |
| production deployed | — | no; `Apply staging` skipped, production untouched |
| Redis required by the GCP runtime | section 89 acceptance | no; unchanged |

No resource was created, replaced or destroyed to reach this state. The state
was never lost: it existed locally and reconciled exactly, so nothing was
imported and nothing was reconstructed.

The bootstrap state now lives in
`gs://care-tfstate-project-990c4414-a33c-47f2-9f4/bootstrap/`, one object per
workspace, so the single operator apply this root still needs no longer depends
on one workstation's disk.

**ES-08 verdict: CLOSED.**

Two things stay open and neither belongs to ES-08: automated production
promotion needs the protected environment's variables (ES-09 section 5), and
clinical activation is an operator milestone under ADR-0009.
