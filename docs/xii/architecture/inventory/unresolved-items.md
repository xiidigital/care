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

## ES-05 follow-up

The lock inventory is implemented with PostgreSQL advisory locks. Real
multi-connection contention coverage and complete serial and parallel suite
runs passed on 2026-08-08; this does not authorize starting ES-06.

**Closed 2026-08-09 — consumer-level contention test.** The remaining ES-05
acceptance gap was that contention had been proven only for the generic lock
helper, not for a real caller.
`care/security/tests/test_sync_permissions_roles_concurrency.py` now runs two
concurrent `sync_permissions_roles` management-command invocations on
independent PostgreSQL connections. The first is suspended inside the critical
section, after a protected `PermissionModel` write and before commit, by a
test-only `post_save` receiver; the second raises `ObjectLocked` from
`pg_try_advisory_xact_lock` on the same key and issues no mutating statement
against `security_permissionmodel`, `security_rolemodel` or
`security_rolepermission`. Normal execution resumes once the first commits. The
lock helper is not mocked, synchronization uses `threading.Event` rather than
sleeps, and the test passes with Redis stopped. No ES-05 gap remains.

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
| 1933 | `docs/xii/architecture/02-target-runtime.md` |
| 1945 | `docs/xii/architecture/02-target-runtime.md` |

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

**verified** `care/emr/reports/report_utils.py:103, 105-122` creates a **new**
`ReportUpload` row and a new object key on every invocation.

**Impact (inferred):** each *successful* invocation produces an additional row
and stored object. A retry is narrower: the failure path deletes the row when
`put_object` raises (`report_utils.py:132-134`), so a retry does not accumulate
rows. It can accumulate objects — a write ambiguous enough to raise after the
bytes landed is not cleaned up, and the retry writes another under a fresh key.
A crash between the row save (`:122`) and the `put_object` (`:127`) leaves an
orphan row.

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
below stands between the `gcs` profile and production use — and until S2 is
closed, *report generation* SHALL NOT be described as production-ready under
`gcs`, even though the rest of the file surface is. See
`02-target-runtime.md` §11.

### S2. Report generation does not retry under GCS — RESOLVED in ES-03

**Superseded by Part B3 below.** The record of the original finding follows.

**verified** `care/emr/tasks/report_generation.py:13` uses
`autoretry_for=(ClientError,)`. Under `s3` this still works, because
django-storages raises `botocore` errors from inside `Storage.save`. Under `gcs`
the failures are `google.api_core.exceptions.*` and no retry occurs.

**Impact (verified):** it does not degrade to "retries less often" — it degrades
to **no retry at all**, silently. The task carries a retry policy that cannot
fire, so the first transient upload failure fails the report, and nothing in the
logs distinguishes that from a policy that fired and exhausted itself.

**Not changed** — both ES-01 §31 and the completion pass explicitly forbid
modifying Celery, and widening `autoretry_for` alters retry semantics beyond the
storage seam. `02-target-runtime.md` §11 records the two acceptable resolutions
and forbids claiming GCS report generation is production-ready until one lands.

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

**RESOLVED by IS-01 — they stay publicly *readable*, but the bucket does not.**
No object carries an ACL any more. The bytes are served by CARE through two
anonymous routes, `facility-cover-image-asset` and `user-profile-picture-asset`
(`care/emr/api/viewsets/file_assets.py`), so who can see a cover image is
unchanged while the bucket becomes private and GCS uniform bucket-level access
is satisfied.

Both URL builders now `reverse()` to those routes. `FACILITY_CDN` and
`BUCKET_HAS_FINE_ACL` were deleted rather than redefined: with CARE serving the
bytes, a CDN belongs in front of CARE, which the long-lived `Cache-Control` on
those responses allows.

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

**RESOLVED by ES-02.** The base64 endpoint no longer exists. Its replacement at
`care/emr/api/viewsets/file_upload.py:294` carries an explicit
`@extend_schema` declaring `request={"multipart/form-data":
FileUploadMultipartSerializer}` and `responses={200: FileUploadRetrieveSpec}`,
so the upload body is now part of the generated schema and discoverable by
schema-generated clients.

