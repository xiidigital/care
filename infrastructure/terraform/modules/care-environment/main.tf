terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  name_prefix = var.name_prefix_override != "" ? var.name_prefix_override : "care-${var.environment}"

  # Bucket names are global. A short digest of the project id keeps them unique
  # without embedding a mutable value in a resource name (ES-07 section 9).
  bucket_suffix = substr(sha256(var.project_id), 0, 8)

  labels = merge(
    {
      application = "care"
      environment = var.environment
      managed-by  = "opentofu"
    },
    var.labels,
  )

  api_service_name    = "${local.name_prefix}-api"
  worker_service_name = "${local.name_prefix}-worker"
  init_job_name       = "${local.name_prefix}-init"
  queue_name          = "${local.name_prefix}-tasks"

  sql_instance_name = "${local.name_prefix}-db${var.sql_instance_name_suffix}"

  bucket_location = var.bucket_location != "" ? var.bucket_location : var.region

  # The Cloud SQL unix socket the native Cloud Run integration mounts. No VPC
  # connector is involved (ES-07 section 31).
  cloudsql_socket_dir = "/cloudsql/${google_sql_database_instance.this.connection_name}"

  # ---------------------------------------------------------------------------
  # The worker URL, and why there are two of them
  #
  # Cloud Run assigns a service's URL at creation. Everything that *dispatches*
  # to the worker — the API, and the init Job — reads it back from the resource,
  # so those values are always exactly right (local.worker_public_url below).
  #
  # The worker itself is the awkward case. Its own settings validate the six
  # Cloud Tasks variables, GCP_WORKER_URL among them, because validation follows
  # the selected backend and not the role: a task_worker running
  # CARE_TASK_BACKEND=cloud_tasks must supply them even though no registered
  # handler enqueues anything. Referencing the service's uri to build the
  # service's own environment is a dependency cycle, so it cannot read it back.
  #
  # So the worker gets a derived value: Cloud Run's newer
  # <service>-<project-number>.<region>.run.app form.
  #
  # Measured during ES-07, and worth stating because it is not obvious: Cloud
  # Run answers on **both** URL forms for the same service. This project's
  # services report the older <service>-<hash>-<region-code>.a.run.app as their
  # canonical `uri`, and both hostnames return 200 from the same revision. The
  # derived value is therefore a working address even when it differs from the
  # reported one, and a mismatch is a cosmetic inconsistency rather than a
  # broken dispatch target.
  #
  # worker_url_override exists to make the two agree exactly, and the check
  # block in run.tf prints the value to use. Nothing dispatches with this today
  # in any case — no registered handler enqueues a follow-up task.
  # ---------------------------------------------------------------------------
  worker_derived_url = "https://${local.worker_service_name}-${data.google_project.this.number}.${var.region}.run.app"

  worker_self_url = var.worker_url_override != "" ? var.worker_url_override : local.worker_derived_url

  patient_bucket  = "${local.name_prefix}-patient-${local.bucket_suffix}"
  facility_bucket = "${local.name_prefix}-facility-${local.bucket_suffix}"
  report_bucket = var.share_report_bucket_with_patient ? local.patient_bucket : (
    "${local.name_prefix}-report-${local.bucket_suffix}"
  )

  # Physical buckets to create. The report alias shares the patient bucket by
  # default, matching what the application already resolves.
  managed_buckets = distinct(compact([
    local.patient_bucket,
    local.facility_bucket,
    local.report_bucket,
  ]))
}
