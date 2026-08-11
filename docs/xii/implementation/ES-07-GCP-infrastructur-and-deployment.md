# ES-07: GCP Infrastructure and Deployment

- **Status:** Draft
- **Related ADR:** ADR-0007: Terraform for GCP Infrastructure as Code
- **Depends on:** completed ES-01 through ES-06, RF1 and RF2
- **Target branch:** `feature/gcp-infrastructure`
- **Managed target:** Google Cloud Platform
- **Primary composition:** Redis-free

## 1. Context

ADR-0007 establishes Terraform as the infrastructure-as-code mechanism for the
initial managed GCP deployment.

Previous engineering phases already completed the application-side architecture
required for a managed, portable deployment.

The current application supports:

- provider-neutral object storage;
- server-mediated file transport;
- configurable asynchronous execution;
- configurable cache backends;
- PostgreSQL distributed locking;
- explicit runtime roles;
- PostgreSQL-backed Recent Views;
- Redis or PostgreSQL best-effort rate limiting.

The primary managed GCP composition is intentionally Redis-free.

Conceptually:

    CARE_PROCESS_ROLE
        api | task_worker | init

    CARE_STORAGE_BACKEND=gcs

    CARE_TASK_BACKEND=cloud_tasks

    CARE_CACHE_BACKEND=postgres

    CARE_RATE_LIMIT_BACKEND=postgres

The corresponding managed infrastructure is:

    Artifact Registry
            |
            v
    immutable CARE image
            |
            +-- Cloud Run API
            |
            +-- private Cloud Run task worker
            |
            +-- Cloud Run init Job

    Cloud SQL PostgreSQL
    Cloud Storage
    Cloud Tasks
    Cloud Scheduler
    Secret Manager
    Cloud Logging / Monitoring

Redis SHALL NOT be provisioned for the primary GCP composition.

Redis compatibility remains an application capability and is not removed by
this specification.

## 2. Objective

Implement the GCP infrastructure required to deploy CARE using Terraform.

At completion, a greenfield development environment SHALL be reproducibly
deployable with:

- Cloud Run API;
- private Cloud Run task worker;
- Cloud Run initialization Job;
- Cloud SQL PostgreSQL;
- Cloud Storage;
- Cloud Tasks;
- Cloud Scheduler;
- Secret Manager;
- Artifact Registry;
- least-privilege service accounts;
- remote Terraform state;
- role-aware runtime configuration;
- no mandatory Redis infrastructure.

This phase SHALL prove the application architecture works in a real GCP
environment.

This phase is infrastructure implementation.

Do not redesign application architecture unless an actual deployment blocker is
demonstrated.

## 3. Branch and repository preconditions

Before modifying anything:

1. switch to `gcp`;
2. verify it contains all merged work through RF2;
3. verify the tracked worktree is clean;
4. create:

    feature/gcp-infrastructure

5. perform all ES-07 work on that branch.

Report before modification:

    git status
    git branch --show-current
    git log -10 --oneline
    git remote -v

Do not work directly on `gcp`.

Do not push unless explicitly instructed.

Do not squash.

Do not begin ES-08.

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

    docs/xii/implementation/ES-01-*.md
    docs/xii/implementation/ES-02-*.md
    docs/xii/implementation/ES-03-*.md
    docs/xii/implementation/ES-04-*.md
    docs/xii/implementation/ES-05-*.md
    docs/xii/implementation/ES-06-*.md

    docs/xii/architecture/inventory/runtime-and-deployment.md
    docs/xii/architecture/inventory/cache-and-redis.md
    docs/xii/architecture/inventory/task-call-sites.md
    docs/xii/architecture/inventory/plugin-impact.md
    docs/xii/architecture/inventory/unresolved-items.md

If actual committed paths differ, locate and use the real files.

The current code and configuration reference remain authoritative for exact
application environment-variable names.

## 5. Mandatory inventory before Terraform

Before writing infrastructure code, re-verify the application configuration
required for the managed GCP composition.

Produce a table containing:

- setting/environment variable;
- required by API?;
- required by task worker?;
- required by init?;
- secret?;
- infrastructure-derived?;
- application default?;
- Terraform source;
- Secret Manager source;
- manually supplied operational value.

At minimum inspect:

    CARE_PROCESS_ROLE
    CARE_STORAGE_BACKEND
    CARE_TASK_BACKEND
    CARE_CACHE_BACKEND
    CARE_RATE_LIMIT_BACKEND

    DATABASE_URL

    GCP project setting
    GCS project setting
    GCS storage bucket settings

    Cloud Tasks location
    Cloud Tasks queue
    worker URL
    Cloud Tasks service-account identity
    OIDC audience

    Django SECRET_KEY
    allowed hosts / origins
    frontend URLs
    email/SMS configuration
    application encryption/authentication secrets

Do not invent names where the application already defines them.

## 6. Infrastructure directory

Create a clear Terraform root.

Preferred layout:

    infrastructure/
      terraform/
        bootstrap/
        modules/
        environments/
          dev/
          staging/
          prod/

or the closest equivalent consistent with the repository.

Do not create deep module hierarchies.

Modules SHALL exist only for meaningful reusable infrastructure boundaries.

Environment configuration must remain easy to review.

## 7. Terraform and provider versions

Pin a minimum Terraform version.

Constrain the Google provider version.

Use committed provider lock files where appropriate.

Do not use unconstrained:

    version = "*"

or equivalent.

Record versions in the final report.

## 8. GCP project input

Terraform SHALL operate against an explicitly supplied GCP project.

Do not hardcode a personal or development project ID.

Use an input such as:

    project_id

Also define:

    region
    environment

and other infrastructure-level inputs as needed.

Default region may be provided only if already decided and documented.

Environment naming must support:

    dev
    staging
    prod

