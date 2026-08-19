---
title: GCP Configuration Reference
document: 07-configuration-reference
version: 0.1.0
status: Draft
source_repository: https://github.com/ohcnetwork/care
target_platform: Google Cloud Platform
deployment_type: Greenfield
depends_on:
  - docs/xii/architecture/00-scope-and-goals.md
  - docs/xii/architecture/01-current-runtime.md
  - docs/xii/architecture/02-target-runtime.md
  - docs/xii/architecture/03-migration-plan.md
  - docs/xii/architecture/04-testing.md
  - docs/xii/architecture/05-upstream-sync.md
  - docs/xii/architecture/06-operations.md
---

# GCP Configuration Reference

## 1. Purpose

This document defines the configuration contract for the greenfield CARE
deployment on Google Cloud Platform.

It specifies:

- environment variables;
- supported backend values;
- required and optional settings;
- validation rules;
- safe defaults;
- production restrictions;
- compatibility variables;
- role-specific configuration;
- backend-specific configuration.

This document describes the intended configuration interface.

Exact implementation details MAY differ where required by the current CARE,
Django, `django-storages` or Google Cloud library versions.

Any implementation difference SHALL preserve the behavior described here.

---

## 2. Configuration Principles

CARE configuration SHALL follow these principles.

### 2.1 Environment-based configuration

Deployment configuration SHALL be injected through:

- environment variables;
- Secret Manager references;
- Cloud Run service configuration;
- Cloud Run Job configuration;
- Terraform variables.

Production configuration SHALL NOT be stored in committed `.env` files.

### 2.2 Explicit backend selection

Backend choices SHALL use explicit variables.

Examples:

```text
CARE_TASK_BACKEND=cloud_tasks
CARE_CACHE_BACKEND=postgres
CARE_TRANSIENT_STATE_BACKEND=postgres
```

Note that rate limiting has no such variable **today**. It is Redis-backed in
every profile, for the reason given in §26.1, and a selection variable with one
legal value would only mislead. It gains one when roadmap item RF2
(`inventory/unresolved-items.md` Part RF) delivers a second legal value; until
then, no such variable is offered.

The application SHALL NOT infer the entire runtime from variables such as:

```text
IS_GCP=true
USE_SERVERLESS=true
PRODUCTION_PROVIDER=google
```

### 2.3 Validate only selected backends

Variables required by an unused backend SHALL not be mandatory.

For example:

```text
REDIS_CACHE_URL
```

SHALL not be required when:

```text
CARE_CACHE_BACKEND=postgres
```

Likewise:

```text
GCP_TASKS_QUEUE
```

SHALL not be required when:

```text
CARE_TASK_BACKEND=celery
```

### 2.4 Fail clearly

Invalid or incomplete production configuration SHALL fail during startup or
deployment validation with a clear error.

Errors SHOULD identify:

- variable name;
- invalid or missing value;
- selected backend;
- supported alternatives.

### 2.5 No secret aliases with insecure defaults

Production secrets SHALL not have usable development defaults.

The GCP settings SHALL not silently use values such as:

```text
secret
changeme
postgres
minioadmin
```

for production credentials.

---

# 3. Configuration Layers

The configuration is divided into these groups:

```text
Django core
application identity
Cloud Run
database
storage
task execution
cache
rate limiting
transient state
optional Redis
email
security
logging and monitoring
service-specific roles
jobs and schedules
```

---

# 4. Settings Module

## 4.1 `DJANGO_SETTINGS_MODULE`

Required for all GCP roles.

```text
DJANGO_SETTINGS_MODULE=config.settings.deployment
```

`config.settings.deployment` is the production settings module the repository
already ships; GCP introduces no settings module of its own. Provider selection
is configuration, not code (ADR-0001), so the same module serves AWS and GCP.

The API, worker and jobs SHOULD all use the same settings module unless a
future role requires a narrowly scoped alternative.

Production SHALL NOT use:

```text
config.settings.local
```

The Celery compatibility runtime MAY use an existing production or
deployment-oriented settings module outside GCP.

---

# 5. Environment Identity

## 5.1 `CARE_ENVIRONMENT`

Required.

Supported values:

```text
dev
staging
prod
```

Example:

```text
CARE_ENVIRONMENT=prod
```

This value SHOULD be included in:

- logs;
- Sentry environment;
- metrics labels;
- deployment annotations;
- task metadata.

Unknown values SHALL be rejected unless explicitly allowed for ephemeral test
environments.

## 5.2 `APP_VERSION`

Recommended.

Example:

```text
APP_VERSION=gcp-v2026.08.05.1
```

This value identifies the logical application release.

**Set by the image, not by the environment (ES-08).** `docker/prod.Dockerfile`
takes it as a build argument and bakes it in; CI passes the commit being built.
It is reported by `/app_version/`, which is how acceptance checks that a
deployed service is running the build it was asked to run.

Do not set it as a deployment environment variable. A value supplied at
deployment overrides the one the image carries, and then the endpoint describes
the deployment configuration instead of the artifact — which is exactly what it
exists to identify. The GCP module used to set it from `var.image` and no longer
does.

## 5.3 `GIT_COMMIT_SHA`

Recommended.

Example:

```text
GIT_COMMIT_SHA=3f49b8a...
```

It SHOULD match the source used to build the deployed image.

## 5.4 `UPSTREAM_COMMIT_SHA`

Recommended.

This records the upstream CARE commit included in the fork.

## 5.5 `DEPLOYED_AT`

Optional.

ISO-8601 timestamp identifying deployment time.

---

# 6. Django Core Configuration

## 6.1 `DJANGO_SECRET_KEY`

Required secret.

Example reference:

```text
projects/<project>/secrets/care-django-secret/versions/<version>
```

It SHALL:

- contain sufficient entropy;
- remain secret;
- differ between environments;
- not use a development default;
- be rotated only through a documented procedure.

## 6.2 `DJANGO_DEBUG`

Production-required value:

```text
DJANGO_DEBUG=false
```

`true` SHALL be prohibited in production.

Development and controlled staging environments MAY enable debugging only when
access is restricted and sensitive data is absent.

## 6.3 `DJANGO_ALLOWED_HOSTS`

Required.

Recommended representation:

```text
DJANGO_ALLOWED_HOSTS=["care-api.example.org",".run.app"]
```

The exact parser MAY accept JSON or comma-separated values according to the
existing CARE environment helper.

A wildcard:

```text
*
```

SHALL not be the normal production value.

## 6.4 `CSRF_TRUSTED_ORIGINS`

Required for browser-facing deployments.

Example:

```text
CSRF_TRUSTED_ORIGINS=[
  "https://care.example.org",
  "https://api.care.example.org"
]
```

Origins SHALL include schemes.

## 6.5 `CORS_ALLOWED_ORIGINS`

Required when the frontend uses a separate origin.

Example:

```text
CORS_ALLOWED_ORIGINS=[
  "https://care.example.org"
]
```

Production SHALL not enable unrestricted CORS.

## 6.6 `CORS_ALLOWED_ORIGIN_REGEXES`

Optional.

Use only when exact origin lists are insufficient.

Regular expressions SHALL be reviewed to avoid broad origin access.

## 6.7 `DJANGO_SECURE_SSL_REDIRECT`

Recommended production value:

```text
true
```

Cloud Run proxy handling SHALL correctly recognize forwarded HTTPS.

## 6.8 `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`

Recommended after the domain strategy is confirmed.

## 6.9 `DJANGO_SECURE_HSTS_PRELOAD`

SHALL not be enabled casually.

Preload has operational consequences outside CARE.

## 6.10 `DJANGO_SECURE_CONTENT_TYPE_NOSNIFF`

Recommended value:

```text
true
```

---

# 7. Cloud Run Configuration

## 7.1 `PORT`

Provided automatically by Cloud Run.

The API and HTTP worker SHALL listen on:

```text
0.0.0.0:${PORT}
```

The application SHALL not require a fixed production port.

## 7.2 `CARE_PROCESS_ROLE`

**Implemented in ES-03, completed in ES-06** (`config/runtime.py`).

Declares what the process is responsible for. It says nothing about where the
process runs, and there is no companion variable that does: ADR-0006 rejects
`CARE_RUNTIME_PROFILE`, `IS_GCP`, `USE_CLOUD_RUN` and any equivalent
deployment-topology switch. A deployment is a composition of a role and the
independently selected backends documented elsewhere in this reference.

Supported values:

```text
api            (default)
task_worker
scheduler
init
```

**Changed in ES-06.** ES-03 shipped `job` and `celery_worker`. `job` named a
deployment shape rather than a responsibility and became `init`;
`celery_worker` named a transport, and a Celery worker is a `task_worker` like
any other. Both old values now raise `ImproperlyConfigured`.

`postgres_queue_worker` is **not** supported; the PostgreSQL queue backend does
not exist. An unsupported value raises `ImproperlyConfigured` at startup, naming
every valid role. It is never mapped to `api`.

Example:

```text
CARE_PROCESS_ROLE=api
```

### What each role does

| Role | Responsibility | Routes | Health probes | Long-running |
| --- | --- | --- | --- | --- |
| `api` | public application API | public API + diagnostics | database, cache, Celery queue when `CARE_TASK_BACKEND=celery` | yes |
| `task_worker` | executes asynchronous work | `internal/tasks/execute/` + diagnostics | database, cache, task registry | yes |
| `scheduler` | decides when periodic work runs | diagnostics only | database, Celery queue when `CARE_TASK_BACKEND=celery` | yes |
| `init` | deployment-time initialization | none served | none; health is the exit status | no |

Diagnostics shared by every role are `/`, `/ping/`, `/health/` and
`/app_version/`.

### The default

`api`, preserved for backward compatibility: an unset value has always meant
"serve the API", and a checkout that runs `manage.py` directly relies on it.
Every entrypoint in this repository sets the role explicitly, and production
deployment definitions SHOULD do the same rather than depend on the default.

### Orthogonality

The role never selects a backend and no backend selection is restricted by
role. These are independent dimensions:

```text
CARE_PROCESS_ROLE
CARE_STORAGE_BACKEND
CARE_TASK_BACKEND
CARE_CACHE_BACKEND
```

A combination is rejected only when a capability it requires is missing — for
example Cloud Tasks selected without its queue settings — never because the
combination is unusual for a named platform.

### Entrypoints

| Role | Entrypoint |
| --- | --- |
| `api` | `scripts/start.sh`, `scripts/start-dev.sh` |
| `task_worker` (HTTP) | `scripts/start-worker.sh` |
| `task_worker` (Celery) | `scripts/celery_worker.sh`, `scripts/celery-dev.sh` |
| `scheduler` | `scripts/celery_beat.sh`, `scripts/celery_beat-dev.sh` |
| `init` | `scripts/initialize.sh` |

All of them run from the same application image.

It SHALL not alter clinical business behavior.

## 7.3 `GUNICORN_WORKERS`

Optional.

Conservative initial value:

```text
GUNICORN_WORKERS=1
```

The value SHALL be selected together with:

- Cloud Run concurrency;
- memory;
- database connections;
- thread count.

## 7.4 `GUNICORN_THREADS`

Optional.

Example:

```text
GUNICORN_THREADS=4
```

The total request concurrency per instance SHALL remain compatible with
database and file-streaming behavior.

## 7.5 `GUNICORN_TIMEOUT`

Optional.

The value SHALL support expected API and file-transfer durations.

It SHALL not be increased indefinitely to hide inefficient or inappropriate
request workloads.

## 7.6 `GUNICORN_GRACEFUL_TIMEOUT`