Deferring the annotation until the body changed was the right order: annotating
a base64 field that was about to be deleted would have been wasted work, and the
schema now describes a contract that will not immediately move.

Both halves of the file contract are annotated — `download` was already.

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

**RESOLVED by ES-04 (2026-08-09).** Two corrections to the analysis above, then
the fix.

**Correction 1 — the mechanism is `FLUSHDB`, not key collision.** `django_redis`
implements `clear()` as `client.flushdb()`, which empties the entire Redis
database and ignores `KEY_PREFIX` entirely. So a `cache.clear()` in one worker
was never merely colliding with another worker's keys; it was deleting all of
them. This also means the recommended fix above — deriving `KEY_PREFIX` from the
worker's database suffix — **would not have worked**. `FLUSHDB` does not look at
keys.

**Correction 2 — the rate limit is not globally keyed.** The "separate concern"
noted above is not a defect. `django_ratelimit._make_cache_key` builds its key
from `[group, rate, key_value, window]`, and CARE's `ratelimit()` puts the caller
dimension into the *group* (`_group = group + f"-{key}"`) before applying the
constant key function. `reset-request-alice` and `reset-request-bob` therefore
occupy different buckets. The constant key function is redundant, not incorrect,
and no production change was made — altering the key shape would reset every live
limiter for no correctness gain. Demonstrated in
`care/utils/tests/test_ratelimit_semantics.py`.

**Fix.** `config/settings/test.py` now uses LocMem for the `default` cache. Each
parallel worker is a separate process with its own cache, so a `clear()` cannot
reach across workers and no key can collide — the defect class is removed by
construction rather than mitigated. The aliases that must stay on Redis are
namespaced per worker with a `KEY_FUNCTION`; this mattered because several lock
keys were constants (`PatientCreateLock` had no per-object component) and
workers would otherwise contend for one lock. Of those aliases only `ratelimit`
remains — `locks` went with ES-05 and `recent_views` with RF1 — and it keeps the
per-worker key function because its buckets are keyed on the test client's fixed
127.0.0.1.

Redis and PostgreSQL cache behaviour is not left untested: it moved to
`care/utils/tests/test_cache_backends.py`, which selects each backend explicitly.
Those Redis tests use their own Redis database precisely because they exercise
`clear()`.

**verified** Before: the three cache-touching modules failed 4 runs out of 4.
After: 4 out of 4 green, and the full parallel suite is 6 for 6 at 2240 tests.
Production cache key semantics are unchanged — the worker-scoped key function is
configured only in test settings.

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

---

## Part B5 — Cache issues after ES-04

Recorded 2026-08-09. Full detail in `cache-and-redis.md` §0.

ES-04 made the cache backend configurable and removed the backend-specific
operations from provider-neutral consumers. It deliberately did **not** solve
locking. These are what it left open, stated so the next phase does not have to
rediscover them.

### K1 (ES-04). Distributed locking still requires Redis

**Status: Resolved by ES-05 (2026-08-08).** Locking is now a transaction-scoped
PostgreSQL advisory lock; the `locks` cache alias no longer exists and locking
is not a Redis dependency. The record below describes the state at the end of
ES-04 and is preserved for history. Locking is consequently **not** on the
Redis-free critical path and has no RF item — see Part RF.

**verified at the time** `care/utils/lock.py` read a dedicated `locks` cache alias that was
always Redis, independent of `CARE_CACHE_BACKEND`. The mechanism is unchanged
Redis `SET ... NX EX`; ES-04 only moved which alias it uses.

**verified** Two properties were established, and only two:

- unsupported lock semantics now fail loudly. `LocMemCache`, `DummyCache` and
  `DatabaseCache` all raise `TypeError` on `nx`, and the shim that returned
  `True` unconditionally is deleted;
- selecting a non-Redis cache cannot silently move locking onto a backend that
  cannot lock.

