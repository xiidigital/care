<!--
Continuous delivery for the maintained CARE fork.

The operational companion to ADR-0008 and ES-08. ADR-0008 says what the
delivery architecture must be; this says what exists, what an operator has to
configure, and what to do when something fails.

Related:
  docs/xii/adr/ADR-0008-automated-continuous-integration.md   the decision
  docs/xii/implementation/ES-08-ci.md                         this phase
  docs/xii/architecture/06-operations.md                      operating GCP
  docs/xii/architecture/07-configuration-reference.md         every variable
  infrastructure/README.md                                    infrastructure
-->

# Continuous Delivery

- **Status:** Implemented on `feature/ci-controlled-delivery` (ES-08)
- **Platform:** GitHub Actions
- **First deployment adapter:** GCP
- **Artifact:** one OCI image, identified by digest

---

## 1. The shape of it

A CARE release is an immutable OCI image. Not a branch, not a tag, not
"whatever staging is running" — a digest, with metadata saying which commit and
which upstream CARE base produced it.

```text
commit
  │
  ▼
CI ─────────────────────────── validation, no credentials
  │
  ▼
build ──────────────────────── one image, published by digest
  │
  ▼
staging deployment ─────────── init → worker → API → Jobs
  │
  ▼
staging acceptance ─────────── against the running environment
  │
  ▼
accepted digest record
  │
  ▼
production promotion ───────── approval, then the same digest
```

Three things this diagram is making a point about:

**The build happens once.** Production deploys the digest staging accepted, not
a rebuild of the same commit. A rebuild can differ — dependencies, build
tooling, generated assets, timestamps — and then "production runs what we
tested" is a claim nobody can check (ADR-0008 section 5).

**Deployment is an adapter.** The build knows nothing about Cloud Run. The
GCP-specific part is `infrastructure/scripts/gcp/` plus the environment
variables that name a project, a region and some services. Another runtime
would be another adapter around the same artifact (ADR-0008 section 7).

**Each phase can fail on its own.** A published image is not a deployment, a
successful `tofu apply` is not a working CARE, a green init Job is not a healthy
API, and a green staging is not production approval (ADR-0008 section 29).

---

## 2. Workflows

| Workflow | Trigger | Credentials | What it does |
| --- | --- | --- | --- |
| `ci.yml` | pull request, merge queue, push to `gcp` or `feature/**`, `workflow_call` | none | lint, full test suite, Redis-free profile tests, production image build and inspection, delivery invariants, secret scan |
| `infra-check.yml` | pull request touching `infrastructure/**`, push to `gcp`, manual | none for validate; `infrastructure-plan` for plan | `tofu fmt -check`, `tofu validate` on every root and module; on request, one environment's `tofu plan` |
| `build-image.yml` | push to `gcp`, manual, `workflow_call` | `artifact-publication` | runs `ci.yml`, then builds and publishes the production image, records provenance, outputs the digest |
| `deploy-app.yml` | `workflow_call` only | the target environment | the GCP deployment adapter: init, worker, API, Jobs, digest verification, deployment record |
| `deploy-staging.yml` | manual, or called by `build-image.yml` | `staging` | deploys a digest to staging, runs acceptance, writes the accepted-digest record |
| `promote-production.yml` | manual only | `production` | verifies eligibility, then deploys the accepted digest. No build step |
| `rollback.yml` | manual only | the target environment | redeploys a previously published digest |
| `infra-apply.yml` | manual only | `infrastructure-apply` | plans and applies one environment. Never destroys |

The upstream CARE workflows (`test-pull-request.yml`, `linter.yml`,
`deploy.yml`, `docs.yml`, `release.yml`, `validate-pr-title.yml`) are left
alone. `deploy.yml` and `docs.yml` are guarded by
`github.repository == 'ohcnetwork/care'` and do not run in this fork.

### Why these boundaries

CI is separate from build because validation should run on a pull request that
must never see a credential. Build is separate from deploy because an artifact
outlives the environment it was first deployed to. Staging is separate from
production because they are different trust levels, and `deploy-app.yml` is
shared between them because they must not be different deployment procedures
(ES-08 sections 6, 101).