## 9. Resource naming

Use deterministic names derived from:

    application
    environment
    resource purpose

Conceptually:

    care-dev-api
    care-dev-worker
    care-dev-init
    care-dev-tasks
    care-dev-db

Do not encode mutable values such as image digests into resource names.

Keep names within GCP service constraints.

## 10. Required APIs

Terraform SHALL enable the APIs required by the selected resources.

At minimum evaluate:

    run.googleapis.com
    sqladmin.googleapis.com
    storage.googleapis.com
    cloudtasks.googleapis.com
    cloudscheduler.googleapis.com
    secretmanager.googleapis.com
    artifactregistry.googleapis.com
    iam.googleapis.com
    iamcredentials.googleapis.com
    serviceusage.googleapis.com
    logging.googleapis.com
    monitoring.googleapis.com

Add others only when actually required.

Do not enable broad unrelated API sets.

Terraform must model resource dependencies on API activation where necessary.

## 11. Remote Terraform state

Terraform state SHALL be remote for managed environments.

Use a dedicated GCS state bucket or an equivalent approved GCP state backend.

State SHALL be separated per environment.

State storage SHALL have:

- versioning;
- restricted IAM;
- uniform bucket-level access;
- public access prevention;
- appropriate lifecycle/recovery policy.

Runtime service accounts SHALL not receive state access.

## 12. Bootstrap

The state bucket cannot be managed by Terraform using itself before the backend
exists.

Create a minimal bootstrap Terraform root or explicitly documented bootstrap
procedure.

Preferred:

    infrastructure/terraform/bootstrap/

Bootstrap may create only infrastructure required for Terraform operation, such
as:

- state bucket;
- minimal IAM for Terraform operator/deployer where appropriate.

Do not hide bootstrap behind undocumented manual console steps.

Document:

    terraform init
    terraform plan
    terraform apply

for bootstrap.

## 13. Environment state separation

Use explicit backend state separation.

Acceptable approaches include:

- separate state prefixes;
- separate buckets;
- separate Terraform roots.

The implementation must make accidental cross-environment apply difficult.

Do not rely only on Terraform workspaces unless there is a clear reason and the
result remains obvious to operators.

## 14. Artifact Registry

Provision an Artifact Registry Docker repository.

The repository SHALL store the CARE application image.

Terraform does not need to build the image.

Image building/pushing remains an operational or CI responsibility.

Expose the repository path as a non-sensitive Terraform output.

The API, worker and init Job SHALL accept an image reference as configuration.

Prefer immutable references for deployments.

## 15. Application image

ES-07 SHALL verify that one CARE image can serve:

    api
    task_worker
    init

and, where applicable, traditional scheduler roles.

Terraform SHALL not require separate API and worker images.

Environment variables and commands distinguish roles.

## 16. Service accounts

Create explicit service accounts for runtime responsibilities.

At minimum evaluate distinct identities for:

    API runtime
    task worker runtime
    init Job
    Cloud Tasks invoker
    Cloud Scheduler invoker
    Terraform/deployment automation

Do not combine identities solely to reduce resource count when it broadens
privileges materially.

Document any deliberate identity sharing.

## 17. API service account

The API runtime identity SHALL receive only permissions required for API
execution.

Expected categories include:

- Cloud SQL connectivity;
- required Secret Manager secret access;
- required GCS bucket access;
- Cloud Tasks enqueue permission.

Do not grant:

    Owner
    Editor

or other broad project roles.

Use resource-specific IAM where practical.

## 18. Worker service account

The worker runtime identity SHALL receive permissions required by registered task
handlers.

Expected categories may include:

- Cloud SQL connectivity;
- required GCS access;
- Secret Manager access.

It does not need permission merely to invoke itself.

Do not reuse the Cloud Tasks invoker identity automatically.

## 19. Cloud Tasks invoker identity

Create or designate a dedicated identity for Cloud Tasks OIDC invocation.

It SHALL receive:

    roles/run.invoker

on the task-worker Cloud Run service only.

Do not grant it invoker permission on the public API unless a verified use case
requires it.

Do not grant broad project permissions.

## 20. Init service account

The init Job identity SHALL receive the permissions required for:

- Cloud SQL;
- storage if initialization actually requires it;
- relevant secrets.

It SHALL NOT receive Cloud Run administration permissions merely because it runs
as a Job.

It SHALL NOT require Redis.

## 21. Scheduler identity

Cloud Scheduler SHALL use a dedicated or narrowly scoped identity when invoking
authenticated endpoints or Jobs.

The exact permission depends on the target mechanism.

Do not reuse a broad deployment identity.

## 22. Secret Manager

Terraform SHALL create secret resources for application secrets that are
appropriate to manage as named infrastructure.

Secret values SHALL NOT be committed in:

    .tf
    .tfvars
    repository files
    Dockerfiles
    source code

Terraform SHOULD manage:

- secret container/resource;
- IAM access.

Secret values SHOULD be injected through an external operational step or secure
CI/CD process.

Do not put secret payload values into Terraform state unless explicitly
necessary and accepted.

## 23. Secret classification

Inventory all current application secrets.

Classify each as:

    required by api
    required by worker
    required by init
    shared
    environment-specific
    optional integration

Do not grant every runtime identity access to every secret.

## 24. Cloud SQL engine

Provision Cloud SQL for PostgreSQL.

Use a PostgreSQL major version supported by CARE and verified by current local
tests.

Do not arbitrarily select a newer major version merely because it is available.

Record the chosen version.

## 25. Cloud SQL instance sizing

Development sizing SHALL be deliberately low-cost.

Do not copy production-sized examples.

Expose sizing as environment configuration.

At minimum make configurable:

    database_version
    tier
    disk_size
    disk_autoresize
    availability_type
    backup settings

Production values SHALL not be guessed without measurements.

