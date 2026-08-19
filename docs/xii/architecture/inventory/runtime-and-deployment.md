---
title: Runtime and Deployment Inventory
document: inventory/runtime-and-deployment
version: 0.3.0
status: Draft
phase: 3
source_repository: https://github.com/ohcnetwork/care
source_branch: gcp
source_commit: 6a2976dc2512c2c532fcc70628c5690fbbbe3f3d
baseline_commit: 2fe40cd16
reviewed: 2026-08-07
---

# Runtime and Deployment Inventory

## ES-05 locking update

Database initialization and request critical sections use PostgreSQL advisory
locks and do not require Redis for locking. A PostgreSQL connection and an
active transaction are required; lock contention returns the existing 423
application response and database failures propagate.

How CARE is built, started and tested today, plus the Phase 0 runtime baseline
(§11). Nothing in the runtime was modified in this phase.

Evidence labels: **verified** / **inferred** / **unknown**.

---

## 1. Process commands

**verified** Five entrypoint scripts define every process CARE runs.

| Process | Script | Command | Used by |
| --- | --- | --- | --- |
| API (prod) | `scripts/start.sh` | `gunicorn --config python:config.gunicorn config.wsgi:application --bind 0.0.0.0:9000 --chdir=/app --workers $GUNICORN_WORKERS` | `docker/prod.Dockerfile` |
| API (dev) | `scripts/start-dev.sh` | `python manage.py runserver_plus 0.0.0.0:9000 --print-sql` | `docker-compose.local.yaml:13` |
| Worker (prod) | `scripts/celery_worker.sh` | `celery --app=config.celery_app worker --max-tasks-per-child=6 --loglevel=info --concurrency=${CELERY_WORKER_CONCURRENCY:-1}` | — |
| Beat (prod) | `scripts/celery_beat.sh` | `celery --app=config.celery_app beat --loglevel=info` | — |
| Worker+Beat (dev) | `scripts/celery-dev.sh` | `watchmedo auto-restart ... celery ... worker -B --loglevel=INFO` | `docker-compose.local.yaml:30` |

**verified** ECS variants also exist: `scripts/start-ecs.sh`,
`scripts/celery_worker-ecs.sh`, `scripts/celery_beat-ecs.sh`.

**verified** `scripts/celery-dev.sh` runs `worker -B` — worker and beat in one
process. `scripts/celery_worker.sh` and `scripts/celery_beat.sh` separate them.

**verified** `Procfile` describes a third shape entirely:

```text
web: gunicorn config.wsgi:application
release: python manage.py collectstatic --noinput && python manage.py migrate
```

**verified** The `Procfile` `release` phase is the **only** place in the
repository where migrations are tied to a deploy step rather than to a
long-running process. It defines no worker.

---

## 2. Migration behavior

**verified** This is the single most important runtime fact for Cloud Run.

| Script | Runs `migrate`? | Line |
| --- | --- | --- |
| `scripts/start.sh` (API, prod) | **no** | — |
| `scripts/start-dev.sh` (API, dev) | **no** | — |
| `scripts/celery_worker.sh` | **no** | — |
| `scripts/celery_beat.sh` | **yes** | `python manage.py migrate --noinput` |
| `scripts/celery-dev.sh` | **yes** | `python manage.py migrate --noinput` |
| `Procfile` | yes, as `release` | line 2 |

**verified** In the Docker-based deployment, **schema migration is a side effect
of starting Celery Beat**. The API container never migrates.

**verified** `scripts/celery_beat.sh` and `scripts/celery-dev.sh` also run two
data-seeding commands after migrating:

```bash
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

**verified** `care/security/management/commands/sync_permissions_roles.py:14`
documents that concurrent runs are *"automatically blocked with redis"* — i.e.
this startup step depends on the distributed lock described in
`cache-and-redis.md` §4.2.

**inferred** Cloud Run has no beat process. Migrations and both sync commands
need an explicit home — a Cloud Run Job or a deploy step — or they will never
run. This is not a refactor; it is a gap that appears the moment beat is removed.

---

## 3. Startup dependencies

**verified** Every prod and dev entrypoint waits for **both** PostgreSQL and
Redis before starting:

| Script | `wait_for_db.sh` | `wait_for_redis.sh` |
| --- | --- | --- |
| `scripts/start.sh` | yes | **yes** |
| `scripts/start-dev.sh` | yes | **yes** |
| `scripts/celery_worker.sh` | yes | yes |
| `scripts/celery_beat.sh` | yes | yes |
| `scripts/celery-dev.sh` | yes | yes |

**verified** The API blocks on Redis at startup even though its only Redis use is
the cache and the token denylist.

**inferred** On Cloud Run this is a cold-start blocker: an instance cannot serve
until Redis answers. Combined with `IGNORE_EXCEPTIONS: True`
(`config/settings/base.py:93`), the runtime is inconsistent — it refuses to
*start* without Redis but silently tolerates Redis failing later.

**verified** `scripts/start.sh`, `celery_worker.sh` and `celery_beat.sh` all
synthesize connection URLs when unset:

```bash
export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
export REDIS_URL="rediss://:${REDIS_AUTH_TOKEN}@${REDIS_HOST}:${REDIS_PORT}/${REDIS_DATABASE}?ssl_cert_reqs=none"
```

**verified** The Redis URL uses the TLS scheme `rediss://` with
`ssl_cert_reqs=none` — TLS without certificate verification.

**Recommend** treating this as a defect to fix, not a baseline to carry forward.
`ssl_cert_reqs=none` encrypts the connection but authenticates nothing, so it
stops passive sniffing and not an active man-in-the-middle — which is most of
what TLS to a managed Redis endpoint is for. The target runtime SHALL verify
against the deployment CA (`ssl_cert_reqs=required` plus `ssl_ca_certs`, or the
system trust store where the provider uses a public CA). If verification must be
disabled anywhere, it SHALL be scoped to local development, where the endpoint
is a container on a private network and there is no CA to verify against.

---

## 4. Static files and i18n

**Corrected 2026-08-16.** Both commands now run at image build time. What this
section recorded — that they ran at container start, in five scripts, repeating
identical work on every cold start — was the defect filed as
`unresolved-items.md` L8 and is no longer true of the production image.

### 4.1 The asset lifecycle

```text
docker build
  builder  installs the venv and the plugins
  assets   FROM builder
           collectstatic   -> staticfiles/  (hashed names, manifest, .gz, .br)
           compilemessages -> locale/**/*.mo
  runtime  FROM base
           COPY --from=builder  .venv
           COPY .               the source
           COPY --from=assets   staticfiles/   <- only these two cross over
           COPY --from=assets   locale/

container start
  wait_for_db, wait_for_redis (when the configuration selects one)
  gunicorn / celery                            <- builds nothing
```

**verified** No production entrypoint runs either command:
`scripts/start.sh`, `scripts/start-worker.sh`, `scripts/celery_worker.sh` and
`scripts/celery_beat.sh`. Held in place by
`care/utils/tests/test_runtime_assets.py`.

**verified** `scripts/initialize.sh` still runs `compilemessages`. The `init`
role is a deployment step that runs once per release, not once per instance, so
it costs no cold start; it is left alone so a deployment that overlays its own
catalogues keeps working.

**verified** `whitenoise` is a dependency (`Pipfile`, `whitenoise = "==6.11.0"`),
so static files are served from the application process, out of the baked
`STATIC_ROOT`. `WHITENOISE_MANIFEST_STRICT = False` (`base.py`).

### 4.2 Production image versus local development

They are deliberately opposite, and neither is a mistake.

| | production image | local development |
| --- | --- | --- |
| Dockerfile | `docker/prod.Dockerfile` | `docker/dev.Dockerfile` |
| assets built | at image build, in `assets` | at container start, in `start-dev.sh` |
| source at runtime | copied into the image | bind-mounted from the host |
| when sources change | rebuild the image | already visible; restart to rebuild assets |

`docker-compose.local.yaml` mounts the working tree over `/app`, so anything a
dev image built would be shadowed by the host checkout; and the sources change
while the container runs, which is the case a build-time artefact cannot serve.
`scripts/start-dev.sh` therefore keeps both commands and says so in a comment.

### 4.3 What the build needs, and what it must not have