---

## 3. GitHub setup, once per repository

Five GitHub Environments. Four exist to give the GCP trust relationship
something to match; one of them also gates a human approval.

| Environment | Protection | Purpose |
| --- | --- | --- |
| `artifact-publication` | none needed | the identity that may publish images |
| `staging` | optional reviewers | staging deployment and acceptance |
| `production` | **required reviewers** | production promotion |
| `infrastructure-plan` | none needed | reads state to plan |
| `infrastructure-apply` | **required reviewers** | applies infrastructure |

GitHub only includes an `environment` claim in the OIDC token when a job
declares `environment:`. That claim is what the GCP binding restricts on, so a
job that declares no environment cannot authenticate at all — including a job
in a pull request from this same repository (ES-08 section 16).

### Variables

Repository or environment **variables**, not secrets. A project id, a region and
a service-account email authenticate nobody; calling them secrets hides the
configuration without protecting anything (ES-08 section 72). No GitHub secret
is required by any of these workflows.

Repository-level (or `artifact-publication`):

```text
GCP_PROJECT_ID                    e.g. example-project
GCP_REGION                        e.g. us-central1
ARTIFACT_REGISTRY_REPOSITORY      e.g. care-staging
IMAGE_NAME                        optional; defaults to care
GCP_WORKLOAD_IDENTITY_PROVIDER    projects/<number>/locations/global/workloadIdentityPools/care-github/providers/github
GCP_PUBLISHER_SERVICE_ACCOUNT     care-ci-publisher@example-project.iam.gserviceaccount.com
```

Per deployment environment (`staging`, `production`):

```text
CARE_ENVIRONMENT                  staging | prod
GCP_PROJECT_ID
GCP_REGION
GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_DEPLOY_SERVICE_ACCOUNT        care-deploy-staging@... | care-deploy-prod@...
ARTIFACT_REGISTRY_REPOSITORY

CARE_API_SERVICE                  optional; defaults to care-<env>-api
CARE_WORKER_SERVICE               optional; defaults to care-<env>-worker
CARE_INIT_JOB                     optional; defaults to care-<env>-init
CARE_APP_JOBS                     optional; defaults to the two cleanup Jobs
```

For `infrastructure-plan` and `infrastructure-apply`:

```text
GCP_PROJECT_ID
GCP_REGION
TF_STATE_BUCKET                   care-tfstate-<project>
GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_INFRA_SERVICE_ACCOUNT         care-infra@example-project.iam.gserviceaccount.com
INITIAL_IMAGE                     optional; only used when creating a service
```

The defaults follow the naming convention the OpenTofu module implements. An
installation that does not follow it sets the overrides; nothing in the scripts
or workflows hardcodes a name (ES-08 sections 78, 123).

### Status checks

Worth making required on the integration branch, once the workflows have run
there: `Format and lint`, `Application regression suite`, `Redis-free
composition`, `Production image`, `Delivery invariants`, `Secret scan`, `fmt and
validate`. Branch protection is a repository setting and is not configured from
this repository (ES-08 section 131).

---

## 4. Workload Identity Federation

No service-account key exists, anywhere. GitHub mints an OIDC token, GCP
exchanges it for a short-lived access token, and three things must hold before
that happens (ADR-0008 section 23):

```text
1  the token is signed by token.actions.githubusercontent.com
2  assertion.repository == the one repository the provider names
3  the job's environment claim matches the identity's impersonation binding
```

Declared in `infrastructure/terraform/modules/github-oidc`, instantiated by the
bootstrap root when `github_repository` is set. Four identities, because their
permissions differ:

| Identity | May do | Trusted from |
| --- | --- | --- |
| `care-ci-publisher` | write to one Artifact Registry repository | `artifact-publication` |
| `care-deploy-staging` | update staging's services and Jobs, run its init Job, act as its runtime identities | `staging` |
| `care-deploy-prod` | the same in production | `production` |
| `care-infra` | read and write state; project roles only if granted | `infrastructure-plan`, `infrastructure-apply` |

