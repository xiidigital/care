---
title: GCP Runtime Configuration Inventory
document: inventory/gcp-configuration
version: 1.0.0
status: Current
phase: ES-07
depends_on:
  - docs/xii/architecture/07-configuration-reference.md
  - docs/xii/adr/ADR-0007-terraform-for-GCP.md
---

# GCP Runtime Configuration Inventory

Which value each runtime role receives in the managed GCP profile, where it
comes from, and whether it is a secret.

This is the ES-07 section 5 inventory and the ES-07 section 125 role-by-role
review, in one table because they are the same table asked twice.

**It records what the code reads, not what the reference proposes.** Where
`07-configuration-reference.md` documents a variable that no code reads, it is
listed in section 5 below as *not set* rather than configured, so that nothing
in the infrastructure implies an effect it does not have.

Verified against `config/settings/base.py`, `config/settings/deployment.py`,
`config/caches.py`, `config/tasks.py`, `config/storage.py`, `config/runtime.py`
and `scripts/*.sh` at the ES-07 branch point.

---

## 1. Legend

| Column | Meaning |
| --- | --- |
| api / worker / init | the role receives this value |
| Secret | value lives in Secret Manager and never in OpenTofu |
| Source | `tofu` — an infrastructure fact OpenTofu computes; `tofu (fixed)` — a constant it sets; `secret` — Secret Manager, written by `provision-secrets.sh`; `tfvars` — an operator decision; `default` — the application default, not set |

---

## 2. Runtime role and backend selection

Four independent selections. None names Redis, and none is inferred from
another.

| Variable | api | worker | init | Secret | Source | Value |
| --- | :-: | :-: | :-: | :-: | --- | --- |
| `CARE_PROCESS_ROLE` | ✓ | ✓ | ✓ | — | tofu (fixed) | `api` / `task_worker` / `init` |
| `CARE_STORAGE_BACKEND` | ✓ | ✓ | ✓ | — | tofu (fixed) | `gcs` |
| `CARE_TASK_BACKEND` | ✓ | ✓ | ✓ | — | tofu (fixed) | `cloud_tasks` |
| `CARE_CACHE_BACKEND` | ✓ | ✓ | ✓ | — | tofu (fixed) | `postgres` |
| `CARE_RATE_LIMIT_BACKEND` | ✓ | ✓ | ✓ | — | tofu (fixed) | `postgres` |
| `DJANGO_SETTINGS_MODULE` | ✓ | ✓ | ✓ | — | tofu (fixed) | `config.settings.deployment` |

`CARE_TASK_BACKEND` reaches the worker and init even though neither enqueues:
settings validate the *selected backend*, not the role, so all six Cloud Tasks
variables below are required wherever `cloud_tasks` is chosen.

`CARE_RATE_LIMIT_BACKEND=postgres` is best-effort and non-atomic under
concurrency. That is the documented semantic of the mode, not a defect — see
`07-configuration-reference.md` §26.1.2.

## 3. Infrastructure-derived values

Every one is computed by OpenTofu from a resource it manages. None is written
in two places.

| Variable | api | worker | init | Secret | Source | Derived from |
| --- | :-: | :-: | :-: | :-: | --- | --- |
| `GCP_PROJECT_ID` | ✓ | ✓ | ✓ | — | tofu | `var.project_id` |
| `GCS_PROJECT_ID` | ✓ | ✓ | ✓ | — | tofu | `var.project_id` |
| `CARE_PATIENT_STORAGE_BUCKET` | ✓ | ✓ | ✓ | — | tofu | `google_storage_bucket` |
| `CARE_FACILITY_STORAGE_BUCKET` | ✓ | ✓ | ✓ | — | tofu | `google_storage_bucket` |
| `CARE_REPORT_STORAGE_BUCKET` | ✓ | ✓ | ✓ | — | tofu | shares the patient bucket |
| `GCP_TASKS_LOCATION` | ✓ | ✓ | ✓ | — | tofu | `var.region` |
| `GCP_TASKS_QUEUE` | ✓ | ✓ | ✓ | — | tofu | `google_cloud_tasks_queue` |
| `GCP_WORKER_URL` | ✓ | ✓ | ✓ | — | tofu | derived worker URL + `/internal/tasks/execute/` |
| `GCP_TASKS_SERVICE_ACCOUNT` | ✓ | ✓ | ✓ | — | tofu | invoker service account |
| `GCP_TASKS_OIDC_AUDIENCE` | ✓ | ✓ | ✓ | — | tofu | derived worker base URL |
| `POSTGRES_HOST` | ✓ | ✓ | — | — | tofu | `/cloudsql/<connection name>` |
| `POSTGRES_PORT` | ✓ | ✓ | — | — | tofu (fixed) | `5432` |
| `POSTGRES_USER` | ✓ | ✓ | — | — | tfvars | `care` |
| `POSTGRES_DB` | ✓ | ✓ | — | — | tfvars | `care` |
| `APP_VERSION` | ✓ | ✓ | ✓ | — | **image** | the commit the image was built from |