`DJANGO_SETTINGS_MODULE=config.settings.deployment` — the module the container
runs, so the manifest written is the one the application looks up. `production`
and `staging` derive from it and override nothing reaching `STATIC_ROOT`,
`STATICFILES_DIRS`, `STORAGES`, `LOCALE_PATHS` or `INSTALLED_APPS`.

`DATABASE_URL` — the only value `deployment.py` requires without a default.
`env.db()` parses it; nothing connects. The Dockerfile supplies an obvious
placeholder.

Nothing else. No secret is injected into a layer, no Cloud SQL, no GCP
credentials, no Redis, no network access.

`assets` is a separate stage rather than two steps in `runtime` because
importing the settings module writes to the filesystem: `deployment.py` eagerly
evaluates `get_jwks_from_file()`, which generates and writes a key set when
`jwks.b64.txt` is absent. In a discarded stage that write is harmless; in the
shipped image it would be a private key every instance shares.

### 4.4 Verifying a built image

```bash
docker run --rm --entrypoint bash <image> -c '
  ls /app/staticfiles/staticfiles.json
  find /app/staticfiles -name "*.br" | wc -l
  find /app/locale -name "*.mo"
'
```

On the 2026-08-16 acceptance image: 1099 files under `STATIC_ROOT`, a 193-entry
manifest, 354 `.gz`, 358 `.br`, four compiled catalogues. `collectstatic
--dry-run` inside the image reports `0 static files copied, 193 unmodified`.

Build from a clean export — `git archive HEAD` into an empty directory — rather
than from a working tree, or the image absorbs whatever untracked files the
builder has (`unresolved-items.md` P2). It is also the only way to confirm the
catalogues are really built: `.mo` files are gitignored, so a clean checkout has
none.

---

## 5. Health checks

**verified** `docker/prod.Dockerfile:65-70` declares:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=12 CMD ["./healthcheck.sh"]
```

**verified** `scripts/healthcheck.sh` dispatches on a role file written by each
entrypoint to `/tmp/container-role`:

| Role | Probe |
| --- | --- |
| `api` | `curl -fsS http://localhost:9000/ping/` |
| `celery-beat` | `ls /tmp/healthy` — a marker file touched before beat starts |
| `celery*` | `celery -A config.celery_app inspect ping -d celery@$HOSTNAME` |

**verified** Roles are written at the top of each script: `api`
(`start.sh`, `start-dev.sh`), `celery` (`celery-dev.sh`), `celery-worker`
(`celery_worker.sh`), `celery-beat` (`celery_beat.sh`).

**verified** The beat health check is a **liveness lie**: `touch /tmp/healthy`
happens *before* `celery beat` is exec'd in `scripts/celery_beat.sh`, so the file
persists even if beat dies.

**verified** The application-level health config is `HEALTHY_DJANGO` at
`config/settings/base.py:453-467`, with three probes: database, cache, and Celery
queue length. See `cache-and-redis.md` §4.9 — the third connects directly to
Redis and is meaningless under Cloud Tasks.

**inferred** For Cloud Run, only the `api` branch is relevant; `/ping/` is the
natural startup and liveness probe.

---

## 6. Images

**verified** Two Dockerfiles: `docker/dev.Dockerfile` and `docker/prod.Dockerfile`.

**verified** `docker/prod.Dockerfile` structure:

| Stage | Lines | Purpose |
| --- | --- | --- |
| `base` | 1-15 | `python:3.13-slim-bookworm`, env setup |
| `builder` | 19-39 | build deps, `pipenv install --deploy --categories "packages"`, plugin install |
| `runtime` | 42-72 | runtime deps, non-root `django` user, venv copy, healthcheck |

**verified** Notable facts:

- Base image `python:3.13-slim-bookworm` (`:1`) — matches `Pipfile`'s
  `python_version = "3.13"`.
- Runs as non-root `django` (`:44-45`, `:63`).
- `EXPOSE 9000` (`:72`).
- **No `CMD` or `ENTRYPOINT`.** The image declares neither; the orchestrator must
  supply the command. **inferred** Cloud Run requires an explicit container
  command, so each service must set it.
- WeasyPrint native deps (`libpango`, `libharfbuzz`) installed in both stages
  (`:23`, `:48`) — these are what make report generation work.
- Plugins are installed **at image build time** (`:34-39`) via
  `install_plugins.py`, parameterized by the `ADDITIONAL_PLUGS` build arg
  (`:37-38`).

**verified** The `CMD` entries at `docker/prod.Dockerfile:70` and
`docker/dev.Dockerfile:36` are the **`HEALTHCHECK` `CMD`**, not a container
command. Neither image declares a top-level `CMD` or `ENTRYPOINT`.

### 6.1 Production image availability

**verified** `.github/workflows/deploy.yml` publishes a production image:

| Fact | Evidence |
| --- | --- |
| Registry | `ghcr.io/${{ github.repository }}` → `ghcr.io/ohcnetwork/care` (`deploy.yml:64, 97`) |
| Dockerfile | `docker/prod.Dockerfile` (`deploy.yml:94`) |
| Architectures | `linux/amd64` + `linux/arm64` (`deploy.yml:44-49`) |
| Push mode | `push-by-digest=true,name-canonical=true,push=true` (`deploy.yml:97`) |
| Triggers | tags `v*`, pushes to `develop`, manual dispatch (`deploy.yml:3-11`) |
| Gate | `github.repository == 'ohcnetwork/care'` (`deploy.yml:31`) |

**inferred** A ready-made multi-arch production image exists upstream, so a GCP
deployment can consume `ghcr.io/ohcnetwork/care` directly rather than building
one — provided it supplies its own container command (§6) and its own migration
step (§2). Note the fork's images are **not** published: the gate at
`deploy.yml:31` restricts the job to the upstream repository.

**verified** The ECS deployment env block in `deploy.yml:19-29` is **entirely
commented out**, as is a further block at `deploy.yml:207-209`. **inferred** the
ECS deploy path is currently inactive upstream.

---

## 7. Compose topology

**verified** `docker-compose.yaml` defines three infrastructure services:

| Service | Image | Host port | Healthcheck |
| --- | --- | --- | --- |
| `db` | `postgres:17-alpine` | 5433→5432 | `pg_isready` |
| `redis` | `redis:8-alpine` | 6380→6379 | `redis-cli ping` |
| `minio` | `minio/minio:latest` | 9100→9000, 9001 | `/minio/health/ready` |

**verified** `docker-compose.local.yaml` adds `backend` and `celery`, both from
the `care_local` image built from `docker/dev.Dockerfile`.

**verified** `backend` depends on `celery` with `condition: service_healthy`
(`docker-compose.local.yaml:23-24`). **inferred** this ordering exists because
`celery-dev.sh` runs the migrations — the API waits for the schema.

**verified** MinIO buckets are created by `docker/minio/init-script.sh`, which
also sets them **public**: `mc anonymous set public local/$BUCKET_NAME`
(`init-script.sh:47`).

**verified** PostgreSQL in compose is **17**; the target described in the
architecture docs is Cloud SQL. Version parity is a deployment decision, not a
code constraint.

**verified** Additional compose files: `docker-compose.pre-built.yaml` and
`docker-compose.coolify.yaml`.

---

## 8. CI

**verified** `.github/workflows/` contains 8 workflows: `deploy.yml`, `docs.yml`,
`linter.yml`, `release.yml`, `reusable-test.yml`, `test-merge-queue.yml`,
`test-pull-request.yml`, `validate-pr-title.yml`.

**verified** `reusable-test.yml` is the test pipeline. Its ordered steps:

| Step | Command | Line |
| --- | --- | --- |
| Build image | `docker buildx build --file docker/dev.Dockerfile --tag care_local ... --platform linux/arm64` | 52-59 |
| Start services | `docker compose -f docker-compose.yaml -f docker-compose.local.yaml up -d --wait` | 63 |
| Check migrations | `make checkmigration` | 67 |
| Fixtures | `make load-fixtures` | 70 |
| Tests | `make test-coverage` | 77 |

**verified** Runner is `ubuntu-24.04-arm` (`:17`); CI builds and tests
**arm64 only** (`:57`).

**verified** `make checkmigration` → `python manage.py makemigrations --check --dry-run`
(`Makefile:47-48`). CI fails on uncommitted model changes.

**verified** `make test-coverage` → `coverage run manage.py test --settings=config.settings.test --keepdb --parallel --shuffle` (`Makefile:63-66`).