Creating an identity and deciding what it may do are separate changes. The
module grants nothing on any environment; an environment grants what it chooses
through `deployment_principals` and `image_publisher_principals`, and those
grants are bound to that environment's own resources
(`modules/care-environment/delivery.tf`).

`care-infra` gets project-level administrative roles only when
`grant_infrastructure_roles = true`. It is off by default: applying from an
authenticated operator is still supported, and an identity holding unused
project administration is a standing risk (ES-08 section 75).

### The bootstrap order

Federation must exist before CI can use it, so the first apply is done by an
authenticated operator. There is no way around this and it is not hidden
(ES-08 section 75):

```bash
cd infrastructure/terraform/bootstrap
# terraform.tfvars: project_id, github_repository = "owner/name"
tofu init
tofu apply
tofu output github_workload_identity_provider
tofu output github_deployment_principals
```

Then put those values into GitHub, and pass the principals into each
environment's tfvars.

---

## 5. Build and publish

`build-image.yml` on a push to `gcp`, or manually.

It runs `ci.yml` in full first: a failed test run must not produce an artifact
that promotion later treats as a candidate (ADR-0008 section 100). Then it
builds `docker/prod.Dockerfile` from a clean checkout with
`APP_VERSION=<commit sha>`, pushes to Artifact Registry, reads the digest back
from the registry, pulls the published image and inspects it, and writes
`release-metadata.json`:

```json
{
  "source_commit": "...",
  "upstream_base_commit": "...",
  "image_digest": "sha256:...",
  "image_ref": "<region>-docker.pkg.dev/<project>/<repo>/care@sha256:...",
  "workflow_run_url": "...",
  "built_at": "..."
}
```

Tags `<commit sha>` and `candidate` exist for humans. Deployment and promotion
use the digest, and the workflows refuse anything else (ADR-0008 section 4).

Nothing is deployed by a build. The job summary prints the digest and says so.

### The build context

`docker/prod.Dockerfile.dockerignore` is an allowlist: everything is excluded,
then the dependency manifests, the Django project, the plugin installer, the
role entrypoints, the translation sources and the reference data are allowed
back in. BuildKit resolves a Dockerfile-specific ignore file before the root
one.

This closed P2. Under the old seven-line denylist, `COPY . $APP_HOME` copied
whatever untracked files the builder had — 180 MB of local scratch on one
machine, and a developer-generated `jwks.b64.txt` with it. Measured on this
branch with 163 MB of untracked scratch present: 781 MB and byte-identical file
inventory whether built from the working tree or from a clean `git archive`
export.

CI plants scratch files in the context before every build and fails if any of
them reaches the image, so the property is checked on each pull request rather
than asserted once.

### The upstream base

`UPSTREAM_BASE` at the repository root, in the format
`05-upstream-sync.md` section 27 prescribes. Updated only during upstream
synchronization; no workflow writes it. Verify with
`git merge-base HEAD upstream/develop`.

---

## 6. Staging deployment

`deploy-staging.yml`, with a digest. Manual by default: this fork's staging is
also the environment release decisions are made against, and redeploying it on
every commit would keep moving it under whoever is looking at it. Chain it from
a build with `deploy_staging: true` when that is what you want
(ES-08 section 105).

The order is in `infrastructure/scripts/gcp/deploy.sh`, not in YAML, so an
operator can run exactly what CI runs:

```text
1  the digest must exist in the registry             (no build, ever)
2  init Job -> the digest, verified
3  init runs; failure stops here                     (ADR-0008 section 11)
4  worker -> the digest, ready, still private
5  API -> the digest, ready
6  application Jobs -> the digest
7  every deployed image read back and compared
```

Worker before API, because the API produces task payloads and a payload whose
handler does not exist yet is a task that fails until it is retried into the
void.

The init boundary is marked in the log:

```text
INIT-STARTED  care-staging-init 2026-08-19T10:00:00Z
INIT-FINISHED care-staging-init-abcde exit=0 2026-08-19T10:03:11Z
```

After a cancellation those two lines are what tell an operator whether the
schema moved (ES-08 section 52). Deployment concurrency is one group per
environment with cancellation disabled, so a second deployment queues instead of
interleaving.

---

## 7. Staging acceptance

`infrastructure/scripts/gcp/acceptance.sh`, against the running environment. A
health check says a process is up; acceptance says the release satisfies the
architecture (ADR-0008 section 44).

| Check | How |
| --- | --- |
| API liveness | `/ping/` |
| database and cache | `/health/`, which reports `backend: postgres`, `table: care_cache` |
| build identity | `/app_version/` compared with the source commit |
| same digest everywhere | API, worker, init Job and application Jobs read back |
| worker IAM | no public member in the policy, and an anonymous request gets 403 before Django |
| route isolation | the API does not serve the task endpoint |
| runtime composition | `CARE_PROCESS_ROLE`, cache, rate limit, task and storage backends read from the deployed services |
| Redis-free | no `REDIS_URL` in either service, no Memorystore instance |
| rate limiting | sequential requests to the password-reset check until 429 |
| Cloud Tasks | a registered maintenance task enqueued with an OIDC token, then observed in the worker's log |
| Cloud Storage | one small object written, read back, compared and deleted |
| scheduled Job | one Job executed on demand; the schedule is not touched |
| email backend | the configured backend, recorded |

Failures are failures: a red required check exits non-zero, no accepted record
is written, and normal promotion refuses that digest (ES-08 section 41).

Cleanup: the storage object is deleted, the task creates nothing, the rate-limit
counters expire. The Job execution leaves an execution record, which is the
point of it.

### What acceptance does not cover

**Recent Views.** It needs an authenticated user, staging deliberately refuses
the fixture loader (ES-08 section 119), and creating a synthetic user from CI
would need either database credentials or a new management command. Verified
manually during ES-07 staging acceptance and recorded as an open item.

**Advisory locking** is covered indirectly: `sync_permissions_roles` takes a
PostgreSQL transaction-scoped advisory lock, and it runs on every init, so a
green init exercises that path. No test deliberately contends for the lock in
staging — deadlocking the staging database is not a check (ES-08 section 176).

**External email delivery** is not verified and is not required to be. Console
email is a valid configuration; CI/CD selects no provider
(ADR-0008 section 22).

---

## 8. Promotion to production

`promote-production.yml`, manual, with a digest.

```text
eligibility (no credentials)
  ├─ is it a digest?
  ├─ is there a staging acceptance record for it?
  └─ summary: digest, source commit, acceptance status
       │
       ▼
production environment approval
       │
       ▼
deploy-app.yml, environment: production
  └─ init → worker → API → Jobs → smoke
```

The eligibility job runs before the gate so an approver sees what they are
approving (ES-08 section 158). The acceptance record is a workflow artifact
named `staging-accepted-<digest hex>`, written by the staging workflow from the
values actually deployed — not typed by an operator (ES-08 section 127).

There is no emergency bypass. With no production environment yet, designing one
would be designing for a situation nobody has met (ES-08 section 129).

Production gets smoke verification, not the acceptance suite: that suite
dispatches tasks, writes objects and exhausts a rate-limit counter
(ADR-0008 section 45).

**No production environment exists today.** The workflow is implemented and
validated; run against an unconfigured `production` environment it fails at the
configuration check, naming what is missing, and creates nothing
(ES-08 sections 107, 183).

---

## 9. Rollback

```bash
# what is deployed, and what has been published
CARE_ENVIRONMENT=staging GCP_PROJECT_ID=... GCP_REGION=us-central1 \
  infrastructure/scripts/gcp/rollback.sh --list

# roll back
CARE_ENVIRONMENT=staging GCP_PROJECT_ID=... GCP_REGION=us-central1 \
  infrastructure/scripts/gcp/rollback.sh --digest sha256:... --confirm
```

or `rollback.yml` with the environment and the digest.