`APP_VERSION` is the one row whose source changed in ES-08, and it is now the
only value in this table that OpenTofu does not set. It used to be
`var.image`, which was correct while OpenTofu owned the image; once application
delivery owned it, the environment variable kept overriding the image's own
value and `/app_version/` reported the digest deployed months earlier — observed
on staging, where the endpoint named the previous digest while all five
resources were running the new one. `docker/prod.Dockerfile` bakes it from a
build argument, CI passes the commit, and the endpoint now describes the
artifact rather than a tfvars entry.

`GCP_WORKER_URL` is derived rather than read back from the service, because the
worker's own settings validate it and a service cannot reference itself. A
`check` block asserts the derivation against the real URL after every apply.

`POSTGRES_*` exist for `scripts/wait_for_db.sh`, which `start.sh` and
`start-worker.sh` run before gunicorn and which connects with these rather than
with `DATABASE_URL`. **init does not run it**, which is why the init role gets
neither these nor the database password.

## 4. Secrets

Containers and IAM are OpenTofu's. Values are `provision-secrets.sh`'s. No
payload passes through OpenTofu, so none is in state.

| Variable | api | worker | init | Secret Manager id | Why each role needs it |
| --- | :-: | :-: | :-: | --- | --- |
| `DJANGO_SECRET_KEY` | ✓ | ✓ | ✓ | `care-<env>-django-secret-key` | settings import it in every process |
| `DATABASE_URL` | ✓ | ✓ | ✓ | `care-<env>-database-url` | every role reaches PostgreSQL |
| `JWKS_BASE64` | ✓ | ✓ | ✓ | `care-<env>-jwks-base64` | `deployment.py` imports the key set at settings load |
| `POSTGRES_PASSWORD` | ✓ | ✓ | — | `care-<env>-database-password` | `wait_for_db.sh`, which init does not run |

`JWKS_BASE64` is **required, not optional.** Its fallback,
`get_jwks_from_file`, generates a *fresh random key set* when the file is
absent. Without the secret, every Cloud Run instance would sign with its own
key and a token issued by one would be rejected by the next — an intermittent
authentication failure that scales with instance count.

Optional secrets — `EMAIL_PASSWORD`, `SENTRY_DSN`, SMS credentials — are
declared per environment through the `optional_secrets` variable, which takes
the roles that may read each. Nothing grants a role a secret it does not need.

dev and staging declare none of them: neither sends external email, and neither
reports to a Sentry project. `EMAIL_PASSWORD` is **not required by any
environment**. It becomes required only for an environment that chooses to
enable external delivery, and it is declared then — the container and its
per-role binding are created by adding one entry to `optional_secrets`, with
the value written by `gcloud secrets versions add` and never by OpenTofu. See
`unresolved-items.md` N1.

## 5. Documented but not set

Listed so that nobody configures a variable that is read by nothing, and so the
omissions are deliberate rather than forgotten.