**verified** `config/settings/test.py:45-46` points the cache at
`django_redis.cache.RedisCache` on `REDIS_URL`, so **CI requires a live Redis**.

---

## 9. Dependencies

**verified** Manager: **Pipenv** (`Pipfile` + `Pipfile.lock`). No
`requirements.txt`, no Poetry, no uv.

**verified** Key pins:

| Package | Version | Relevance |
| --- | --- | --- |
| `python_version` | `3.13` | `Pipfile [requires]` |
| `django` | `==6.0` | |
| `celery` | `==5.6.0` | |
| `django-redis` | `==6.0.0` | supplies `delete_pattern`, `nx=`, `get_redis_connection` |
| `redis` | `==7.1.0` (extras `hiredis`) | |
| `boto3` | `==1.43.6` | only S3 client today |
| `psycopg` | `==3.3.2` (extras `c`) | |
| `gunicorn` | `==23.0.0` | |
| `whitenoise` | `==6.11.0` | |
| `django-ratelimit` | `==4.1.0` | |
| `healthy-django` | `==0.1.0` | |
| `weasyprint` | `==68.0` | report rendering |
| `drf-spectacular` | `==0.29.0` | |
| `sentry-sdk` | `==2.58.0` | |

**verified** `pyproject.toml:22` sets `requires-python = "==3.13.*"` and
`pyproject.toml:58` sets ruff `target-version = "py313"`.

**verified** **Absent** from `Pipfile`: `django-storages`, any
`google-cloud-*` package, `django-celery-beat`, `django-celery-results`.

**Superseded by IS-01 for the first two.** `Pipfile:55` now carries
`django-storages = {extras = ["s3", "google"], version = "==1.14.6"}`, which
brings `google-cloud-storage` in transitively. `django-celery-beat` and
`django-celery-results` remain absent.

**verified** `django-anymail` is installed with the `amazon-ses` extra.
**inferred** email delivery is AWS SES today; GCP has no drop-in equivalent, so
this needs an explicit decision.

---

## 10. Existing GCP-related code

**verified** A repository-wide search for `gcp`, `google.cloud`, `cloud run`,
`cloudsql`, `cloud_tasks`, `django-storages` and `django_storages` across
`*.py`, `*.yml`, `*.yaml`, `*.sh`, `Pipfile` and `*.toml`, excluding
`docs/xii/`, returns exactly **two** matches:

| File | Line | Content |
| --- | --- | --- |
| `care/utils/csp/config.py` | 20 | `GCP = "GCP"` — a `CSProvider` enum member |
| `care/emr/utils/file_manager.py` | 130 | `# bulk delete is not supported by some providers: GCP` |

**verified** The `CSProvider.GCP` member is **never branched on**.
`BUCKET_PROVIDER` is only ever compared against `CSProvider.AWS_ROLE_BASED`
(`care/utils/csp/config.py:35, 48, 62`).

**verified** There is no Terraform, no Cloud Build config, no `app.yaml`, no
`service.yaml`, and no GCP credentials handling anywhere in the repository.

**Conclusion (verified):** GCP support does not exist. This is genuinely
greenfield.

**Superseded by IS-01.** Both rows above are gone: `care/utils/csp/config.py` was
deleted with the provider-specific bucket configuration, and the `file_manager.py`
bulk-delete comment went with the boto3 code. Re-running the same search now
returns matches in `config/storage.py`, `config/settings/base.py`,
`care/utils/tests/test_storage_config.py` and `Pipfile` — the `gcs` backend
option and its tests.

**Superseded by ES-07 (2026-08-11).** Infrastructure now lives under
`infrastructure/terraform/` and deploys the Redis-free GCP profile with
OpenTofu. Cloud Run uses attached service accounts and ADC; no GCP credential
file is committed or injected. The application boundary remains unchanged:
GCP-specific lifecycle logic is infrastructure configuration, while storage is
selected through the existing `CARE_STORAGE_BACKEND=gcs` setting.

---

## 11. Baseline command results

**Status: GREEN.** Recorded 2026-08-06. The blockers listed in the previous
revision of this section are resolved; the record below supersedes them.

No destructive command was run: no volume was removed, no database was reset, no
container or image existed before the run. `docker volume ls`, `docker ps -a` and
`docker images` were all empty at the start, so every artifact below was created
by this baseline.

### 11.1 Environment

| Field | Value |
| --- | --- |
| Operating system | Microsoft Windows 11 Pro, version 10.0.26200 |
| Shell | PowerShell 5.1.26100.8875; Git Bash for the `docker compose` invocations |
| Docker Engine | client **29.6.2**, server **29.6.2** (Docker Desktop, WSL2 backend) |
| Docker Compose | **v5.3.1** |
| Container kernel | `Linux-6.6.114.1-microsoft-standard-WSL2-x86_64-with-glibc2.36` |
| Repository branch | `feature/gcp-phase-0-inventory` |
| Repository commit | `2fe40cd16` |
| Working tree | clean before and after |
| Python in image | 3.13.14 |
| Django in image | 6.0 |
| Plugins | none — `plug_config.py` declares `plugs = []`, `ADDITIONAL_PLUGS` unset |

**verified** Docker Desktop upgraded its own components when it was launched: the
CLI reported `29.5.2` / Compose `v5.1.4` before the daemon started and
`29.6.2` / `v5.3.1` afterwards. The versions in the table are the ones the
baseline actually ran on.

### 11.2 Environment files

**verified** No gitignored environment file had to be created. This corrects the
previous revision, which listed a missing `.env` as a blocker.

| File | Status | Role |
| --- | --- | --- |
| `docker/.local.env` | **tracked in git** | `env_file` for `backend` and `celery` (`docker-compose.local.yaml:10, 29`) |
| `docker/.prebuilt.env` | **tracked in git** | `env_file` for `db` (`docker-compose.yaml:11`) |
| `.env` (repository root) | gitignored, **absent, not required** | — |

**verified** There is no `docker/.local.env.example` and no
`docker/.prebuilt.env.example`. The two `.env` files are the real, committed
artifacts, not templates.

**verified** `docker compose config` resolves with **no** missing-variable
warnings without a root `.env`. Every interpolation in the compose files supplies
a default: `BACKUP_DIR` (`docker-compose.yaml:14`), `MINIO_ACCESS_KEY` and
`MINIO_SECRET_KEY` (`:42-43`), `POSTGRES_USER` (`:18`), and `ADDITIONAL_PLUGS`
(`docker-compose.local.yaml:8`).

**verified** Compose v5.3.1 interpolates values *inside* `env_file`. The literal
`BUCKET_KEY=${MINIO_ACCESS_KEY:-minioadmin}` at `docker/.local.env:14` arrives in
the container as `BUCKET_KEY=minioadmin`. Confirmed by reading the resolved
environment inside `backend`.

### 11.3 Commands executed

**verified** GNU Make is not installed on this host, so each `Makefile` target
was translated to the exact `docker compose` command it wraps. File order and
flags are unchanged from the `Makefile`.

| Step | `Makefile` target | Command executed |
| --- | --- | --- |
| Build | `build` (`:19-20`) | `docker compose -f docker-compose.yaml -f docker-compose.local.yaml build` |
| Start + wait | `up` (`:25-26`) | `docker compose -f docker-compose.yaml -f docker-compose.local.yaml up -d --wait` |
| Service state | `list` (`:41-42`) | `docker compose -f docker-compose.yaml -f docker-compose.local.yaml ps` |
| Migration check | `checkmigration` (`:47-48`) | `docker compose exec backend bash -c "python manage.py makemigrations --check --dry-run"` |
| Fixtures | `load-fixtures` (`:38-39`) | `docker compose exec backend bash -c "python manage.py load_fixtures"` |
| Tests | `test` (`:56-57`) | `docker compose exec backend bash -c "python manage.py test  --keepdb --parallel --shuffle"` |

**verified** The four `exec` targets in the `Makefile` pass **no** `-f` flags, so
they rely on Compose's default file resolution — which finds only
`docker-compose.yaml`, where `backend` is not defined. This works anyway:
Compose v5 `exec` resolves the container by project label (project name `care`,
derived from the directory), not by service presence in the loaded config.
Confirmed empirically — `docker compose exec backend bash -c "echo OK"` succeeds.
**inferred** This is an implicit dependency on Compose's lookup behaviour rather
than an intentional design, but it is not currently broken.