Optional.

Should allow orderly request completion during revision replacement.

## 7.7 `GUNICORN_KEEPALIVE`

Optional.

Use a conservative value compatible with Cloud Run proxy behavior.

## 7.8 Cloud Run minimum instances

Managed through Terraform rather than Django environment variables.

Recommended default:

```text
API: 0
HTTP task worker: 0
```

A minimum greater than zero requires explicit cost justification.

## 7.9 Cloud Run maximum instances

Managed through Terraform.

The value SHALL respect the database connection budget.

---

# 8. Google Cloud Identity

## 8.1 `GCP_PROJECT_ID`

Required for GCP environments.

Example:

```text
GCP_PROJECT_ID=care-production
```

## 8.2 `GCP_REGION`

Required.

Example:

```text
GCP_REGION=us-central1
```

The selected region SHOULD align with:

- Cloud Run;
- Cloud SQL;
- Cloud Tasks location;
- storage location where practical;
- expected users;
- legal and organizational requirements.

## 8.3 `GOOGLE_APPLICATION_CREDENTIALS`

SHALL normally be absent in Cloud Run.

Cloud Run SHALL use its attached service account and Application Default
Credentials.

This variable MAY be used locally for controlled integration tests.

Committed credential files are prohibited.

---

# 9. Database Configuration

## 9.1 `DATABASE_URL`

Required secret or protected configuration.

Example conceptual value:

```text
postgresql://care:<password>@/<database>?host=/cloudsql/<connection-name>
```

The exact form SHALL follow the chosen Cloud SQL connection mechanism and the
existing CARE environment parser.

The URL SHALL not be logged.

## 9.2 `CONN_MAX_AGE`

Required to be deliberately configured.

Conservative initial example:

```text
CONN_MAX_AGE=60
```

The final value SHALL be determined through testing.

A longer lifetime may reduce connection setup overhead but retain more
connections.

## 9.3 `CONN_HEALTH_CHECKS`

Recommended where supported.

Example:

```text
CONN_HEALTH_CHECKS=true
```

## 9.4 `CARE_DATABASE_APPLICATION_NAME`

Optional.

May identify application role in PostgreSQL sessions.

Examples:

```text
care-api
care-worker
care-jobs
```

## 9.5 `CARE_DATABASE_STATEMENT_TIMEOUT_MS`

Optional.

A deployment MAY configure a statement timeout.

It SHALL be tested against report generation, cleanup and administrative
commands.

## 9.6 `CARE_DATABASE_LOCK_TIMEOUT_MS`

Optional.

Useful to prevent indefinitely waiting on locks.

The value SHALL not cause normal migrations or transactions to fail
unnecessarily.

---

# 10. Database Initialization Configuration

## 10.1 `CARE_CREATE_CACHE_TABLE`

**Not implemented, and ES-04 chose not to add it.**

It was conceived as a flag telling the initialization pipeline whether to run
`createcachetable`. A flag would have to be kept in agreement with
`CARE_CACHE_BACKEND` by hand, and the failure mode is quiet: set `postgres`
without setting the flag and the service starts, then errors on first cache
read.

`scripts/initialize.sh` calls the command unconditionally instead. With no
table-name argument it walks `settings.CACHES` and acts only on `DatabaseCache`
aliases, so it creates the table under `postgres` and does nothing under
`redis`, `locmem` or `dummy`. The condition lives in the cache configuration,
which is where the backend is already decided, and there is nothing to keep in
sync.

It is idempotent, and instantiating the Redis-backed aliases opens no
connection, so a PostgreSQL-cache deployment can initialize with no broker
running.

The application runtime still creates no tables on startup.

## 10.2 `CARE_RUN_SYNC_PERMISSIONS`

## 10.3 `CARE_RUN_SYNC_VALUESETS`

**Neither is implemented, and ES-03 chose a script over both.**

These were conceived as flags telling an initialization pipeline which commands
to run. The pipeline is now a committed script instead, `scripts/initialize.sh`:

```bash
python manage.py migrate --noinput
python manage.py createcachetable      # no-op unless the postgres cache is selected
python manage.py compilemessages -v 0
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

A Cloud Run Job runs it as its container command; the Celery Beat entrypoints
call it for local compatibility. A deployment that wants one step alone runs
that `manage.py` command directly, which is clearer than a boolean.

Before ES-03 this sequence existed only inside the beat entrypoints, so a
runtime without beat never migrated at all. See
`inventory/runtime-and-deployment.md` §13.1.

Ordinary API and worker startup does **not** run it. Several Cloud Run instances
start concurrently, and ADR-0003 requires that instance startup never migrate.

---

# 11. Storage Backend Selection

## 11.1 General rule

Storage provider selection SHALL occur through Django `STORAGES`.

Application code SHALL use logical aliases.

The application SHALL not use:

```text
CARE_STORAGE_PROVIDER=gcp
```

inside business logic to branch between SDKs.

The settings module MAY use provider selection to construct `STORAGES`.

## 11.2 `CARE_STORAGE_BACKEND`

**Implemented in IS-01** (`config/storage.py`, `config/settings/base.py`).

Supported values:

```text
s3
gcs
```

Intended use:

```text
s3   -> MinIO, AWS S3 or compatible service  (default)
gcs  -> GCP production
```

Default:

```text
CARE_STORAGE_BACKEND=s3
```

The default preserves the existing local MinIO behaviour, so no local
configuration change is required.

Production GCP value:

```text
CARE_STORAGE_BACKEND=gcs
```

An unsupported value raises `ImproperlyConfigured` at startup, naming the
supported values.

`filesystem` is **not** a supported value. Per ES-01 §9 a filesystem backend may
remain test-only and is not exposed as a production option; tests substitute
`django.core.files.storage.InMemoryStorage` through `override_settings` instead.

## 11.3 `CARE_PATIENT_STORAGE_ALIAS`

Optional.

Default:

```text
patient
```

Changing logical aliases is discouraged.

## 11.4 `CARE_FACILITY_STORAGE_ALIAS`

Optional.

Default:

```text
facility
```

## 11.5 `CARE_REPORT_STORAGE_ALIAS`

Optional.

Default:

```text
report
```

---

# 12. GCS Storage Configuration

## 12.1 `CARE_PATIENT_STORAGE_BUCKET`

Required when GCS is selected.

## 12.2 `CARE_FACILITY_STORAGE_BUCKET`

Required when GCS is selected.

## 12.3 `CARE_REPORT_STORAGE_BUCKET`

Required when GCS is selected.

The report bucket MAY equal the patient bucket.

Logical aliases SHALL remain separate.

## 12.4 `GCS_PROJECT_ID`

Optional alias.

The implementation SHOULD normally reuse:

```text
GCP_PROJECT_ID
```

A separate project value MAY be supported for cross-project buckets only if
required.

## 12.5 `GCS_LOCATION`

Infrastructure-level value.

Managed by Terraform when creating buckets.

It is not normally needed by Django after bucket creation.

## 12.6 `GCS_DEFAULT_ACL`

Production SHOULD not depend on public or object-level default ACLs.

Uniform bucket-level access is preferred.

## 12.7 `GCS_QUERYSTRING_AUTH`

Recommended target value:

```text
false
```

because the normal file flow passes through Django and does not expose signed
provider URLs.

## 12.8 `GCS_FILE_OVERWRITE`

The value SHALL reflect CARE's object-name policy.

**Resolved in IS-01: overwrite SHALL be enabled, on every object-storage alias
and on both backends.** `config/storage.py` sets `file_overwrite: True`
unconditionally; it is not driven by an environment variable.

The earlier suggestion that `false` "may be appropriate" for unique, immutable
names is **incorrect**, and tested to be so. `file_overwrite = False` does not
reject a duplicate name — Django's `Storage.get_available_name` silently *renames*
the object, returning e.g. `patient/<internal_name>_a1b2c3`. CARE derives the key
from `internal_name` on every subsequent read, so the rename is never recorded
and the database row would point at an object that does not exist. That is
precisely the "duplicate-name handling affects database object references"
hazard this section warns about, and `false` causes it rather than preventing it.

`True` also matches the behaviour being replaced: `boto3.put_object` overwrote
unconditionally.

In practice collisions do not occur — `internal_name` is a UUID plus a timestamp
— so the setting matters only as a guarantee.

Verified by:

- `care/utils/tests/test_storage_config.py` — every alias is built with
  `file_overwrite: True`;
- `care/emr/tests/test_storage.py` — against real MinIO, re-saving returns the
  same name and replaces the content;
- the same file demonstrates the failure mode: `InMemoryStorage`, which has no
  such option, renames on collision.

## 12.9 `GCS_MAX_MEMORY_SIZE`

Optional backend setting.

This value SHALL be coordinated with Django upload handlers and Cloud Run
memory.

It SHALL not cause large objects to be loaded fully into memory.

---

# 13. S3 and MinIO Configuration

These settings apply when:

```text
CARE_STORAGE_BACKEND=s3
```

**Corrected 2026-08-07 to match the implementation.** Earlier revisions of this
section specified `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT_URL`,
`S3_REGION_NAME`, `S3_ADDRESSING_STYLE` and `S3_SIGNATURE_VERSION`. **No such
settings exist.** ES-01 §11.1 required reusing the tracked local configuration
rather than inventing a parallel set, so the credential and endpoint variables
below are the pre-existing ones.

## 13.1 Credentials and endpoints, per alias

Each alias draws from its own set. All have defaults, so a local checkout needs
no new value.

| Alias | Region | Key | Secret | Endpoint |
| --- | --- | --- | --- | --- |
| `patient` | `FILE_UPLOAD_REGION` | `FILE_UPLOAD_KEY` | `FILE_UPLOAD_SECRET` | `FILE_UPLOAD_BUCKET_ENDPOINT` |
| `report` | `FILE_UPLOAD_REGION` | `FILE_UPLOAD_KEY` | `FILE_UPLOAD_SECRET` | `FILE_UPLOAD_BUCKET_ENDPOINT` |
| `facility` | `FACILITY_S3_REGION_CODE` | `FACILITY_S3_KEY` | `FACILITY_S3_SECRET` | `FACILITY_S3_BUCKET_ENDPOINT` |

Each falls back to the shared `BUCKET_REGION` / `BUCKET_KEY` / `BUCKET_SECRET` /
`BUCKET_ENDPOINT`. An endpoint is emitted only when set, so AWS S3 works without
one; MinIO sets `BUCKET_ENDPOINT=http://minio:9000`.

## 13.2 `BUCKET_PROVIDER`

Credential source, not provider selection — provider selection is
`CARE_STORAGE_BACKEND` alone.

```text
AWS_ROLE_BASED  -> omit key, secret and endpoint; the SDK resolves the
                   instance role
anything else   -> supply key and secret explicitly
```

## 13.3 Bucket variables

```text
CARE_PATIENT_STORAGE_BUCKET
CARE_FACILITY_STORAGE_BUCKET
CARE_REPORT_STORAGE_BUCKET
```

Defaulting to `FILE_UPLOAD_BUCKET`, `FACILITY_S3_BUCKET` and
`FILE_UPLOAD_BUCKET` respectively. These are the **only** place a bucket name is
resolved; nothing else derives one.

## 13.4 Not configurable

`addressing_style` and `signature_version` are **not** exposed. botocore's
defaults are used, which are correct for both AWS S3 and MinIO (verified against
the local MinIO container).

**Known limitation:** an S3-compatible provider that requires explicit path-style
addressing or `s3v4` signing cannot currently be configured. No such provider is
in use. Adding them is a small change to `build_object_storage` in
`config/storage.py` if one appears.

