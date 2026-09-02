# Bootstrap — remote state

State has to live somewhere before OpenTofu can keep state remotely. This root
creates that somewhere. It runs **once per project**, on local state, and the
environment roots then keep their state in the bucket it creates.

This is the documented answer to the bootstrap dependency in ADR-0007
("Bootstrap"): there is no undocumented console step behind the declarative
deployment.

## What it creates

| Resource | Why |
| --- | --- |
| `google_storage_bucket.state` | Remote state for every environment, one prefix each |
| `google_project_service` × 2 | `storage` and `serviceusage`, the only APIs the bucket needs |
| `google_service_account.deployer` | Optional, off by default — see below |

The bucket has versioning, uniform bucket-level access, enforced public access
prevention, a 90-day retention rule on *superseded* generations only, and
`prevent_destroy`.

## What it deliberately does not create

- **Application infrastructure.** That is `environments/<env>/`.
- **Deployer IAM roles.** `create_deployer_service_account` makes the identity
  and grants it state access, nothing more. What a deployer may do to a project
  is decided with the CI/CD design (ES-08), and pre-granting it would be exactly
  the broad-project-role habit ADR-0007 rejects. ES-07 applies dev from an
  operator workstation, so the default is `false`.

## Prerequisites

- `tofu` ≥ 1.8
- `gcloud`, authenticated:

```bash
gcloud auth application-default login
```

The operator needs, on the target project: `roles/storage.admin` (create the
bucket and set its IAM) and `roles/serviceusage.serviceUsageAdmin` (enable the
two APIs). `roles/iam.serviceAccountAdmin` additionally, only if you set
`create_deployer_service_account = true`. Owner works but is broader than
required; see `../../README.md` for the full permission discussion.

## Run it

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set `project_id`. Then:

```bash
tofu init
```

```bash
tofu plan
```

Read the plan. Then:

```bash
tofu apply
```

Note the `state_bucket` output — each environment's `backend.tf` names it.

## Where this root's own state lives

**In the bucket it created**, under the `bootstrap/` prefix, one object per
workspace. `versions.tf` declares a partial `backend "gcs" {}` — no bucket, no
prefix — because this root is generic per project and the bucket name is derived
from the project id. Supply both at init:

```bash
tofu init \
  -backend-config=bucket=care-tfstate-<project_id> \
  -backend-config=prefix=bootstrap
```

It did not start there. State has to exist before it can be kept remotely, so
the first apply of a project runs on local state and is migrated afterwards:

```bash
tofu init -migrate-state \
  -backend-config=bucket=<state_bucket output> \
  -backend-config=prefix=bootstrap
```

**On a greenfield project, comment out the `backend "gcs" {}` block for the
first apply**, then restore it and migrate. `tofu init` cannot reach a bucket
that does not exist yet — that is the bootstrap circularity, and this is the one
manual step it leaves. It happens once per project and it is not hidden.

After migrating, the local `terraform.tfstate` is left behind as a backup. It is
git-ignored and no longer authoritative; the bucket is. Do not resurrect it by
commenting the backend back out on an already-migrated project — you would plan
against a stale snapshot.

### Why remote matters here

Local state means one workstation is a single point of failure for the record of
the identity infrastructure. It also means the one apply this root still needs
from an operator — `grant_infrastructure_roles` — can only be run from that
machine. Keeping it in the bucket removes both.

## Tearing down

Don't, unless you are decommissioning the project. `prevent_destroy` is set and
`force_destroy` is false, so `tofu destroy` fails against a non-empty bucket.
Destroying this bucket destroys the record of every environment OpenTofu
manages. To do it deliberately: destroy every environment first, then remove
the `lifecycle` block, then destroy.