### 11.4 Build result

**verified** `care_local:latest` built successfully.

| Field | Value |
| --- | --- |
| Image | `care_local:latest` |
| Manifest list digest | `sha256:4ea640b476e9050288e7f98899d848a987339e56e78ff3c4b75b3a96a8b6f70b` |
| Size | 1.6 GB |
| Platform | `linux/amd64` |
| Dockerfile | `docker/dev.Dockerfile` |

**The first build attempt failed.** Classified as a **dependency-build** failure,
not an application defect:

```text
zipfile.BadZipFile: Bad CRC-32 for file '_brotli.cpython-313-x86_64-linux-gnu.so'
ERROR: Couldn't install package: {}
failed to solve: process "/bin/sh -c pipenv  install --system --categories \"packages dev-packages docs\""
  did not complete successfully: exit code: 1
```

A corrupted wheel had been written into the BuildKit pip cache mount declared at
`docker/dev.Dockerfile:22`. Because the cache mount persists across builds, a
plain retry would have reused the same corrupt file. The minimum correction was
to drop only the cache mounts —
`docker builder prune --filter type=exec.cachemount` (92.29 MB, all of it created
minutes earlier by that same failed build). No volume, container or image was
touched. The rebuild succeeded and the failure has not recurred. **inferred**
transient; no source change was made or needed.

### 11.5 Service health

**verified** All five services reached `healthy` under `up -d --wait`, and were
still healthy an hour later.

| Service | Container | Health | Ports |
| --- | --- | --- | --- |
| `db` | `care-db-1` | healthy | 5433→5432 |
| `redis` | `care-redis-1` | healthy | 6380→6379 |
| `minio` | `care-minio-1` | healthy | 9100→9000, 9001→9001 |
| `celery` | `care-celery-1` | healthy | — |
| `backend` | `care-backend-1` | healthy | 9000→9000, 9876→9876 |

**verified** `curl http://localhost:9000/ping/` inside `backend` returns
`{"status": "OK"}`.

### 11.6 Startup sequence

**verified** from container logs, in order.

`celery` (`scripts/celery-dev.sh`):

| Step | Evidence |
| --- | --- |
| Waited for PostgreSQL | `Waiting for PostgreSQL to become available...` ×2, then `PostgreSQL is available` |
| Waited for Redis | `Redis is available` |
| Ran migrations | `Running migrations:` — **312** `Applying ... OK` lines across `admin, auth, authtoken, contenttypes, emr, facility, security, sessions, sites, users` |
| Ran `sync_permissions_roles` | no stdout; verified by effect (§11.7) |
| Ran `sync_valueset` | no stdout; verified by effect (§11.7) |
| Worker started | banner `celery@b2c90b2c6f0b v5.6.0`, `concurrency: 16 (prefork)`, `transport: redis://redis:6379/0`, 8 registered tasks, `Connected to redis://redis:6379/0` |
| Beat started | `worker -B`; `/app/celerybeat-schedule`, `-shm` and `-wal` present and being written |

`backend` (`scripts/start-dev.sh`):

| Step | Evidence |
| --- | --- |
| Waited for PostgreSQL | `PostgreSQL is available` |
| Waited for Redis | `Redis is available` |
| `collectstatic` | `198 static files copied to '/app/staticfiles', 926 post-processed.` |
| Server started | `starting server...`, health check passing on `/ping/` |

**verified** The database did not exist beforehand; `scripts/wait_for_db.sh`
created it (`Creating Database` path) before migrations ran.

**verified — minor logging gap.** The `celery` log ends at
`mingle: all alone` and never emits the usual `celery@<host> ready.` line, nor
any `beat: Starting...` line. The worker is nonetheless live —
`celery -A config.celery_app inspect ping` returns `1 node online` — and beat is
live, evidenced by the schedule files above. **inferred** log truncation under
`watchmedo auto-restart`, not a process failure. Recorded so that a future reader
does not mistake the missing lines for a broken worker.

> **The inference was wrong, and it is fixed.** It was not truncation and had
> nothing to do with `watchmedo`. The local Celery containers run
> `config.settings.production` — `DJANGO_SETTINGS_MODULE` is unset for them, so
> the `setdefault` in `config/celery_app.py` applies — which imported a
> `LOGGING` config with `disable_existing_loggers: True`, and `django.setup()`
> disabled every logger Celery had built before it. Filed as
> `unresolved-items.md` L2 and closed on 2026-08-16. Both lines are back:
> `celery@<host> ready.` and `beat: Starting...`, each emitted once.

### 11.7 Migration and synchronization results

| Check | Result |
| --- | --- |
| `migrate` | **312** migrations applied, 0 errors |
| `makemigrations --check --dry-run` | `No changes detected` — no model drift |
| `sync_permissions_roles` | `security_permissionmodel` = **115**, `security_rolemodel` = **10**, `security_rolepermission` = **546** |
| `sync_valueset` | `emr_valueset` = **30** |

**verified** Neither sync command prints to stdout. Both run under
`set -euo pipefail` in `scripts/celery-dev.sh`, so a failure would have aborted
container startup; their success is additionally confirmed by the row counts
above, queried directly from `care-db-1`.

### 11.8 Fixture result

**verified** `python manage.py load_fixtures` completed successfully:
`All fixtures loaded successfully!`

Seventeen fixture groups loaded — organizations, facility, departments,
locations, devices, users, patients, encounters, facility organization
memberships, secondary facility, questionnaires, report templates, lab
definitions, inventory, billing, scheduling, managing organization.

Resulting counts: `users_user` = 10, `facility_facility` = 2,
`emr_organization` = 12. Ten test accounts are printed by the command; the
credentials are development-only and are not reproduced here.

### 11.9 Test results

**verified** Command:
`docker compose exec backend bash -c "python manage.py test  --keepdb --parallel --shuffle"`

Settings module is `config.settings.test`, selected automatically by
`manage.py:15-16`. `--parallel` used **16** workers (17 test databases including
the primary).

| Run | Shuffle seed | Tests | Pass | Fail | Skip | Test duration | Wall clock | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 8926493199 | 1912 | 1911 | 1 | 0 | 22.296 s | 138 s | 1 |
| 2 | 8078922123 | 1912 | 1912 | 0 | 0 | 29.482 s | 48 s | 0 |
| 3 | 3975608265 | 1912 | 1912 | 0 | 0 | 21.158 s | 37 s | 0 |

**Test count: 1912. Skipped: 0. Expected failures: 0. Warnings: none emitted by
the test runner.**

Run 1's longer wall clock includes first-time creation of the 17 test databases;
runs 2 and 3 reused them via `--keepdb`.

**Baseline verdict: green.** Two of three runs are fully clean; the single
failure in run 1 is a test-isolation flake in the suite itself, characterised
below, not a defect in the application under test.

### 11.10 Known defect — flaky rate-limit test

**verified** Run 1 failed one test:

```text
care/emr/tests/test_reset_password_api.py:375
  ResetPasswordAPITest.test_password_request_rate_limiting
AssertionError: 200 != 429
```

**verified** It passes deterministically in isolation, both as a single test and
as the whole module, run serially:
`python manage.py test care.emr.tests.test_reset_password_api --keepdb` → `Ran 23 tests ... OK`.

**verified** Mechanism — two facts combine:

1. `config/ratelimit.py:9`, `get_ratelimit_key`, returns the **constant** string
   `"ratelimit"`. The rate-limit counter is therefore process-global: it is not
   keyed by IP, user or test.
2. `config/settings/test.py:45-56` points the cache at **Redis**, shared by all
   16 parallel workers under a single `KEY_PREFIX` of `test_`. Meanwhile
   `cache.clear()` runs in `setUp` at `care/emr/tests/test_reset_password_api.py:24`
   and `care/emr/tests/test_valueset_api.py:23, 52`.

**inferred** A concurrent worker calling `cache.clear()` wipes the shared global
counter partway through the test's 11-request loop, so the final request returns
200 instead of 429. `--shuffle` decides whether the interleaving happens, which
is why the failure is intermittent.

**Not fixed.** This is a pre-existing upstream test-isolation defect, unrelated to
GCP work, and fixing it would be an application change outside the scope of
recording a baseline. It is reproducible in principle on upstream CI, which runs
the same `--parallel --shuffle` combination via `make test-coverage`
(`.github/workflows/reusable-test.yml:77`).

