---
title: Unresolved Items
document: inventory/unresolved-items
version: 0.4.0
status: Draft
phase: 3
source_repository: https://github.com/ohcnetwork/care
source_branch: gcp
source_commit: 6a2976dc2512c2c532fcc70628c5690fbbbe3f3d
baseline_commit: 2fe40cd16
reviewed: 2026-08-07
---

# Unresolved Items

Open questions, code defects found while inventorying, and contradictions between
the existing GCP documents and the verified state of the repository.

Nothing here was fixed in Phase 0. Each item states what is **verified**, what is
**inferred**, and what remains **unknown**.

---

## Part A — Contradictions with existing documents

Per the Phase 0 brief, the other GCP documents were not rewritten. Where a
verified code fact contradicts them, it is recorded here instead.

### A1. Document paths are inconsistent across the set

**verified** The eight architecture documents live at
`docs/xii/architecture/`. They were committed there in `e280e0f09`.

**verified** `01-current-runtime.md` referenced two *different* wrong paths for
the same target document:

| Line (before correction) | Text |
| --- | --- |
| 1933 | `docs/gcp/02-target-runtime.md` |
| 1945 | `docs/xii/gcp/02-target-runtime.md` |

**Corrected** in this phase — both now read `docs/xii/architecture/02-target-runtime.md`.

**unknown** Whether the other seven documents contain the same wrong paths. Not
audited; only `01-current-runtime.md` was in scope. **Recommend** a path sweep
across all eight before they are published.

### A2. `01-current-runtime.md` was wrapped in a broken code fence