## 26. Cloud SQL high availability

Dev does not need HA unless required for testing.

Production SHOULD support HA configuration.

Make environment differences explicit.

Do not enforce production HA on dev if it materially raises cost.

## 27. Cloud SQL deletion protection

Production SHALL enable Cloud SQL deletion protection.

Dev may allow destruction when explicitly configured.

Terraform resource lifecycle behavior and Cloud SQL deletion protection must be
consistent.

Test or inspect destructive plan behavior.

## 28. Cloud SQL backups

Configure automated backups appropriately.

At minimum:

- dev: reasonable low-cost baseline;
- prod: explicit backup policy.

Do not leave production backup behavior accidental.

## 29. Database creation

Terraform MAY create:

- application database;
- infrastructure-level database user where required.

Terraform SHALL NOT create Django tables.

Do not embed schema SQL.

## 30. Database credentials

Prefer a secure GCP-supported connection mechanism.

Evaluate:

- Cloud SQL connector/socket;
- IAM DB authentication;
- username/password through Secret Manager.

Use the simplest secure mechanism supported by current CARE/Django configuration
without redesigning database access.

Do not add a repository abstraction or custom database client.

## 31. Cloud SQL connectivity

Choose a concrete Cloud Run-to-Cloud SQL connectivity mechanism.

Prefer native Cloud Run Cloud SQL integration where sufficient.

Do not introduce Serverless VPC Access merely because it is common in older
examples.

A VPC connector SHALL be added only if required by an actual dependency.

Document the final connectivity path.

## 32. DATABASE_URL

Construct the application database configuration using the existing CARE
configuration contract.

Do not modify Django database architecture merely to satisfy Terraform.

If runtime secret composition requires assembling DATABASE_URL outside Terraform
to avoid state exposure, document the operational process.

## 33. PostgreSQL cache

The Redis-free profile uses:

    CARE_CACHE_BACKEND=postgres

Initialization SHALL create the configured database cache table through:

    createcachetable

Terraform SHALL not create that table.

Verify the resulting table exists after init.

## 34. PostgreSQL rate limiting

The Redis-free profile uses:

    CARE_RATE_LIMIT_BACKEND=postgres

Terraform SHALL configure the environment required for the existing PostgreSQL
best-effort rate-limit backend.

Terraform SHALL not create:

    care_ratelimit_cache

directly.

The init Job SHALL create it through Django's existing initialization path.

Verify both cache tables remain distinct.

## 35. Recent Views

Recent Views are PostgreSQL-backed after RF1.

Terraform SHALL not create their table.

The normal Django migration executed by init SHALL create it.

Verify the table exists after initialization.

## 36. PostgreSQL advisory locks

Core locking uses PostgreSQL advisory locks.

No Redis locking infrastructure SHALL be provisioned.

No Terraform resource is required specifically for advisory locks.

Do not introduce an external lock service.

## 37. Cloud Storage buckets

Provision the buckets required by the GCS storage profile.

Determine the actual logical aliases from the application configuration.

At minimum review:

    patient
    facility
    report

If aliases intentionally share physical buckets, preserve that design.

Do not create arbitrary one-bucket-per-alias resources without verifying current
configuration.

## 38. GCS bucket security

Buckets SHALL:

- use uniform bucket-level access;
- prevent public access;
- avoid public object ACL assumptions.

Do not grant:

    allUsers

or:

    allAuthenticatedUsers

storage access.

Normal file transport goes through CARE.

## 39. GCS object permissions

Grant API and worker identities the minimal object-level permissions needed by
their operations.

Do not grant bucket administration permissions where object access is enough.

The init Job should not receive storage access unless initialization requires it.

## 40. GCS lifecycle

Do not add deletion lifecycle rules unless a concrete application requirement
exists.

Temporary or incomplete upload cleanup remains application-owned unless
explicitly moved by another ADR.

Production retention behavior must be documented.

## 41. GCS location

Bucket location SHALL be compatible with the selected deployment and data
requirements.

Prefer region alignment with Cloud Run/Cloud SQL where appropriate.

Expose location as configuration if different environments require it.

Do not silently create multi-region buckets that materially increase cost or
change residency characteristics.

## 42. Cloud Run API service

Provision a Cloud Run service for the API.

Configure:

    CARE_PROCESS_ROLE=api

Use the same application image used by other roles.

Configure:

- port;
- CPU;
- memory;
- concurrency;
- request timeout;
- min instances;
- max instances.

Use conservative, low-cost dev defaults.

Do not guess production capacity.

## 43. API ingress

The API is intended to serve frontend and client traffic.

Configure Cloud Run ingress consistently with the deployment design.

If public internet access is required, make that explicit.

Cloud Run unauthenticated invocation and CARE application authentication are
separate concepts.

Do not accidentally make the worker public because the API is public.

## 44. API minimum instances

Dev SHOULD default to:

    min_instances = 0

unless a concrete blocker exists.

Production may configure a higher minimum.

Keep this environment-specific.

## 45. API maximum instances

Configure an explicit maximum instance count to protect Cloud SQL and budget.

Do not leave unbounded scaling by accident.

Dev should use a conservative maximum.

Production values require later measurement.

## 46. API concurrency

Set Cloud Run concurrency deliberately.

Do not blindly use very high concurrency.

Consider:

- Django worker count;
- database connections;
- file streaming;
- Cloud SQL connection limits.

Expose as environment configuration.

## 47. API startup

API startup SHALL use the existing API role entrypoint.

Do not run:

    migrate
    createcachetable
    sync_permissions_roles
    sync_valueset

during Cloud Run instance startup.

Verify this in the deployed revision.

## 48. Cloud Run task-worker service

Provision a second Cloud Run service for task execution.

Configure:

    CARE_PROCESS_ROLE=task_worker