---

# 14. Static File Configuration

## 14.1 `STATIC_URL`

Existing CARE value MAY remain.

## 14.2 `STATIC_ROOT`

Set in Django settings or image build configuration.

## 14.3 Static backend

The target backend remains:

```text
whitenoise.storage.CompressedManifestStaticFilesStorage
```

No runtime variable is required unless the project intentionally makes it
configurable.

## 14.4 `CARE_COLLECTSTATIC`

Build-time or deployment variable only.

The production image SHOULD run `collectstatic` during build.

The API SHALL not run it during every instance startup.

---

# 15. Upload Configuration

**Corrected 2026-08-07 to match the implementation.** Earlier revisions
specified `CARE_MAX_UPLOAD_SIZE`, `CARE_ALLOWED_UPLOAD_MIME_TYPES`,
`CARE_ALLOWED_UPLOAD_EXTENSIONS` and `CARE_BLOCKED_UPLOAD_EXTENSIONS`. **None
exists.** ES-02 §12 requires reusing CARE's existing limit rather than inventing
one, so the settings below are the real ones.

Uploads use `multipart/form-data` (ADR-0002). See the frontend file-flow
inventory §12 for the request contract.

## 15.1 `MAX_FILE_UPLOAD_SIZE`

The maximum accepted upload, **in megabytes**.

```text
MAX_FILE_UPLOAD_SIZE=5
```

Defined at `config/settings/config.py`. CARE compares it against
`UploadedFile.size` before anything is written, so an oversized file is rejected
without touching storage.

Note the unit: this is MB, not bytes, unlike the two Django settings below.

## 15.2 `FILE_UPLOAD_MAX_MEMORY_SIZE`

Bytes. Above this, Django's upload handlers spool the upload to a
`TemporaryUploadedFile` instead of holding it in memory.

```text
FILE_UPLOAD_MAX_MEMORY_SIZE=2621440
```

Django's own default, stated explicitly by ES-02 so the limit is visible rather
than implicit. At the defaults, anything over 2.5 MB is temp-file backed while
`MAX_FILE_UPLOAD_SIZE` still caps the total at 5 MB.

## 15.3 `DATA_UPLOAD_MAX_MEMORY_SIZE`

Bytes. Bounds the **non-file** part of a request body.

```text
DATA_UPLOAD_MAX_MEMORY_SIZE=2621440
```

Multipart file parts are exempt, so this does **not** cap upload size. It did
cap the previous base64 transport, where the file travelled as JSON: a 5 MB file
became roughly 6.7 MB of body and exceeded this limit. Multipart removes that
interaction.

## 15.4 `FILE_UPLOAD_TEMP_DIR`

Not configured. Django's default temporary directory is used.

Set it only if a deployment needs temporary uploads on a specific volume.
Temporary files are ephemeral and SHALL NOT be treated as durable storage.

## 15.5 `ALLOWED_MIME_TYPES`

The MIME allowlist, defined at `config/settings/base.py`.

The value checked against it is **sniffed from the file's leading bytes** with
`python-magic`, not taken from the request. A browser-declared `Content-Type` is
never trusted.

## 15.6 Extension rules

Not settings. Extension policy lives in `FileNameValidator`
(`care/utils/models/validators.py`), applied through `FileUploadCreateSpec`.
Security-sensitive defaults are in code, not deployment configuration.

---

# 16. Download Configuration

## 16.1 Inline MIME types

**Not a setting.** The inline allowlist is `SAFE_INLINE_FORMATS` in
`care/emr/utils/file_download.py`. Types in it are served with:

```text
Content-Disposition: inline
```

and everything else as `attachment`. This preserves the behaviour the presigned
`ResponseContentDisposition` used to provide. ES-02 §21 forbids broadening it
without a verified requirement.

Likely candidates:

```text
application/pdf
selected image types
```

## 16.2 `CARE_DOWNLOAD_CHUNK_SIZE`

Optional.

May control streaming chunk size where the implementation exposes it.

The value SHALL be benchmarked.

## 16.3 `CARE_ENABLE_RANGE_REQUESTS`

Optional.

Default:

```text
false
```

unless media-range support is implemented and tested.

---

# 17. Task Backend Selection

## 17.1 `CARE_TASK_BACKEND`

**Implemented in ES-03** (`config/tasks.py`, `config/settings/base.py`).

Supported values:

```text
celery       (default)
cloud_tasks
```

Default:

```text
CARE_TASK_BACKEND=celery
```

The default preserves the existing local and traditional behaviour, so no
local configuration change is required.

Recommended GCP value:

```text
CARE_TASK_BACKEND=cloud_tasks
```

`postgres` is **not** a supported value. Earlier revisions of this section listed
it. The PostgreSQL queue backend is not implemented, and accepting the value
would select a backend that cannot execute anything, so it raises
`ImproperlyConfigured` at startup like any other unsupported value. The error
names the value supplied and the values supported.

Only the selected backend's variables are required. Celery needs no `GCP_*`
value; Cloud Tasks needs no broker.

## 17.2 `CARE_TASK_DEFAULT_DELAY_SECONDS`

**Not implemented, and not needed.** No verified call site uses a delay. The
dispatcher accepts a per-call `delay_seconds`, which the Celery backend maps to
`countdown` and the Cloud Tasks backend to `schedule_time`; no call site passes
one today. A global default would be configuration for a behaviour nothing uses.

## 17.3 `CARE_TASK_PAYLOAD_VERSION`

**Not a setting.** The envelope carries a `version` field, currently `1`, defined
as a constant in `care/utils/tasks/envelope.py`. It describes the wire format
rather than a deployment choice, so it is not configurable: a worker and its
dispatcher must agree, and an environment variable could only make them disagree.
The worker rejects a version it does not recognise with HTTP 400.

## 17.4 `CARE_TASK_MAX_PAYLOAD_BYTES`

**Implemented.**

Default:

```text
CARE_TASK_MAX_PAYLOAD_BYTES=10240
```

Measured against the JSON-encoded validated payload, at the call site, before
anything is queued. Exceeding it raises `InvalidTaskPayloadError`.

Task payloads carry identifiers, not records. The ceiling is a guard against a
caller quietly enqueueing a whole clinical object; the schemas in
`care/emr/tasks/handlers.py` are the real constraint.

---

# 18. Cloud Tasks Configuration

Required when:

```text
CARE_TASK_BACKEND=cloud_tasks
```

**Implemented in ES-03.** All six below are validated together at startup when
`cloud_tasks` is selected, and the error names every one that is missing. None
is required under `celery`; each defaults to an empty string.

## 18.1 `GCP_TASKS_PROJECT_ID`

Optional.

Defaults to:

```text
GCP_PROJECT_ID
```

## 18.2 `GCP_TASKS_LOCATION`

Required.

Example:

```text
GCP_TASKS_LOCATION=us-central1
```

## 18.3 `GCP_TASKS_QUEUE`

Required.

Example:

```text
GCP_TASKS_QUEUE=care-default
```

Multiple queues MAY later use task-class-specific variables.

## 18.4 `GCP_WORKER_URL`

Required.

Example:

```text
GCP_WORKER_URL=https://care-prod-worker-...run.app/internal/tasks/execute/
```

The URL SHALL target the private worker service.

## 18.5 `GCP_TASKS_SERVICE_ACCOUNT`

Required.

This is the service account identity attached to OIDC task requests.

Example:

```text
care-tasks-invoker@care-production.iam.gserviceaccount.com
```

## 18.6 `GCP_TASKS_OIDC_AUDIENCE`

Required, but defaults to `GCP_WORKER_URL`, so a correct deployment usually
supplies one value rather than two.

Often equal to the worker service origin.

It SHALL match worker IAM expectations.

## 18.7 `GCP_TASKS_DEFAULT_DEADLINE_SECONDS`

Optional.

The value SHALL remain within Cloud Tasks and Cloud Run supported limits.

Different queues MAY use different infrastructure-level deadlines.

## 18.8 `GCP_TASKS_DEFAULT_QUEUE`

Optional alias for `GCP_TASKS_QUEUE`.

The project SHOULD avoid maintaining redundant names indefinitely.

## 18.9 Retry configuration

Retry policy SHOULD be managed in Terraform.

Examples:

```text
max attempts
max retry duration
minimum backoff
maximum backoff
maximum doublings
```

Task code SHALL not silently override infrastructure policy without
documentation.

---

# 19. Cloud Tasks Worker Configuration

## 19.1 `CARE_TASK_HANDLER_ENDPOINT_ENABLED`

**Implemented in ES-03.**

Default:

```text
true  when CARE_PROCESS_ROLE=task_worker
false otherwise
```

It does not merely reject requests: `config/urls.py` registers
`internal/tasks/execute/` only when it is set, so the public API service does
not route the endpoint at all and there is no public surface to protect.

The path matches the one `GCP_WORKER_URL` should point at:

```text
POST /internal/tasks/execute/
```

## 19.2 `CARE_TASK_ALLOWED_QUEUE_NAMES`

**Not implemented.** Cloud Run IAM is the authentication boundary, and the route
is absent from the API role entirely. Validating a `X-CloudTasks-QueueName`
header would add a check that any caller who reached the worker could satisfy,
which reads as defence in depth without being any.

## 19.3 `CARE_TASK_LOG_PAYLOAD`

**Implemented.**

Production-required value, and the default:

```text
CARE_TASK_LOG_PAYLOAD=false
```

The worker logs the task name and outcome always, and the payload only when this
is enabled. `InvalidTaskPayloadError` messages name fields and counts, never
values, so a validation failure cannot become the route by which a payload
reaches a log.

## 19.4 `CARE_TASK_HANDLER_TIMEOUT_SECONDS`

**Not implemented.** The Cloud Run request deadline bounds execution, and adding
a second application-level timeout would need a decision about what to do with a
half-finished report. Revisit if a handler is observed approaching the deadline.

## 19.5 `CARE_TASK_RETRYABLE_EXCEPTIONS`

**Not a setting, deliberately.** Retry classification is two application
exceptions in code -- `RetryableTaskError` and `PermanentTaskError`, in
`care/utils/tasks/exceptions.py`. Provider exceptions are translated at the
operation boundary that raised them.

An environment variable naming importable exception classes would reintroduce
exactly the coupling ADR-0003 removed, and would let configuration load
arbitrary code.

---

# 20. Celery Configuration

Used when:

```text
CARE_TASK_BACKEND=celery
```

## 20.1 `CELERY_BROKER_URL`

Required.

Common local value:

```text
redis://redis:6379/0
```

## 20.2 `CELERY_RESULT_BACKEND`

Optional depending on CARE call-site requirements.

Existing local compatibility MAY use the broker URL.

## 20.3 `CELERY_TASK_ALWAYS_EAGER`

Test-only option.

SHALL not be enabled in production unintentionally.

## 20.4 `CELERY_BEAT_ENABLED`

**Not implemented, and not needed.** Beat is a separate process started by
`scripts/celery_beat.sh` or by `celery worker -B`. The GCP profile does not run
Celery Beat because it does not start that process, not because a variable
disables it; a flag would be configuration that nothing reads.

The schedule itself is still registered in code
(`care/emr/tasks/__init__.py`) on the `on_after_finalize` signal, but
registration is inert without a beat process.

ADR-0003 requires that the same operation is never scheduled by Beat and Cloud
Scheduler at once. A deployment picks one; both invoke the same management
commands.

## 20.5 `CELERY_WORKER_CONCURRENCY`

