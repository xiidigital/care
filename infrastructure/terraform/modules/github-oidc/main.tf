# GitHub Actions to GCP, without a key.
#
# ADR-0008 section 23 prefers short-lived federated identity over downloaded
# service-account keys, and ES-08 section 15 forbids creating or committing a
# service-account JSON key at all. This module is what makes that possible: a
# workload identity pool that trusts GitHub's OIDC issuer, a provider that
# accepts tokens from exactly one repository, and a small set of automation
# identities that specific workflow contexts may impersonate.
#
# WHAT MAKES IT SAFE
#
# Three independent restrictions, and all three must hold before a token
# becomes a GCP identity:
#
#   1  the token is signed by https://token.actions.githubusercontent.com
#   2  the provider's attribute condition requires assertion.repository to be
#      exactly this repository — a token from any other repository, including a
#      fork of this one, is rejected at exchange
#   3  the impersonation binding names a principalSet restricted by the
#      environment claim, so a job that does not run in that GitHub Environment
#      cannot become that identity even from the right repository
#
# The third is what separates staging from production. GitHub only puts an
# `environment` claim in the token when the job declares `environment:`, and a
# protected environment adds approval before the job — and therefore before the
# token — exists at all (ES-08 sections 16, 159).
#
# WHAT IT DOES NOT DO
#
# It grants no permission on any environment. An identity created here can
# authenticate and nothing else until an environment's OpenTofu grants it
# something, which is `deployment_principals` in the care-environment module.
# Creating the identity and deciding what it may do are deliberately two
# separate reviewable changes.

terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

locals {
  # GitHub's OIDC issuer. Not configurable: a different issuer is a different
  # trust relationship and should be a different module.
  issuer_uri = "https://token.actions.githubusercontent.com"

  pool_name = "projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github.workload_identity_pool_id}"

  # One binding per identity per environment claim it accepts.
  environment_bindings = merge([
    for key, identity in var.identities : {
      for environment in identity.environments :
      "${key}|${environment}" => { identity = key, environment = environment }
    }
  ]...)
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions"
  description               = "Federated identity for GitHub Actions workflows in ${var.github_repository}."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.provider_id
  display_name                       = "GitHub OIDC"
  description                        = "Accepts GitHub Actions tokens from ${var.github_repository} only."

  # Only the claims the trust expressions use. Mapping a claim that nothing
  # restricts on is an attribute somebody will later mistake for a control.
  attribute_mapping = {
    "google.subject"         = "assertion.sub"
    "attribute.repository"   = "assertion.repository"
    "attribute.ref"          = "assertion.ref"
    "attribute.environment"  = "assertion.environment"
    "attribute.workflow_ref" = "assertion.workflow_ref"
    "attribute.event_name"   = "assertion.event_name"
  }

  # The repository gate. Without an attribute condition, any GitHub repository
  # in the world could exchange a token against this pool.
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = local.issuer_uri
  }
}

# ---------------------------------------------------------------------------
# Automation identities
#
# Separate identities for publication, staging deployment, production
# deployment and infrastructure (ES-08 section 17). They are separate because
# their permissions differ and because the boundary should survive a mistake in
# one workflow: a build that can publish an image cannot deploy, and a staging
# deployment cannot touch production.
# ---------------------------------------------------------------------------

resource "google_service_account" "automation" {
  for_each = var.identities

  project      = var.project_id
  account_id   = each.value.account_id
  display_name = each.value.display_name
  description  = each.value.description
}

# Who may become each identity: a GitHub Actions job in this repository running
# in one of the named GitHub Environments. Nothing else, including a job in this
# repository that declares no environment.
resource "google_service_account_iam_member" "environment_impersonation" {
  for_each = local.environment_bindings

  service_account_id = google_service_account.automation[each.value.identity].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.environment/${each.value.environment}"
}

# Same, keyed on the branch instead, for an identity that is used by a workflow
# with no GitHub Environment. Empty in the default configuration: every
# credentialed workflow in this repository declares an environment, which is the
# stronger form.
resource "google_service_account_iam_member" "ref_impersonation" {
  for_each = merge([
    for key, identity in var.identities : {
      for ref in identity.refs :
      "${key}|${ref}" => { identity = key, ref = ref }
    }
  ]...)

  service_account_id = google_service_account.automation[each.value.identity].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.ref/${each.value.ref}"
}

# ---------------------------------------------------------------------------
# Infrastructure permissions
#
# Applying this repository's infrastructure needs administrative roles on the
# services it manages. They are listed rather than summarised as Editor, and
# they are not granted unless an operator asks for them: until then the
# infrastructure workflows run against whatever the operator's own credentials
# allow, which is how ES-07 operated (ES-08 section 75).
# ---------------------------------------------------------------------------

resource "google_project_iam_member" "infrastructure" {
  for_each = var.grant_infrastructure_roles ? toset(var.infrastructure_roles) : toset([])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.automation[var.infrastructure_identity].email}"
}
