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