**verified** Before correction the file opened with a stray ```` ````markdown ````
at line 1 and closed the fence at line 35 with four backticks, where three were
required to close the inner `text` block. Two further stray ` ``` ` lines sat at
the end of the file.

**verified consequence** The YAML frontmatter and all of sections 1-2 rendered as
literal code rather than as document content, and the entire tail of the file sat
inside an unterminated block.

**Corrected** in this phase.

### A3. `01-current-runtime.md` §38 misstated patient-bucket credentials

**verified** The document listed `FILE_UPLOAD_REGION`, `FILE_UPLOAD_KEY` and
`FILE_UPLOAD_SECRET` as the settings patient files use.

**verified** They are not. `get_patient_bucket_config`
(`care/utils/csp/config.py:46-56`) reads `FACILITY_S3_REGION`, `FACILITY_S3_KEY`
and `FACILITY_S3_SECRET`. The three `FILE_UPLOAD_*` credential settings
(`config/settings/base.py:537-539`) are read by no code in the repository.

**Corrected** in this phase. See also §2 below for the underlying defect.

### A4. `01-current-runtime.md` §26 omitted three task definitions

**verified** The document inventoried `care/emr/tasks/` accurately but did not
mention the three task-decorated functions defined elsewhere:
`handle_cascade` (`care/emr/models/location.py:159`),
`summarise_monetary_components` (`care/emr/models/resource_category.py:123`),
and `rebalance_account_task` (`care/emr/resources/account/sync_items.py:81`).

**Corrected** in this phase with a descriptive addition only.

### A5. The stated goal "keep Redis optional" is not currently supportable

**verified** Three hard couplings prevent it, detailed in `cache-and-redis.md` §1:
`cache.set(..., nx=True)` (`care/utils/lock.py:18, 44`),
`cache.delete_pattern(...)` (`care/emr/resources/base.py:313, 315`), and
`get_redis_connection("default")` (`care/emr/models/valueset.py:77`).

**inferred** This does not contradict the *goal*, but it does contradict any
document that treats Redis removal as configuration. It is schema and code work.

**unknown** Whether `02-target-runtime.md` or `03-migration-plan.md` make that
assumption. Not audited.

### A6. "All uploads pass through Django" is partly already true

**verified** `POST /api/v1/files/upload-file/`
(`care/emr/api/viewsets/file_upload.py:213-270`) already proxies uploads through
Django as base64.

**inferred** Any document describing the Django-proxied upload as new work should
account for this endpoint — the task is to replace a base64 path with a streaming
one and to remove the presigned alternative, not to build from nothing.

---

## Part B — Code defects found during inventory

These are pre-existing upstream issues, not regressions. None was fixed.

### B1. Patient and report buckets use facility credentials

**verified** `care/utils/csp/config.py:46-56` and `:59-70` set
`aws_access_key_id` / `aws_secret_access_key` from `FACILITY_S3_KEY` /
`FACILITY_S3_SECRET` while returning `settings.FILE_UPLOAD_BUCKET` as the bucket.

**verified** `FILE_UPLOAD_REGION`, `FILE_UPLOAD_KEY`, `FILE_UPLOAD_SECRET`
(`config/settings/base.py:537-539`) are dead settings.

**Impact (inferred):** per-bucket credential separation is impossible today. A
GCP design assuming distinct service accounts or HMAC keys per bucket must fix
this first. Note the *endpoint* settings are wired correctly, so the bug is
invisible in single-credential local and MinIO setups — which is likely why it
has survived.

**unknown** Whether this is intentional consolidation or an unnoticed
copy-paste. **Recommend** raising upstream before diverging.

**Partly resolved in IS-01.** The `patient` and `report` storage aliases now read
`FILE_UPLOAD_REGION`, `FILE_UPLOAD_KEY` and `FILE_UPLOAD_SECRET`, so those three
settings are no longer dead and per-bucket credentials are configurable.
`get_patient_bucket_config` and `get_report_bucket_config` still contain the
original defect, but are now reached only by the legacy signed-URL path, which
IS-02 removes. See `storage-call-sites.md` §11.4 for the behaviour change.

### B2. LocMem/Dummy cache shims silently disable locking

**verified** `config/caches.py:6-9` and `:12-16` accept the `nx` kwarg, ignore it,
and unconditionally `return True`.

**verified** `Lock.acquire` (`care/utils/lock.py:17-19`) treats any truthy return
as success. Under either shim, **no lock is ever contended**.

**verified** The shims are referenced only from `care/utils/tests/test_utils.py:18`.

**Impact (inferred):** the most dangerous item in this document. Substituting a
PostgreSQL or LocMem cache backend does not degrade locking — it removes it,
silently, with call sites that still read as correct. Any PostgreSQL lock must be
a real conditional write (`INSERT ... ON CONFLICT DO NOTHING` or
`pg_try_advisory_lock`).

### B3. Redis outage accepts revoked JWTs

**verified** `config/settings/base.py:93` sets `IGNORE_EXCEPTIONS: True`.

**verified** `config/authentication.py:21` treats a cache miss as
"token not invalidated".

**Impact (inferred):** with Redis unreachable, `cache.get` returns `None` and
revoked access tokens are honored. This is a security property degrading open.
Contrast with `Lock.acquire`, which degrades closed under the same condition.

**unknown** Whether this is a known accepted risk upstream.

### B4. `cleanup_incomplete_file_uploads` aborts the batch on one storage error

**verified** `care/emr/tasks/cleanup_incomplete_file_uploads.py:34-40` logs and
then re-raises inside the per-file loop, **before**
`ids_to_delete.append(file.id)` at line 41.

**verified** `quiet=True` only suppresses `NoSuchKey`
(`care/emr/utils/file_manager.py:106`); any other `ClientError` propagates.

**Impact (inferred):** one undeletable object stalls the entire cleanup
indefinitely. Rows already deleted from storage in that page are never removed
from the database, so the next run retries them — self-healing but non-progressing.

### B5. Report generation is not idempotent under retry

**verified** `care/emr/tasks/report_generation.py:12-14` declares
`autoretry_for=(ClientError,)` with `max_retries: 3`.

**verified** `care/emr/reports/report_utils.py:102, 104-121` creates a **new**
`ReportUpload` row and a new object key on every invocation.

**Impact (inferred):** each retry produces an additional row and stored object.
The failure path deletes the row only when `put_object` itself raises
(`report_utils.py:129-131`); a crash between the row save (`:121`) and the
`put_object` (`:124`) leaves an orphan.

### B6. `expires` and `max_retries` interact badly

**verified** `report_generation.py:13` and `totp.py:11, 38` all set
`expires=10 * 60` alongside `max_retries: 3`.

**inferred** A task that expires 10 minutes after dispatch can have queued
retries discarded on expiry, so the effective retry count is less than 3 whenever
the queue is backed up. Not verified against Celery's exact expiry semantics for
retried tasks. **unknown** whether the interaction was considered.

### B7. TOTP emails retry on any exception, including post-send failures

**verified** `care/emr/tasks/totp.py:9` and `:36` use
`autoretry_for=(Exception,)`.

**verified** `msg.send()` is the last statement (`:32`, `:59`).

**inferred** A failure after the SMTP handoff but before task completion
re-sends the email. Low severity, but relevant if Cloud Tasks changes retry
timing.

### B8. Storage writes are not covered by the surrounding transaction

**verified** `care/emr/api/viewsets/file_upload.py:255-268` wraps the model save
and `put_object` in one `transaction.atomic()` block.

**inferred** Object storage is not transactional. A commit failure after a
successful `put_object` orphans the object; the DB row rolls back but the bytes
remain. `cleanup_incomplete_file_uploads` does not catch these, because it keys
off `FileUpload` rows — and the row no longer exists.

### B9. `mark_upload_completed` trusts the client

**verified** `care/emr/api/viewsets/file_upload.py:177-184` sets
`upload_completed = True` with no check that an object exists in the bucket.

**inferred** Inherent to the presigned-PUT design: Django never observes the
upload. A Django-proxied upload path removes this class of problem entirely.

### B10. `delete_objects` is dead code — RESOLVED (IS-01)

**verified** `care/emr/utils/file_manager.py:112-133` had no caller and carried a
GCP-specific `NotImplemented` branch.

**Removed in IS-01.** It was deleted rather than ported: it had no caller, and
ES-01 §17 forbids provider-specific batch calls. Django Storage defines no
portable bulk delete; a future caller should iterate `Storage.delete()`.

### B11. Celery beat health check is a liveness lie

**verified** `scripts/celery_beat.sh` runs `touch /tmp/healthy` **before**
exec'ing `celery beat`.

**verified** `scripts/healthcheck.sh` probes the beat role with `ls /tmp/healthy`.

**inferred** The marker persists after beat dies, so the container reports healthy
while scheduling nothing. Low relevance under Cloud Run (no beat), but it means
the current runtime may have silently failing schedules.

### B12. Dead cache import shadowed by a local variable

**verified** `care/facility/models/facility.py:4` imports
`from django.core.cache import cache`, never calls it, and binds a local
`cache = []` at line 226 inside `sync_cache`.

**Impact:** cosmetic. Recorded because it produces a false positive in any
cache-usage grep.

---

## Part B2 — Storage issues open after IS-01

Recorded 2026-08-06. Only issues that remain genuinely unresolved after the
storage seam moved onto Django Storage. Full detail in
`storage-call-sites.md` §11.

### S1. The GCS profile cannot serve files end to end — RESOLVED (2026-08-07)

**Was:** `care/emr/utils/legacy_signed_urls.py` constructed a boto3 client
directly and was S3-only, so `CARE_STORAGE_BACKEND=gcs` configured persistence
against Google Cloud Storage while both signed-URL flows silently kept pointing
at S3/MinIO — and at the *old* bucket names, since they resolved buckets through
the now-deleted `care/utils/csp/`.

**Resolved** by removing the signed-URL transport outright rather than porting
it. CARE now serves every object through Django Storage, so `download_url`
carries neither a provider nor a bucket and both profiles behave identically.
Verified under `gcs`: persistence resolves to `GoogleCloudStorage` and
`download_url` is still `/api/v1/files/{id}/download/`.

**Consequence:** IS-02 is no longer a prerequisite for a GCS deployment. Only S2
below stands between the `gcs` profile and production use.

### S2. Report generation does not retry under GCS — RESOLVED in ES-03

**Superseded by Part B3 below.** The record of the original finding follows.

**verified** `care/emr/tasks/report_generation.py:13` uses
`autoretry_for=(ClientError,)`. Under `s3` this still works, because
django-storages raises `botocore` errors from inside `Storage.save`. Under `gcs`
the failures are `google.api_core.exceptions.*` and no retry occurs.

**Not changed** — both ES-01 §31 and the completion pass explicitly forbid
modifying Celery, and widening `autoretry_for` alters retry semantics beyond the
storage seam.

**Now the only item blocking the GCS profile**, and the last provider-specific
reference in any storage consumer. **Decision needed:** a provider-neutral retry
predicate, or an explicit translation at the storage boundary. Report generation
still succeeds under `gcs`; only retry-on-transient-failure is absent.

### S3. Overwrite safety depends on a backend option, not on Django Storage

**verified** `Storage.save()` renames on collision unless the backend is
configured otherwise; `InMemoryStorage` demonstrably does. CARE relies on
overwrite semantics, which are supplied by `file_overwrite: True` on each alias
in `config/storage.py`.

**inferred** Any future alias, or any backend swapped in for testing, must set it
or CARE will silently write to a renamed object while the database keeps the
original `internal_name`. Asserted in `care/utils/tests/test_storage_config.py`.

### S4. Still true after IS-01, unchanged by it

These were recorded in Part B and remain accurate; IS-01 changed the persistence
call underneath them but not the behaviour:

| # | Item | Note |
| --- | --- | --- |
| B4 | `cleanup_incomplete_file_uploads` aborts the batch on one storage error | Semantics preserved deliberately, per ES-01 §17 |
| B5 | Report generation is not idempotent under retry | Untouched |
| B8 | Storage writes are not covered by the surrounding transaction | Untouched; still orphans objects on commit failure |
| B9 | `mark_upload_completed` trusts the client | **Largely defused.** No client can write to the bucket any more, so a file marked complete without one is now only a bookkeeping inconsistency, not an unverified external write. The endpoint is redundant; IS-02 decides its fate |

---

## Part C — Open questions requiring a decision

### C1. Where do migrations run under Cloud Run?

**verified** Today, `migrate` runs only in `scripts/celery_beat.sh` and
`scripts/celery-dev.sh`. The API containers never migrate.
`Procfile:2` defines a `release` phase, but no Docker path uses it.

**Decision needed.** Cloud Run Job, deploy step, or an init container. Must also
cover `sync_permissions_roles` and `sync_valueset`, which run in the same scripts
— and the first depends on the Redis lock from B2.

### C2. Do cover images and avatars remain public?

**verified** Written with `ACL: public-read` when `BUCKET_HAS_FINE_ACL` is set
(`care/utils/file_uploads/cover_image.py:49-51`); read via unsigned concatenated
URLs (`care/facility/models/facility.py:207-212`, `care/users/models.py:202-207`).

**Decision needed.** GCS uniform bucket-level access rejects per-object ACLs. If
these become private, both URL builders need Django routes, and `FACILITY_CDN`
(`config/settings/base.py:673`) needs a new meaning.

### C3. Is the API allowed to start without Redis?

**verified** `scripts/start.sh` and `scripts/start-dev.sh` both call
`wait_for_redis.sh`.

**Decision needed.** Retaining the wait makes Redis a hard Cloud Run cold-start
dependency. Removing it contradicts `IGNORE_EXCEPTIONS: True` only in spirit —
but see B3 for the security consequence of tolerating a missing Redis.

### C4. Does the Celery result backend get ported at all?

**verified** `CELERY_RESULT_BACKEND = CELERY_BROKER_URL`
(`config/settings/base.py:423`); no `AsyncResult` anywhere; no caller reads a
task result or ID.

**inferred** It can be dropped. Flagged because dropping it is cheap and removes
a Redis dependency outright.

### C5. What replaces the Celery queue-length health check?

**verified** `config/settings/base.py:458-466` constructs
`DjangoCeleryQueueLengthHealthCheck` with `broker=REDIS_URL`.

**Decision needed.** Under Cloud Tasks there is no Redis queue. Left as-is, the
health endpoint reports unhealthy in the target runtime.

### C6. Email delivery on GCP

**verified** `Pipfile` installs `django-anymail` with the `amazon-ses` extra.

**Decision needed.** GCP has no SES equivalent. Options are keeping SES
cross-cloud, or switching provider — which changes `EMAIL_BACKEND` and the
Anymail extra.

### C7. Celery's hardcoded `Asia/Kolkata` timezone

**verified** `config/celery_app.py:16` sets `enable_utc=False` and
`timezone="Asia/Kolkata"`, overriding `CELERY_TIMEZONE`
(`config/settings/base.py:417-419`).

**Decision needed.** `crontab(hour="0", minute="0")`
(`care/emr/tasks/__init__.py:14`) means midnight IST. Cloud Scheduler needs that
made explicit rather than inherited.

### C8. `ADDITIONAL_PLUGS` must match at build and deploy

**verified** Consumed at image build (`docker/prod.Dockerfile:39`) to `pip install`,
and again at every process start (`config/settings/base.py:19`) to populate
`INSTALLED_APPS`.

**verified** Invalid JSON is logged and swallowed (`plugs/manager.py:27-28`).

**Decision needed.** A mismatch yields `ModuleNotFoundError` at startup; a typo
yields silent plugin loss. Both warrant an explicit startup assertion.

### C9. `internal_name` exposure

**verified** `care/emr/resources/file_upload/spec.py:108` returns
`internal_name` — the storage object key — carrying the in-source comment
`# Not sure if this needs to be returned`.