**Application rollback is not database rollback.** Migrations applied by the
release being rolled back stay applied. The delivery system does not reverse
them and will not pretend to (ADR-0008 sections 13, 27). If the current schema
is incompatible with the older artifact, roll forward instead: fix the defect,
build a new digest, accept it in staging.

The rollback candidate is the digest the environment *ran*, read from the
platform, not the previous tag and not the previous commit. Every deployment
prints it and records it as `previous_digest` (ES-08 section 92).

Init runs during a rollback. The older artifact's own initialization contract is
what should decide whether the environment it is going back into is usable.

Artifact Registry has no cleanup policy on the CARE repositories, so previous
digests remain pullable. Do not add aggressive retention: it deletes rollback
candidates (ES-08 section 55).

---

## 10. Infrastructure delivery

Separate path, separate identity, separate gate (ADR-0008 section 18).

```text
pull request touching infrastructure/**
  → tofu fmt -check, tofu validate            no credentials

manual, infra-check.yml
  → tofu plan for one environment             infrastructure-plan

manual, infra-apply.yml
  → plan, refuse if destructive, apply        infrastructure-apply, approved
```

An application release runs none of this. A new digest needs no
`tofu apply`, because OpenTofu no longer owns the image field.

`tofu destroy` appears in no workflow. It is not a rollback mechanism
(ADR-0008 section 43), and deleting infrastructure stays an operator action
against a checkout where ADR-0007's deletion protections can be turned off
deliberately.

A plan that destroys or replaces anything stops the apply unless the operator
passes `allow_destructive`. Plan output is not published: on a public repository
logs and artifacts are public, and a plan carries every value it touched
(ES-08 section 64). The job summary reports the change counts and every
destructive line; run the plan locally for the rest.

### The image ownership boundary

OpenTofu creates and configures the Cloud Run services and Jobs. Application
delivery moves their image field. Exactly that field is under `ignore_changes`,
in `modules/cloud-run-service/main.tf` and in `run.tf` for the Jobs.

`var.image` is the **initial** image — what a greenfield service is created
with. After that the deployed digest is whatever the last release deployed, and
a stale value in tfvars neither rolls an environment back nor shows up in a plan
(ES-08 sections 140, 143).

Two more fields are ignored for the same reason and no other: `client` and
`client_version`, which Cloud Run uses to record the tool that last wrote the
resource. Deploying with gcloud stamps them, and OpenTofu would otherwise
propose unsetting them on every plan afterwards — five diffs produced by nothing
but the act of deploying.

Everything else about those resources still belongs to OpenTofu, and a plan
still reports drift in any of it. Verified: after a deployment moved all five
staging resources to a new digest, and after an apply, `tofu plan` reports
**No changes**.

---

## 11. Greenfield bootstrap

A new environment is not a new release, and its first apply cannot complete
(N4). The order (ADR-0008 section 20, ES-08 section 33):

```text
1  bootstrap        state bucket, and GitHub federation if CI will manage this project
2  infrastructure   tofu apply -- fails partway, on a secret with no version
3  secrets          provision-secrets.sh writes the payloads
4  infrastructure   tofu apply again -- now completes
5  artifact         a published image digest
6  init             run the init Job for that digest
7  runtime          deploy worker and API
```

Step 2 failing is expected, not a broken configuration. Cloud Run mounts secret
*versions*; OpenTofu creates only *containers*, deliberately, because a version
resource takes the payload as an argument and would put every credential in
state. `infrastructure/README.md` section 7.4 quotes the error.

Steps 5 to 7 are `build-image.yml` and `deploy-staging.yml` once an environment
exists. They do not create one.

### The secret boundary

OpenTofu creates secret containers and their IAM. `provision-secrets.sh`
creates the values, piping each straight into `gcloud`; nothing is printed,
logged or written to a file. No workflow reads a secret payload — Cloud Run
resolves secret references itself — and the delivery invariants test fails if
one starts to (ES-08 section 160).