| Variable | Why not set |
| --- | --- |
| `CARE_ENVIRONMENT` | `07-configuration-reference.md` §5.1 calls it required; no code reads it. `SENTRY_ENVIRONMENT` carries the same value where it has an effect. |
| `CARE_TRANSIENT_STATE_BACKEND` | §29 documents it; no code reads it. There is no transient-state backend selector in the implementation. |
| `CARE_REPORT_PROGRESS_BACKEND` | §32 documents it; no code reads it. |
| `REDIS_URL`, `REDIS_CACHE_URL`, `REDIS_RATE_LIMIT_URL` | nothing selects Redis. `wait_for_redis.sh` reads the two backend selections and exits without waiting. |
| `CELERY_BROKER_URL` | Celery is not the selected task backend. |
| `CARE_CREATE_CACHE_TABLE`, `CARE_RUN_SYNC_*` | never implemented; `scripts/initialize.sh` is the pipeline. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Cloud Run resolves the attached service account through ADC. A value here would mean a key file, which is prohibited. |
| `DISABLE_RATELIMIT` | defaults to false, which is the required production value. Setting it would only create a way to get it wrong. |
| `PORT` | injected by Cloud Run from the configured container port. |

## 6. Explicit non-secret constants

| Variable | Value | Why stated rather than defaulted |
| --- | --- | --- |
| `CARE_CACHE_TABLE` | `care_cache` | the two DatabaseCache tables must stay distinct; the application refuses a collision, and this makes the separation visible in the infrastructure too |
| `CARE_RATE_LIMIT_TABLE` | `care_ratelimit_cache` | as above |
| `CARE_TASK_LOG_PAYLOAD` | `false` | payloads reference clinical data |
| `CARE_TASK_HANDLER_ENDPOINT_ENABLED` | `true` (worker only) | the one flag deciding whether the private route is registered; not left to be inferred |
| `DJANGO_DEBUG` | `false` | including dev — it is a real deployment |
| `DJANGO_ALLOWED_HOSTS` | `[..., ".run.app"]` | the generated Cloud Run hostname is not knowable before the service exists; Django reads a leading dot as a subdomain wildcard |
| `CSRF_TRUSTED_ORIGINS` | `[..., "https://*.run.app"]` | same reason |
| `CONN_MAX_AGE` | `60` | persistent connections; the figure that turns instance count into a Cloud SQL connection count |
| `DJANGO_EMAIL_BACKEND` | console (dev and staging) | the application default is SMTP to `localhost:587`, which nothing in a Cloud Run container answers; leaving it unset made every email task fail *after* Cloud Tasks had delivered it. The console backend writes the rendered message to stdout, where Cloud Logging keeps it, and is a **valid configuration in every environment** — generation is exercised end to end and only external delivery is absent. No provider is named anywhere in this infrastructure. An environment that wants delivery clears this and supplies `EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_USER`/`EMAIL_USE_TLS` or `EMAIL_USE_SSL`/`EMAIL_FROM` through `extra_env` plus `EMAIL_PASSWORD` through `optional_secrets` — `unresolved-items.md` N1 |

## 7. Tables created by initialization, never by OpenTofu

`scripts/initialize.sh` creates all of these. There is no schema SQL anywhere in
the infrastructure, and there must never be (ADR-0007 *Application schemas*).

| Object | Created by |
| --- | --- |
| Django model tables | `migrate` |
| `care_cache` | `createcachetable` |
| `care_ratelimit_cache` | `createcachetable`, same invocation |
| `emr_uservaluesetrecentview` | `migrate` |
| permissions and roles | `sync_permissions_roles`, under a PostgreSQL advisory lock |
| valuesets | `sync_valueset` |

`createcachetable` is called once with no table argument. It walks
`settings.CACHES` and acts on every `DatabaseCache` alias, so it creates both
tables under the Redis-free profile and neither under Redis — the condition
lives in the cache configuration rather than being duplicated in shell.

## 8. Scheduled operations

| Beat entry | Beat schedule | Cloud Scheduler | Timezone | Target |
| --- | --- | --- | --- | --- |
| `cleanup_expired_token_slots` | `crontab(hour=0, minute=0)` | `0 0 * * *` | Asia/Kolkata | Job `care-<env>-cleanup-token-slots` |
| `cleanup_incomplete_file_uploads` | every `FILE_UPLOAD_EXPIRY_HOURS` (24 h) | `30 1 * * *` | Asia/Kolkata | Job `care-<env>-cleanup-uploads` |

The timezone is `settings.TIME_ZONE`, which is `CELERY_TIMEZONE` — the timezone
these schedules already ran in. Defaulting to UTC would have moved both by five
and a half hours silently.