**verified** With Redis stopped and `CARE_CACHE_BACKEND=postgres`, ordinary
caching works and `Lock` raises `ConnectionError`.

**Consequence:** CARE is not Redis-free, and must not be described as such. ~25
call sites across billing, scheduling, inventory and
`sync_permissions_roles` depend on this. **ADR-0005 / ES-05 owns the
replacement.** A correct PostgreSQL lock needs a real conditional insert
(`INSERT ... ON CONFLICT DO NOTHING` or `pg_try_advisory_lock`) plus an explicit
expiry column and a sweeper, since PostgreSQL has no native TTL.

### K2 (ES-04). Recent views still require Redis — CLOSED by RF1

**Status:** Closed 2026-08-09. Superseded by RF1, which is done.

**What it said.** `care/emr/utils/recent_views.py` kept a bounded MRU list per
user and valueset using `LPUSH`, `LTRIM` and `LREM` on a dedicated
`recent_views` alias, and raised `ImproperlyConfigured` naming the feature when
that alias was not Redis-backed. It was not migrated during ES-04 because
Django's cache API has no list primitives, and emulating one by reading a JSON
blob, editing it and writing it back loses the atomicity `LPUSH`/`LTRIM`
provide.

**How it was closed.** Not by cache emulation — by the explicit PostgreSQL model
the entry named as the preferred fix. `emr.UserValueSetRecentView` is keyed by
user, valueset and code with a `last_viewed_at` timestamp; `LPUSH` after `LREM`
became `update_or_create` under a unique constraint, `LTRIM` became a delete of
rows outside the newest `MAX_RECENT_VIEW`, and `LREM` by code became a delete by
code. The alias, the constant and the `django_redis` import are gone. See RF1 in
Part RF.

### K3 (ES-04). The JWT denylist still fails open

**verified** `config/authentication.py:21` checks every authenticated request
against the cache. Under the Redis profile `IGNORE_EXCEPTIONS` is `True`, so a
Redis outage makes `cache.get` return `None`, which reads as "not invalidated" —
revoked tokens are accepted.

**Unchanged by ES-04, and stated rather than fixed.** ADR-0004 requires cache
failure semantics to be explicit, and this one now is: it is a *performance*
default applied to a *correctness-sensitive* consumer. Fixing it properly means
deciding whether token revocation belongs in the cache at all, which is a
security-model decision, not a cache-portability one. Previously recorded as §4
of this document; re-stated here because ES-04 is where the failure policy was
supposed to be pinned down.

**inferred** Under `CARE_CACHE_BACKEND=postgres` the exposure changes shape
rather than disappearing: `DatabaseCache` has no `IGNORE_EXCEPTIONS`, so a
database outage raises instead of failing open — but a database outage also
stops the request for other reasons.

### K4 (ES-04). DatabaseCache `incr` is not atomic

**Status: Resolved by the ES-04/L1 follow-up (2026-08-09), by making the
PostgreSQL cache unreachable from the rate limiter rather than by making its
`incr` atomic.**

**verified, and re-verified during the L1 follow-up.** `django_ratelimit` counts
with `add()` then `incr()`. The original entry described `DatabaseCache.incr` as
a `SELECT` then an `UPDATE`; the mechanism is slightly worse than that.
`DatabaseCache` does not define `incr` at all — it inherits `BaseCache.incr`,
which is a `get()` followed by a `set()` with no row lock, no
`SELECT ... FOR UPDATE` and no `UPDATE ... SET value = value + 1`. Two
concurrent requests both read *n* and both write *n+1*, so an increment is lost
and the limiter undercounts. Redis `INCR` is a single command executed by the
server and has no such window.

Both facts are now asserted by
`care/utils/tests/test_ratelimit_backend.py::WhyNotPostgresTests`: the
structural one (`DatabaseCache.incr is BaseCache.incr`) and the behavioural one
(interleaving the four statements `BaseCache.incr` executes loses an
increment), with the Redis contrast alongside.

**How it was addressed.** Rate limiting no longer reads the `default` cache, so
selecting `CARE_CACHE_BACKEND=postgres` no longer puts counters on a non-atomic
backend. The counters live in a dedicated `ratelimit` alias which is always
Redis. See L1 below.

