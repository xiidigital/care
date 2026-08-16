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
| `APP_VERSION` | ✓ | ✓ | ✓ | — | tofu | the image reference |

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

dev declares none of them: it sends no real email and reports to no Sentry
project. `EMAIL_PASSWORD` becomes required for staging and prod together with
the SMTP settings in section 6 — see `unresolved-items.md` N1.

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
| `DJANGO_EMAIL_BACKEND` | console (dev only) | the application default is SMTP to `localhost:587`, which nothing in a Cloud Run container answers; leaving it unset made every email task fail *after* Cloud Tasks had delivered it, and the queue retried a send that could not succeed. dev writes the message to stdout, where Cloud Logging keeps it. staging and prod set nothing and need a real relay — `unresolved-items.md` N1 |

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
same ES-07 failure. **N1 is still open and still blocks staging:** no
environment other than dev, which writes to the console, can send email at all.