The second entry changes shape: Beat fired it on an interval measured from
whenever beat last started, which has no wall-clock meaning, and Cloud Scheduler
expresses cron only. A fixed daily time is the closest equivalent; the operation
is idempotent and time-of-day independent.

## 9. Connection budget

Gunicorn is synchronous, so a worker process holds at most one Django
connection. The bound is processes, not requests.

```
API    max_instances x GUNICORN_WORKERS
worker max_instances x GUNICORN_WORKERS
```

dev: `2 x 2` + `2 x 2` = **8**, against roughly 25 permitted by `db-f1-micro`.
The `database_connection_budget` output recomputes this for any environment.

## 10. ES-07 acceptance evidence

Run against the applied dev environment, project
`project-990c4414-a33c-47f2-9f4`, region `us-central1`, image
`care@sha256:b50abe44…a751`.

| ES-07 | What was proven | Evidence |
| --- | --- | --- |
| §53, §59 | worker rejects anonymous invocation | `POST /internal/tasks/execute/` → `403` from Cloud Run IAM, HTML error page, never reaching Django |
| §53 | role route isolation | same path on the API → `404` (route not registered under `CARE_PROCESS_ROLE=api`); `/api/v1/facility/` on the worker → `403` at the platform |
| §58, §98, §99 | Cloud Tasks end to end, dispatched by the application | TOTP enable → `enqueue_task_on_commit` → queue → OIDC → private worker → `204`, the "done, never redelivered" contract; rendered message in the worker's log |
| §63 | init runs the five operations | execution `care-dev-init-m7twm`, `Container called exit(0)` |
| §33, §34 | both cache tables exist and are distinct | `Cache table 'care_cache' already exists.` / `Cache table 'care_ratelimit_cache' already exists.` — from `createcachetable`, never from OpenTofu |
| §93 | GCS transport through CARE | 125 bytes uploaded multipart, downloaded back, and read directly from the bucket: three identical SHA-256 digests |
| §95 | PostgreSQL best-effort rate limiting | twelve sequential bad logins → `401 ×4` then `429 ×8`; counted exactly, sequentially |
| §96 | Recent Views survive instance boundaries | one entry written, then served identically by **two distinct Cloud Run instances** |
| §100 | Cloud Scheduler replaces Beat | manual trigger → Job `care-dev-cleanup-token-slots-nf2v6` succeeded; the automatic `0 0 * * *` Asia/Kolkata fired at 18:30 UTC on two consecutive days |
| §101 | one image, three roles | API, worker and init all on the same digest; `/app_version/` reports it |
| §105 | invariants are enforced, not documented | making the worker public, disagreeing Cloud SQL protections, and enabling the fixture loader outside dev each fail at plan time |
| §106 | stateful resources resist destroy | `tofu plan -destroy` on dev fails with four `prevent_destroy` errors on the secrets |
| §135 | no drift | `tofu plan -detailed-exitcode` → `0`, "No changes" |
| §140 | no Redis | no Memorystore instance or cluster in the project; every `redis` match in the configuration is a comment or the deliberately empty `redis_resources` output |

Two findings came out of this and are recorded rather than fixed here:
`unresolved-items.md` **L8** (`collectstatic` dominates cold start) and **N1**
(staging and prod cannot send email).

**2026-08-16.** L8 is closed — assets are built into the image and cold start
fell from 38.7s to 4.3s on the API — along with L2 and N2, which came out of the
same ES-07 failure.

**2026-08-17.** N1 is **reclassified, not closed**. It is an operational
capability and a deployment follow-up, not a staging or production blocker.
Console email delivery is a valid configuration in every managed environment;
staging now selects it explicitly, as dev does. External mailbox delivery
remains unconfigured and unverified, and is enabled later through
provider-neutral settings and Secret Manager without application redesign.

## 11. Staging acceptance evidence

Applied 2026-08-17/18 on branch `feature/staging-readiness`. Project
`project-990c4414-a33c-47f2-9f4`, region `us-central1`, environment `staging`,
state prefix `environments/staging`, image
`care-staging/care@sha256:3284c115…ba42`, built from a clean `git archive HEAD`
export at `fd243e17faed`.

**Staging shares dev's project.** Recorded as N3 below; it is a deviation from
this document's preferred shape, not from the isolation ADR-0007 requires.