**Note** `django_ratelimit.E003` is no longer silenced anywhere.
`SILENCED_SYSTEM_CHECKS` was removed from `config/settings/test.py`; the check
now passes on its own merits in every settings module, and the test suite sees
the same check results a production process does.

### K5 (ES-04). Dummy cache is not rejected in production

**verified** `CARE_CACHE_BACKEND=dummy` is accepted by any settings module.
ES-04 §10.4 says production use "SHOULD be rejected or loudly warned"; the
health check reports it as intentionally disabled, which is a report rather than
a warning at startup.

**inferred, low severity.** Selecting it is deliberate and its effects are
immediate and obvious. Recorded because the ES wording asked for it and the
weaker option was taken.

### K6 — JWT denylist is coupled to the default performance cache

**Status:** Open  
**Origin:** ES-04 cache architecture audit  
**Category:** Security / cache responsibility boundary

`config/authentication.py` uses the default Django cache alias for JWT denylist
lookups.

The default cache is intentionally configured as a performance cache and may
use failure-tolerant semantics such as:

```text
IGNORE_EXCEPTIONS = true
```

**Note, 2026-08-09.** This entry was committed truncated, and its unterminated
code fence caused everything after it — Part L included — to render as a code
block. The fence is now closed; no wording was added or removed. K6 restates K3
above, which records the same coupling in full, including the shape it takes
under `CARE_CACHE_BACKEND=postgres`. Treat K3 as the authoritative entry.

---

## Part L — Runtime roles (ES-06)

Recorded 2026-08-09 on `feature/runtime-roles`. ES-06 implemented ADR-0006:
four runtime roles, isolated route surfaces, role-aware health, and
initialization as its own ephemeral role. These are the items it did **not**
resolve, plus the findings its verification surfaced.

### L1. A non-Redis `default` cache blocks every management command

**Status:** Resolved 2026-08-09 by the ES-04 follow-up, ahead of ES-07.
**Severity:** was blocking the intended managed-cloud composition.
**Origin:** ES-06 verification of §36 and §38. **Category:** cache /
rate-limiting.

**verified** With `CARE_CACHE_BACKEND=postgres`, `manage.py migrate` — and every
other management command — aborted before doing anything:

```text
SystemCheckError: System check identified some issues:
ERRORS:
?: (django_ratelimit.E003) cache backend
   django.core.cache.backends.db.DatabaseCache does not support atomic increment
```

**verified** The same check fires for `locmem` (`is not a shared cache`). Only
the Redis backend passes it. `config/settings/test.py` silences `E003` and
`W001`; no other settings module does, so the error is invisible in the test
suite and appears the moment a real process selects a non-Redis cache.

**Consequence.** The `init` role runs management commands exclusively, so under
`CARE_CACHE_BACKEND=postgres` a deployment cannot initialize at all. The
composition documented in `07-configuration-reference.md` §60–62 and named in
ES-06 §36 is therefore not currently runnable end to end. The blocker is the
rate limiter's cache requirement, not the runtime-role architecture: with
`CARE_CACHE_BACKEND=redis` the same processes run with **no reachable Redis**,
because initialization touches no cache.

**Not fixed in ES-06, deliberately.** ES-06 §54 forbids redesigning the cache,
and every honest fix is a rate-limiter decision rather than a runtime one:

1. give the rate limiter its own cache alias — the shape `CARE_RATE_LIMIT_BACKEND`
   already anticipated in `07-configuration-reference.md` §2.2 — so the
   `default` cache stops determining whether CARE can start;
2. silence `E003` outside tests, which trades a startup error for the silent
   overshoot recorded in K4 and is the worse option;
3. keep Redis mandatory for rate limiting and say so, which contradicts
   ADR-0006's position that Redis is capability-specific.

#### Resolution (2026-08-09)

**Option 1, combined with the honest half of option 3.** Option 2 was never
considered further: silencing `E003` would have converted a loud startup failure
into the silent undercount proved in K4.