Optional.

Must respect database and Redis connection budgets.

---

# 21. PostgreSQL Task Queue Configuration

This section applies only if the optional queue backend is approved.

## 21.1 `CARE_POSTGRES_QUEUE_SCHEMA`

Optional.

Example:

```text
CARE_POSTGRES_QUEUE_SCHEMA=care_tasks
```

## 21.2 `CARE_POSTGRES_QUEUE_NAMES`

Optional list.

Example:

```text
CARE_POSTGRES_QUEUE_NAMES=["default","reports","email"]
```

Do not create multiple queues without a workload reason.

## 21.3 `CARE_POSTGRES_WORKER_CONCURRENCY`

Optional.

Conservative default:

```text
1
```

## 21.4 `CARE_POSTGRES_WORKER_POLL_INTERVAL`

Optional.

Only relevant if the chosen queue implementation polls.

## 21.5 `CARE_POSTGRES_WORKER_HEARTBEAT_SECONDS`

Optional.

Used for worker-health diagnostics.

## 21.6 `CARE_POSTGRES_JOB_RETENTION_DAYS`

Optional.

Defines retention for completed or failed task records.

## 21.7 `CARE_POSTGRES_QUEUE_ENABLED`

May be redundant with `CARE_TASK_BACKEND=postgres`.

The implementation SHOULD prefer one authoritative switch.

---

# 22. Cache Backend Selection

**Corrected 2026-08-09 to match the ES-04 implementation.** Sections 22 to 25
proposed a larger set of variables than was built. What exists is marked
implemented; the rest is marked not implemented and says why, so nobody
configures a variable that is read by nothing.

## 22.1 `CARE_CACHE_BACKEND`

**Implemented.** Built and validated in `config/caches.py`.

Optional, not required. It defaults to `redis`, so an unconfigured checkout
behaves exactly as it did upstream.

Supported values:

```text
postgres
redis
locmem
dummy
```

Any other value raises `ImproperlyConfigured` at settings import, naming the
value given and listing the four supported ones.

Recommended low-cost GCP value:

```text
CARE_CACHE_BACKEND=postgres
```

Local compatibility value, and the default:

```text
CARE_CACHE_BACKEND=redis
```

The test profile does not read this variable. `config/settings/test.py` sets
LocMem directly, because each parallel worker needs its own cache — see §48 and
`inventory/unresolved-items.md` E7. Backend-specific behaviour is covered by
`care/utils/tests/test_cache_backends.py`, which selects each backend
explicitly.

## 22.2 What this variable does *not* select

**Implemented, and important.** `CARE_CACHE_BACKEND` selects the `default` cache
only. One other alias exists, and has its own selector:

```text
ratelimit      config/caches.py      selected by CARE_RATE_LIMIT_BACKEND (§26.1)
```

It is Redis under `CARE_RATE_LIMIT_BACKEND=redis` (the default), a dedicated
`DatabaseCache` table under `postgres`, and absent entirely under `disabled`.
Under Redis it falls back to `REDIS_URL` rather than reading `REDIS_CACHE_URL`,
and sets `IGNORE_EXCEPTIONS` to true so an outage fails closed to captcha rather
than to a 500 on the login path (§26.4).

Selecting `postgres` here therefore makes Redis optional **for ordinary
caching** and says nothing about rate limiting, which is a separate decision.
Since RF2 that decision can also be Redis-free, so a deployment choosing
`postgres` for both needs no Redis at all — see §26.1.4 for the combinations.

**Corrected 2026-08-09.** This section previously listed a `locks` alias and a
`recent_views` alias. Locking has not used the cache since ES-05 — it is a
PostgreSQL transaction-scoped advisory lock. Recent views has not used the cache
since RF1 — it is `emr.UserValueSetRecentView`, read and written through the
ORM, with no backend selector and no Redis fallback. Both aliases are removed,
as is the `build_redis_only_cache` helper that built them. The `ratelimit` alias
was added by the ES-04/L1 follow-up.

That one alias is the entire distance between the current state and a Redis-free
profile. Its architectural direction is roadmap item RF2 in
`inventory/unresolved-items.md`, Part RF. It is not implemented and does not
belong to a current phase.

---

# 23. PostgreSQL Cache Configuration

Required when:

```text
CARE_CACHE_BACKEND=postgres
```

## 23.1 `CARE_CACHE_TABLE`

**Implemented.** Optional.

Default:

```text
care_cache
```

## 23.2 `CARE_CACHE_TIMEOUT`

**Implemented.** Optional. Default `300`.

This is the *default* entry lifetime only. Call sites whose data has its own
lifetime pass an explicit TTL and are unaffected by it — report progress uses
120 s, the FHIR lookup cache 10 s. Raising this does not extend those.

```text
CARE_CACHE_TIMEOUT=300
```

## 23.3 `CARE_CACHE_MAX_ENTRIES`

**Not implemented.** Django's `DatabaseCache` accepts `MAX_ENTRIES` and
`CULL_FREQUENCY` through `OPTIONS`, but no measured usage exists to set them
from, and a wrong cull threshold degrades hit rate silently. Django's defaults
(300 entries, cull 1/3) apply. Add it when there is data, not before.

## 23.4 `CARE_CACHE_CULL_FREQUENCY`

**Not implemented.** See §23.3.

## 23.5 `CARE_CACHE_KEY_PREFIX`

**Implemented.** Optional.

Default:

```text
care
```

A value such as `care:prod` separates environments sharing one backend. It must
not embed a provider name, host, bucket or cloud project — those are deployment
facts, and putting them in a key makes the key change when infrastructure does.

## 23.6 `CARE_CACHE_VERSION`

**Not implemented.** Django's cache `VERSION` is available as a key-invalidation
lever, but nothing in CARE needs to bump it: invalidation is explicit and
key-scoped since `delete_pattern` was removed.

## 23.7 Table creation

**Implemented.**

The table is created by `scripts/initialize.sh`, which runs
`python manage.py createcachetable` directly after `migrate`. The command is
called unconditionally: with no table-name argument it walks `settings.CACHES`
and acts only on `DatabaseCache` aliases, so it creates the table under
`postgres` and does nothing under `redis`, `locmem` or `dummy`. The condition
therefore lives in the cache configuration rather than being duplicated in
shell, where it could drift.

Nothing creates the table during request startup. Several Cloud Run instances
start concurrently and must not race.

A missing table is reported by name. The cache health check returns 500 with
`cache table 'care_cache' does not exist; run
\`python manage.py createcachetable\``, and a read raises `ProgrammingError`
rather than reading as a cache miss — a silent fallback would make a skipped
initialization step look merely like slowness.

---

# 24. LocMem Cache Configuration

## 24.1 `CARE_CACHE_LOCATION`

**Not implemented.** The LocMem `LOCATION` is derived from
`CARE_CACHE_KEY_PREFIX`, so two deployments with different prefixes already get
separate stores and there is nothing a separate variable would add.

## 24.2 `CARE_CACHE_MAX_ENTRIES`

**Not implemented.** See §23.3.

## 24.3 What LocMem is and is not

**Implemented as documented behaviour.** LocMem is process-local and ephemeral.
It is:

```text
not shared across processes
not shared across Cloud Run instances
not suitable for global rate limits
not suitable for distributed locks
not suitable for cross-instance report progress
```

It is appropriate for process-local performance values, regenerated schema data,
and tests. The cache health check reports `shared: false` under LocMem and runs
no round trip, so a passing health check cannot be mistaken for evidence that
the cache is shared.

---

# 25. Redis Cache Configuration

Used when:

```text
CARE_CACHE_BACKEND=redis
```

## 25.1 `REDIS_CACHE_URL`

**Implemented.** Optional, with a documented fallback.

Resolution order (ES-04 §24):

```text
REDIS_CACHE_URL
    ↓
legacy REDIS_URL
    ↓
ImproperlyConfigured
```

The fallback is what keeps the existing local Docker Compose profile working
without anyone adding a new variable. `REDIS_URL` is retained because Celery and
the `ratelimit` alias read it. (It was also read by the `locks` and
`recent_views` aliases; both are gone — ES-05 and RF1.)

Provider-neutral by design: Upstash, Memorystore or a local container are all
selected by URL. There is no `USE_UPSTASH` or `USE_MEMORYSTORE` switch.

```text
rediss://default:<password>@<host>:6379/0
```

The URL is never logged, and the error raised when neither variable is set does
not echo it — these routinely carry a password.

## 25.2 `REDIS_CACHE_PREFIX`

**Not implemented.** Use `CARE_CACHE_KEY_PREFIX` (§23.5), which applies to every
backend. A Redis-specific prefix variable would make the key depend on which
backend was selected, which is the coupling ADR-0004 removes.

## 25.3 `REDIS_CACHE_TIMEOUT`

**Not implemented.** Use `CARE_CACHE_TIMEOUT` (§23.2), for the same reason.

## 25.4 `REDIS_CACHE_SOCKET_TIMEOUT`

**Not implemented.** No timeout is configured, so `redis-py` defaults apply.
Worth revisiting before running against a managed Redis over the public
internet, where a hung connection is likelier than against a local container.

## 25.5 `REDIS_CACHE_CONNECT_TIMEOUT`

**Not implemented.** See §25.4.

## 25.6 `REDIS_CACHE_IGNORE_EXCEPTIONS`

**Not implemented as a variable; the behaviour it describes is.** The value is
set per responsibility in code rather than per deployment, because it is a
correctness property of each consumer and not an operational preference:

| Alias | `IGNORE_EXCEPTIONS` | Why |
| --- | --- | --- |
| `default` | `true` | Performance cache degrades to a miss on a Redis outage. |
| `ratelimit` | `true` | An unknown count is reported as `should_limit`, so the outage fails closed to captcha rather than to a 500 (§26.4). |

The `locks` row was removed on 2026-08-09: ES-05 replaced cache locking with a
PostgreSQL advisory lock and the alias no longer exists. The `recent_views` row
went the same day: RF1 moved recent views to a PostgreSQL model, so there is no
Redis exception left to ignore or propagate.

**Known consequence, recorded rather than fixed.** The JWT denylist at
`config/authentication.py:21` reads the `default` cache, so under a Redis outage
`cache.get` returns `None`, which reads as "not invalidated" and revoked tokens
are accepted. That is a security property inheriting a performance default.
Tracked as K3 in `inventory/unresolved-items.md`; resolving it means deciding
whether revocation belongs in a cache at all.

or a dedicated non-cache backend is preferred.

---

# 26. Rate-Limit Backend Selection

## 26.1 `CARE_RATE_LIMIT_BACKEND`

**Implemented (RF2, 2026-08-11).** Selects where rate-limit counters live.

```text
CARE_RATE_LIMIT_BACKEND=redis      # default
CARE_RATE_LIMIT_BACKEND=postgres
CARE_RATE_LIMIT_BACKEND=disabled
```

An unrecognised value raises `ImproperlyConfigured` naming the three supported
ones. It is never inferred from `CARE_CACHE_BACKEND`, and the two are validated
independently — see §26.1.4.

This section previously said the variable did not exist and could not be
implemented, on the grounds that only `redis` could be implemented *correctly*.
The technical claim underneath that was right and still is. What changed is the
conclusion: the modes are not equally strong, and RF2 makes that difference
explicit rather than resolving it by refusing to offer the weaker one.

`django-ratelimit` was **not** replaced. Both counting modes are the same
library, counting the same way, into the same dedicated alias.

### 26.1.1 `redis` — strict, atomic, recommended where limits must hold