Use the same image.

Use the worker HTTP entrypoint implemented by ES-06.

## 49. Worker ingress and IAM

The worker SHALL NOT permit anonymous public invocation.

This is mandatory.

Terraform SHALL ensure:

- `allUsers` does not have `roles/run.invoker`;
- `allAuthenticatedUsers` does not have `roles/run.invoker`;
- the Cloud Tasks invoker identity does have the required permission.

Do not rely on obscurity, random URLs or application secrets.

## 50. Worker min/max instances

Worker dev defaults SHOULD support scale-to-zero:

    min_instances = 0

Set an explicit maximum to protect downstream services.

Expose environment-specific limits.

## 51. Worker concurrency

Choose worker concurrency based on task semantics and database limits.

Do not simply mirror API concurrency without analysis.

A low conservative dev value is acceptable.

## 52. Worker timeout

Cloud Run request timeout must support the longest currently valid task execution
within Cloud Run limits.

Inspect actual task behavior.

Do not set maximum possible timeout without reason.

Document the chosen value.

## 53. Worker route verification

After deployment verify:

- `/internal/tasks/execute/` exists on worker;
- representative public API routes do not;
- task route does not exist on API.

Do not infer from local tests only.

## 54. Cloud Tasks queue

Provision the queue used by the current Cloud Tasks backend.

Match the application configuration:

    project
    location
    queue name

Do not hardcode values separately in application config and Terraform when
outputs can be wired directly.

## 55. Cloud Tasks retry policy

Configure explicit retry policy.

Review current task semantics and maintenance behavior.

At minimum configure:

- max attempts;
- min backoff;
- max backoff;
- max doublings where relevant;
- max retry duration if appropriate.

Do not create infinite retries accidentally.

## 56. Cloud Tasks rate limits

Configure:

- max dispatches per second;
- max concurrent dispatches.

Use low, safe dev defaults.

Values must account for:

- worker max instances;
- worker concurrency;
- Cloud SQL capacity.

Do not optimize prematurely.

## 57. Cloud Tasks OIDC

Cloud Tasks SHALL attach an OIDC token generated for the dedicated invoker
service account.

Configure:

- service-account email;
- audience.

Audience must match the deployed worker configuration.

Do not use downloaded JSON credentials.

## 58. Cloud Tasks end-to-end verification

After deployment enqueue at least one harmless registered task through the CARE
dispatcher or a dedicated verified test path.

Prove:

    application enqueue
        ->
    Cloud Tasks queue
        ->
    OIDC
        ->
    private worker
        ->
    registered handler
        ->
    successful completion

Do not rely only on queue creation.

## 59. Negative worker IAM verification

Perform an unauthenticated request to the worker.

Expected result:

    rejected by Cloud Run IAM

The request should not reach the Django task view.

Also verify an unrelated authenticated identity without `roles/run.invoker`
cannot invoke the worker where practical.

Record evidence.

## 60. Init Cloud Run Job

Provision a Cloud Run Job using the same CARE image.

Configure:

    CARE_PROCESS_ROLE=init

Run the existing initialization command/entrypoint.

Do not duplicate initialization commands in Terraform.

## 61. Init resources

Configure CPU/memory/timeouts appropriate for:

- migrations;
- compilemessages;
- sync commands.

Use conservative dev values.

The Job terminates when done.

No min instances exist.

## 62. Init dependencies

The init Job SHALL have:

- Cloud SQL access;
- required secrets;
- only other permissions proven necessary.

It SHALL NOT require Redis in the primary composition.

## 63. Init execution verification

Execute the Job after infrastructure and application image are ready.

Verify all five initialization operations complete:

    migrate
    createcachetable
    compilemessages
    sync_permissions_roles
    sync_valueset

Record Job execution result.

## 64. Init failure verification

Where practical in dev, prove a failing initialization command causes the Job to
fail rather than report success.

Do not damage shared environments solely for this test.

A safe controlled failure path is sufficient.

## 65. Cloud Scheduler inventory

Re-verify current periodic jobs before creating scheduler resources.

Use the ES-03 inventory and current source.

Map every Beat schedule to:

- management command;
- async dispatch;
- direct authenticated HTTP operation;
- Cloud Run Job execution.

Do not invent scheduler targets.

## 66. Cloud Scheduler implementation

Replace managed-profile Celery Beat with Cloud Scheduler resources.

Each scheduled job SHALL invoke an explicit application operation.

Prefer:

- Cloud Run Job execution for bounded management/batch commands;
- authenticated HTTP trigger where that matches the current architecture.

Do not keep a permanently running Beat container in GCP.

## 67. Scheduler authentication

Use managed identities.

Do not use static query-string secrets.

Grant only the permission needed to invoke the chosen target.

## 68. Scheduler semantics

Preserve current schedule timing and behavior unless a documented correction is
required.

Record:

- old Beat schedule;
- new Cloud Scheduler cron;
- timezone;
- target;
- retry behavior.

Do not silently change timezone semantics.

## 69. Scheduler dev behavior

It is acceptable to disable or reduce automatic schedules in dev when they could
delete test data or cause unwanted cost.

Environment-specific schedule enablement must be explicit.

## 70. Secret injection

Configure Cloud Run services and Jobs to consume required secrets from Secret
Manager or the approved runtime mechanism.

Do not expose secret contents in Terraform outputs.

Do not place secret payloads in environment-specific committed `.tfvars`.

## 71. Non-secret environment variables

Terraform MAY configure non-sensitive environment variables directly.

Examples include:

    CARE_PROCESS_ROLE
    CARE_STORAGE_BACKEND
    CARE_TASK_BACKEND
    CARE_CACHE_BACKEND
    CARE_RATE_LIMIT_BACKEND
    region
    queue names
    bucket names

Keep environment-specific values reviewable.