`django_ratelimit` 4.1.0 already supports selecting an alias —
`settings.RATELIMIT_USE_CACHE`, read by both `core.get_usage` and `checks.py`.
CARE had simply never set it, so it defaulted to `default`. It is now set to a
dedicated `ratelimit` alias built by `config.caches.build_ratelimit_cache`, and
the ADR-0004 cache choice no longer has any bearing on whether CARE starts.

**That alias is Redis in every profile, and option 3's premise was right.** No
portable Django cache backend offers the atomic `INCR` the library needs; K4
above proves `DatabaseCache` does not, and `LocMem` is not shared across
processes at all. So Redis is optional for ordinary cache — ADR-0004 stands — and
required for rate limiting. **CARE is not Redis-free.** It is Redis-optional for
the `default` cache and Redis-required for rate limiting. (Recent views was in
that sentence until RF1 removed it.) The
claim in ADR-0006 that Redis is capability-specific is unchanged and in fact
strengthened: rate limiting is now one of the named capabilities that requires
it, instead of requiring it implicitly through a cache alias that had nothing to
do with it.

**Scope of that constraint, stated so it is not read as permanent.** It binds
`django-ratelimit` counting through the Django cache API — not PostgreSQL, which
can increment atomically, and not CARE's architecture, which reaches the limiter
through the `config/ratelimit.py` seam. Replacing the library with a
provider-neutral limiter is roadmap item **RF2** in Part RF. Until that is
scheduled and delivered, rate limiting needs Redis and this section describes
the current state accurately.

**The `init` role no longer depends on Redis at all.** The check reads
configuration, not connectivity, so an unreachable Redis in the rate-limit alias
still checks clean. Verified directly:

```text
$ CARE_CACHE_BACKEND=postgres REDIS_URL=redis://unreachable-host:6379 \
    DJANGO_SETTINGS_MODULE=config.settings.production bash scripts/initialize.sh
Running migrations:
  No migrations to apply.
Cache table 'care_cache' already exists.
init exit status: 0
```

with the negative control confirming the URL really was unreachable
(`redis ping: unreachable -> ConnectionError`). `manage.py check` under the same
configuration reports `System check identified no issues (0 silenced)` — the
`0 silenced` matters, because nothing is being hidden to get there.

**Failure policy.** The alias sets `IGNORE_EXCEPTIONS: True` — the recent-views
alias used to set it to False, and is the contrast this paragraph was written
against; RF1 removed it — and `RATELIMIT_FAIL_OPEN` is left explicitly
False. A Redis outage therefore makes the count unknown, which
`django_ratelimit` reports as `should_limit`, which sends CARE's wrapper to
captcha validation. Fail-closed with a captcha escape, rather than an unhandled
500 on the login path (`07-configuration-reference.md` §26.4).

**One-time effect at deploy.** Counters move from the `default` cache's key space
to `care-ratelimit`, so live limiter state resets once when the change ships.
Windows are minutes; this is not material.

Covered by `care/utils/tests/test_ratelimit_backend.py` (28 tests: alias
selection, system checks including a negative control, the K4 atomicity proof,
rate limiting with a PostgreSQL `default` cache, counters shared across
independent Redis connections, window and expiry behaviour, backend-unavailable
policy, and `init` under an unreachable Redis).

### L2. Deployment settings disable every logger created during settings import

**Status:** Open. **Severity:** diagnostics. **Origin:** ES-06 §33 verification.

**verified** `config/settings/deployment.py:75` sets
`"disable_existing_loggers": True`. `django.setup()` applies that dictConfig, and
`logging.config` permanently disables every logger object that already exists —
which includes every logger created while the settings module was importing, and
every logger Celery created before Django was set up.

**This explains §11.6 of `runtime-and-deployment.md`**, which recorded that the
local Celery container never emits `celery@<host> ready.` and beat never emits
`beat: Starting...`, and attributed it to log truncation under `watchmedo`. It
is not truncation. The local Celery containers run
`config.settings.production` (via the `setdefault` in `config/celery_app.py`,
since `DJANGO_SETTINGS_MODULE` is unset for them), which imports
`deployment.py`, which disables Celery's loggers.