Counters live in a dedicated Redis-backed `ratelimit` cache alias, configured by
the variables in §28. Redis `INCR` is a single server-side command, so
concurrent requests cannot lose an increment: the configured rate is the rate.

This is the default, so an existing deployment that sets nothing keeps exactly
the behaviour it had. **Choose it when accurate limits matter.**

### 26.1.2 `postgres` — best-effort, non-atomic under concurrency, Redis-free

Counters live in a dedicated `DatabaseCache` alias on its own table
(§27). No Redis is required, configured or contacted.

**This mode is deliberately weaker, and must not be described as equivalent to
`redis`.** `DatabaseCache` does not implement `incr` at all — it inherits
`BaseCache.incr`, a `get()` followed by a `set()` with no row lock:

```text
read current count
        |
concurrent requests may read the same value
        |
both write the same incremented value
        |
actual usage is undercounted
```

So a burst of concurrent requests is undercounted, and **more requests are
allowed through than the configured limit**. Sequentially the count is exact;
under concurrency it is a floor, not a ceiling.

That is not a defect to be reported — it is the documented semantic of the mode,
demonstrated over independent PostgreSQL connections in
`care/utils/tests/test_ratelimit_modes.py::PostgresBestEffortConcurrencyTests`.
A control test in the same class runs the identical requests without overlap and
shows an exact count, so the limitation is bounded to concurrency rather than
general.

The weaker guarantee is visible in three places, on purpose:

| Where | What it says |
| --- | --- |
| `manage.py check` | `django_ratelimit.W001` is left unsilenced |
| startup summary | `rate_limit_backend=postgres rate_limit_semantics=best_effort_non_atomic` |
| this reference | §16 security wording, and this section |

### 26.1.3 `disabled` — no CARE application rate limiting

The wrapper returns "not rate limited" without a counter operation, a cache
lookup, or any backend access. No `ratelimit` cache alias is configured, and
`django_ratelimit` is not added to `INSTALLED_APPS` — so `manage.py check` is
clean with nothing silenced and nothing to connect to.

Call sites are unchanged: they call the same wrapper and get `False`.

**This mode must be chosen, never inferred.** An unreachable Redis does *not*
disable rate limiting; it triggers the failure policy in §26.4. Disabling a
security control is an explicit decision with an explicit variable.

### 26.1.4 Orthogonal to `CARE_CACHE_BACKEND`

Cache and rate limiting answer different questions and are selected separately.
All combinations are valid:

| `CARE_CACHE_BACKEND` | `CARE_RATE_LIMIT_BACKEND` | Valid |
| --- | --- | --- |
| `postgres` | `postgres` | yes — the Redis-free profile |
| `postgres` | `redis` | yes — PostgreSQL caching, strict counters |
| `redis` | `postgres` | yes — Redis caching, no Redis dependency for limits |
| `redis` | `redis` | yes — the traditional profile |
| any | `disabled` | yes |

Rate limiting is not cache, and does not become cache by sharing a technology.
Even with both on PostgreSQL it keeps its own table, its own retention and its
own failure policy. Rate limiting reading the `default` cache is what made
`CARE_CACHE_BACKEND=postgres` abort every management command
(`unresolved-items.md` L1); the separation is what fixed it, and RF2 preserves
it rather than collapsing it now that both can name PostgreSQL.

### 26.1.5 None of this discourages Redis

A deployment that already operates a Redis-compatible service is running the
composition with the least engineering effort, the best counter performance and
the strongest guarantee. RF2 added a second store; it removed nothing. See §18
of the RF2 record — `django-redis`, the Redis alias, the Redis environment
variables and Celery's Redis broker all remain supported.

## 26.2 `CARE_RATE_LIMIT_DEFAULT`

**Implemented under its existing name, `RATE_LIMIT`** (`settings.DJANGO_RATE_LIMIT`).

Default:

```text
RATE_LIMIT=5/10m
```

The syntax is `django-ratelimit`'s; see its rates documentation.

## 26.3 `DISABLE_RATELIMIT`

Production-required value:

```text
false
```

Disabling rate limiting in production SHALL require an explicit exceptional
decision.

Predates `CARE_RATE_LIMIT_BACKEND` and is kept unchanged. The two are
independent and either switches the wrapper off: this one is a per-environment
override — local and test settings set it — while
`CARE_RATE_LIMIT_BACKEND=disabled` is a deployment declaring that CARE performs
no application rate limiting at all, and additionally configures no alias and
does not install the library's app.

## 26.4 `CARE_RATE_LIMIT_FAILURE_POLICY` — not a variable; the policy is fixed

**No variable.** The policy is **fail closed, with a captcha escape**, and it is
not configurable — an operator should not be able to turn a security control off
by misreading a setting name.

The policy is the same in both counting modes. The mechanism is not, because
the two stores fail differently.

**`redis`** — built from the library's own settings:

| Setting | Value | Effect |
| --- | --- | --- |
| `CACHES["ratelimit"]["OPTIONS"]["IGNORE_EXCEPTIONS"]` | `True` | A Redis outage makes `add()`/`incr()` return `None` instead of raising |
| `RATELIMIT_FAIL_OPEN` | `False` (set explicitly) | An unknown count reports `should_limit` rather than "under the limit" |

**`postgres`** — `DatabaseCache` has no `IGNORE_EXCEPTIONS`; a missing cache
table or a broken connection raises. `config.ratelimit` therefore carries the
policy itself: it catches `DatabaseError` and reports *limited*, which lands the
caller on the same captcha path a Redis outage would. The call is wrapped in a
savepoint, so the failed statement does not poison the surrounding
`ATOMIC_REQUESTS` transaction and the captcha path stays usable — without it,
fail-closed-with-an-escape would degrade into a 500.

The `except` is scoped to `DatabaseError` deliberately. A broader clause would
let any bug on the login path disguise itself as "limited".

**`disabled`** — not applicable. There is no store to fail.

So when the rate-limit store is unreachable, `config.ratelimit.ratelimit()`
falls through to captcha validation: a caller who solves the captcha proceeds, a
caller who does not is refused. This is deliberately *not* `controlled_error`
(`IGNORE_EXCEPTIONS: False`), which would fail closed as an unhandled 500 on the
login and password-reset paths — the same outcome, expressed as an outage.

`RATELIMIT_FAIL_OPEN` is set explicitly even though `False` is the library
default, so that a security decision is visible in the settings file rather than
inherited silently.

Asserted by `test_ratelimit_backend.py::BackendUnavailableTests` (Redis) and
`test_ratelimit_modes.py::PostgresFailurePolicyTests` (PostgreSQL).

---

# 27. PostgreSQL Rate-Limit Configuration

Applies only when `CARE_RATE_LIMIT_BACKEND=postgres`. Ignored otherwise.

This section previously said none of these variables existed and that nothing
needed to replace them. RF2 implemented the mode, so it now documents what it
actually takes.

## 27.1 `CARE_RATE_LIMIT_TABLE`

**Implemented.** Defaults to `care_ratelimit_cache`.

Its own table, never `CARE_CACHE_TABLE`. Sharing one table would put ordinary
cache entries and security counters under a single `MAX_ENTRIES` cull budget,
where ordinary cache churn could evict live rate-limit counters.

Created by the existing `python manage.py createcachetable` step in
`scripts/initialize.sh`. That command walks `settings.CACHES` and acts on every
`DatabaseCache` alias, so it picks this one up with **no new command, no
migration and no startup hook**. Verified:

| `CARE_CACHE_BACKEND` | `CARE_RATE_LIMIT_BACKEND` | Tables created |
| --- | --- | --- |
| `postgres` | `postgres` | `care_cache`, `care_ratelimit_cache` |
| `redis` | `postgres` | `care_ratelimit_cache` only |
| `postgres` | `redis` | `care_cache` only |
| `postgres` | `disabled` | `care_cache` only |

## 27.2 `CARE_RATE_LIMIT_CACHE_TIMEOUT`

**Implemented.** Defaults to `86400` (one day). This is not cosmetic.

`django-ratelimit` seeds a counter with the window's own TTL, but every
subsequent increment goes through `BaseCache.incr`, which re-`set`s the key with
the **alias** timeout rather than the remaining window. Django's default of 300
seconds would silently expire any counter whose window is longer than five
minutes — CARE's password-reset endpoints use `10/h` — and an expired counter
restarts at zero, which admits a fresh allowance mid-window.

A day comfortably outlives every window CARE configures. Counters are namespaced
by window, so an entry that outlives its window is never read again; it is only
culled.

Lower it only if you are certain no configured rate has a longer period.

## 27.3 `CARE_RATE_LIMIT_MAX_ENTRIES`

**Implemented.** Defaults to `10000`.

`DatabaseCache` culls a third of the table whenever the row count passes
`MAX_ENTRIES`, and it does not choose which rows. At Django's default of 300
that would drop live counters under ordinary load — a sharper failure than the
non-atomic increment this mode does accept.

## 27.4 Retention and cleanup

No variable, and no cleanup job. `DatabaseCache` deletes expired rows as part of
its own culling, and counters are written with an expiry. There is nothing to
schedule.

## 27.5 The routed database connection — not a variable

**No variable, and deliberately so.** When `CARE_RATE_LIMIT_BACKEND=postgres`,
CARE adds a second `DATABASES` alias (`ratelimit`) that is a copy of `default`
with `ATOMIC_REQUESTS` disabled, and installs
`config.db_routers.RateLimitCacheRouter` to send the rate-limit table there. It
is the same database — the separation is transactional, not physical.

It exists because of an interaction that is invisible from either side alone.
`ATOMIC_REQUESTS` is on, and DRF's exception handler calls `set_rollback()` for
every `APIException` it converts into a response. A failed login raises
`AuthenticationFailed`, so the request transaction is rolled back — **taking the
counter increment with it**. Without the router the limiter counts requests that
succeed and forgets the ones that fail, on endpoints that exist to throttle
repeated failures.

The router matches on table name, so only the rate-limit table moves; the
ordinary `default` cache keeps its ADR-0004 behaviour. The same interaction for
the `default` cache is recorded in `inventory/unresolved-items.md` rather than
changed by RF2.

Asserted, including a control that fails if the router is removed, by
`test_ratelimit_modes.py::CounterSurvivesRequestRollbackTests`.

Note that `CARE_CACHE_TABLE` (§10) is unrelated and independent: that is the
ADR-0004 `default` cache.

---

# 28. Redis Rate-Limit Configuration

Applies only when `CARE_RATE_LIMIT_BACKEND=redis`, which is the default and the
only mode with strict atomic counting. Ignored under `postgres` and `disabled`.

Independent of `CARE_CACHE_BACKEND` in both directions — see §26.1.4.

## 28.1 `REDIS_RATE_LIMIT_URL`

**Implemented.** Optional in the sense that it falls back to `REDIS_URL`, so the
existing local compose profile keeps working with no new variable. Set it when
rate-limit counters should live somewhere other than the Celery Redis; it is
configurable independently of `REDIS_CACHE_URL`.

Treat as a secret: it routinely carries a password, and it is never logged.

## 28.2 `REDIS_RATE_LIMIT_PREFIX`

**Implemented.** Defaults to `care-ratelimit`.

Example:

```text
care:prod:ratelimit
```

## 28.3 `REDIS_RATE_LIMIT_SOCKET_TIMEOUT`

**Not implemented.** The alias uses `django-redis`'s connection defaults. Add it
here if an operator ever needs a bound tighter than the default, rather than
carrying an unimplemented variable in the reference.

## 28.4 Failure behavior

