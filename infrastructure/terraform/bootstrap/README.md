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

Locally, in `terraform.tfstate`, ignored by git. That is the honest resolution
of the circular dependency, and it is safe: this root manages one bucket and two
API enablements, all of which are trivially re-importable.

If you would rather keep it in the bucket it just created, migrate it after the
first apply:

```bash
tofu init -migrate-state -backend-config=bucket=<state_bucket> -backend-config=prefix=bootstrap
```

You will need a `backend "gcs" {}` block in `versions.tf` for that. It is not
there by default because a fresh clone would then fail `tofu init` against a
bucket that does not exist yet — which is the bootstrap problem again.

## Tearing down

Don't, unless you are decommissioning the project. `prevent_destroy` is set and
`force_destroy` is false, so `tofu destroy` fails against a non-empty bucket.
Destroying this bucket destroys the record of every environment OpenTofu
manages. To do it deliberately: destroy every environment first, then remove
the `lifecycle` block, then destroy.