**Worked around, narrowly.** `config/runtime.py` resolves its logger when it
logs rather than at import, so the ES-06 startup summary survives for the `api`
and `task_worker` roles. **verified** for gunicorn, `runserver_plus` and a
Celery worker.

**Not worked around for the `scheduler` role.** In a Celery Beat process no log
record of any level reaches stderr, including records emitted directly on the
root logger — verified by probe. The `beat_init` receiver runs and the summary
is emitted; it is discarded downstream. ES-06 §33 says to use the existing
logging infrastructure and not to add a framework, so no bypass was invented.

**Recommended fix:** set `disable_existing_loggers` to `False` in
`deployment.py`, matching `base.py` and `test.py`, and re-verify Celery's own
startup lines return. That is a one-line change to a settings module shared by
production and staging and was not made inside a runtime-roles phase.

### L3. The Celery Beat container probe is still a start marker

**Status:** Open, pre-existing. **Origin:** `runtime-and-deployment.md` §5.

**verified** `scripts/celery_beat.sh` and `scripts/celery_beat-dev.sh` `touch
/tmp/healthy` *before* beat is exec'd, and `scripts/healthcheck.sh` checks for
that file. The probe therefore reports "the container started", not "beat is
scheduling".

**Not replaced in ES-06, with a reason.** The obvious replacement — asserting
that beat's schedule file was written recently — is unsound here: CARE's
periodic work is sparse (daily, and every `FILE_UPLOAD_EXPIRY_HOURS` hours), so
a healthy beat can legitimately leave the file untouched for hours and a staleness
threshold would restart-loop a working scheduler. ES-06 §28 says not to build a
health framework where one is not needed, and inventing a probe that fails on
healthy processes is worse than a marker that is honestly documented.

**Note** the managed-cloud composition does not run this process at all: it uses
a platform scheduler, so this probe is a traditional-deployment concern.

### L4. The task worker is unsafe to expose publicly

**Status:** Open by design. **Carried to ES-07 as a production blocker.**

**verified** `POST /internal/tasks/execute/` has no application-layer
authentication: no shared secret, no bearer token, no HMAC. This is the ES-03
decision, and ES-06 §14 requires it be preserved — an application secret added
to compensate for undeployed IAM would become the permanent authentication
mechanism.

**verified** ES-06 narrowed the exposure as far as the application can: the
route is registered only under `CARE_PROCESS_ROLE=task_worker`, so an API
service does not route it at all, and a worker serves no public API.

**Unchanged requirement for ES-07**, stated so it cannot be lost:

```text
the task_worker service SHALL reject unauthenticated invocation at the
platform boundary; on Cloud Run that means IAM, with roles/run.invoker
granted to the Cloud Tasks service account alone, and no allUsers binding
```

`scripts/start-worker.sh` states the same requirement at the point where a
deployment would use it.

### L5. `recent_views` keeps Redis on the API's capability list — CLOSED by RF1

**Status:** Closed 2026-08-09. The dependency it recorded no longer exists.

**What it said.** The `recent_views` cache alias was Redis in every
configuration and set `IGNORE_EXCEPTIONS: False`. It was used by specific API
endpoints, not by process startup, so with Redis absent those endpoints raised
and the rest of the API served normally. ES-06 stopped `wait_for_redis.sh` from
turning that capability dependency into a startup dependency; it did not remove
the dependency.

**How it was closed.** RF1 replaced the Redis lists with a PostgreSQL model, so
the alias no longer exists in any configuration and the recent-views endpoints
serve normally with no Redis at all.

**What remains on the API's capability list.** Rate limiting, and only rate
limiting. Note the asymmetry, because it was previously stated too strongly:
`recent_views` lacked a replacement because none had been built, and now one has
(RF1). Rate limiting lacks one because the *current library* requires an atomic
increment the Django cache API cannot express portably (RF2) — a constraint of
`django-ratelimit`, not of PostgreSQL. A Redis-compatible service is therefore
still required by the API role until RF2 is done. This does not change the
`init` role, which needs none.

