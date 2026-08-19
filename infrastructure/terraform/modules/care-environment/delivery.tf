# Delivery permissions.
#
# What an automated release needs in this environment, and nothing else
# (ADR-0008 section 23, ES-08 sections 114-117). Every binding here is on a
# resource this module created: a Cloud Run service, a Job, this environment's
# registry repository, its queue, its buckets, its service accounts. There is
# no project-level role in this file except the one Cloud Logging has no
# smaller form of, and that one is opt-in and documented where it is granted.
#
# The principals are inputs. This module does not know whether they are GitHub
# Actions identities, a Jenkins service account or an operator, and the
# workload-identity trust that decides who may become them lives in the
# bootstrap root, not here.
#
# Empty by default. An environment applied before CI exists grants nothing, and
# an unused identity with deployment permissions is a liability rather than a
# control — the same reasoning that made the bootstrap deployer opt-in.

locals {
  # The runtime identities a deployment must be able to act as. Deploying a
  # Cloud Run revision that runs as X requires iam.serviceAccountUser on X;
  # without it the deployment fails with a permission error that names the
  # wrong thing.
  #
  # tasks_invoker is included for acceptance rather than deployment: creating a
  # Cloud Tasks task that carries an OIDC token for an identity is acting as it.
  deployment_actas_accounts = {
    api           = google_service_account.api.email
    worker        = google_service_account.worker.email
    init          = google_service_account.init.email
    tasks_invoker = google_service_account.tasks_invoker.email
  }

  # Cartesian products, flattened once, so each for_each below is a plain map
  # with stable keys.
  deployment_actas = merge([
    for principal in var.deployment_principals : {
      for role, email in local.deployment_actas_accounts :
      "${principal}|${role}" => { principal = principal, account = email }
    }
  ]...)

  deployment_jobs = merge([
    for principal in var.deployment_principals : {
      for key, job in google_cloud_run_v2_job.jobs :
      "${principal}|${key}" => { principal = principal, job = job.name }
    }
  ]...)

  deployment_buckets = var.grant_deployment_storage_access ? merge([
    for principal in var.deployment_principals : {
      for key, bucket in google_storage_bucket.buckets :
      "${principal}|${key}" => { principal = principal, bucket = bucket.name }
    }
  ]...) : {}
}

# ---------------------------------------------------------------------------
# Artifact publication
#
# The build identity writes images and reads them back to resolve a digest. It
# gets nothing else: not Cloud Run, not Cloud SQL, not a secret
# (ES-08 section 115).
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository_iam_member" "publishers" {
  for_each = toset(var.image_publisher_principals)

  project    = var.project_id
  location   = google_artifact_registry_repository.care.location
  repository = google_artifact_registry_repository.care.name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

# Cloud Run pulls the image itself, but a deployment reads the digest back to
# prove what it deployed, so the deploying identity needs read on the same
# repository.
resource "google_artifact_registry_repository_iam_member" "deployment_readers" {
  for_each = toset(var.deployment_principals)

  project    = var.project_id
  location   = google_artifact_registry_repository.care.location
  repository = google_artifact_registry_repository.care.name
  role       = "roles/artifactregistry.reader"
  member     = each.value
}

# ---------------------------------------------------------------------------
# Cloud Run
#
# roles/run.developer, bound to each service and Job individually rather than to
# the project. The same role at project level would let a staging deployment
# touch dev, and later production — which is exactly the boundary ES-08
# section 116 asks to be able to demonstrate.
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service_iam_member" "deployment_api" {
  for_each = toset(var.deployment_principals)

  project  = var.project_id
  location = var.region
  name     = module.api.name
  role     = "roles/run.developer"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "deployment_worker" {
  for_each = toset(var.deployment_principals)

  project  = var.project_id
  location = var.region
  name     = module.worker.name
  role     = "roles/run.developer"
  member   = each.value
}

resource "google_cloud_run_v2_job_iam_member" "deployment_jobs" {
  for_each = local.deployment_jobs

  project  = var.project_id
  location = var.region
  name     = each.value.job
  role     = "roles/run.developer"
  member   = each.value.principal
}

resource "google_service_account_iam_member" "deployment_actas" {
  for_each = local.deployment_actas

  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value.account}"
  role               = "roles/iam.serviceAccountUser"
  member             = each.value.principal
}

# ---------------------------------------------------------------------------
# Acceptance
#
# Staging acceptance proves things about the running environment that cannot be
# proven anywhere else: that Cloud Tasks reaches the worker, that storage
# round-trips, that the worker executed what was dispatched. Each needs one
# permission, and each is separately switchable so that an environment which
# does not run acceptance grants none of them (ES-08 section 117).
# ---------------------------------------------------------------------------

resource "google_cloud_tasks_queue_iam_member" "deployment_enqueuer" {
  for_each = var.grant_deployment_task_dispatch ? toset(var.deployment_principals) : toset([])

  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.default.name
  role     = "roles/cloudtasks.enqueuer"
  member   = each.value
}

# Object access on this environment's buckets. Objects, not bucket
# administration: the acceptance check writes one small object, reads it back
# and deletes it.
resource "google_storage_bucket_iam_member" "deployment_objects" {
  for_each = local.deployment_buckets

  bucket = each.value.bucket
  role   = "roles/storage.objectUser"
  member = each.value.principal
}

# The one unavoidable project-level grant, and it is opt-in.
#
# Acceptance proves a dispatched task was executed by reading the worker's log
# entry, and Cloud Logging has no per-service IAM: roles/logging.viewer is
# project-wide read of log entries. It is read-only and grants nothing over any
# resource, but it is wider than everything else in this file, so it is named
# here rather than bundled into a "deployment permissions" set.
#
# An operator who considers it too wide sets this false and runs acceptance with
# --skip-tasks, or gives acceptance its own identity.
resource "google_project_iam_member" "deployment_log_viewer" {
  for_each = var.grant_deployment_log_read ? toset(var.deployment_principals) : toset([])

  project = var.project_id
  role    = "roles/logging.viewer"
  member  = each.value
}