**Decision needed.** Low risk while the bucket is private; unnecessary surface
either way.

### C10. Base64 upload endpoint is missing from the OpenAPI schema

**verified** `care/emr/api/viewsets/file_upload.py:213` has no `@extend_schema`;
its body fields are read straight from `request.data`.

**inferred** Schema-generated clients cannot discover it. Whether it is
public API or an internal affordance is **unknown**.

---

## Part D — Unknowns not resolvable from this repository

| # | Unknown | Why |
| --- | --- | --- |
| D1 | Which frontend components consume `signed_url` / `read_signed_url` | Frontend is a separate repository |
| D2 | Whether any client uses the base64 `upload-file` endpoint | Absent from OpenAPI; no caller here |
| D3 | Which plugins a given deployment installs | Governed by `ADDITIONAL_PLUGS`, outside version control |
| D4 | Whether known CARE plugins are GCP-compatible | No plugin source vendored |
| D5 | Meaning of `PLUGIN_CONFIGS` keys | No consumer in this repository |
| D6 | Typical file sizes and report generation duration | No instrumentation or metrics in the repository |
| D7 | Whether B1 and B3 are known upstream | Requires checking the upstream issue tracker |
| ~~D8~~ | ~~Green baseline test count and duration~~ | **Resolved 2026-08-06** — 1912 tests, 0 skipped, ~21-29 s with `--keepdb --parallel`; see `runtime-and-deployment.md` §11.9 |