### L6. Traditional deployments must now initialize explicitly

**Status:** Action required by operators. Not a defect.

**verified** `scripts/celery_beat.sh` no longer calls `scripts/initialize.sh`.
A traditional deployment that relied on starting Celery Beat to migrate will
stop migrating.

**Required change:** run `scripts/initialize.sh` as a deployment step before
starting or promoting the application. It needs a database and the application
image, and nothing else — no broker, worker or scheduler.

### L7. `ruff check .` fails on pre-existing issues outside ES-06

**Status:** Open, pre-existing at the ES-06 branch point.

**verified** Three errors in `care/utils/tests/test_lock.py` (import ordering,
an unused import, nested `with`) and 33 files that `ruff format --check` would
reformat. None is in a file ES-06 touched: every file this phase changed passes
both `ruff check` and `ruff format --check`.

**Not fixed**, because repo-wide formatting churn would bury the runtime-role
diff. Worth a dedicated cleanup commit.

---

## Part RF — Redis-free modernization roadmap

Recorded 2026-08-09. **RF1 is implemented and closed; RF2 remains architectural
direction only and is not implemented, planned into a current phase, or
authorized to start.**

Neither item belongs to ES-04, ES-05, ES-06 or ES-07. RF1 was executed as a
focused modernization of one capability. RF2 is future modernization work, to be
specified in its own Engineering Specification if and when it is scheduled.

### Why this part exists

Three separate statements are true at once, and the documents were conflating
them:

| Concept | Statement |
| --- | --- |
| **Compatibility** | Redis is fully supported and is a first-class deployment choice. A deployment that already operates Redis SHOULD use it. Nothing in the architecture discourages that. |
| **Portability** | CARE SHALL NOT depend *architecturally* on Redis. Redis-dependent capabilities are isolated behind explicit seams, and business code does not know whether Redis exists. |
| **Redis-free target** | A fully Redis-free deployment is a **future** supported profile, not a current one. Exactly one capability still requires redesign to reach it: rate limiting. Recent views was the other, and RF1 closed it. |

The seams that make portability real today are:

```text
cache alias            config/caches.py, selected by CARE_CACHE_BACKEND
rate limit wrapper     config/ratelimit.py
recent views service   care/emr/utils/recent_views.py (PostgreSQL, RF1)
async dispatcher       ADR-0003 task backend selection
```

The recent views seam is still a seam — callers go through
`RecentViewsManager`, not the model — but it no longer isolates a Redis
dependency, because there is none left to isolate.

Everything else that once required Redis has already been made portable or
moved off it: distributed locking is PostgreSQL advisory locking (ES-05),
recent views are a PostgreSQL model (RF1), `delete_pattern` is gone (ES-04), the
ordinary cache is configurable (ES-04), and Celery is one selectable dispatcher
among others (ES-03).

### RF1 — Redis-free Recent Views — **DONE**

**Status:** Complete, 2026-08-09.
**Category:** portability / schema.
**Closes:** the "preferred future fix" note in K2 and the open dependency in L5.

**Goal, as stated:** replace the Redis list operations behind recent views with
a PostgreSQL persistence model, preserving current behaviour. Met.

**What was built.** `emr.UserValueSetRecentView` — one row per user, valueset
and code, with `system`, `display`, `designation` and `last_viewed_at`. A unique
constraint on `(user, valueset, code)` and a composite index on
`(user, valueset, -last_viewed_at)`. Migration `emr/0081_recent_views_postgres`.

The mapping from the Redis commands is direct:

```text
LREM by code then LPUSH   update_or_create on the unique constraint
LTRIM 0, MAX-1            delete rows outside the newest MAX_RECENT_VIEW
LRANGE 0, -1              ORDER BY last_viewed_at DESC
LREM by code              delete by code
DEL                       delete the scope
```