Specified in §26.4 and tested by
`care/utils/tests/test_ratelimit_backend.py::BackendUnavailableTests`, which
points the alias at a closed port and asserts that an outage does not raise,
that it fails closed, and that a valid captcha is still an escape.

---

# 29. Transient-State Backend Selection

## 29.1 `CARE_TRANSIENT_STATE_BACKEND`

Required.

Supported values:

```text
postgres
redis
```

Recommended low-cost GCP value:

```text
CARE_TRANSIENT_STATE_BACKEND=postgres
```

## 29.2 Durable versus disposable state

The variable SHALL select shared short-lived state only.

Correctness-critical or auditable state SHOULD use explicit PostgreSQL models
regardless of cache backend.

---

# 30. PostgreSQL Transient-State Configuration

## 30.1 `CARE_TRANSIENT_STATE_TABLE`

Optional.

Used only if a dedicated generic state table is implemented.

A generic table SHOULD not replace domain-specific models without need.

## 30.2 `CARE_TRANSIENT_STATE_DEFAULT_TTL`

Optional.

## 30.3 `CARE_TRANSIENT_STATE_CLEANUP_BATCH_SIZE`

Optional.

---

# 31. Redis Transient-State Configuration

## 31.1 `REDIS_TRANSIENT_STATE_URL`

Required when Redis transient state is selected.

## 31.2 `REDIS_TRANSIENT_STATE_PREFIX`

Recommended.

Example:

```text
care:prod:state
```

## 31.3 `REDIS_TRANSIENT_STATE_DEFAULT_TTL`

Optional.

---

# 32. Report Progress Configuration

## 32.1 `CARE_REPORT_PROGRESS_BACKEND`

Recommended explicit variable.

Supported values:

```text
database_model
cache
```

When:

```text
cache
```

is selected, the configured shared cache backend is used.

When:

```text
database_model
```

is selected, durable task or report-progress records are used.

## 32.2 Recommended initial value

If users need reliable cross-instance visibility and failure history:

```text
CARE_REPORT_PROGRESS_BACKEND=database_model
```

If disposable progress is sufficient:

```text
CARE_REPORT_PROGRESS_BACKEND=cache
```

## 32.3 `CARE_REPORT_PROGRESS_TIMEOUT`

Required only for cache-backed progress.

Example:

```text
CARE_REPORT_PROGRESS_TIMEOUT=600
```

The current two-minute behavior MAY be too short for real report generation and
SHALL be reviewed.

---

# 33. Optional Redis Provider Configuration

## 33.1 Provider-neutral configuration

The application SHOULD not require a provider name.

Standard Redis URLs should be sufficient.

## 33.2 `REDIS_SSL_CERT_REQS`

Optional.

Production SHOULD verify certificates.

Disabling verification SHALL require explicit justification.

## 33.3 `REDIS_MAX_CONNECTIONS`

Recommended.

The value SHALL respect provider plan limits and Cloud Run scaling.

## 33.4 `REDIS_HEALTH_CHECK_INTERVAL`

Optional.

## 33.5 `REDIS_RETRY_ON_TIMEOUT`

Optional.

Behavior SHALL be selected per responsibility.

## 33.6 Upstash

An Upstash deployment MAY configure:

```text
REDIS_CACHE_URL=rediss://...
REDIS_RATE_LIMIT_URL=rediss://...
REDIS_TRANSIENT_STATE_URL=rediss://...
```

No `USE_UPSTASH` variable is required.

---

# 34. Email Configuration

## 34.0 Two capabilities, configured separately

CARE distinguishes **application email generation** from **external email
delivery**.

| | |
| --- | --- |
| generation | render the message, dispatch the work, execute the task, report the failure |
| delivery | hand the bytes to a relay that reaches a mailbox |

Generation is application behaviour and is always present. Delivery is an
**optional operational capability** selected per environment.

**No email provider is mandated by this repository, and none SHALL be.** The
settings below are Django's own generic ones. Nothing here names, assumes or
requires a particular relay, API or vendor, and no credential value belongs in
any tracked file — see §34.11.

### Console mode is a valid configuration in every environment

```text
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

SHALL be accepted in dev, staging and production alike. Under it:

- email-producing workflows are operational;
- rendered messages appear on stdout, and therefore in the platform log sink;
- the asynchronous path is verifiable end to end —
  `API -> Cloud Tasks -> worker -> Django email backend -> logs`;
- external delivery is intentionally absent.

Console mode SHALL NOT be labelled invalid for staging or production, SHALL NOT
be rejected by an environment guard, and SHALL NOT fail a deployment. An
informational, non-failing note is permitted where it is useful and not noisy.

Selecting a real relay later requires no application redesign: set the backend
and the transport settings below, and supply the password through the platform
secret store. See `inventory/unresolved-items.md` N1.

## 34.1 `EMAIL_BACKEND`

Read from `DJANGO_EMAIL_BACKEND`. Any Django email backend, by dotted path.

The application default is:

```text
django.core.mail.backends.smtp.EmailBackend
```

which reads §34.2–§34.7. A managed environment that has not been given a relay
SHALL select the console backend rather than leave the SMTP default in place
against a host it cannot reach.

## 34.2 `EMAIL_HOST`

Required when the SMTP backend is selected. Defaults to `localhost`, which no
container in a managed environment answers.

## 34.3 `EMAIL_PORT`

Integer. Defaults to `587`.

## 34.4 `EMAIL_HOST_USER`

Read from `EMAIL_USER`. Not a secret by itself; protected where the relay
treats it as one.

## 34.5 `EMAIL_HOST_PASSWORD`

Read from `EMAIL_PASSWORD`. A secret whenever the relay authenticates. It SHALL
be injected from the platform secret store and SHALL NOT appear in any tracked
file, example or plan.

## 34.6 `EMAIL_USE_TLS`

Boolean. STARTTLS on a submission port, typically 587.

## 34.7 `EMAIL_USE_SSL`

Boolean. Implicit TLS, typically 465. Django rejects a configuration that sets
both this and §34.6; the application states no preference between them, because
which one applies is a property of the relay an operator selects.

## 34.8 `DEFAULT_FROM_EMAIL`

Read from `EMAIL_FROM`.

Example, and only an example — the address is deployment data:

```text
CARE <no-reply@example.org>
```

## 34.9 `SERVER_EMAIL`

Recommended for framework-generated error notifications where used. **Not read
by the implementation**; `config/settings/base.py` leaves it commented out, and
the deployed logging configuration declares the `django` logger explicitly so
that `mail_admins` is not restored.

## 34.10 `CARE_EMAIL_TASK_QUEUE`

Optional. **Not read by the implementation.**

May select a dedicated queue name when task isolation is implemented.

## 34.11 What the repository SHALL NOT contain

This is a public repository. It SHALL contain only generic variable names,
generic secret-store hooks, provider-neutral prose and non-secret placeholders.

It SHALL NOT contain SMTP usernames or passwords, API keys, relay hostnames
belonging to a private deployment, private sender addresses, production
credentials, committed secret values, or a provider-specific proposal presented
as required architecture.

Real email configuration belongs to deployment operations outside this
repository, or to secure secret injection at runtime.

---

# 35. Sentry Configuration

## 35.1 `SENTRY_DSN`

Optional secret.

If absent, Sentry SHALL remain disabled.

## 35.2 `SENTRY_ENVIRONMENT`

Recommended.

Defaults to:

```text
CARE_ENVIRONMENT
```

## 35.3 `SENTRY_TRACES_SAMPLE_RATE`

Optional.

Production value SHALL be chosen with privacy and cost considerations.

## 35.4 `SENTRY_PROFILES_SAMPLE_RATE`

Optional.

## 35.5 `SENTRY_EVENT_LEVEL`

Optional.

## 35.6 Integration selection

The GCP settings SHALL enable integrations according to active backends:

```text
Django integration -> normally enabled
Celery integration -> only when Celery is used
Redis integration -> only when Redis is used
```

---

# 36. Logging Configuration

## 36.1 `CARE_LOG_FORMAT`

Supported values SHOULD include:

```text
json
text
```

Recommended GCP value:

```text
json
```

## 36.2 `CARE_LOG_LEVEL`

Recommended default:

```text
INFO
```

Production `DEBUG` logging SHALL not be enabled broadly without review.

## 36.3 `CARE_LOG_REQUEST_BODIES`

Production-required value:

```text
false
```

## 36.4 `CARE_LOG_TASK_PAYLOADS`

Production-required value:

```text
false
```

## 36.5 `CARE_LOG_FILE_CONTENTS`

Production-required value:

```text
false
```

## 36.6 `CARE_LOG_SQL`

Production default:

```text
false
```

Temporary SQL logging MAY be enabled in controlled non-production
environments.

## 36.7 `CARE_REQUEST_ID_HEADER`

Optional.

Example:

```text
X-Request-ID
```

---

# 37. Health-Check Configuration

## 37.1 `CARE_HEALTH_DATABASE_ENABLED`

Recommended:

```text
true
```

## 37.2 `CARE_HEALTH_CACHE_ENABLED`

Recommended when the selected cache is required for normal operation.

## 37.3 `CARE_HEALTH_REDIS_ENABLED`

SHALL default according to active Redis responsibilities.

It SHALL not be required by a profile that has selected no Redis-backed
responsibility, and it SHALL NOT become required by the future Redis-free
profile of §44.2.

## 37.4 `CARE_HEALTH_CELERY_ENABLED`

GCP Cloud Tasks profile:

```text
false
```

Local Celery profile:

```text
true
```

## 37.5 `CARE_HEALTH_POSTGRES_QUEUE_ENABLED`

Only when the PostgreSQL queue backend is selected.

## 37.6 `CARE_HEALTH_STORAGE_ENABLED`

Optional.

A storage diagnostic check MAY be useful.

It SHALL avoid writing test objects on every public health request.

## 37.7 `CARE_HEALTH_PUBLIC_DETAILS`

Production-required value:

```text
false
```

Detailed dependency diagnostics SHOULD require operator authorization.

---

# 38. Cloud Run Job Configuration

## 38.1 `CARE_JOB_NAME`

Recommended log metadata.

Examples:

```text
migrate
sync-permissions
cleanup-incomplete-uploads
```

## 38.2 `CARE_JOB_COMMAND`

Prefer command configuration through the Cloud Run Job container command
rather than arbitrary runtime shell execution.

## 38.3 `CARE_JOB_TIMEOUT_SECONDS`

Infrastructure-level setting managed by Terraform.

## 38.4 `CARE_JOB_MAX_RETRIES`

Infrastructure-level setting.

## 38.5 `CARE_JOB_DRY_RUN`

Optional for commands supporting non-destructive previews.

Destructive jobs SHOULD support a dry-run mode where practical.

---

# 39. Cloud Scheduler Configuration

Scheduler configuration SHOULD primarily live in Terraform.

Per schedule, define:

```text
name
cron expression
timezone
target
authentication
retry policy
enabled state
```

## 39.1 `CARE_SCHEDULER_TIMEZONE`

Optional shared default.

The timezone SHALL be explicit.

It SHALL not inherit an unrelated Celery timezone accidentally.

## 39.2 Cleanup cadence

Variables MAY control schedule creation, but Terraform remains authoritative.

Examples:

```text
CARE_EXPIRED_TOKEN_CLEANUP_CRON
CARE_INCOMPLETE_UPLOAD_CLEANUP_CRON
```

The application SHALL not dynamically register GCP production schedules at
startup.

---

# 40. Authentication and JWT Configuration

CARE's existing authentication configuration SHALL remain authoritative.

Relevant secrets and variables may include:

```text
JWKS_BASE64
JWT-related keys
token lifetimes
issuer and audience settings
```

These SHALL be preserved during GCP adaptation.

Private signing material SHALL be stored in Secret Manager.

Authentication behavior SHALL not change merely because the application moves
to Cloud Run.

---

# 41. External Service Configuration

Existing CARE integrations MAY require variables for:

```text
Snowstorm or terminology services
SMS providers
SMTP
Sentry
other plugins
```

Each integration SHALL define:

- required variables;
- whether values are secret;
- timeout;
- failure behavior;
- health-check behavior;
- enabled state.

Optional integrations SHALL not prevent API startup when disabled.

---

# 42. Plugin Configuration

Plugins may add environment variables and dependencies.

Required production plugins SHALL be inventoried.

A plugin SHALL not be enabled without confirming compatibility with:

- GCP settings;
- Django Storage API;
- Cloud Tasks or selected task backend;
- operation without a Redis-backed `default` cache, and without a Celery broker;
- Cloud Run startup;
- empty-database initialization.

A plugin SHALL NOT assume that a Redis connection is available for its own use,
and SHALL NOT assume one is absent. CARE's Redis-dependent capabilities are
named in §26.1 and §22.2; a plugin that needs Redis for something else declares
that as its own dependency.

Plugin configuration SHALL not be mixed into the core GCP contract without a
documented reason.

---

# 43. Role-Specific Variable Matrix

| Variable group | API | HTTP worker | Jobs | PostgreSQL queue worker | Celery worker |
|---|---:|---:|---:|---:|---:|
| Django core | Required | Required | Required | Required | Required |
| Database | Required | Required | Required | Required | Required |
| Storage | Required | As handlers require | As commands require | As tasks require | As tasks require |
| Cloud Tasks enqueue | Usually required | Optional | Optional | No | No |
| Cloud Tasks worker URL | Required for enqueue | No | Optional | No | No |
| Task handler endpoint | No | Required | No | No | No |
| PostgreSQL queue config | No unless producer | No | Optional | Required | No |
| Celery broker | No | No | No | No | Required |
| Redis cache | Only if selected | Only if selected | Only if selected | Only if selected | Often |
| Email | As required | As required | Rarely | As tasks require | As tasks require |

Variables SHALL be injected only where needed where practical.

---

# 44. Default GCP Profile

**Two valid managed-GCP compositions exist.** They differ only in whether a
Redis-compatible service is present. §44.1 is the profile below and is available
now; §44.2 is future work and SHALL NOT be configured yet.

Recommended initial configuration:

```text
DJANGO_SETTINGS_MODULE=config.settings.deployment
CARE_ENVIRONMENT=prod
DJANGO_DEBUG=false