---

## Part E — Baseline blockers

**RESOLVED 2026-08-06.** A green baseline exists. Full record in
`runtime-and-deployment.md` §11.

Baseline: commit `2fe40cd16`, all five compose services healthy, 312 migrations
applied, `makemigrations --check` clean, fixtures loaded, **1912 tests, 0 skipped**,
green on 2 of 3 runs. See E7 for the single flake.

Disposition of the blockers recorded in the previous revision:

| # | Blocker | Disposition |
| --- | --- | --- |
| E1 | Docker daemon not running | **resolved** — Docker Desktop started; engine 29.6.2, Compose v5.3.1 |
| E2 | `make` not installed | **not a blocker** — every `Makefile` target was translated to its underlying `docker compose` command; see `runtime-and-deployment.md` §11.3 |
| E3 | No Python environment | **not applicable** — all execution happens inside the container |
| E4 | Host Python 3.14.5 vs required `==3.13.*` | **not applicable** — the image ships Python 3.13.14 |
| E5 | Redis not running | **resolved** — supplied by the compose `redis` service, healthy |
| E6 | `.env` absent | **withdrawn — the claim was wrong.** No root `.env` is required. `docker compose config` resolves with no warnings; every interpolation has a default. The real env files, `docker/.local.env` and `docker/.prebuilt.env`, are **tracked in git**. There are no `.example` variants of either. |

