# Bootstrap runs on local state, by design: it is what creates the bucket the
# other roots keep their state in, so it cannot use that bucket itself. See
# README.md for the one-time procedure and for how to migrate this state into
# the bucket afterwards if you want it there.

terraform {
  # OpenTofu is the IaC tool for this repository (ADR-0007, amended
  # 2026-08-11). The HCL is unchanged from Terraform; only the CLI differs.
  required_version = ">= 1.8.0, < 2.0.0"

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