**inferred, relevant to the target runtime:** the global rate-limit key is also a
correctness concern beyond tests — it means the limit is shared across all
callers, not per client. Recorded here as an observation only.

### 11.11 Reproducing this baseline

```bash
# 1. build
docker compose -f docker-compose.yaml -f docker-compose.local.yaml build

# 2. start and wait for health
docker compose -f docker-compose.yaml -f docker-compose.local.yaml up -d --wait

# 3. confirm state
docker compose -f docker-compose.yaml -f docker-compose.local.yaml ps

# 4. migrations already ran in the celery container; confirm no drift
docker compose exec backend bash -c "python manage.py makemigrations --check --dry-run"

# 5. fixtures
docker compose exec backend bash -c "python manage.py load_fixtures"

# 6. tests
docker compose exec backend bash -c "python manage.py test  --keepdb --parallel --shuffle"
```

No `.env` file is needed. No teardown, volume deletion or database reset is
required or advised — `make teardown` (`Makefile:34-35`) and `make reset-db`
(`:76-78`) are destructive and were deliberately not used.

---

## 12. Runtime facts most relevant to Cloud Run

**verified**, ordered by how much they constrain the design:

1. **Migrations run only in Celery Beat startup** (§2). Removing beat removes
   migrations.
2. **`sync_permissions_roles` and `sync_valueset` run at beat startup** (§2) and
   the first depends on a Redis lock.
3. **The API blocks on Redis before serving** (§3).
4. **The prod image declares no `CMD`** (§6). Every Cloud Run service must set one.
5. **`collectstatic` and `compilemessages` run per cold start** (§4).
6. **Celery timezone is hardcoded to `Asia/Kolkata`** (`config/celery_app.py:16`);
   any Cloud Scheduler translation must account for IST.
7. **The Celery queue-length health check binds to Redis** (§5).
8. **CI builds arm64 only** (§8); Cloud Run defaults to amd64.
9. **`django-anymail[amazon-ses]`** (§9) ties email to SES.

---

## 13. Runtime changes in ES-03

Recorded 2026-08-07. Section 12 listed nine facts constraining the Cloud Run
design. This section records which of them ES-03 addressed and which it left.

### 13.1 Migrations no longer belong to Celery Beat

**verified** §2 recorded the single most important runtime fact: schema
migration was a side effect of starting Celery Beat, and the API container never
migrated. Removing beat removed migrations.

**Resolved.** The sequence moved to `scripts/initialize.sh`:

```bash
python manage.py migrate --noinput
python manage.py compilemessages -v 0
python manage.py sync_permissions_roles
python manage.py sync_valueset
```

**verified** Both `scripts/celery_beat.sh` and `scripts/celery-dev.sh` now call
it rather than inlining the four commands, so local Compose keeps its existing
startup ordering exactly -- `backend` still waits on `celery` for the schema.
Confirmed by restarting the stack: all five services healthy, `migrate` reported
`No migrations to apply`, `makemigrations --check` clean, and the two sync
commands produced their expected row counts (115 permissions, 10 roles, 30
value sets).

**verified** The script is `0755` in the index and lands at `$APP_HOME` in the
production image through `COPY --chmod=0755 ./scripts/*.sh`
(`docker/prod.Dockerfile:59`), so a Cloud Run Job can invoke it directly.

**What remains local-only, explicitly:** the beat entrypoints still run it.
That is deliberate -- removing it would break upstream development for no
benefit in this phase -- but nothing in the target runtime depends on it. The
API and task-worker roles do not run it, and ADR-0003 requires that ordinary
instance startup never migrate, because Cloud Run starts instances concurrently.

### 13.2 A third process role exists

**verified** `CARE_PROCESS_ROLE` accepts `api`, `task_worker`, `job` and
`celery_worker`, defaulting to `api`. It selects route availability and logging
metadata only.

**verified** `CARE_TASK_HANDLER_ENDPOINT_ENABLED` defaults to true only for
`task_worker`. `config/urls.py` registers `internal/tasks/execute/` only when it
is set, so the public API service does not route the internal endpoint at all.
Asserted both ways in `WorkerRouteSeparationTests`.

**Not addressed:** the image still declares no `CMD` (§6), so each Cloud Run
service must supply its own command. That is ES-06 work.

### 13.3 Celery is unchanged

**verified** Against a running worker after the change:

```text
care.emr.models.resource_category.summarise_monetary_components
care.emr.tasks.cleanup_expired_token_slots.cleanup_expired_token_slots
care.emr.tasks.cleanup_incomplete_file_uploads.cleanup_incomplete_file_uploads
care.emr.tasks.report_generation.generate_report_task
care.emr.tasks.totp.send_totp_disabled_email
care.emr.tasks.totp.send_totp_enabled_email
1 node online.
```

Six names, all identical to before. The two that disappeared -- `handle_cascade`
and `rebalance_account_task` -- were never dispatched by anything.

**verified** Beat cadence is unchanged: daily at midnight IST for token slots,
every `FILE_UPLOAD_EXPIRY_HOURS` hours for uploads.

### 13.4 Unchanged, and still constraining

| # from §12 | Fact | Status after ES-03 |
| --- | --- | --- |
| 3 | The API blocks on Redis before serving | unchanged; C3 still open |
| 4 | The prod image declares no `CMD` | unchanged; ES-06 |
| 5 | `collectstatic` and `compilemessages` run per cold start | unchanged |
| 6 | Celery timezone hardcoded to `Asia/Kolkata` | unchanged; C7 still open |
| 7 | The Celery queue-length health check binds to Redis | unchanged; C5 still open. Under `CARE_TASK_BACKEND=cloud_tasks` there is no Redis queue, so `HEALTHY_DJANGO` would report unhealthy. ES-03 changed no health check. |
| 8 | CI builds arm64 only | unchanged |
| 9 | `django-anymail[amazon-ses]` ties email to SES | unchanged; C6 still open |

### 13.5 Dependencies

**verified** Added to `[packages]`:

| Package | Version | Why |
| --- | --- | --- |
| `google-cloud-tasks` | `==2.24.0` | the Cloud Tasks dispatch backend |

**verified** Three transitive additions came with it: `grpcio==1.83.0`,
`grpcio-status==1.83.0`, `grpc-google-iam-v1==0.14.5`.

**verified** No existing pin moved. A plain `pipenv lock` re-resolved the whole
graph and moved 43 unrelated transitive versions, including `rpds-py` from
`0.30.0` to `2026.6.3`; ES-03 §32 forbids unrelated upgrades, so that resolution
was discarded and only the four new entries were grafted onto the existing
lockfile. Validated with `pipenv install --deploy`, which verifies the lock
against the `Pipfile` hash, then by rebuilding the image and running the suite.

**verified** Nothing was removed. Celery, Redis clients and `boto3` all remain.

### 13.6 Regression result

**verified** Recorded on `feature/async-runtime-modernization` at `b8b7b934a`,
same host and Docker versions as §11.1.

| Run | Mode | Seed | Tests | Pass | Fail | Skip | Duration | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | serial `--shuffle` | 4814652866 | 2106 | 2106 | 0 | 0 | 186.6 s | 0 |
| 2 | `--parallel --shuffle` | 2339620662 | 2106 | 2106 | 0 | 0 | 37.0 s | 0 |
| 3 | `--parallel --shuffle` | 6536861927 | 2106 | 2106 | 0 | 0 | 23.4 s | 0 |
| 4 | `--parallel --shuffle` | 8675247865 | 2106 | 2106 | 0 | 0 | 24.3 s | 0 |

**Serial is green**, which is the binding requirement.

**verified** 2106 tests, of which **87 are new in ES-03**. The pre-ES-03 count on
this branch point is therefore 2019, up from the Phase 0 baseline of 1912 through
ES-01 and ES-02.

**E7 did not fire in any of the three parallel runs.** That is worth stating
carefully: E7 is intermittent and was observed at 5-in-6 during ES-01, so three
clean runs is not evidence that it is fixed. Nothing in ES-03 touches the cache,
the rate limiter or the favorites viewset, and the shared `KEY_PREFIX` that
causes it is unchanged. It remains open.

---

## 14. Runtime changes in ES-04

Recorded 2026-08-09 on `feature/cache-modernization`.

### 14.1 Cache became a runtime choice