### E7. Shared-Redis test isolation failures under `--parallel`

**Scope corrected 2026-08-06 (during IS-01).** Originally recorded as a single
flaky test. It is a defect *class* affecting at least **six** tests in **two**
families, and it fires far more often than the first sample suggested.

**verified** Root cause: `config/settings/test.py:45-56` points the cache at
Redis with a single `KEY_PREFIX = "test_"`, shared by all 16 parallel workers,
while `cache.clear()` runs in `setUp` at `care/emr/tests/test_reset_password_api.py:24`
and `care/emr/tests/test_valueset_api.py:23, 52`. A clear in one worker discards
cache state another worker is mid-way through asserting on.

**verified** Affected tests observed failing:

| Family | Test | Mechanism |
| --- | --- | --- |
| Rate limiting | `test_password_request_rate_limiting` | `config/ratelimit.py:9` returns the constant key `"ratelimit"`, so the counter is global and a concurrent clear resets it — `200 != 429` |
| Rate limiting | `test_password_check_rate_limiting` | same |
| Rate limiting | `test_password_confirm_rate_limiting` | same |
| Favorites | `test_add_favorite` | asserts on values read back from the shared cache (`care/emr/api/viewsets/favorites.py:40-56`) |
| Favorites | `test_remove_favorite_single` | same |
| Favorites | `test_favorite_lists_returns_list_on_first_call` | same |

**verified** Measured on `feature/django-storages` at 1962 tests:

| Configuration | Result |
| --- | --- |
| Full suite, serial (`--shuffle`, no `--parallel`) | **1962/1962 OK** |
| Full suite, `--parallel --shuffle`, 6 runs | 1 green, 5 with 1-2 failures |
| Only `test_favorites_api`, `test_valueset_api`, `test_reset_password_api` in parallel, 4 runs | **4/4 failed** (76 tests, no storage code involved) |

**verified** That last row is the decisive one: the defect reproduces with the
three cache-touching modules alone, so it is independent of any other change.

**inferred** The observed rate rose from 1-in-3 during the Phase 0 baseline to
5-in-6 here. No cache, lock or rate-limit code changed between the two. The
likeliest explanation is scheduling: more tests and slower ones (MinIO round
trips) alter how work is distributed across the 16 workers and widen the window
in which a concurrent `cache.clear()` can land. The isolated reproduction above
shows the defect does not need those tests to be present at all.

**Not fixed.** ES-01 §31 explicitly excludes fixing rate limiting, and the
favorites half is equally out of scope. Upstream CI runs the same
`--parallel --shuffle` combination (`.github/workflows/reusable-test.yml:77`), so
it can occur there too. **unknown** whether it is known upstream.

**Recommended fix, for whoever owns it:** give each parallel worker its own cache
namespace, e.g. derive `KEY_PREFIX` from the worker's database suffix in
`config/settings/test.py`. That removes the whole class rather than the six
symptoms.

**inferred, separate concern:** a globally-keyed rate limit is not only a test
problem — it means the limit is shared across all callers rather than per client.

### E8. Transient wheel corruption in the BuildKit pip cache

**verified** The first image build failed with
`zipfile.BadZipFile: Bad CRC-32 for file '_brotli.cpython-313-x86_64-linux-gnu.so'`
during `pipenv install` at `docker/dev.Dockerfile:22`.

