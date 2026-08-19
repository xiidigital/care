# CARE on Google Cloud Platform

Infrastructure as code for the managed GCP deployment, implementing ADR-0007
and ES-07.

The primary composition is **Redis-free**. No Memorystore instance, no Redis VM,
no managed Redis-compatible service, and no Redis variable in any runtime
environment. Redis remains fully supported by the application; it is simply not
selected here.

```
Artifact Registry ──> one immutable CARE image
                          │
                          ├── Cloud Run  care-<env>-api      CARE_PROCESS_ROLE=api
                          ├── Cloud Run  care-<env>-worker   CARE_PROCESS_ROLE=task_worker   (private)
                          └── Cloud Run Jobs                 CARE_PROCESS_ROLE=init

Cloud SQL PostgreSQL   application data, Django cache, rate-limit counters,
                       recent views, advisory locks
Cloud Storage          patient / facility / report objects, private
Cloud Tasks            authenticated dispatch to the private worker
Cloud Scheduler        the two operations Celery Beat used to fire
Secret Manager         runtime secrets
Cloud Logging          container stdout and stderr
```

---

## 1. Tool

**OpenTofu**, not Terraform.

ADR-0007 originally selected Terraform. It was amended on 2026-08-11 to select
OpenTofu instead; the HCL is unchanged, and only the CLI and the
`required_version` constraint differ. See the ADR's *Decision* and *Alternatives
Considered* sections.

Everywhere below, `tofu` is the command.

## 2. Prerequisites

| Tool | Why |
| --- | --- |
| `tofu` ≥ 1.8 | applies the configuration |
| `gcloud` | authentication, image push, secret provisioning, verification |
| `docker` | builds the application image |
| a GCP project with billing | everything |

No `kubectl`. No Redis. No local PostgreSQL.

Authenticate once:

```bash
gcloud auth application-default login
```

Application Default Credentials, for both the operator and the runtime. No
service-account JSON key is created, downloaded or committed anywhere in this
repository (ADR-0007, ES-07 section 113).

## 3. Permissions

### Operator, for bootstrap

- `roles/storage.admin`
- `roles/serviceusage.serviceUsageAdmin`

### Operator, for an environment apply

- `roles/run.admin`
- `roles/cloudsql.admin`
- `roles/storage.admin`
- `roles/cloudtasks.admin`
- `roles/cloudscheduler.admin`
- `roles/secretmanager.admin`
- `roles/artifactregistry.admin`
- `roles/iam.serviceAccountAdmin`
- `roles/resourcemanager.projectIamAdmin` — the module grants
  `roles/cloudsql.client` at project level
- `roles/monitoring.editor` — only when `alerts_enabled` is true

`roles/owner` covers all of it and is what a single-maintainer project usually
has. It is broader than required, and ES-07 section 115 asks that the
*long-term* deployment identity be narrower than the bootstrap one — so when
CI/CD arrives (ES-08), grant the list above rather than Owner.

No organisation-level or folder-level permission is needed. Everything here
works in an ordinary project (ES-07 section 116).

## 4. Layout

```
infrastructure/
  README.md                      this file
  scripts/
    publish-image.sh             build and push; prints the digest
    provision-secrets.sh         generate secret values; never prints one
  terraform/
    bootstrap/                   the state bucket. Once per project.
    modules/
      care-environment/          one complete CARE environment
      cloud-run-service/         a role-aware Cloud Run service
    environments/
      dev/  staging/  prod/      one root each, one state prefix each
```

Two modules, not ten. `care-environment` is a meaningful reusable boundary —
three environments instantiate it — and `cloud-run-service` is used twice and
is where the "the worker cannot be public" guard lives. Nothing else is grouped
into a module merely because it could be (ADR-0007 *Terraform structure*).

## 5. State

Remote, in GCS, one bucket per project and one prefix per environment.

- versioning on, for recovery from a bad apply
- uniform bucket-level access
- public access prevention enforced
- superseded generations deleted after 90 days; the live object never is
- `prevent_destroy`

Runtime service accounts receive no access to it. The prefix is fixed in each
root's `backend` block and cannot be passed on the command line, so a
cross-environment apply requires editing a tracked file.