`CARE_CACHE_BACKEND` selects `postgres`, `redis`, `locmem` or `dummy`, defaulting
to `redis` so an unconfigured checkout is unchanged. Only the selected backend's
variables are validated: a PostgreSQL-cache deployment needs no Redis URL.

Two aliases are **not** selected by it and remain Redis in every profile —
`locks` (needs `SET ... NX`) and `recent_views` (needs list commands). They read
`REDIS_URL` rather than `REDIS_CACHE_URL`, and both set `IGNORE_EXCEPTIONS` to
false so a failure cannot be read as success.

**Superseded since.** Neither alias exists any more. ES-05 replaced cache locking
with PostgreSQL advisory locks, removing `locks`; RF1 replaced the recent-views
lists with `emr.UserValueSetRecentView`, removing `recent_views` along with the
`build_redis_only_cache` helper that built both. A `ratelimit` alias was added by
the ES-04/L1 follow-up; RF2 then made it selectable through
`CARE_RATE_LIMIT_BACKEND` — Redis (strict, default), a dedicated `DatabaseCache`
table (best-effort, Redis-free), or absent entirely. Under Redis it keeps
`IGNORE_EXCEPTIONS: True` so an outage fails closed to captcha rather than to a
500; under PostgreSQL the wrapper reaches the same challenge by catching
`DatabaseError`. No alias in `CACHES` is Redis-only any more.

**verified end to end.** With Redis stopped and `CARE_CACHE_BACKEND=postgres`, a
cache round trip succeeds and cache health reports 200. Re-verified for RF1 with
the Redis container stopped: the recent-views suite is green and the recent-views
endpoints serve normally. Re-verified for RF2 with `postgres` + `postgres` +
`cloud_tasks` and every Redis URL unroutable: the API starts, `/health/` returns
200, and the login limiter admits five attempts and returns 429 on the sixth,
with no Redis connection attempted. Redis is optional throughout and required
only where selected — the runtime says so rather than degrading quietly.

### 14.2 Initialization gained a step

`scripts/initialize.sh` now runs `createcachetable` directly after `migrate`. It
is called unconditionally and is inherently conditional: with no table-name
argument the command acts only on `DatabaseCache` aliases, so it does nothing
unless the postgres cache is selected. It opens no Redis connection, so a
PostgreSQL-cache environment initializes with no broker running.

**verified** `bash -x scripts/initialize.sh` runs all five steps and exits 0.
Under `redis` the cache table is not created; under `postgres` it is.

### 14.3 E7 is resolved

The root cause was not the rate-limit key. All 16 `--parallel` workers shared one
Redis database, and `django_redis`'s `clear()` is `FLUSHDB`, which empties the
database and ignores `KEY_PREFIX`. The test profile now uses LocMem for the
`default` cache — each worker is its own process — and the two Redis-only aliases
are namespaced per worker with a `KEY_FUNCTION`.

The §11 note above should be read with this correction: the shared `KEY_PREFIX`
was not merely unchanged, it was **inert**. It sat inside `OPTIONS`, and
`BaseCache` reads `KEY_PREFIX` from the top level.

### 14.4 Baseline command results

Local Docker Compose, restarted clean, initialization run, `--keepdb`.

| # | Command | Seed | Tests | Passed | Failed | Errors | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `--shuffle` (serial) | 4565933570 | 2240 | 2240 | 0 | 0 | 156.1 s |
| 2 | `--parallel --shuffle` | 9051344208 | 2240 | 2240 | 0 | 0 | 43.1 s |
| 3 | `--parallel --shuffle` | 5340382458 | 2240 | 2240 | 0 | 0 | 35.1 s |
| 4 | `--parallel --shuffle` | 9388364516 | 2240 | 2240 | 0 | 0 | 38.2 s |
| 5 | `--parallel --shuffle` | 7326946839 | 2240 | 2240 | 0 | 0 | 34.3 s |
| 6 | `--parallel --shuffle` | 5534949877 | 2240 | 2240 | 0 | 0 | 30.5 s |
| 7 | `--parallel --shuffle` | 5139346334 | 2240 | 2240 | 0 | 0 | 30.2 s |

Parallel worker count: 16. Skipped: 0.

**verified** 2240 tests, of which **130 are new in ES-04**, from 2110 at the
ES-03 merge point.

**Six parallel runs, six green.** ES-01 measured E7 at 1 green in 6 on the same
command, and the three cache-touching modules alone failed 4 of 4 immediately
before the fix and passed 4 of 4 immediately after. Unlike the ES-03 note above,
this is evidence: the defect was reproduced on demand, the mechanism was
identified, and the reproduction no longer fires.

---

## 15. Runtime changes in ES-06

Recorded 2026-08-09 on `feature/runtime-roles`. This section supersedes §1, §2,
§3 and §5 wherever they conflict; the earlier text is left in place as the
record of what the runtime was before roles existed.

### 15.1 Re-verified process inventory

Every startup path in the repository, as it stands after ES-06. The previous
inventory listed five entrypoints; there are now eight, because two roles that
had been implicit — the HTTP task worker and the local scheduler — became
explicit.