**verified** Resolved by pruning only BuildKit cache mounts
(`docker builder prune --filter type=exec.cachemount`). The rebuild succeeded and
it has not recurred. **inferred** transient corruption, not a repository defect —
recorded only so the same symptom is recognised quickly if it reappears.

---

## Part B3 — Task-runtime issues after ES-03

Recorded 2026-08-07. Full detail in `task-call-sites.md` §7.

### S2. Report generation does not retry under GCS — RESOLVED (2026-08-07)

**Was:** `care/emr/tasks/report_generation.py:13` declared
`autoretry_for=(ClientError,)`. Under `s3` django-storages raises `botocore`
errors from inside `Storage.save`, so the retry fired. Under `gcs` the same
failures arrive as `google.api_core.exceptions.*` and no retry occurred at all.

**Resolved** by classifying at the operation boundary rather than by exception
type. `report_utils.generate_and_upload_report` catches any failure of the
object write, deletes the orphan row as before, and re-raises
`RetryableTaskError`. The task declares `autoretry_for=(RetryableTaskError,)`
and imports no provider library; a permanent failure such as a missing template
raises `PermanentTaskError` and is not retried.

**verified** `care/emr/tests/test_task_runtime.py::ReportRetryPortabilityTests`
asserts the same classification under an S3 and a GCS `STORAGES` configuration,
simulating each provider's transient write failure without importing either
library, and parses the task module's AST to prove it imports no `botocore`,
`boto3` or `google.*` name.

**Consequence:** the last provider-specific reference in any storage consumer is
gone. Nothing now stands between the `gcs` profile and production use at the
application level.

**Deliberately narrow:** the classification treats *any* object-write failure as
transient. That over-classifies a genuinely permanent failure such as a
permissions error, which will now be retried three times before failing. The
bounded retry count makes that cheap, and the alternative -- enumerating
provider error codes -- is exactly the coupling S2 was about.

### S5. Report generation is still not idempotent under retry

**Unchanged from B5.** Each run creates a new `ReportUpload` row and a new object
key, so three retries can leave three rows and three stored objects.

**What changed:** the retry is now bounded by an explicit classification rather
than by whether the deployment happens to use S3 -- which means under `gcs` the
duplication is newly *possible*, where previously no retry happened at all.

**Not fixed, and why.** ADR-0003 says to add an execution record only where
duplication is harmful, and ES-03 §25 forbids moving report progress to a new
cache or model architecture. A durable fix needs a decision on
`CARE_REPORT_PROGRESS_BACKEND` (configuration reference §32), which is deferred.
**Decision needed** before the GCS profile carries real report volume.

### S6. TOTP emails duplicate on redelivery

**verified** No de-duplication exists, and at-least-once delivery means a
redelivered task re-sends.

**Partly mitigated.** `autoretry_for` narrowed from `(Exception,)` -- which
re-sent after any post-SMTP failure, B7 -- to transient connection errors only.

**Accepted rather than fixed.** A duplicate notification is visible but not
destructive, and suppressing it requires the same execution-record machinery as
S5.

### S7. `cleanup_incomplete_file_uploads` can loop forever

**verified, pre-existing, found while reading the code for ES-03.** The loop at
`care/emr/tasks/cleanup_incomplete_file_uploads.py` re-fetches the same page
until the queryset is empty, but only appends a row to `ids_to_delete` when
`file.internal_name` is truthy. A matching row with an empty `internal_name` is
never deleted and never stops matching, so the loop does not terminate.

**inferred** It has presumably never fired because `internal_name` is set at
creation. It becomes reachable if a row is ever created without one.

**Not fixed.** ES-03 §7 excludes redesigning unrelated domain services and the
operation's semantics were preserved deliberately. It matters more now than it
did: as a Cloud Run Job this is an unbounded hang rather than a stuck beat
schedule. Related to B4, which is the same loop aborting on one storage error.

### S8. `CARE_TASK_BACKEND=cloud_tasks` is verified at contract level only

**verified** Every Cloud Tasks assertion mocks the client. The request CARE
builds is checked field by field -- queue parent, worker URL, POST, JSON body,
envelope shape, OIDC service account and audience, schedule time, task name --
but no request has been made to Google.

**unknown** Whether a real queue accepts them, and whether Cloud Run IAM rejects
an unauthenticated caller as designed. Both require deployed infrastructure and
belong to ES-06/ES-07.

### S9. The Celery queue-length health check is wrong under Cloud Tasks

**Unchanged from C5, restated because ES-03 makes it concrete.**
`config/settings/base.py` still constructs `DjangoCeleryQueueLengthHealthCheck`
with `broker=REDIS_URL` unconditionally. Under `CARE_TASK_BACKEND=cloud_tasks`
there is no Redis queue to measure, so the health endpoint reports unhealthy in
the target runtime.