## 72. Redis-free primary environment

The dev managed environment SHALL be deployed without Redis.

Do not provision:

- Memorystore;
- Redis VM;
- Upstash;
- another Redis-compatible service.

Use:

    CARE_CACHE_BACKEND=postgres
    CARE_RATE_LIMIT_BACKEND=postgres
    CARE_TASK_BACKEND=cloud_tasks
    CARE_STORAGE_BACKEND=gcs

If deployment fails because an undiscovered Redis dependency remains, stop and
report the exact call path.

Do not silently add Redis.

## 73. Redis compatibility

Do not remove application Redis support.

Terraform MAY later expose optional Redis resources.

ES-07 does not need to provision Redis in dev merely to prove compatibility.

Local/traditional regression already covers Redis-enabled application behavior.

If an optional Redis module is introduced, it must remain disabled by default
and justified by actual reuse.

## 74. Cloud Run health

Configure platform health behavior consistently with ES-06.

Do not probe routes unavailable for a role.

Verify:

API:
    /ping/ or approved role endpoint

Worker:
    worker-compatible diagnostic endpoint

Do not assume the worker exposes the public API.

## 75. Startup probe

If Cloud Run startup probes are used, configure them against role-appropriate
paths.

Do not add probes only because Terraform supports them.

Use them where they materially improve rollout correctness.

## 76. Liveness

Cloud Run manages container lifecycle.

Application liveness checks must remain lightweight.

Do not perform expensive database/storage checks on every liveness probe.

## 77. Readiness/dependency diagnostics

Use existing application health diagnostics appropriately.

Do not make a public liveness endpoint depend on optional infrastructure that is
not selected.

## 78. Cloud Logging

Use stdout/stderr integration.

Do not install an extra logging agent inside the container.

Record service/resource labels through the platform.

Do not modify application logging architecture unless a deployment blocker
exists.

## 79. Logging defect L2

If the known Django logging issue from ES-06 still exists, verify its actual
effect in Cloud Run.

Do not silently redesign logging inside Terraform.

If it prevents essential operational visibility, record it as a separate
application follow-up.

## 80. Monitoring

Add minimal useful monitoring foundations.

At minimum evaluate:

- Cloud Run request errors;
- Cloud Run worker errors;
- Cloud Run Job failures;
- Cloud Tasks queue failures/retries;
- Cloud SQL availability;
- scheduler failures.

Do not create a large speculative monitoring suite.

## 81. Alerts

For dev, alerts may be minimal or disabled.

For production-ready configuration, expose variables or modules for meaningful
alerts.

Do not page operators for expected scale-to-zero behavior.

## 82. Budget awareness

Document cost-driving resources.

At minimum:

- Cloud SQL;
- Cloud Run min instances;
- Cloud Run CPU/memory;
- Cloud Tasks volume;
- storage;
- logging volume.

Primary dev defaults SHALL favor low baseline cost.

No Redis baseline cost SHALL be introduced.

## 83. Environment layout

Implement:

    dev
    staging
    prod

at least structurally.

Only dev must be actually applied during ES-07 unless the user explicitly
provides and approves staging/prod targets.

Do not create real production infrastructure merely to prove the layout.

## 84. Dev environment

Dev SHALL:

- use low-cost Cloud SQL configuration;
- allow scale-to-zero API/worker;
- use Redis-free application backends;
- have destruction settings suitable for a development environment;
- still protect against accidental deletion where appropriate.

Dev must be fully functional, not a mock configuration.

## 85. Staging environment

Provide configuration structure for staging.

Do not necessarily apply it.

Staging should resemble production architecture with potentially smaller sizing.

## 86. Production environment

Provide production configuration structure.

Production SHALL enable stronger stateful-resource protections.

Do not guess expensive production sizing.

Use variables/placeholders with documented required decisions.

## 87. Resource deletion behavior

Explicitly define deletion behavior for:

- Cloud SQL;
- GCS buckets;
- secrets;
- Artifact Registry where relevant.

Production stateful resources must not be accidentally destroyable through a
routine plan.

Dev may permit explicit destroy.

## 88. Terraform lifecycle

Use `prevent_destroy` only where appropriate and where it does not make normal
environment teardown impossible without documented procedure.

Do not sprinkle lifecycle blocks everywhere.

Cloud-native deletion protection may be preferable for some resources.

## 89. GCS destroy behavior

Terraform must not silently delete non-empty production buckets.

Make production bucket deletion semantics explicit.

Do not use `force_destroy=true` in production.

Dev may use it only when deliberately configured.

## 90. Cloud SQL destroy behavior

Production must use deletion protection.

Dev may disable it for teardown.

Document how to intentionally destroy dev.

## 91. IAM testing

Do not consider IAM correct solely because Terraform plans it.

Verify after apply:

- API identity can access required resources;
- worker identity can access required resources;
- init identity can initialize;
- Tasks identity can invoke worker;
- anonymous caller cannot invoke worker.

Avoid granting broad roles simply to make tests pass.

## 92. Secrets testing

Verify runtime services can read only required secrets.

Where practical, test at least one negative permission boundary.

Do not expose secret contents in test output.

## 93. GCS end-to-end application test

After initialization and deployment:

1. authenticate to CARE through an appropriate dev path;
2. upload a small file using the multipart CARE endpoint;
3. verify object exists in GCS;
4. download it through CARE;
5. verify downloaded bytes equal uploaded bytes.

Do not access GCS directly from the frontend.

This test proves ADR-0001 and ADR-0002 in the real GCP profile.

## 94. API Redis-free verification

With no Redis resource present, verify:

- API starts;
- health succeeds;
- login/rate-limit path executes;
- recent views operate;
- file transport operates;
- async dispatch works;
- advisory locking paths do not require Redis.