## 6. Bootstrap, once per project

State cannot live in a bucket that state has to create. That circularity is
resolved explicitly rather than by an undocumented console step:

```bash
cd infrastructure/terraform/bootstrap
```

```bash
cp terraform.tfvars.example terraform.tfvars
```

Set `project_id`, then:

```bash
tofu init && tofu plan
```

```bash
tofu apply
```

Full detail, including how to migrate this root's own state into the bucket
afterwards, is in `terraform/bootstrap/README.md`.

**Bootstrap once; apply an environment repeatedly.** They are separate
operations with separate state and separate lifetimes.

## 7. Deploying an environment

Cloud Run needs an image, and the registry that holds the image is created by
the same configuration. ES-07 section 131 requires that this be handled
explicitly rather than hidden, so the first deployment is staged.

### 7.1 Configure

```bash
cd infrastructure/terraform/environments/dev
```

```bash
cp backend.hcl.example backend.hcl && cp terraform.tfvars.example terraform.tfvars
```

Set the bucket in `backend.hcl`, and `project_id` and `image` in
`terraform.tfvars`. Both are gitignored.

```bash
tofu init -backend-config=backend.hcl
```

### 7.2 Create the registry

Set `image` to the tag you are about to publish — it need not exist yet — and
create the repository alone:

```bash
tofu apply -target=module.care.google_artifact_registry_repository.care
```

This is the one legitimate use of `-target` in the workflow. It is a
bootstrapping step, not routine.

### 7.3 Publish the image

```bash
infrastructure/scripts/publish-image.sh --project <project> --repository care-dev
```

It prints an immutable digest reference. Put that in `terraform.tfvars` as
`image`. One image serves the API, the worker and every Job.

### 7.4 Apply

```bash
tofu plan
```

Read it. Then:

```bash
tofu apply
```

**The first apply of a new environment stops part-way, and that is expected.**
It creates the Cloud SQL instance, the buckets, the queue, the service accounts
and the four secret *containers*, and then fails creating the Cloud Run
services:

```text
Error waiting to create Service: ... secret_key_ref.name:
Secret projects/<n>/secrets/care-<env>-jwks-base64/versions/latest was not found
```

A container with no version cannot be mounted, and OpenTofu never creates a
version, because that would put the value in state. So the sequence is
apply → provision secrets → apply again, and the second apply completes.
Nothing is wrong at this point and nothing needs to be undone; go to 7.5 and
then run `tofu apply` once more.

### 7.5 Provision secrets

The apply created the secret containers and their IAM. It created no values:
OpenTofu never holds a secret, so none can reach state.

```bash
infrastructure/scripts/provision-secrets.sh --env dev --project <project> --image <image>
```

This generates `DJANGO_SECRET_KEY`, `JWKS_BASE64`, the database password and
`DATABASE_URL`, creates the Cloud SQL user, and pipes every value directly into
Secret Manager. Nothing is printed or written to disk.

Then finish the apply:

```bash
tofu apply
```

### 7.6 Initialize the database

**Before serving traffic.** API and worker instances never migrate — several
start concurrently, and none may race the schema.

```bash
gcloud run jobs execute care-dev-init --region us-central1 --project <project> --wait
```

It runs the committed `scripts/initialize.sh`:

```
migrate
createcachetable        creates care_cache and care_ratelimit_cache
compilemessages
sync_permissions_roles  under a PostgreSQL advisory lock
sync_valueset
```

`--wait` returns non-zero on failure, and the Job has `max_retries = 0`, so a
failed initialization fails the deployment procedure instead of being retried
into an apparent success.

### 7.7 Restart the services

The services were created before the schema existed and may have unhealthy
revisions. Force a new revision now that initialization has completed:

```bash
gcloud run services update care-dev-api --region us-central1 --project <project> --update-env-vars=CARE_INIT_GENERATION=1
```

Repeat for `care-dev-worker`. On subsequent deployments this is unnecessary:
change the image, and the ordinary sequence in section 9 applies.

### 7.8 Development data (dev only, optional)

A greenfield environment has no account to log in as. The fixture Job creates
one, along with synthetic facilities, patients and clinical records.

