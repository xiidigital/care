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
  # The worker URL, computed rather than read back
  #
  # The worker's own settings validate the Cloud Tasks variables, GCP_WORKER_URL
  # among them, because validation follows the selected backend and not the
  # role. Reading google_cloud_run_v2_service.worker.uri to build the worker's
  # own environment would be a self-reference, so the URL is derived from the
  # service name and project number instead — Cloud Run's deterministic form.
  #
  # Derived values that must equal a real one are a standing invitation to drift,
  # so it is asserted against the actual uri after apply. See the check block in
  # run.tf.
  # ---------------------------------------------------------------------------
  worker_base_url = "https://${local.worker_service_name}-${data.google_project.this.number}.${var.region}.run.app"
  worker_task_url = "${local.worker_base_url}/internal/tasks/execute/"

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
