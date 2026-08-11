locals {
  state_bucket_name = coalesce(
    var.state_bucket_name != "" ? var.state_bucket_name : null,
    "care-tfstate-${var.project_id}",
  )
}

# The state backend has a bootstrap dependency on itself, so the APIs it needs
# are enabled here rather than by the environment roots. Only the two the
# bucket actually requires; the environment roots enable their own.
resource "google_project_service" "bootstrap" {
  for_each = toset([
    "storage.googleapis.com",
    "serviceusage.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  # Leave the API enabled if this root is destroyed. Disabling Service Usage or
  # Storage on a project would break far more than this bucket.
  disable_on_destroy = false
}

# Terraform state is sensitive infrastructure metadata (ADR-0007 "State"):
# it records resource identifiers, IAM bindings and connection names.
resource "google_storage_bucket" "state" {
  project  = var.project_id
  name     = local.state_bucket_name
  location = var.region

  # Recovery: a corrupt or truncated state file is restored from a prior
  # generation rather than rebuilt by hand.
  versioning {
    enabled = true
  }

  # No object ACLs. Access is decided by bucket IAM alone, which is what makes
  # "restrict access" reviewable in one place.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      # Superseded generations only. The live state object is never matched by
      # this rule, whatever its age.
      days_since_noncurrent_time = var.noncurrent_version_retention_days
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  # Deleting the state bucket destroys the record of every managed environment.
  # force_destroy stays false so a `tofu destroy` here fails loudly against a
  # non-empty bucket instead of succeeding quietly.
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bootstrap]
}

# Deployment automation identity. See the variable's description for why this is
# opt-in. It gets no roles here: the permissions a deployer needs are decided
# with the CI/CD design (ES-08), and granting them speculatively would be the
# broad-project-role habit ADR-0007 rejects.
resource "google_service_account" "deployer" {
  count = var.create_deployer_service_account ? 1 : 0

  project      = var.project_id
  account_id   = "care-deployer"
  display_name = "CARE deployment automation"
  description  = "Applies CARE infrastructure. Created by the bootstrap root; roles are granted with the CI/CD design."
}

# State access is the one permission the deployer needs to exist at all, and it
# is scoped to the bucket rather than granted at project level.
resource "google_storage_bucket_iam_member" "deployer_state" {
  count = var.create_deployer_service_account ? 1 : 0

  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer[0].email}"
}