It is **development tooling and not part of any deployment**: it is never
executed by an apply, it seeds users whose passwords are published, and the
module refuses to create it for any environment but `dev`.

Build the fixture image — a thin layer over the runtime image that adds Faker,
which the production image correctly omits:

```bash
infrastructure/scripts/publish-image.sh --project <project> --repository care-dev --fixtures-from <runtime-image@sha256:...>
```

Set both values in `terraform.tfvars`, apply, then execute it by hand:

```
enable_fixture_loader = true
fixture_image         = "us-central1-docker.pkg.dev/<project>/care-dev/care-fixtures@sha256:..."
```

```bash
gcloud run jobs execute care-dev-load-fixtures --region us-central1 --project <project> --wait
```

`load_fixtures` is destructive to data already present and runs inside one
transaction. Set `scheduler_jobs_enabled = false` first if the data is meant to
survive — the cleanup schedules are indifferent to whether data is synthetic.

Credentials are in `care/fixtures/fixtures.md`.

## 7.9 Staging

Staging is the same procedure with `staging` substituted for `dev` throughout —
same module, same composition, same nine steps. What differs is stated here so
nobody has to infer it from a diff.

**A separate project is the intended shape.** ADR-0007 requires environment
separation to include stateful resources and secrets. Every stateful name the
module builds derives from `var.environment`, so a staging environment applied
into the dev project would still have its own Cloud SQL instance, its own
buckets, its own secret containers and its own queue — but it would share the
project's quotas, IAM surface and audit trail. Prefer a project of its own; if
one is not available, the isolation above is what you are relying on, and it is
worth saying so in the deployment record.

Either way, **bootstrap the state bucket in whichever project holds staging**
(section 6) before `tofu init`. State prefix `environments/staging` is fixed in
`main.tf` and cannot be overridden from the command line.

Never point staging at a dev database, bucket or secret value. Run
`provision-secrets.sh --env staging` to generate its own; secret values are
per-environment by construction, not by convention.

Two differences from dev in the tracked configuration, both deliberate:

| | dev | staging |
| --- | --- | --- |
| Cloud SQL | `db-f1-micro`, no PITR | `db-g1-small`, PITR on, 7 backups |
| deletion protection | off, both guards | **on**, both guards |
| buckets | `force_destroy`, unversioned | protected, versioned |
| fixture loader | available | **refused by the module** |
| alerts | off | on |

Deletion protection being on means a teardown is a reviewable edit to a tracked
file, not something a routine plan can do. That is the point of it.

### Email in staging

Staging defaults to Django's console email backend, and that is a valid
configuration rather than a stopgap. Under it CARE renders the message,
dispatches through Cloud Tasks, executes in the worker and writes the rendered
message where Cloud Logging keeps it — the whole generation path, verifiable
end to end. Only the relay hop is absent.

Verify it as part of acceptance:

```bash
gcloud logging read 'resource.labels.service_name="care-staging-worker" AND textPayload:"Subject:"' --project <project> --limit 5
```

**No email provider is named anywhere in this repository, and none may be.**
Enabling external delivery later is a configuration change: clear
`django_email_backend`, supply `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER` and
`EMAIL_USE_TLS` (or `EMAIL_USE_SSL`) through `extra_env`, declare
`EMAIL_PASSWORD` in `optional_secrets`, and write its value with
`gcloud secrets versions add`. No infrastructure architecture changes, and no
credential is ever committed. See
`docs/xii/architecture/inventory/unresolved-items.md` N1.

## 8. Verifying

```bash
curl -sS "$(tofu output -raw api_url)/ping/"
```