Secret payload provisioning stays manual and outside CI. That is allowed and
deliberate (ES-08 section 34): automating it would mean giving CI read access to
every runtime credential, or putting the values in state.

---

## 12. Metadata and auditability

| Record | Where | Retention |
| --- | --- | --- |
| `release-metadata.json` | build workflow artifact | 90 days |
| production image inventory | build and CI artifacts | 14–90 days |
| `deployment-record.json` | deployment workflow artifact | 90 days |
| `staging-accepted-<digest>` | staging workflow artifact | 90 days |
| the image itself | Artifact Registry | no cleanup policy |
| who deployed, when, and the result | GitHub Actions run history | GitHub default |

Nothing is committed back to the branch: a bot commit per deployment is noise in
the history that matters (ES-08 section 186). The image in the registry is the
authoritative artifact; the JSON records are how it is described.

No record contains a secret. Every field is an identifier.

---

## 13. Manual recovery

Automation orchestrates commands an operator can run. All of these work from a
checkout with `gcloud` authenticated (ES-08 section 109):

```bash
export CARE_ENVIRONMENT=staging
export GCP_PROJECT_ID=example-project
export GCP_REGION=us-central1

# what would happen
infrastructure/scripts/gcp/deploy.sh --digest sha256:... --dry-run

# deploy, with init and the ordering and the verification
infrastructure/scripts/gcp/deploy.sh --digest sha256:...

# accept
infrastructure/scripts/gcp/acceptance.sh --digest sha256:... --source-sha <commit>

# smoke only
infrastructure/scripts/gcp/smoke.sh --digest sha256:...

# roll back
infrastructure/scripts/gcp/rollback.sh --list
infrastructure/scripts/gcp/rollback.sh --digest sha256:... --confirm
```

Underneath, and worth knowing when a script is not what you want:

```bash
# what is deployed
gcloud run services describe care-staging-api --region us-central1 \
  --format='value(spec.template.spec.containers[0].image)'

# run init by hand
gcloud run jobs execute care-staging-init --region us-central1 --wait

# the worker must refuse an anonymous request
curl -s -o /dev/null -w '%{http_code}\n' "$(gcloud run services describe \
  care-staging-worker --region us-central1 --format='value(status.url)')/internal/tasks/execute/"
```

Image inspection needs no cloud at all:

```bash
infrastructure/scripts/verify-image.sh --image <ref>
infrastructure/scripts/verify-image-startup.sh --image <ref>
```

---

## 14. Failure recovery

| Failure | What it means | What to do |
| --- | --- | --- |
| CI red | the candidate is not releasable | fix and push; no artifact was published |
| publish fails after CI | registry or identity problem | check the `artifact-publication` variables and the AR writer binding; rerun |
| digest not found | the digest was never published to this environment's repository | publish it, or pass one that exists |
| init fails | migrations or reference data did not apply | inspect the execution; nothing else was deployed; do not retry blindly |
| worker not ready | the new revision cannot start | read the revision's logs; the API was not touched |
| worker unexpectedly public | the IAM boundary is broken | deployment aborts; fix the binding before anything else |
| API digest mismatch | the rollout did not take | read the service back; do not assume the CLI's success |
| acceptance red | this digest is not promotable | fix forward, or fix the environment and rerun acceptance on the same digest |
| promotion refused | no acceptance record for that digest | accept it in staging first |
| plan destructive | a resource would be destroyed or replaced | read the plan locally; `allow_destructive` only when that is the intent |

An environment-caused acceptance failure is fixed by fixing the environment and
rerunning acceptance against the same digest. The accepted record then describes
the successful rerun. A digest that failed is not made promotable by correcting
something around it (ES-08 sections 147, 148).

---

## 15. Consuming the artifact independently

The image is an ordinary OCI image and nothing in it is environment-specific:

```bash
docker pull <region>-docker.pkg.dev/<project>/<repository>/care@sha256:<digest>
```

Environment configuration is supplied at deployment. Run it under Docker
Compose, on a VM, or on another OCI runtime; the roles are selected by command
and environment (ADR-0006).