| # | Acceptance item | Result | Evidence |
| --- | --- | --- | --- |
| 1 | init Job succeeds | pass | execution `care-staging-init-h2dsn`, "successfully completed"; `migrate`, `createcachetable`, `compilemessages`, `sync_permissions_roles`, `sync_valueset` |
| 2 | API starts and responds | pass | `/ping/` `200`, `/health/` `200`, `/app_version/` reports the deployed digest; `/api/v1/facility/` `403` from DRF, so Django and Cloud SQL are both live |
| 3 | worker remains private | pass | anonymous `GET /ping/` and `POST /internal/tasks/execute/` on the worker → `403` from Cloud Run IAM, before Django |
| 4 | Cloud Tasks OIDC invocation | pass | `POST /internal/tasks/execute/` → `204`, user agent `Google-Cloud-Tasks`, queue `care-staging-tasks` drained to empty |
| 5 | task route absent on the API | pass | `GET` and `POST` of that path on the API → `404`; the route is not registered under `CARE_PROCESS_ROLE=api` |
| 6 | public API absent on the worker | pass | authenticated `GET /api/v1/facility/` on the worker → `404`, while its own `/ping/` → `200` and the task route → `400` |
| 7 | GCS multipart upload / download | pass | 12 MiB written through the `patient` alias — above the GCS chunk size, so a resumable upload — `exists` true, size exact, byte-for-byte round trip, then deleted |
| 8 | PostgreSQL default cache | pass | set/get/delete round trip, plus the row observed in `care_cache` by direct SQL |
| 9 | PostgreSQL best-effort rate limiting | pass | five `incr` on the `ratelimit` alias → `5`, row present in `care_ratelimit_cache`; `RATELIMIT_USE_CACHE` is `ratelimit`, so the default cache is not the limiter's store |
| 10 | Recent Views persist across instances | pass | one entry written by a **Cloud Run Job**, then served identically by **three distinct API instances** (`…562c52`, `…04c1f1`, `…bfec7e`), 360 reads, all `200` |
| 11 | PostgreSQL advisory locking | pass | second thread on an independent connection refused with `ObjectLocked`; reacquired after the holder's transaction committed, so the lock is transaction-scoped |
| 12 | Cloud Scheduler triggers an operation | pass | manual trigger of `care-staging-cleanup-expired-token-slots` → Job `care-staging-cleanup-token-slots-mzt9r`, `Deleted 0 expired token slots`, `exit(0)` |
| 13 | console email end to end | pass | API → Cloud Tasks → worker → console backend → Cloud Logging. `Executing task send_totp_enabled_email`, then the rendered message: `Subject: Two-Factor Authentication Enabled`, `To:`, `From:`, full HTML body. Worker answered `204` |
| 14 | Redis-free | pass | no Memorystore instance or cluster; no `REDIS*` variable in either service's environment; no Redis cache alias at runtime; the worker logs `Redis is not required by the selected configuration (CARE_CACHE_BACKEND=postgres, CARE_TASK_BACKEND=cloud_tasks); not waiting for it.` |
| 15 | post-apply plan is clean | pass | `tofu plan -detailed-exitcode` → `0`, "No changes", both immediately after apply and again after the verification artefacts were removed |

**The runtime composition, read from the deployed environment**, not from the
configuration that produced it: `CARE_STORAGE_BACKEND=gcs`,
`CARE_TASK_BACKEND=cloud_tasks`, `CARE_CACHE_BACKEND=postgres`,
`CARE_RATE_LIMIT_BACKEND=postgres`, `DJANGO_EMAIL_BACKEND=console`, both cache
aliases resolving to `DatabaseCache`, both storage aliases to
`GoogleCloudStorage`.

**What item 13 does not prove.** No message left the environment. Console mode
demonstrates generation, dispatch, execution and logging; external mailbox
delivery is unconfigured, unverified and out of scope (N1).

**Verification artefacts, all removed.** A temporary Cloud Run Job
(`care-staging-acceptance`) ran the in-runtime probes for items 7–11, because
the declared Jobs run fixed commands and staging refuses the fixture loader. A
synthetic superuser drove items 4, 10 and 13 through the real API. Both were
deleted afterwards — staging holds no user and no acceptance data — and neither
was managed by OpenTofu, which is why item 15 is clean.