The worker must reject anonymous callers:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$(tofu output -raw worker_task_endpoint)"
```

`403` is correct — Cloud Run IAM rejected it before Django saw it. Anything in
the 2xx range means the boundary is broken and the deployment is invalid.

Confirm no Redis exists:

```bash
gcloud redis instances list --project <project> --region us-central1
```

## 9. Ordinary deployment sequence

**An ordinary application release no longer runs `tofu apply`.** ES-08 gave the
image field of the Cloud Run services and Jobs to application delivery, and the
resources ignore changes to it: `var.image` is what a greenfield service is
*created* with, and after that the deployed digest is whatever the last release
deployed. A stale `image` in tfvars is harmless and a plan will not propose
putting it back.

So there are two sequences now, and which one you want depends on whether
infrastructure changed.

**Application only** — the normal case:

```
1. push to gcp                         CI, then build-image.yml publishes a digest
2. deploy-staging.yml <digest>          init -> worker -> API -> Jobs, then acceptance
3. promote-production.yml <digest>      approved, same digest, no rebuild
```

or the same thing by hand, which is what those workflows run:

```bash
export CARE_ENVIRONMENT=staging GCP_PROJECT_ID=<project> GCP_REGION=us-central1
infrastructure/scripts/gcp/deploy.sh     --digest sha256:...
infrastructure/scripts/gcp/acceptance.sh --digest sha256:...
```

`deploy.sh` updates the init Job to the digest, executes it, **stops if it
fails**, then deploys the worker, then the API, then the application Jobs, and
reads each deployed image back to confirm it is the digest that was asked for.
The init gate is the step that matters: an API revision that depends on an
unapplied migration must not take traffic.

**Infrastructure changed** — a separate path with its own approval:

```
1. tofu plan                           infra-check.yml, or locally
2. review                              destructive changes require explicit intent
3. tofu apply                          infra-apply.yml, approved
4. then an application release if one is needed
```

See `docs/xii/architecture/08-continuous-delivery.md` for the workflows, the
GitHub and Workload Identity Federation setup, acceptance, rollback and the
manual equivalents of every step.

## 10. Cost

Cloud SQL is the only resource that costs money while nothing is happening.
Everything else scales to zero.

| Resource | dev | staging | Note |
| --- | --- | --- | --- |
| Cloud SQL | ~USD 8/month (`db-f1-micro`) | ~USD 25–30/month (`db-g1-small`) | shared core; staging adds PITR write-ahead log retention and 7 backups |
| Cloud Run API / worker | ~0 idle | ~0 idle | `min_instances = 0` in both |
| Cloud Run Jobs | ~0 idle | ~0 idle | compute only while executing |
| Cloud Storage | usage | usage + versioning | staging keeps noncurrent generations |
| Cloud Tasks | usage | usage | first million operations/month free |
| Artifact Registry | ~0.10/GB/month | ~0.10/GB/month | one image each |
| Logging | usage | usage | first 50 GiB/month free |
| Monitoring | none | alert policies | staging has `alerts_enabled = true` |
| **Redis** | **none** | **none** | none is provisioned in either |

Cloud SQL is the standing cost in both, and staging's is the larger one because
it is a real instance with point-in-time recovery — the whole point being that a
migration's cost becomes visible here before production sees it.

Raising `api_min_instances` above zero is the single easiest way to turn this
into a continuous cost, which is why production requires the decision explicitly.

## 11. Teardown

Dev is designed to be destroyable: `sql_deletion_protection` and
`bucket_force_destroy` are set for it.

```bash
cd infrastructure/terraform/environments/dev && tofu destroy
```

Two things survive on purpose:

- **Secrets.** `prevent_destroy` is set. Delete them deliberately with
  `gcloud secrets delete` if the environment is really gone.
- **APIs.** `disable_on_destroy = false`; disabling a project API can cascade
  into unrelated resources.

Cloud SQL reserves a deleted instance name for about a week. To rebuild sooner,
set `sql_instance_name_suffix` in `terraform.tfvars`.

Staging and production have both deletion guards on. Destroying either means
deliberately turning them off first, which is a reviewable change to a tracked
file — not something a routine plan can do.

`tofu destroy` is not a rollback mechanism. To roll back, deploy the previous
image digest and run the init Job.

## 12. Related documents

- `docs/xii/adr/ADR-0007-terraform-for-GCP.md` — the decision
- `docs/xii/implementation/ES-07-GCP-infrastructur-and-deployment.md` — this phase
- `docs/xii/architecture/06-operations.md` — operating the deployment
- `docs/xii/architecture/07-configuration-reference.md` — every environment variable
- `docs/xii/architecture/inventory/gcp-configuration.md` — which role gets which value