CARE_PROCESS_ROLE=api

CARE_STORAGE_BACKEND=gcs
CARE_TASK_BACKEND=cloud_tasks
CARE_CACHE_BACKEND=postgres
CARE_TRANSIENT_STATE_BACKEND=postgres
CARE_REPORT_PROGRESS_BACKEND=database_model

CARE_RATE_LIMIT_BACKEND=postgres

# No Redis variable of any kind. Rate limiting counts into its own PostgreSQL
# table, with the best-effort semantics described in §26.1.2; caching,
# queueing, locking, recent views and initialization need no Redis either.
#
# For strict atomic limits instead, set CARE_RATE_LIMIT_BACKEND=redis and
# provide REDIS_RATE_LIMIT_URL=rediss://<managed-redis>.

GCP_PROJECT_ID=<project>
GCP_REGION=<region>

CARE_PATIENT_STORAGE_BUCKET=<bucket>
CARE_FACILITY_STORAGE_BUCKET=<bucket>
CARE_REPORT_STORAGE_BUCKET=<bucket>

GCP_TASKS_LOCATION=<location>
GCP_TASKS_QUEUE=<queue>
GCP_WORKER_URL=<private-worker-url>
GCP_TASKS_SERVICE_ACCOUNT=<invoker-service-account>

CARE_LOG_FORMAT=json
CARE_LOG_LEVEL=INFO
CARE_LOG_REQUEST_BODIES=false
CARE_LOG_TASK_PAYLOADS=false
```

Secrets are injected separately.

## 44.1 Managed GCP with Redis — available now

The profile above. Composition:

```text
Cloud SQL          durable state and the application cache
Cloud Storage      files
Cloud Tasks        asynchronous dispatch
Redis-compatible   rate limiting
```

Advantages:

- it is the existing implementation, verified end to end;
- it requires the least engineering effort;
- it gives the best rate-limit performance — counters are one server-side atomic
  command.

A deployment that already operates a Redis-compatible service SHOULD choose this
composition. Nothing in this reference discourages it, and it may additionally
select Redis for the `default` cache and for Celery if that suits the operator.

**Corrected 2026-08-09.** This section previously stated that the profile "SHALL
start without any Redis variable". That contradicted the `REDIS_RATE_LIMIT_URL`
in the block above it and was wrong. What is true, and verified: the `init` role
needs no reachable Redis, and the API needs one for rate limiting and recent
views (§62, `unresolved-items.md` L1).

## 44.2 Managed GCP Redis-free — future

```text
Cloud SQL          durable state and the application cache
Cloud Storage      files
Cloud Tasks        asynchronous dispatch
no Redis
```

**This profile does not exist.** It required both roadmap items in
`inventory/unresolved-items.md`, Part RF. One is now delivered:

- **RF1** — Redis-free recent views, replacing the Redis list operations with a
  PostgreSQL persistence model. **Done** — `emr.UserValueSetRecentView`;
- **RF2** — Redis-free rate limiting, replacing `django-ratelimit` with an
  implementation supporting PostgreSQL atomic counters. **Open.**

RF2 belongs to no current phase and is not designed here. Until it ships, this
composition SHALL NOT be configured, offered to operators, or used as a cost
baseline.

It is recorded because it is the intended long-term direction and because the
architecture is deliberately kept able to reach it — not because it is available.

---

# 45. Local Upstream-Compatible Profile

Conceptual local configuration:

```text
DJANGO_SETTINGS_MODULE=config.settings.local
CARE_ENVIRONMENT=dev

CARE_STORAGE_BACKEND=s3
CARE_TASK_BACKEND=celery
CARE_CACHE_BACKEND=redis
CARE_TRANSIENT_STATE_BACKEND=redis

BUCKET_ENDPOINT=http://minio:9000
BUCKET_KEY=minioadmin
BUCKET_SECRET=minioadmin
FILE_UPLOAD_BUCKET=patient-bucket
FACILITY_S3_BUCKET=facility-bucket

CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

REDIS_CACHE_URL=redis://redis:6379/1
REDIS_RATE_LIMIT_URL=redis://redis:6379/2
REDIS_TRANSIENT_STATE_URL=redis://redis:6379/3
```

The storage variables are the pre-existing ones documented in §13; `S3_*` names
are not accepted. The shared `BUCKET_*` values serve every alias unless an
alias-specific `FILE_UPLOAD_*` or `FACILITY_S3_*` value overrides them.

The implementation MAY preserve existing local `REDIS_URL` compatibility while
introducing more specific variables gradually.

Development defaults SHALL not flow into production settings.

---

# 46. Optional Upstash Profile

Conceptual example:

```text
CARE_TASK_BACKEND=cloud_tasks
CARE_CACHE_BACKEND=redis
CARE_TRANSIENT_STATE_BACKEND=redis

REDIS_CACHE_URL=rediss://...
REDIS_RATE_LIMIT_URL=rediss://...
REDIS_TRANSIENT_STATE_URL=rediss://...
```

The same URL MAY be reused initially.

The application SHALL not assume separate physical databases are supported or
necessary without checking the provider.

Namespaces or key prefixes SHALL isolate responsibilities.

---

# 47. Consolidated PostgreSQL Profile

Only if the PostgreSQL queue backend is approved:

```text
CARE_TASK_BACKEND=postgres
CARE_CACHE_BACKEND=postgres
CARE_TRANSIENT_STATE_BACKEND=postgres