Do not fake Redis unavailability by merely leaving an unused variable blank if
a Redis resource still exists.

The environment should actually contain no selected Redis service.

## 95. PostgreSQL rate-limit verification

Verify PostgreSQL best-effort rate limiting in deployed GCP.

Sequential requests must count correctly.

Do not attempt to prove strict concurrency guarantees that the backend
explicitly does not provide.

Confirm the dedicated rate-limit cache table exists and remains distinct from
the default cache table.

## 96. Recent Views verification

Exercise a Recent Views API path in deployed GCP.

Verify persistence survives API instance replacement/restart.

Do not rely on local process memory.

## 97. Locking verification

Execute a representative PostgreSQL advisory-lock consumer in the GCP
environment.

Initialization already exercises `sync_permissions_roles`.

No Redis lock dependency should appear.

## 98. Cloud Tasks application dispatch verification

Prefer dispatching through the CARE application API or operation that normally
enqueues a task.

Do not only use:

    gcloud tasks create-http-task

because that proves infrastructure but bypasses the application's dispatcher.

A direct infrastructure task may be used as an additional diagnostic.

## 99. Worker task result verification

Select a harmless registered task.

Verify observable completion through:

- expected database state;
- expected email test sink;
- expected log;
- another deterministic side effect.

Do not use a destructive task solely for testing.

## 100. Scheduler end-to-end verification

Select one harmless or controlled periodic operation.

Prove:

    Cloud Scheduler
        ->
    target
        ->
    CARE operation
        ->
    successful completion

Do not wait for an inconvenient production cron interval if the Job can be
manually triggered for verification.

## 101. Image rollout verification

Deploy one immutable image reference to:

- init Job;
- API;
- worker.

Verify all use the same image identifier.

Do not silently deploy `latest` for acceptance evidence if immutable digest/tag
is available.

## 102. Initialization sequencing

Document and verify the actual dev deployment sequence.

At minimum:

1. Terraform infrastructure apply;
2. image available;
3. init Job updated;
4. init Job executed;
5. successful completion verified;
6. API/worker revision activated;
7. scheduler/tasks enabled or verified.

Do not deploy API revisions that depend on unapplied migrations before init
succeeds.

## 103. Bootstrap vs application deployment

State bootstrap and application-environment apply SHALL be separate concepts.

Document both.

An operator should be able to understand:

    bootstrap once
    environment apply repeatedly

## 104. Terraform plan verification

Run:

    terraform fmt -check
    terraform validate
    terraform plan

for dev.

Also validate staging/prod roots where feasible without applying.

No provider configuration error is acceptable.

## 105. Static infrastructure testing

Where useful, add Terraform tests, validation rules, or policy assertions for
critical invariants.

At minimum ensure through Terraform logic/tests where practical:

- worker cannot be configured public accidentally;
- production Cloud SQL deletion protection cannot default off;
- Redis-free dev does not instantiate Redis resources;
- role environment variables are correct.

Do not add a large policy framework.

## 106. Destructive plan test

Demonstrate protections for stateful production configuration without actually
destroying production resources.

Use plan inspection or targeted safe test environment.

At minimum verify accidental deletion/replacement of Cloud SQL is visibly
protected.

## 107. Terraform outputs

Expose useful non-sensitive outputs such as:

- API URL;
- worker service name/URL if operationally needed;
- Artifact Registry repository path;
- Cloud SQL instance identifier;
- bucket names;
- queue names;
- init Job name.

Do not output:

- secret contents;
- passwords;
- tokens.

## 108. Generated application environment

Terraform MAY generate maps/outputs used to configure Cloud Run.

Do not generate committed `.env` files containing secrets.

Environment configuration should remain represented declaratively in Terraform.

## 109. Local compatibility

ES-07 SHALL not break local Docker Compose.

Run the existing local regression after infrastructure changes if shared
scripts/configuration were modified.

Infrastructure files alone should not alter local behavior.

If application code is modified to accommodate GCP, run full application tests.

## 110. Application modifications during ES-07

Application source changes are allowed only when a real GCP deployment reveals a
bug or missing integration that prevents the already-approved architecture from
working.

Before making such a change:

1. record the blocker;
2. explain why it is application-side;
3. make the smallest correction;
4. add regression coverage;
5. update relevant documentation.

Do not use ES-07 as an excuse for broad refactoring.

## 111. No Redis fallback

If the Redis-free deployment reveals an unexpected Redis call:

Do not provision Redis automatically.

Stop and identify:

- call site;
- responsibility;
- whether it violates completed architecture;
- smallest correction.

Redis may only be added to the managed profile by an explicit decision.

## 112. Cloud-specific code boundary

Terraform and deployment configuration may contain GCP-specific logic.

CARE business/domain code SHALL NOT branch on:

    GCP
    Cloud Run
    Cloud SQL
    Cloud Tasks
    Cloud Storage

beyond the already-approved provider backend implementations.

## 113. Credentials

Use Application Default Credentials / workload identity for GCP runtime access.

Do not create or commit service-account JSON keys.

Local Terraform operator authentication may use:

    gcloud auth application-default login

or another approved operator mechanism.

Document the chosen workflow.

## 114. Developer prerequisites

Document required tools for infrastructure work.

At minimum:

    terraform
    gcloud
    Docker
    authenticated GCP access

Do not require kubectl.

Do not require Redis.

## 115. GCP permissions for Terraform operator

Document the permissions required to bootstrap/apply infrastructure.

Avoid telling operators simply to use Owner permanently.

A broad bootstrap identity may be used temporarily when unavoidable, but the
long-term deployment identity should be narrower.

## 116. Project ownership assumptions

Do not assume organization-level permissions exist.

If a resource requires organization/folder-level control, document it as an
external prerequisite.

Keep the initial deployment viable in a normal GCP project where possible.