| Entrypoint | Role | Long-running | Routes served | migrate | createcachetable | sync_permissions_roles | sync_valueset | collectstatic | compilemessages | Celery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scripts/start.sh` | `api` | yes | public API + diagnostics | no | no | no | no | no | no | no |
| `scripts/start-dev.sh` | `api` | yes | public API + diagnostics | no | no | no | no | **yes** | **yes** | no |
| `scripts/start-worker.sh` | `task_worker` | yes | task endpoint + diagnostics | no | no | no | no | no | no | no |
| `scripts/celery_worker.sh` | `task_worker` | yes | none (broker) | no | no | no | no | no | no | worker |
| `scripts/celery-dev.sh` | `task_worker` | yes | none (broker) | no | no | no | no | no | no | worker |
| `scripts/celery_beat.sh` | `scheduler` | yes | none | no | no | no | no | no | no | beat |
| `scripts/celery_beat-dev.sh` | `scheduler` | yes | none | no | no | no | no | no | no | beat |
| `scripts/initialize.sh` | `init` | **no** | none | **yes** | **yes** | **yes** | **yes** | no | **yes** | no |
| `Procfile` `web` | `api` | yes | public API + diagnostics | no | no | no | no | no | no | no |
| `Procfile` `release` | `init` | no | none | yes | yes | yes | yes | yes | yes | no |

**Updated 2026-08-16.** The `collectstatic` and `compilemessages` columns were
`yes` for every long-running entrypoint until L8 was closed; they are built into
the image now (§4). `scripts/start-dev.sh` is the one long-running entrypoint
that still builds them, because local development bind-mounts the source over
`/app` and a build-time artefact would be shadowed.

**verified** The `-ecs` scripts remain thin `exec` wrappers over `start.sh`,
`celery_worker.sh` and `celery_beat.sh` and inherit their roles unchanged.

### 15.2 Migration behavior: the beat coupling is gone

§2 recorded the single most important runtime fact — schema migration was a side
effect of starting Celery Beat. ES-03 moved the sequence into
`scripts/initialize.sh` but left the beat entrypoints calling it. ES-06 removed
those calls.

**verified** No long-running entrypoint contains `migrate`, `createcachetable`,
`sync_permissions_roles`, `sync_valueset` or `initialize.sh`. Asserted in
`care/utils/tests/test_runtime_roles.py::InitializationSeparationTests` against
the script sources with comments stripped.

**verified** `Procfile` `release` was `collectstatic && migrate`, which ran one
of the five initialization steps. It now runs `collectstatic` and the whole
`scripts/initialize.sh` sequence.

**Consequence for traditional deployments, stated plainly.** A deployment that
relied on starting Celery Beat to migrate will no longer migrate. Initialization
must become an explicit step: run `scripts/initialize.sh` before starting or
promoting the application. This is a deliberate breaking change required by
ADR-0006, and it is the same change managed-cloud deployment requires.

### 15.3 Startup dependencies: Redis is conditional

§3 recorded that every entrypoint blocked on Redis before serving, and called it
a Cloud Run cold-start blocker.

**Resolved.** `scripts/wait_for_redis.sh` now waits only when the selected
configuration needs Redis in order to start:

```text
CARE_CACHE_BACKEND=redis   the default cache is Redis
CARE_TASK_BACKEND=celery   the broker is Redis
```

With neither selected it exits 0 after logging both values. The defaults are
unchanged — `redis` and `celery` — so local and traditional deployments wait
exactly as before.

**verified** `init` runs to completion with `REDIS_URL` pointed at a closed port
and `CARE_TASK_BACKEND=cloud_tasks`: the whole sequence succeeds and the process
exits 0. Nothing in initialization opens a Redis connection.

**Honestly recorded, and now closed.** Between the ES-04/L1 follow-up and RF2,
the `ratelimit` alias was Redis in every configuration. It backed request
handling rather than process startup, so its absence degraded the affected
endpoints instead of preventing the API from serving, which is why it was never
a reason to block startup.

The `recent_views` alias was in the same position until RF1 replaced it with a
PostgreSQL model. `ratelimit` left the list with RF2, which made the store
selectable rather than replacing the library.

Startup is Redis-conditional and the API's *capability* surface no longer
contains a mandatory Redis item. A Redis-free deployment profile is a current
option: `CARE_CACHE_BACKEND=postgres` + `CARE_RATE_LIMIT_BACKEND=postgres` +
`CARE_TASK_BACKEND=cloud_tasks`. Redis is still required by the Celery broker
and by strict rate limiting, both of which are selections.

### 15.4 Route isolation

**verified** by resolution against each role's URLconf, and confirmed live
against running processes:

| Path | `api` | `task_worker` | `scheduler` / `init` |
| --- | --- | --- | --- |
| `/`, `/ping/`, `/health/`, `/app_version/` | yes | yes | yes |
| `/api/v1/...` | yes | **no** | **no** |
| `/admin/` | yes | **no** | **no** |
| `/api/schema/`, `/swagger/`, `/redoc/` | yes | **no** | **no** |
| `api/{plug}/...` | yes | **no** | **no** |
| `/internal/tasks/execute/` | **no** | yes | **no** |

**verified live.** Against the running API: `/ping/` 200, `/health/` 200,
`/api/v1/users/` 403 (route present, authentication required),
`/internal/tasks/execute/` **404**. Against a `task_worker` gunicorn started
from the same image: `/ping/` 200, `/health/` 200,
`/internal/tasks/execute/` 405 for `GET` (route present, method rejected), and
`/api/v1/users/`, `/api/v1/auth/login/`, `/admin/`, `/swagger/` all **404**.

The four diagnostic routes are shared deliberately, and that is the exact shared
scope. `home` is among them for a concrete reason: every error template extends
`base.html`, which reverses `home`, so a worker without that route would answer
an ordinary 404 with `NoReverseMatch`.

### 15.5 Health checks

§5 recorded three fixed probes — database, cache and Celery queue length — and
noted that the third connects directly to Redis and is meaningless under Cloud
Tasks.

**Resolved.** `HEALTHY_DJANGO` is composed by `config.health.build_health_checks`
from the role and the selected backends:

| Role | Probes |
| --- | --- |
| `api` | database, cache, Celery queue length *only when* `CARE_TASK_BACKEND=celery` |
| `task_worker` | database, cache, task registry |
| `scheduler` | database, Celery queue length *only when* `CARE_TASK_BACKEND=celery` |
| `init` | none |

**verified live.** The API role under the local profile reports three probes,
all 200. A `task_worker` process reports database, cache and
`{"registered_tasks": 6}`, and no Celery queue probe even under
`CARE_TASK_BACKEND=celery` — delivery to a worker is inbound, so a worker
probing the queue would be reporting on a dependency it does not use to receive
work. Under `CARE_TASK_BACKEND=cloud_tasks` the API reports database and cache
only.

**Container probes.** `scripts/healthcheck.sh` now dispatches on
`/tmp/container-probe`, which each entrypoint writes, with values `http`,
`celery` and `beat`. The probe follows the *transport*, not the role: an HTTP
task worker and a Celery task worker carry the same role and answer on different
channels. The `init` role writes no probe file and has none.

**Unchanged:** the beat probe is still `touch /tmp/healthy` before beat is
exec'd, so it proves the container started rather than that beat is alive. §5
called this a liveness lie and it remains one; see `unresolved-items.md` L3 for
why no replacement was invented.

### 15.6 Startup logging

Each long-running process logs one line naming its role and the three backend
names — no URL, credential or connection string:

```text
CARE runtime: process_role=api storage_backend=s3 task_backend=celery cache_backend=redis
```

**verified** for `api` (gunicorn and `runserver_plus`, through `config/wsgi.py`),
for `task_worker` over HTTP (same path), and for `task_worker` over Celery
(through the `celeryd_init` signal).

**Not visible for `scheduler`.** The line is emitted — the `beat_init` receiver
runs, confirmed by probe — but no log record of any level reaches stderr from a
Celery Beat process in this repository. Celery's own `beat: Starting...` line is
missing for the same reason, which §11.6 recorded as unexplained log truncation.
The cause is now identified: see `unresolved-items.md` L2.

### 15.7 Images

§6 recorded that the production image declares no `CMD`, and §13.2 listed that
as ES-06 work. It is **unchanged and deliberate**: four roles run from one
image, and the orchestrator supplies the command. §7.2 of the configuration
reference now lists the entrypoint for each role, which is what the absent `CMD`
requires a deployment to know.

**verified same-image execution.** Every local service runs `care_local`:
`init`, `backend` (api), `celery` (task_worker) and `beat` (scheduler). The HTTP
task worker was additionally started from the same image by role and command
alone. No role required a rebuild and none needs a distinct image.

`scripts/start.sh` and `scripts/start-worker.sh` bind `0.0.0.0:${PORT:-9000}`
rather than a fixed 9000, so a platform that assigns the port can use the same
image without a wrapper. The default is unchanged.

### 15.8 Compose topology

§7 recorded `backend` and `celery`, with `backend` waiting for `celery` to
become healthy because `celery-dev.sh` ran the migrations.

`docker-compose.local.yaml` now defines four application services, one per role:

| Service | Role | Notes |
| --- | --- | --- |
| `init` | `init` | ephemeral; runs `wait_for_db.sh` then `initialize.sh`, then exits |
| `backend` | `api` | depends on `init: service_completed_successfully` |
| `celery` | `task_worker` | `celery worker`; no longer `worker -B` |
| `beat` | `scheduler` | new; `celery beat` |

**verified** `docker compose up -d --wait` handles the one-shot `init` service
correctly: it runs, exits 0, and the three long-running services then start and
reach healthy. `make up` is unchanged.

**verified** Initialization failure propagates. With `DATABASE_URL` pointed at a
closed port, `scripts/initialize.sh` fails at `migrate`, runs no later step, and
exits **1**.

### 15.9 Managed-cloud composition, verified without deploying anything

**verified** With `CARE_PROCESS_ROLE=api`, `CARE_TASK_BACKEND=cloud_tasks`,
`CARE_STORAGE_BACKEND=gcs` and `CARE_CACHE_BACKEND=postgres` plus the Cloud
Tasks variables, settings load, the public API resolves, the internal task route
does not, and health reports database and cache only.

**verified** With `CARE_PROCESS_ROLE=task_worker` and the same backends, the
internal task route resolves, no public route resolves, and health reports
database, cache and task registry.

**Blocked at the time, and not by the role architecture.** Under
`CARE_CACHE_BACKEND=postgres` no management command ran at all:
`django_ratelimit`'s `E003` system check rejects every non-Redis `default`
cache, and the `init` role is management commands exclusively. Recorded as
`unresolved-items.md` L1 and fixed by the follow-up that gave rate limiting its
own alias. RF2 later made that alias's backend selectable as well, and `E003` is
now silenced only under `CARE_RATE_LIMIT_BACKEND=postgres`, where CARE knowingly
accepts what it reports.

### 15.10 Regression result

Recorded on `feature/runtime-roles`, same host and Docker versions as §11.1.

| Run | Mode | Seed | Tests | Pass | Fail | Skip | Duration | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | serial `--shuffle` | 8706916825 | 2282 | 2282 | 0 | 0 | 167.0 s | 0 |
| 2 | `--parallel --shuffle` | 1498679159 | 2282 | 2282 | 0 | 0 | 42.0 s | 0 |
| 3 | `--parallel --shuffle` | 7138364412 | 2282 | 2282 | 0 | 0 | 44.3 s | 0 |
| 4 | `--parallel --shuffle` | 3179802003 | 2282 | 2282 | 0 | 0 | 40.2 s | 0 |

Parallel worker count: 16. Skipped: 0. E7 did not recur; the ES-04 fix holds.

**verified** 2282 tests. The pre-ES-06 count was measured rather than assumed:
the suite was run at the branch point (`5066cfebb`) and reported **2234**. The
difference is the 48-test runtime-roles module, one test added to the
worker-route module and one dispatcher test removed when role validation moved
out of the task-backend module.

---

## 16. Runtime changes in the pre-staging hardening branch

Three findings from the ES-07 deployment, closed together because they were all
first observed in the same failure: an email task that could not succeed,
retried for hours, with the reason missing from the logs.

### 16.1 Assets are built once, by the image

See §4, rewritten. In short: `docker/prod.Dockerfile` gained an `assets` stage;
production entrypoints build nothing; `scripts/start-dev.sh` still does, because
local development mounts the source over `/app`.

Cold start on Cloud Run, before and after, same environment and same day:

```text
                     before      after
