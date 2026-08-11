# Runtime identities.
#
# Five, because five things do measurably different work. ADR-0007 permits
# combining identities only when the effective privileges stay narrow; here they
# would not. The API enqueues tasks and the worker does not. The Cloud Tasks
# invoker may invoke the worker and nothing else, and nothing else may invoke the
# worker. The init Job touches the database and no bucket. Folding those together
# would give every process the union.
#
# None of them has a JSON key. Cloud Run attaches the identity and the client
# libraries resolve it through Application Default Credentials
# (ADR-0007 "Service accounts and least privilege").

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-api"
  display_name = "CARE ${var.environment} API runtime"
  description  = "Cloud Run API service. Cloud SQL, its own secrets, bucket objects, and enqueueing to the CARE queue."

  depends_on = [google_project_service.required]
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-worker"
  display_name = "CARE ${var.environment} task worker runtime"
  description  = "Cloud Run task worker. Cloud SQL, its own secrets and bucket objects. Holds no invoker permission on itself."

  depends_on = [google_project_service.required]
}

resource "google_service_account" "init" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-init"
  display_name = "CARE ${var.environment} initialization job"
  description  = "Cloud Run Jobs running management commands. Cloud SQL and secrets only; no storage, no Cloud Run administration."

  depends_on = [google_project_service.required]
}

# Separate from the worker's own identity on purpose. This one exists to be
# named in an OIDC token, and it may invoke the worker; the worker may not
# invoke itself (ES-07 section 18).
resource "google_service_account" "tasks_invoker" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-tasks-inv"
  display_name = "CARE ${var.environment} Cloud Tasks invoker"
  description  = "OIDC identity Cloud Tasks presents to the private worker. roles/run.invoker on the worker service and nothing else."

  depends_on = [google_project_service.required]
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-scheduler"
  display_name = "CARE ${var.environment} Cloud Scheduler invoker"
  description  = "Executes the periodic Cloud Run Jobs. Scoped to those jobs; not a deployment identity."

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Cloud SQL connectivity
#
# roles/cloudsql.client is project-scoped because Cloud SQL exposes no
# instance-level IAM binding — there is no narrower grant available for the
# connect permission. It confers connecting and nothing about the data: the
# database user and its password are still required, and neither is managed
# here (see secrets.tf).
# ---------------------------------------------------------------------------

resource "google_project_iam_member" "cloudsql_client" {
  for_each = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
    init   = google_service_account.init.email
  }

  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${each.value}"
}

# ---------------------------------------------------------------------------
# Cloud Tasks
# ---------------------------------------------------------------------------

# Only the API enqueues. No registered handler creates a follow-up task, so the
# worker gets no enqueue permission; give it one when a handler needs it, not
# before.
resource "google_cloud_tasks_queue_iam_member" "api_enqueuer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.default.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.api.email}"
}

# Creating a task that carries an OIDC token for the invoker identity is an
# impersonation, and Cloud Tasks checks it against the *caller*. Without this
# the enqueue fails with a permission error naming the invoker account, which
# reads like a queue problem and is not one.
resource "google_service_account_iam_member" "api_acts_as_invoker" {
  service_account_id = google_service_account.tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

# ---------------------------------------------------------------------------
# Storage
#
# roles/storage.objectUser is object create, read, list and delete — what the
# Django storage backend does. Not objectAdmin, which adds object IAM
# management, and not an admin role on the bucket itself: neither identity ever
# changes bucket configuration (ES-07 section 39).
#
# The init Job is absent from this list deliberately. Initialization migrates,
# creates cache tables, compiles messages and syncs reference data; it opens no
# object.
# ---------------------------------------------------------------------------

resource "google_storage_bucket_iam_member" "runtime_objects" {
  for_each = {
    for pair in setproduct(local.managed_buckets, ["api", "worker"]) :
    "${pair[0]}:${pair[1]}" => {
      bucket = pair[0]
      member = pair[1] == "api" ? google_service_account.api.email : google_service_account.worker.email
    }
  }

  bucket = google_storage_bucket.buckets[each.value.bucket].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${each.value.member}"
}