**One deliberate identity correction during the run.** The probes first ran as
the `init` service account and failed item 7 with
`storage.objects.get denied`. That is correct behaviour, not a defect:
`iam.tf` grants `roles/storage.objectUser` to the `api` and `worker` identities
only, and excludes `init` explicitly because initialization opens no object.
Re-run under the worker identity, item 7 passed.

---

## 12. Delivery configuration (ES-08)

Not application configuration. None of this reaches the image, and the
application never reads any of it — it is what CI/CD needs in order to
authenticate and to know which environment it is acting on
(ADR-0008 section 38).

### 12.1 Automation identities

Created by the bootstrap root when `github_repository` is set
(`modules/github-oidc`). No key exists for any of them; GitHub exchanges an OIDC
token for a short-lived credential.

| Identity | Account | May do | Impersonated from |
| --- | --- | --- | --- |
| publisher | `care-ci-publisher` | write one Artifact Registry repository | `artifact-publication` |
| staging deploy | `care-deploy-staging` | update staging's services and Jobs, run its init Job, act as its runtime identities | `staging` |
| production deploy | `care-deploy-prod` | the same, in production | `production` |
| infrastructure | `care-infra` | state bucket; project roles only when granted | `infrastructure-plan`, `infrastructure-apply` |

The provider accepts tokens only where `assertion.repository` equals the one
configured repository; impersonation is restricted further by the environment
claim. Both must hold.

### 12.2 What an environment grants them

`modules/care-environment/delivery.tf`, all bound to that environment's own
resources, all empty until `deployment_principals` and
`image_publisher_principals` are set:

| Grant | Scope | Why |
| --- | --- | --- |
| `roles/artifactregistry.writer` | this environment's repository | publish |
| `roles/artifactregistry.reader` | this environment's repository | resolve a digest before deploying it |
| `roles/run.developer` | each service, each Job, individually | update the image, execute init |
| `roles/iam.serviceAccountUser` | api, worker, init, tasks_invoker | deploy a revision that runs as them; mint an OIDC task |
| `roles/cloudtasks.enqueuer` | this environment's queue | acceptance dispatch |
| `roles/storage.objectUser` | this environment's buckets | acceptance round trip |
| `roles/logging.viewer` | **project** | acceptance observes the worker executing the task |

`roles/logging.viewer` is the only project-level grant, because Cloud Logging has
no per-service IAM. It is read-only and separately switchable
(`grant_deployment_log_read`).

None of these can read a secret payload. Cloud Run resolves secret references
itself, and no workflow or deployment script calls
`gcloud secrets versions access` — the delivery-invariants test fails if one
starts to.

### 12.3 GitHub variables

Variables, not secrets: a project id, a region and a service-account email
authenticate nobody (ES-08 section 72). No GitHub secret is required by any
delivery workflow.

```text
GCP_PROJECT_ID                    GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_REGION                        GCP_PUBLISHER_SERVICE_ACCOUNT
ARTIFACT_REGISTRY_REPOSITORY      GCP_DEPLOY_SERVICE_ACCOUNT
IMAGE_NAME              optional  GCP_INFRA_SERVICE_ACCOUNT
CARE_ENVIRONMENT                  TF_STATE_BUCKET
CARE_API_SERVICE        optional  INITIAL_IMAGE          optional
CARE_WORKER_SERVICE     optional
CARE_INIT_JOB           optional
CARE_APP_JOBS           optional
```

The optional ones default to the naming convention this module implements
(`care-<environment>-<resource>`). Nothing in the scripts or workflows hardcodes
a name, so an installation that names things differently sets the overrides
instead of forking the workflows.

Full setup in `docs/xii/architecture/08-continuous-delivery.md`.

### 12.4 The runtime image field

Owned by application delivery, not by OpenTofu (ES-08 section 140). The Cloud
Run services and Jobs ignore changes to `containers[0].image`; `var.image` is the
image a greenfield service is *created* with, and after that the deployed digest
is whatever the last release deployed.

Read the deployed digest from the platform, not from tfvars:

```bash
gcloud run services describe care-<env>-api --region <region> \
  --format='value(spec.template.spec.containers[0].image)'
```
