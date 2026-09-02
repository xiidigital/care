# Bootstrap creates the bucket the other roots keep their state in, so on a
# greenfield project it cannot start out keeping its own state there. It runs
# once on local state, and then this backend takes over. See README.md.
#
# The backend is declared **partial** on purpose: no bucket, no prefix. This
# root is generic per project, and the bucket name is derived from the project
# id, so naming one here would tie a reusable root to one installation. The
# operator supplies both at init:
#
#   tofu init -migrate-state \
#     -backend-config=bucket=<state_bucket output> \
#     -backend-config=prefix=bootstrap
#
# On a greenfield project the bucket does not exist yet and `tofu init` cannot
# reach it. Comment this block out for the first apply, then restore it and
# migrate. That one manual step is the honest residue of the bootstrap
# circularity; it is not hidden, and it happens once per project.

terraform {
  # OpenTofu is the IaC tool for this repository (ADR-0007, amended
  # 2026-08-11). The HCL is unchanged from Terraform; only the CLI differs.
  required_version = ">= 1.8.0, < 2.0.0"

  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