`RecentViewsManager` is kept as the service boundary and still returns the same
`MinimalCodeConcept` dicts in the same order, so the four endpoints are
unchanged. It now takes a user and a valueset instead of a pre-built Redis key
string; no caller sees the model.

**Behaviour preserved:** per-user, per-valueset ordering by recency; the bound
expressed by `MAX_RECENT_VIEW` (still `getattr(settings,
"MAX_RECENT_VIEW_FOR_VALUESET", 20)`, read at class definition); de-duplication
by `code` alone, ignoring `system`, exactly as `_remove_by_code` did; removal by
code; clearing; and the early return for a payload with no code.

**Atomicity.** `update_or_create` inside a minimally scoped transaction with the
trim. The unique constraint is what makes concurrency safe: the loser of a race
gets an `IntegrityError`, which `update_or_create` retries as a fetch. A burst of
concurrent writes can leave the scope transiently above the bound — each writer
trims against the window it saw — so reads are also bounded, and the next write
brings the table back down. Verified with real threads on real PostgreSQL in
`care/emr/tests/test_recent_views.py::ConcurrentWriteTests`.

**Data migration: none, deliberately.** The table starts empty. Recent-view
state in Redis is ephemeral, non-critical user convenience state with no
clinical or audit value, the deployment is greenfield, and reading the old lists
would reintroduce — for one migration — exactly the dependency this removes.

**Redis coupling removed:** `CACHES["recent_views"]`,
`RECENT_VIEWS_CACHE_ALIAS`, `build_redis_only_cache` (its last two consumers,
locking and recent views, are both gone), and every `django_redis` /
`get_redis_connection` / `LPUSH` / `LTRIM` / `LREM` / `LRANGE` reference on this
path. `django-redis` stays in the dependencies: rate limiting needs it, and so
does `CARE_CACHE_BACKEND=redis`.

**Consequence, now realised:** a deployment that runs without Redis keeps the
recent-views endpoints. Rate limiting (RF2) is the only capability left holding
the Redis requirement.

### RF2 — Redis-free Rate Limiting

**Status:** Future work. Not scheduled. Not part of ES-04/05/06/07.
**Category:** portability / security control.
**Related:** K4 (why `DatabaseCache` cannot carry the counters), L1 (why the
counters moved to their own alias).

**Goal:** replace `django-ratelimit` with a provider-neutral rate-limiting
implementation that supports PostgreSQL atomic counter semantics, without
weakening the limit.

**Direction only.** The constraint is not Redis; it is the current library.
`django-ratelimit` counts through the Django cache API with `add()` then
`incr()` and requires the increment to be atomic. Django's `DatabaseCache`
inherits `BaseCache.incr`, an unlocked `get()`-then-`set()`, so concurrent
requests lose increments — proved in K4 and asserted by
`care/utils/tests/test_ratelimit_backend.py::WhyNotPostgresTests`. PostgreSQL
itself can count atomically; the Django cache API is what cannot express it.
The direction is therefore an implementation that can use a PostgreSQL atomic
counter directly rather than through a cache backend, while keeping Redis as an
equally supported store.

**Not designed here.** No table, no API, no counter algorithm, no window
strategy, no library selection and no migration path is specified by this entry.

**Behaviour to preserve:** the existing rate definitions and group/key shape;
the fixed fail-closed-with-captcha-escape policy documented in
`07-configuration-reference.md` §26.4; and counters shared across all instances
of a role.

**Consequence while open:** rate limiting requires a Redis-compatible service in
every deployment profile, including the managed-cloud one. This is the single
capability that keeps a Redis-free deployment out of reach today.

### What RF1 and RF2 do not cover

- Distributed locking. Already PostgreSQL (ADR-0005 / ES-05). No RF item needed.
- Ordinary caching. Already configurable (ADR-0004 / ES-04). No RF item needed.
- Celery. Already one dispatcher among others (ADR-0003 / ES-03); a deployment
  that selects `cloud_tasks` needs no broker.
- Any change to rate limiting. RF2 remains documentation of direction; RF1 did
  not touch the limiter, the `ratelimit` alias, or `django-ratelimit`.