REDIS_RATE_LIMIT_URL=rediss://<managed-redis>
```

Note that "consolidated PostgreSQL" does not mean Redis-free: rate limiting
still needs Redis (§26.1). This profile consolidates the queue, cache and
transient state — not the rate limiter, which is RF2
(`inventory/unresolved-items.md` Part RF) and is future work. Recent views is
already PostgreSQL and needs no variable at all (RF1).

This profile requires:

- queue schema;
- queue worker;
- worker health monitoring;
- task-record retention;
- additional Cloud SQL capacity planning.

It SHALL not be labeled scale-to-zero when immediate task execution requires an
active worker.

---

# 48. Test Profile

**Corrected 2026-08-07 to match the implementation.** Earlier revisions proposed
a `fake` task backend and a `filesystem` storage backend. Neither exists, and
neither turned out to be necessary.

The real test configuration is:

```text
CARE_TASK_BACKEND=celery      (the default; with CELERY_TASK_ALWAYS_EAGER=True)
CARE_STORAGE_BACKEND=s3       (the default, against the local MinIO)
```

`config/settings/test.py` sets `CELERY_TASK_ALWAYS_EAGER = True`, so a Celery
dispatch executes inline. That is a better test double than a `fake` backend
would be, because it exercises the real dispatch path.

`CARE_STORAGE_BACKEND` accepts only `s3` and `gcs` (§11.2); `filesystem` is not
one of them and would raise `ImproperlyConfigured` at startup. Tests that must
avoid a real bucket substitute `django.core.files.storage.InMemoryStorage`
through `override_settings` on `STORAGES`, which is a test-local override rather
than a configuration value.

Cloud Tasks is tested by mocking the client and asserting the request CARE
builds, so no test needs GCP credentials, a queue, or a network:

```text
care/utils/tests/test_task_dispatcher.py
care/utils/tests/test_cloud_tasks_backend.py
care/utils/tests/test_task_worker.py
```

Tests that need a specific storage provider use `override_settings(STORAGES=...)`
rather than a settings-wide backend value.

A `fake` backend would still be rejected by `validate_task_backend`, in test
settings as in production. Nothing needs it.

---

# 49. Deprecated Compatibility Variables

During implementation, CARE may temporarily continue accepting existing
variables such as:

```text
REDIS_URL
BUCKET_PROVIDER
BUCKET_REGION
BUCKET_KEY
BUCKET_SECRET
BUCKET_ENDPOINT
BUCKET_EXTERNAL_ENDPOINT
FILE_UPLOAD_BUCKET
FILE_UPLOAD_BUCKET_ENDPOINT
FILE_UPLOAD_BUCKET_EXTERNAL_ENDPOINT
FACILITY_S3_BUCKET
FACILITY_S3_BUCKET_ENDPOINT
FACILITY_S3_BUCKET_EXTERNAL_ENDPOINT
```

Compatibility behavior SHALL:

- emit deprecation warnings where safe;
- map old values to new settings only when unambiguous;
- avoid mixing old and new values silently;
- define precedence clearly;
- document eventual removal.

Because the production deployment is greenfield, new GCP environments SHOULD
use only the new variables.

---

# 50. Configuration Precedence

Recommended precedence:

1. explicit new configuration variable;
2. supported compatibility variable;
3. safe non-secret default;
4. configuration error.

If both new and old variables are set with conflicting values:

- the new value MAY take precedence;
- startup SHOULD emit a warning;
- production MAY reject the conflict to avoid ambiguity.

Secret values SHALL never be printed in warnings.

---

# 51. Safe Defaults

Safe defaults MAY exist for:

```text
log level
cache timeout
task delay
process role in local development
non-secret feature toggles
```

Defaults SHALL not exist for production:

```text
Django secret key
database password
SMTP password
Redis password
private signing keys
service-account credentials
production bucket names
production trusted origins
```

---

# 52. Prohibited Production Defaults

The GCP settings SHALL reject or warn critically about:

```text
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=*
DISABLE_RATELIMIT=true
public storage configuration
default MinIO credentials
localhost database URL
localhost Redis URL
local MinIO endpoint
service-account JSON bundled in image
CARE_LOG_REQUEST_BODIES=true
CARE_LOG_TASK_PAYLOADS=true
```

The exact enforcement MAY differ between `dev`, `staging` and `prod`.

---

# 53. Startup Validation

At startup, CARE SHOULD validate:

- selected backend names;
- required variables for each backend;
- mutually incompatible settings;
- role-specific requirements;
- production security values;
- storage aliases;
- cache table configuration;
- worker URL format;
- queue location and name;
- Redis URL scheme when Redis is selected.

Startup validation SHALL avoid making destructive calls.

External connectivity checks belong in readiness or diagnostics, not settings
parsing.

---

# 54. Configuration Diagnostics

An authorized management command SHOULD display effective non-secret
configuration.

Conceptual command:

```bash
python manage.py check_gcp_configuration
```

It MAY report:

```text
environment
process role
storage backend and aliases
task backend
cache backend
rate-limit backend
transient-state backend
report-progress backend
required service availability
health-check selection
```

It SHALL redact:

```text
passwords
secret keys
tokens
complete URLs containing credentials
private key material
```

---

# 55. Configuration Test Matrix

At minimum, automated tests SHALL validate:

| Profile | Tasks | Cache | Rate limits | State | Storage |
|---|---|---|---|---|---|
| GCP default (§44.1) | Cloud Tasks | PostgreSQL | Redis | PostgreSQL | GCS |
| GCP Redis | Cloud Tasks | Redis | Redis | Redis | GCS |
| Local | Celery | Redis | Redis | Redis | MinIO/S3 |
| GCP LocMem | Cloud Tasks | LocMem | Redis | PostgreSQL | GCS |
| Consolidated PostgreSQL | PostgreSQL queue | PostgreSQL | Redis | PostgreSQL | configured storage |
| Test | fake/eager | Dummy | test backend | test backend | filesystem |

The consolidated profile applies only if implemented.

**Corrected 2026-08-09.** The rate-limit column previously read `PostgreSQL` for
three profiles. No PostgreSQL rate-limit store exists (§26.1, §27); every profile
uses Redis. A row for the Redis-free profile of §44.2 is deliberately absent —
it cannot be tested because it does not exist, and it now arrives with RF2 alone
(RF1 shipped).

---

# 56. Configuration Change Procedure

Before changing production configuration:

1. identify affected services;
2. determine whether a new revision is required;
3. determine whether the value is secret;
4. test in staging;
5. review IAM and dependency implications;
6. deploy the new configuration;
7. run smoke tests;
8. monitor logs and metrics;
9. record the change.

Changing a backend value may require additional resources.

Example:

```text
CARE_CACHE_BACKEND=postgres -> redis
```

requires a valid Redis service and secret.

---

# 57. Backend Change Semantics

## Cache backend

Cache values are disposable.

Changing cache backend does not require state migration.

## Rate-limit backend

Changing backend resets or separates counters unless a deliberate state
transfer is implemented.

The operational effect SHALL be understood.

## Transient-state backend

Existing temporary state may become unavailable after a switch.

Correctness-critical state SHALL not rely on an unplanned backend switch.

## Task backend

Queued tasks do not automatically move between backends.

Backend changes SHALL occur only when the previous queue is empty or its
remaining work is intentionally handled.

For the initial greenfield launch, no legacy production queue exists.

## Storage backend

Storage objects do not automatically move between providers.

The greenfield GCP launch starts with empty GCS buckets.

After real use begins, changing storage requires a separate migration plan.

---

# 58. Configuration Ownership

Each configuration group SHOULD have an owner.

Suggested ownership:

```text
Django security -> application maintainers
Cloud Run -> platform maintainers
Cloud SQL -> database/platform maintainers
storage -> application and platform maintainers
tasks -> application and platform maintainers
Redis -> platform maintainers
email -> application operations
secrets -> security/platform maintainers
```

Ownership MAY be held by the same person in a small deployment, but
responsibilities SHALL remain explicit.

## 58.1 Delivery configuration is not application configuration (ES-08)

CI/CD needs a second, disjoint set of values: which project and region an
environment lives in, which services and Jobs it has, which identity to
authenticate as, which registry to publish to. None of it is read by the
application, none of it reaches the image, and none of it belongs in this
reference's tables.

It lives in GitHub repository and environment **variables**, and it is documented
in `docs/xii/architecture/08-continuous-delivery.md` section 3, with the IAM side
in `inventory/gcp-configuration.md` section 12.

Two rules worth stating here, because they are about configuration rather than
about workflows:

- **A project id and a region are not secrets.** Classifying them as secrets
  hides the configuration without protecting anything. No GitHub secret is
  required by any delivery workflow — authentication is a short-lived federated
  credential (ES-08 sections 72, 73).
- **Changing an environment value never requires rebuilding CARE.** Every value
  in this reference is supplied at deployment or runtime; that is what makes one
  artifact promotable across environments (ADR-0008 section 38).

---

# 59. Configuration Documentation Requirements

Each new variable SHALL document:

- name;
- purpose;
- whether required;
- whether secret;
- supported values;
- default;
- applicable roles;
- applicable environments;
- validation behavior;
- operational impact.

Undocumented production variables SHALL not be introduced casually.

---

# 60. Example API Service Configuration

Non-secret conceptual values:

```text
DJANGO_SETTINGS_MODULE=config.settings.deployment
CARE_ENVIRONMENT=prod
CARE_PROCESS_ROLE=api
DJANGO_DEBUG=false

CARE_STORAGE_BACKEND=gcs
CARE_TASK_BACKEND=cloud_tasks
CARE_CACHE_BACKEND=postgres
CARE_TRANSIENT_STATE_BACKEND=postgres
CARE_REPORT_PROGRESS_BACKEND=database_model

REDIS_RATE_LIMIT_URL=rediss://<managed-redis>

GCP_PROJECT_ID=care-production
GCP_REGION=us-central1
GCP_TASKS_LOCATION=us-central1
GCP_TASKS_QUEUE=care-default
GCP_WORKER_URL=https://care-prod-worker-...run.app/internal/tasks/execute/
GCP_TASKS_SERVICE_ACCOUNT=care-tasks-invoker@care-production.iam.gserviceaccount.com

CARE_PATIENT_STORAGE_BUCKET=care-prod-patient-files
CARE_FACILITY_STORAGE_BUCKET=care-prod-facility-files
CARE_REPORT_STORAGE_BUCKET=care-prod-reports

CARE_CACHE_TABLE=care_cache
CARE_LOG_FORMAT=json
CARE_LOG_LEVEL=INFO
```

Secrets:

```text
DJANGO_SECRET_KEY
DATABASE_URL
EMAIL_HOST_PASSWORD
JWKS_BASE64 or equivalent private material
SENTRY_DSN, when enabled
```

---

# 61. Example Worker Service Configuration

```text
DJANGO_SETTINGS_MODULE=config.settings.deployment
CARE_ENVIRONMENT=prod
CARE_PROCESS_ROLE=task_worker

CARE_STORAGE_BACKEND=gcs
CARE_TASK_BACKEND=cloud_tasks
CARE_CACHE_BACKEND=postgres
CARE_TRANSIENT_STATE_BACKEND=postgres
CARE_REPORT_PROGRESS_BACKEND=database_model

REDIS_RATE_LIMIT_URL=rediss://<managed-redis>

CARE_TASK_HANDLER_ENDPOINT_ENABLED=true
CARE_TASK_LOG_PAYLOAD=false

GCP_PROJECT_ID=care-production
GCP_REGION=us-central1

CARE_PATIENT_STORAGE_BUCKET=care-prod-patient-files
CARE_FACILITY_STORAGE_BUCKET=care-prod-facility-files
CARE_REPORT_STORAGE_BUCKET=care-prod-reports

CARE_LOG_FORMAT=json
CARE_LOG_LEVEL=INFO
```

The worker does not necessarily need queue-enqueue configuration unless tasks
can create follow-up tasks.

---

# 62. Example Initialization Job Configuration

```text
DJANGO_SETTINGS_MODULE=config.settings.deployment
CARE_ENVIRONMENT=prod
CARE_PROCESS_ROLE=init

CARE_STORAGE_BACKEND=gcs
CARE_CACHE_BACKEND=postgres

GCP_PROJECT_ID=care-production
GCP_REGION=us-central1

CARE_LOG_FORMAT=json
CARE_LOG_LEVEL=INFO
```

The initialization job may not require task-dispatch configuration.

The exact settings validation SHALL account for process role.

**ES-06 note, resolved 2026-08-09.** `CARE_CACHE_BACKEND=postgres` is shown here
and in sections 60 and 61 as the intended managed-cloud selection. It was
briefly **blocked** for any process that runs a management command:
`django_ratelimit`'s `E003` system check rejects every non-Redis `default`
cache, and `init` runs `manage.py` exclusively. Rate limiting now reads its own
`ratelimit` alias rather than `default`, so the block is gone — see
`inventory/unresolved-items.md` item L1.

Note the absence of `REDIS_RATE_LIMIT_URL` in the `init` configuration above. It
is deliberate and it is safe: `init` rate-limits nothing, the `E003` check reads
configuration rather than connectivity, and `createcachetable` walks `CACHES`
touching only `DatabaseCache` aliases. **The `init` role needs no reachable
Redis**, verified with an unreachable host and a `0` exit status.

---

# 63. Definition of Configuration Completion

Configuration implementation is complete when:

- GCP settings load with explicit validated values;
- the `init` role runs to completion with no reachable Redis;
- the default GCP profile needs Redis only for the capability named in §26.1
  and §22.2 — rate limiting — and not for ordinary caching, queueing, storage,
  locking, recent views or initialization. The original wording here was "starts
  without Redis"; that is not achievable *with the current rate limiter*,
  because `django-ratelimit` requires an atomic `INCR` that the Django cache API
  cannot express portably. It is achievable in principle, and the direction is
  recorded as RF2 in `inventory/unresolved-items.md` Part RF — future work,
  outside ES-04 through ES-07, and not a condition of completion here. Recent
  views left this list with RF1;
- local Celery, Redis and MinIO remain supported;
- GCS storage aliases resolve;
- MinIO aliases resolve locally;
- Cloud Tasks variables are required only when selected;
- PostgreSQL cache variables are validated;
- optional Redis responsibilities use independent URLs;
- role-specific services receive only required configuration;
- production rejects insecure defaults;
- diagnostics redact secrets;
- tests cover supported profile combinations;
- configuration documentation matches implementation.

---

## 64. Next Document

The next document is:

```text
docs/xii/architecture/08-terraform-architecture.md
```

It will define:

- Terraform repository structure;
- environment composition;
- modules;
- APIs;
- service accounts;
- IAM;
- Cloud SQL;
- Cloud Storage;
- Artifact Registry;
- Cloud Run services;
- Cloud Run Jobs;
- Cloud Tasks;
- Cloud Scheduler;
- Secret Manager;
- monitoring;
- state management;
- outputs;
- resource-protection rules.