## 117. Domain/DNS

Custom domain configuration is outside the critical path unless required by the
frontend/backend integration.

The dev environment may use the generated Cloud Run API URL.

Do not block ES-07 on production DNS.

Document how a custom domain would integrate later.

## 118. CORS/CSRF/allowed hosts

Real Cloud Run URLs may require application configuration changes for:

- ALLOWED_HOSTS;
- CSRF trusted origins;
- CORS;
- frontend URL.

Use existing environment settings.

Do not hardcode Cloud Run hostnames in source.

Test real browser/API behavior where possible.

## 119. Frontend

ES-07 does not deploy the frontend unless existing project architecture already
includes that deployment in the same infrastructure scope.

The backend API URL SHALL be exposed for frontend configuration.

Do not reintroduce frontend storage configuration.

## 120. Database connection limits

Cloud Run scaling SHALL account for Cloud SQL connection limits.

Document the approximate upper bound implied by:

    max API instances
    × application DB connections/workers
    +
    max worker instances
    × application DB connections/workers

Use conservative dev limits.

Do not create a scaling configuration that can trivially exhaust Cloud SQL.

## 121. Connection pooling

Do not introduce PgBouncer or another pooler unless deployment evidence shows it
is needed.

Use Django/Cloud SQL supported behavior first.

Document connection management.

## 122. Cloud SQL cost

Cloud SQL is the principal persistent baseline cost in the Redis-free managed
profile.

Document the chosen dev tier and why.

Do not add HA to dev merely for architectural symmetry.

## 123. GCS cost

Document storage class and location.

Use Standard unless a verified workload suggests another class.

Do not optimize storage classes prematurely.

## 124. Cloud Run cost controls

Dev configuration should use:

- min instances zero;
- bounded max instances;
- reasonable CPU/memory;
- bounded request/task concurrency.

Do not enable always-allocated CPU without a requirement.

## 125. Environment variables and secrets review

Before apply, produce a final role-by-role environment table:

    setting | api | worker | init | source | secret?

Use it to verify least privilege and remove accidental shared configuration.

## 126. Plugin considerations

Plugins may add:

- Django apps;
- tasks;
- settings;
- endpoints.

ES-07 infrastructure supports core CARE only unless an installed plugin is
explicitly present.

Do not create infrastructure for hypothetical plugins.

Document that Cloud Tasks-incompatible plugin tasks remain a known plugin
compatibility limitation where applicable.

## 127. Dev apply approval

Do not apply infrastructure to an arbitrary or production GCP project without
verifying the intended project.

Before first real apply, report:

    project_id
    region
    environment
    resources to be created
    cost-driving resources

If project information is already available in the environment/configuration,
use it.

Do not repeatedly ask for values that can be discovered safely through existing
gcloud/Terraform configuration.

## 128. Existing GCP resources

Before applying, inspect whether resources with the intended names already
exist.

This is a greenfield CARE deployment, but the GCP project itself may not be
empty.

Do not delete or adopt unrelated resources automatically.

If a naming collision exists, report it.

## 129. Import policy

Do not automatically `terraform import` existing resources unless they are
clearly intended to belong to this CARE deployment.

Prefer unique environment-specific naming.

## 130. Apply sequence

For dev:

1. bootstrap state if not already done;
2. initialize environment Terraform;
3. plan;
4. review;
5. apply base infrastructure;
6. ensure application image is available;
7. deploy/update init Job;
8. run init;
9. deploy/update API and worker;
10. verify IAM;
11. verify Cloud Tasks;
12. verify Scheduler;
13. run application acceptance tests.

The exact resource dependency graph may allow Terraform to create API/worker
before init, but traffic promotion/acceptance SHALL still require successful
initialization.

## 131. Terraform/app image chicken-and-egg

Cloud Run resources require an image.

Handle the initial deployment explicitly.

Acceptable strategies include:

- supply an already-published application image;
- deploy infrastructure in stages;
- use a deliberately documented bootstrap image only if absolutely necessary.

Do not hide this problem.

Prefer publishing the actual CARE image before creating runtime resources.

## 132. Build verification

If ES-07 includes building/publishing the dev image operationally:

- build from the final branch;
- tag immutably;
- push to Artifact Registry;
- record digest.

Do not redesign Docker build files unless necessary.

## 133. Real environment acceptance

Successful `terraform apply` is not sufficient.

The dev environment must pass actual application acceptance.

At minimum:

- init Job succeeds;
- API responds;
- worker is private;
- task dispatch works;
- GCS upload/download works;
- PostgreSQL cache works;
- PostgreSQL rate limiting works;
- Recent Views works;
- no Redis resource/dependency exists;
- Scheduler can trigger a periodic operation.

## 134. Failure cleanup

If dev apply partially fails:

- do not blindly destroy stateful resources;
- inspect Terraform state;
- correct the failure;
- re-plan.

Document any resource requiring manual recovery.

## 135. Drift

After successful apply, run a second plan.

Expected:

    no changes

or only explicitly understood ephemeral/provider-computed differences.

Unexpected persistent drift must be investigated.

## 136. Documentation

Update:

    ADR-0007 implementation checklist
    06-operations.md
    07-configuration-reference.md
    runtime-and-deployment.md
    unresolved-items.md

Add infrastructure documentation covering:

    Terraform layout
    bootstrap
    dev deployment
    state
    IAM
    Cloud SQL
    GCS
    Cloud Tasks
    Cloud Scheduler
    Cloud Run roles
    initialization
    Redis-free composition
    teardown/recovery

Do not rewrite unrelated architecture documents.

## 137. Environment examples

Provide non-secret example variable files or documented examples for:

    dev
    staging
    prod

Do not commit real project secrets.

Project IDs may be examples unless the repository intentionally tracks a
specific deployment project.

