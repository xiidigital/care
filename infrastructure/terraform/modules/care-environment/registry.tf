# Artifact Registry.
#
# One Docker repository per environment holding one image, which the API, the
# worker and every Job run. ADR-0007 forbids requiring a separate image per
# role: roles differ by command and environment.
#
# OpenTofu does not build or push. That is an operational or CI responsibility
# (ES-07 section 14); see infrastructure/scripts/publish-image.sh.

resource "google_artifact_registry_repository" "care" {
  project       = var.project_id
  location      = var.region
  repository_id = local.name_prefix
  format        = "DOCKER"
  description   = "CARE application images for the ${var.environment} environment."
  labels        = local.labels

  docker_config {
    # Not immutable. Tags must stay movable for a rollback to a previously
    # published digest, and immutability here would not add safety: deployments
    # reference a digest, which is immutable whatever the tag policy says.
    immutable_tags = false
  }

  depends_on = [google_project_service.required]
}

# Cloud Run pulls through its own service agent, not through the runtime
# identities, and that agent already has access to repositories in the same
# project. No pull binding is declared here on purpose — granting the runtime
# service accounts reader access would be permission that nothing uses.