care-dev-api          38.7s       4.3s     startup probe 4 attempts -> 1
care-dev-worker       37.5s       5.4s     startup probe 4 attempts -> 1
```

The startup probe budget (`initial_delay 10s + 24 x 10s`) is unchanged. It was
not retuned downwards: an unused threshold costs nothing and an exhausted one
kills an instance.

### 16.2 Logging: what a deployed process actually emits

`config/settings/deployment.py` sets `disable_existing_loggers: False` and
declares the `django` logger explicitly. Before that, `django.request`,
`django`, every `celery.*` logger and every logger built during settings import
were disabled by `django.setup()`, and a disabled logger drops records before
any handler runs — including the root handler.

What this means operationally:

- an unhandled view exception now appears, with type, message and traceback;
- a chained exception shows both halves and the `direct cause` line, so a
  translated failure still names the provider error underneath it;
- each record is emitted **once**. The explicit `django` entry is what
  guarantees that: Django's `DEFAULT_LOGGING` attaches a DEBUG-gated console
  handler to `django`, which would print a second copy wherever `DEBUG` is on —
  the local Celery containers, for instance — and `mail_admins`, which would
  make every 500 attempt an SMTP connection nothing will answer;
- Cloud Run still splits a multi-line message into one entry per line. That is
  ingestion, not Django. The entry carrying `<ExceptionType>: <message>` is now
  among them, which is the part that was missing.

Nothing was added: no framework, no agent, no provider-specific call. Logs go to
stdout and stderr and Cloud Run forwards them.

`care/utils/tests/test_deployment_logging.py` asserts these in a subprocess
under the deployment settings, because a dictConfig is process-global.

### 16.3 Email task failure semantics

An email task can now end three ways, and the HTTP status the worker returns is
the whole contract with Cloud Tasks:

| outcome | status | Cloud Tasks | log |
| --- | --- | --- | --- |
| sent | 204 | done | `Executing task <name>` |
| permanently unsendable | 200 | done, no redelivery | ERROR + full traceback |
| temporarily unsendable | 503 | redelivered | WARNING |
| not characterised | 500 | redelivered | ERROR + full traceback |

`care/utils/mail.py` decides which. Permanent covers authentication rejected, an
unsupported capability, recipients refused 5xx, any 5xx response code, and a
backend that cannot be constructed. Transient covers 4xx responses, deferrals,
dropped connections and timeouts.

**The one judgement call**, and the reason it is a judgement call: a relay that
is down refuses a connection exactly as an absent one does. A refused connection
is read as a configuration fault only when `EMAIL_HOST` cannot be a relay for
this process — empty or loopback, in images that ship no MTA. A deployment with
a real local submission agent keeps its retries while that agent answers.

**Reading the failure.** A permanent mail failure looks like this in Cloud
Logging, and the message is meant to be actionable on its own:

```text
PermanentTaskError: No mail relay is configured: EMAIL_HOST='localhost' cannot
be reached by this process and no attempt will succeed until the environment is
given one ([Errno 111] Connection refused)
```

If you see it, the environment has no relay. Retrying will not help and the
queue will not try — that is the point. Fix the configuration (§16.4).

**A 200 is not a claim that the mail was sent.** It answers Cloud Tasks'
question, which is only ever "redeliver or not". The 204 is the one that means
delivered.

The Celery transport carries the same classification through `autoretry_for`,
which names `RetryableTaskError` only. It previously named `OSError`, which
would have retried the permanent case — `smtplib.SMTPException` derives from
`OSError`.

### 16.4 The remaining email prerequisite for staging

**`unresolved-items.md` N1 is open and blocks the first staging or production
deployment.** Nothing in this branch gave any environment the ability to send
mail; it changed only what happens when a send fails.

- **dev** sets `django_email_backend =
  "django.core.mail.backends.console.EmailBackend"`, so messages are written to
  stdout and kept by Cloud Logging. Verified end to end on 2026-08-16: a TOTP
  change through the API enqueued a task, Cloud Tasks delivered it, the worker
  answered 204 and the rendered message appears in the logs. This is a test
  sink and must not become a staging or production default.
- **staging and prod** set nothing and inherit Django's `EMAIL_HOST=localhost`.
  Before either is deployed: provision a relay; set `EMAIL_HOST`, `EMAIL_PORT`
  and `EMAIL_USER`; declare `EMAIL_PASSWORD` through `optional_secrets` for the
  roles that send; and leave `django_email_backend` empty so Django's SMTP
  backend is used.

---

## 17. Deployment changes in ES-08

The runtime is unchanged. Roles, entrypoints, images, health, routes and backend
selections are exactly as ES-06 and the pre-staging hardening left them. What
changed is who moves the image, and how a deployment is performed.

### 17.1 One image, one owner for the image field

`docker/prod.Dockerfile` still produces one image for `api`, `task_worker` and
`init`, and ES-08 added no second image: the fixture image remains development
only and is not part of any deployment.

What changed is ownership. OpenTofu creates and configures the Cloud Run services
and Jobs, and `containers[0].image` — that field alone — is under
`ignore_changes`, because application delivery moves it now. `var.image` is the
image a greenfield service is created with, and after that the deployed digest is
whatever the last release deployed.

Consequence for anyone reading tfvars: **it does not tell you what is running.**
Read the platform:

```bash
gcloud run services describe care-<env>-api --region <region> \
  --format='value(spec.template.spec.containers[0].image)'
```

Verified against the applied staging environment: `tofu plan` reports no changes
after a deployment moved every service and Job to a new digest.

### 17.2 The production build context is an allowlist

`docker/prod.Dockerfile.dockerignore` excludes everything and admits the
dependency manifests, the Django project, the plugin installer, the role
entrypoints, the translation sources and the reference data. The image therefore
contains no `.github`, no `infrastructure`, no `docs`, no editor metadata, no
generated key material and none of the builder's scratch — 2225 files, and the
same 2225 whether built from a working tree with 163 MB of untracked files in it
or from a clean `git archive` export.

The development and fixture images keep the root `.dockerignore`, now covering the
categories ES-08 section 21 names. They legitimately need most of the working
tree.

### 17.3 Initialization is now a gate, not a step

`scripts/initialize.sh` is unchanged and is still the only definition of the
sequence. What is new is that the deployment procedure treats its exit status as
a decision: `infrastructure/scripts/gcp/deploy.sh` updates the init Job to the
selected digest, executes it, and stops the deployment if it fails — no worker
revision, no API revision, no retry that would turn a failure into a slow
failure and then into an apparent success.

The state-changing window is bounded in the log by `INIT-STARTED` and
`INIT-FINISHED` lines carrying the execution id and exit status, so after a
cancellation an operator can tell whether the schema moved.

### 17.4 Deployment order is executed, not documented

worker before API, both after init, Jobs after both, and each one read back and
compared with the requested digest. Previously this was a documented sequence
that an operator performed; it is now a script, and the workflow runs that same
script.

### 17.5 What still requires an operator

- provisioning secret payloads (`provision-secrets.sh`);
- the first `tofu apply` of a new environment, and the second one after secrets;
- the bootstrap apply that creates the GitHub trust relationship;
- GitHub repository configuration: environments, variables, protection;
- any infrastructure apply, which is gated and separate from application release.

None of these is automated, and each is documented in
`docs/xii/architecture/08-continuous-delivery.md` rather than implied.