**Current limitation.** The maintainers' Artifact Registry repositories are
private, so this pull needs a reader binding in that project. That is an
operational and security decision, not an architectural one (ES-08 sections 70,
180): the release architecture is unchanged by where the artifact is mirrored,
and a public mirror can be added later without touching the build contract.

Another operator forking this repository configures their own project, registry
and federation — section 3 and section 4 above — and the same workflows publish
to and deploy their environments. Nothing in the common build path names this
fork's project, and the delivery invariants test enforces that
(ES-08 section 181).

---

## 16. What CI/CD deliberately does not do

- **Select an email provider.** Console email is valid; no promotion gate
  requires external delivery (ADR-0008 section 22).
- **Require Redis.** Both profiles stay supported; the Redis-free composition is
  verified in CI and in staging acceptance (ADR-0008 section 31).
- **Reverse migrations.** Ever (ADR-0008 section 13).
- **Run `tofu destroy`.** Not as rollback, not as cleanup (ADR-0008 section 43).
- **Apply infrastructure on an application release.** Lifecycles are separate
  (ADR-0008 section 19).
- **Deploy production automatically.** Promotion is manual, approved and
  auditable (ADR-0008 section 41).
- **Keep permanently running CI compute.** GitHub-hosted runners only
  (ADR-0008 section 49).


---

## The default-branch control plane

*Added by ES-08 D10.*

GitHub registers a `workflow_dispatch` entry point only for a workflow present
on the repository's **default branch**, and pushing the file on a branch is not
enough — it must be merged. The delivery implementation lives on the `gcp`
release lineage, so the operator-facing entry points live on `develop` instead,
as thin shims:

| entry point (on `develop`) | calls (on `gcp`) |
| --- | --- |
| `delivery-build.yml` | `build-image.yml@gcp` |
| `delivery-deploy-staging.yml` | `deploy-staging.yml@gcp` |
| `delivery-promote-production.yml` | `promote-production.yml@gcp` |
| `delivery-rollback.yml` | `rollback.yml@gcp` |
| `delivery-infrastructure.yml` | `infra-check.yml@gcp`, `infra-apply.yml@gcp` |

A shim holds no credential, declares no GitHub Environment and does no work. The
rule it embodies is ADR-0008 section 5a:

```
workflow definition location  !=  source revision  !=  deployed image
```

`develop` is not a deployment target and hosting these files does not make it
one: nothing in them builds or deploys `develop`.

### Running a release

```bash
# 1. build a trusted revision -- source_ref must already be reachable from gcp
gh workflow run delivery-build.yml -f source_ref=gcp

# 2. deploy the digest that build published; never a tag, never a branch
gh workflow run delivery-deploy-staging.yml \
    -f image_digest=sha256:... -f source_sha=<commit>

# 3. promote, after staging acceptance recorded that digest
gh workflow run delivery-promote-production.yml -f image_digest=sha256:...
```

### Why a ref input is not a privilege escalation

`source_ref` is attacker-reachable — anyone who may dispatch may type a ref — so
it is not trusted. `verify-source.yml` runs first, holding no credential and
declaring no environment. It resolves the ref and asserts

```
git merge-base --is-ancestor <resolved-sha> origin/gcp
```

Reachability, not string comparison: a tag or explicit commit already merged to
`gcp` is accepted, and a branch that merely *contains* `gcp` is not. Nothing an
untrusted contributor can push is reachable from `gcp` until a maintainer merges
it, which is the review boundary this leans on.

Every credentialed job then depends on that gate and checks out **the SHA it
resolved**, never the ref that was typed: re-resolving a ref after checking it
would let a branch move in between.

Both properties are machine-checked (`check_credentialed_jobs_verify_source_trust`),
as is the shim-to-implementation wiring (`check_reusable_workflow_calls_resolve`).

### Ordering

The shims resolve `@gcp`. The delivery implementation must be merged to `gcp`
before they can run; until then a dispatch fails visibly with "workflow was not
found" rather than silently doing the wrong thing.