**Not fixed.** ES-03 §44 forbids changing Redis behaviour, and health checks are
scoped to the cache and runtime-profile work. **Decision needed** before the
first GCP deployment, or the readiness probe fails on a correctly configured
service.

### S10. Plugin tasks are Celery-only

**verified** The core registry is a closed, explicit mapping. A plugin task is
registered with Celery by `autodiscover_tasks` as before, but is not executable
through Cloud Tasks; `enqueue_task` raises `UnknownTaskError` at the call site.

**unknown** Whether any deployed plugin defines a task at all. See
`plugin-impact.md` §10 for what a future registration mechanism would have to
settle first -- namespacing and the trust boundary around importable modules.

---

## Part B4 — ES-03 pre-merge architectural findings

Recorded 2026-08-07 by a documentation-only audit performed after ES-03 was
implemented and before it was merged. No application code, test or runtime
behaviour was changed by that audit or by this section.

**Identifier note.** These three findings are named `A1`, `C1` and `C2` in the
ES-03 closeout. Parts A and C of this document already use those labels for
unrelated items -- Part A `A1` is a document-path inconsistency, Part C `C1` and
`C2` are open questions about migrations and cover images. To keep both sets
searchable, the findings below are written as **A1 (ES-03)**, **C1 (ES-03)** and
**C2 (ES-03)**, and are referred to that way everywhere else.

Two findings from the same audit are **deliberately not recorded here**, because
they already exist:

- the `sync_permissions_roles` Redis lock and the LocMem/Dummy shim that makes
  it silently succeed -- see `cache-and-redis.md` §4.2 and ADR-0005, whose
  implementation checklist already carries "LocMem false-lock behavior removed";
- Cloud Tasks queue retry policy, including max attempts and maximum retry
  duration -- see `07-configuration-reference.md` §18.9 and `06-operations.md`.

---

### A1 (ES-03). Cloud Tasks worker security depends on platform IAM

**Classification:** `ES-06 / ES-07 blocker` for production deployment.
**Not an ES-03 implementation defect.**

**verified** The internal worker route

```text
POST /internal/tasks/execute/
```

has **no application-layer authentication and no application-layer
authorization**. `care/utils/tasks/views.py` is a plain Django function view
carrying only `@csrf_exempt` and `@require_POST`. Because it is not a DRF view,
`REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]` and
`["DEFAULT_PERMISSION_CLASSES"]` -- which protect every CARE viewset -- do not
apply to it. `AuthenticationMiddleware` runs and resolves `request.user` to
`AnonymousUser`; the view never reads it. No authentication backend is reached.

**verified by probe**, worker role, Django test client:

```text
no credentials at all      -> 204   (task executed)
garbage bearer token       -> 204   (token ignored entirely)
GET                        -> 405
```

**This is intentional.** ADR-0003 requires the worker to "require platform IAM
authentication", and ES-03 §19 requires that application-level checks "SHALL not
pretend to replace platform IAM". The route is registered only when
`CARE_TASK_HANDLER_ENDPOINT_ENABLED` is true, which defaults to true only for

```text
CARE_PROCESS_ROLE=task_worker
```

so the public API service does not route it at all. Verified both ways:
`NoReverseMatch` under the `api` role, resolvable under `task_worker`.

The target GCP architecture relies on **Cloud Run IAM validating the Cloud Tasks
OIDC token before the request reaches Django**. The dispatcher attaches an OIDC
token for `GCP_TASKS_SERVICE_ACCOUNT` with audience `GCP_TASKS_OIDC_AUDIENCE`;
nothing in CARE verifies that token, and nothing in CARE should.

#### Requirements this places on ES-06 / ES-07

1. **The worker service MUST NOT allow unauthenticated invocation.** Deployed
   with unauthenticated invocation permitted, the endpoint executes any
   registered task for any caller -- including `cleanup_expired_token_slots`,
   which hard-deletes rows.
2. **Cloud Run IAM is a production security requirement, not an optional
   hardening measure.** It is the only authentication in the path. There is no
   second layer to fall back on, by design.
3. **ES-06 / ES-07 MUST enforce authenticated-only invocation** in the
   infrastructure definition, not by convention or runbook. The guarantee
   currently exists only as prose in ADR-0003 and in this document.
4. **The Cloud Tasks invoker service account MUST hold only the permission it
   needs**: `roles/run.invoker` on the worker service, and nothing broader. It
   is an invoker identity, not an application identity.
5. **No application shared secret SHALL be added to compensate for missing
   IAM.** A shared secret would have to be provisioned, injected, rotated and
   compared in application code, and would be strictly weaker than IAM while
   creating the impression that the endpoint is self-protecting. If IAM is
   absent the correct response is to fix the deployment, not the application.

