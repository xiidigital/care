# Exactly the APIs the declared resources need. Enabling a broad set "to be
# safe" widens the project's attack surface for no benefit (ES-07 section 10).
#
# Everything that could race an API activation takes an explicit dependency on
# this resource; the provider does not infer one.

resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",             # Cloud Run services and Jobs
    "sqladmin.googleapis.com",        # Cloud SQL
    "storage.googleapis.com",         # Cloud Storage
    "cloudtasks.googleapis.com",      # Cloud Tasks
    "cloudscheduler.googleapis.com",  # Cloud Scheduler
    "secretmanager.googleapis.com",   # Secret Manager
    "artifactregistry.googleapis.com" # container images
    ,
    "iam.googleapis.com",            # service accounts
    "iamcredentials.googleapis.com", # OIDC token minting for Cloud Tasks
    "serviceusage.googleapis.com",   # this resource itself
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  # Disabling an API on destroy would affect resources outside this environment
  # in a shared project, and can cascade to dependent services.
  disable_on_destroy = false
}