## 138. Terraform formatting and validation

All Terraform SHALL pass:

    terraform fmt -check
    terraform validate

for every relevant root.

No ignored syntax/provider errors.

## 139. Security verification

Before declaring ready:

- worker unauthenticated invocation rejected;
- Tasks identity invocation succeeds;
- no public bucket access;
- runtime service accounts use least privilege;
- no committed service account keys;
- no plaintext production secret values;
- state bucket access is restricted.

## 140. Redis-free verification

Before declaring ready, prove no Redis infrastructure exists in the dev managed
composition.

Search Terraform for:

    redis
    memorystore

Any occurrence must be:

- documentation;
- optional disabled compatibility code;
- explicit non-default composition.

No Redis resource may be active in dev.

## 141. Regression

If application source or shared scripts are modified:

run:

- focused tests;
- full serial suite;
- full parallel suite.

If ES-07 changes only Terraform/docs, application full regression need not be
repeated unnecessarily, but local runtime compatibility SHALL still be checked
where scripts/configuration changed.

## 142. Commit strategy

Use focused commits.

Suggested sequence:

    feat(infra): bootstrap terraform remote state

    feat(infra): provision core gcp services and identities

    feat(infra): provision cloud sql and storage

    feat(infra): provision cloud run api worker and init job

    feat(infra): provision cloud tasks and scheduler

    test(infra): validate gcp iam and runtime composition

    docs(infra): complete gcp deployment architecture

Different grouping is acceptable if it is more reviewable.

Do not create one giant Terraform commit.

Do not squash.

Do not push.

## 143. Allowed modifications

May modify/create:

- infrastructure Terraform;
- infrastructure documentation;
- environment examples;
- deployment scripts;
- GCP-specific operational tooling;
- application configuration only when required by a demonstrated deployment
  blocker;
- tests for such blocker fixes;
- ADR-0007 checklist;
- relevant inventories.

## 144. Forbidden modifications

Do not:

- redesign storage;
- redesign file transport;
- redesign async runtime;
- redesign cache;
- redesign rate limiting;
- redesign Recent Views;
- redesign locking;
- reintroduce Redis as mandatory;
- add Kubernetes;
- add another IaC framework;
- implement CI/CD beyond what is necessary to document deployment sequencing;
- begin ES-08.

## 145. Acceptance criteria

ES-07 is complete only when:

- work occurs on `feature/gcp-infrastructure`;
- Terraform layout exists;
- Terraform/provider versions are constrained;
- remote-state bootstrap is documented and verified;
- dev/staging/prod layouts exist;
- required GCP APIs are declared;
- Artifact Registry exists;
- service accounts and IAM are least-privilege;
- Cloud SQL PostgreSQL is provisioned;
- Cloud SQL deletion protection behavior is explicit;
- GCS buckets are private;
- Cloud Run API is deployed;
- Cloud Run worker is deployed;
- worker unauthenticated invocation is rejected;
- Cloud Tasks OIDC identity can invoke the worker;
- Cloud Run init Job exists and completes successfully;
- API/worker do not run initialization during startup;
- Cloud Tasks end-to-end execution succeeds;
- Cloud Scheduler replaces Beat in the managed composition;
- application uses GCS successfully;
- Redis-free dev profile runs without Redis;
- PostgreSQL default cache works;
- PostgreSQL best-effort rate limiting works;
- Recent Views work through PostgreSQL;
- advisory locking works;
- PostgreSQL cache/rate-limit tables are created by init, not Terraform;
- secrets are not committed;
- state is remote and protected;
- production stateful-resource protections are represented;
- `terraform fmt -check` passes;
- `terraform validate` passes;
- post-apply Terraform plan shows no unexplained drift;
- dev environment passes end-to-end acceptance;
- ADR-0007 checklist is updated;
- ES-08 has not started.

## 146. Final report

At completion provide:

1. branch;
2. initial and final commit;
3. commits created;
4. files created;
5. files modified;
6. files deleted;
7. Terraform version;
8. Google provider version;
9. Terraform directory structure;
10. bootstrap implementation;
11. remote-state configuration;
12. environment layout;
13. GCP project used;
14. region used;
15. APIs enabled;
16. Artifact Registry result;
17. application image/digest used;
18. service accounts created;
19. IAM bindings;
20. Cloud SQL configuration;
21. Cloud SQL connectivity method;
22. Cloud SQL deletion/backup protection;
23. database/user configuration;
24. GCS buckets;
25. GCS IAM;
26. Cloud Run API configuration;
27. Cloud Run worker configuration;
28. worker IAM verification;
29. Cloud Run init Job configuration;
30. init execution result;
31. cache/rate-limit table verification;
32. Cloud Tasks queue configuration;
33. Cloud Tasks OIDC configuration;
34. task end-to-end result;
35. Cloud Scheduler jobs;
36. scheduler end-to-end result;
37. Secret Manager resources;
38. secret-access IAM;
39. networking configuration;
40. whether a VPC connector was required;
41. health configuration;
42. logging/monitoring configuration;
43. Redis-free verification;
44. GCS upload/download result;
45. Recent Views GCP result;
46. PostgreSQL rate-limit result;
47. locking result;
48. API acceptance result;
49. worker route-isolation result;
50. same-image verification;
51. `terraform fmt -check` result;
52. `terraform validate` result;
53. dev plan/apply result;
54. post-apply drift plan result;
55. staging/prod validation result;
56. destructive-protection verification;
57. local compatibility verification if applicable;
58. application tests if application code changed;
59. documentation updated;
60. unresolved GCP items;
61. deviations from ADR-0007 or ES-07;
62. estimated persistent dev cost drivers;
63. final verdict:

    READY TO MERGE

or:

    NOT READY TO MERGE

Stop after ES-07.

Do not begin ES-08.