#### What ES-03 does contribute

Two structural defences, neither cryptographic and neither a substitute for IAM:

- **Route absence.** The API role does not register the URL.
- **A closed registry.** Only six server-defined task names are executable. No
  import path, dotted callable or arbitrary payload resolves to code.

---

### C1 (ES-03). `transaction.on_commit` changes failure semantics

**Classification:** deliberate behaviour change introduced by ES-03. Recorded,
not fixed in this phase.

**verified** Before ES-03, asynchronous dispatch happened inside the request
transaction:

```text
database transaction
    ↓
Celery .delay()
    ↓
broker failure
    ↓
request fails and transaction rolls back
```

After ES-03, three of the four dispatch sites use `enqueue_task_on_commit`:

```text
database transaction commits
    ↓
on_commit callback runs
    ↓
async dispatch fails
    ↓
request may fail after durable state has already committed
```

**verified by probe** -- an exception raised in an `on_commit` callback
propagates to the caller *after* the transaction has committed:

```text
db work done | dispatch registered | raised AFTER commit: backend down
```

**Why the change was made.** `ATOMIC_REQUESTS` is enabled
(`config/settings/base.py`). Dispatching inside the transaction allows a worker
-- under Cloud Tasks, a separate service reaching the database directly -- to
observe rows the request has not committed, or rows it is about to roll back.
ADR-0003 requires that a task never observe state that was subsequently rolled
back. `on_commit` is what satisfies that requirement.

**The cost.** The failure mode moves rather than disappearing. Instead of a
clean rollback, a dispatch failure can now leave a:

```text
committed-but-not-enqueued
```

state: the durable change is persisted, the task was never queued, and the
client receives an error for a request that partly succeeded.

#### Currently affected cases

| Call site | Committed state | Work that may be lost |
| --- | --- | --- |
| `care/emr/api/viewsets/totp.py` | the user's `mfa_settings` change | the TOTP enabled/disabled notification email |
| `care/emr/models/resource_category.py` | the parent category's recalculated components | the fan-out recomputing each child category |

Neither is self-correcting today. The TOTP notification is simply not sent. A
child category keeps stale `calculated_monetary_components` until the next edit
to its parent triggers the fan-out again.

Report generation is **not** affected: it dispatches directly rather than on
commit, because its handler reads only rows committed by earlier requests, and a
dispatch failure there leaves no database change behind.

#### Not fixed in this phase

A durable fix requires a mechanism this phase deliberately does not choose.
Future reliability work may require one of:

- a transactional outbox;
- a durable execution record;
- a retry or reconciliation job;
- a domain-specific recovery mechanism.

**No option is selected here.** Each has a materially different cost, and the
choice interacts with the report-progress and task-state decisions ADR-0003
defers.

**Where this belongs.** This is idempotency and reliability work under ADR-0003,
alongside S5 and S6. It is **not** a cache concern (ADR-0004) and **not** a
locking concern (ADR-0005); no cache or lock behaviour is involved, and no
change to either would address it.

---

### C2 (ES-03). Task registry loads lazily

**Classification:** `small async-runtime hardening item`. **Not a merge
blocker.**

**verified** The explicit task registry imports its handler modules lazily, on
first lookup. `care/utils/tasks/registry.py::load_registry` imports the modules
named in `HANDLER_MODULES` -- currently `care.emr.tasks.handlers` -- the first
time `get_task` or `registered_task_names` is called.

**verified by probe**, after full `django.setup()` *and* importing `config.urls`
so that every viewset is loaded:

```text
handlers imported at startup?          False
registry imported at startup?          True
handlers imported after first lookup?  True
```

Registration is deterministic once triggered -- ordered, idempotent and
thread-safe -- but it is deferred.

#### Consequences

- A syntax error or import failure in handler registration **may not fail
  application startup**.
- The failure surfaces instead at **first dispatch** on the API side, or at the
  **first worker invocation** on the worker side, as a runtime error rather than
  a boot error.
- This is **weaker than the fail-fast behaviour used for configuration
  validation**, where an invalid `CARE_TASK_BACKEND`, an invalid
  `CARE_PROCESS_ROLE` or incomplete Cloud Tasks settings raise
  `ImproperlyConfigured` during settings import and prevent the process from
  starting at all.

#### Preferred future improvement

Validate or load the registry during appropriate worker startup, or through a
Django system check, so that a broken registration is caught before traffic
reaches the process -- **without exposing the internal task route on the API
role**. That constraint is the reason the obvious fix is not simply importing
the handlers from `config/urls.py`.

**Not implemented now.** The lazy import is what keeps `care.utils` from
importing `care.emr` models at module-import time, and undoing it carelessly
would reintroduce a dependency on Django app-loading order.
