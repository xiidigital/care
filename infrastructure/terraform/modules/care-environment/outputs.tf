# Operational identifiers only.
#
# No secret value, no password and no token is output here, and none could be:
# OpenTofu never holds one. The secret outputs below are Secret Manager
# *resource names*, which are how an operator addresses a secret, not its
# contents (ADR-0007 "Secret Manager", ES-07 section 107).

output "api_url" {
  description = "Public API endpoint. This is the value the frontend is configured with."
  value       = module.api.uri
}

output "api_service_name" {
  value = module.api.name
}

output "worker_url" {
  description = "Private worker endpoint. Operationally useful for verifying the IAM boundary; it is not reachable without roles/run.invoker."
  value       = module.worker.uri
}

output "worker_service_name" {
  value = module.worker.name
}

output "worker_task_endpoint" {
  description = "The URL Cloud Tasks dispatches to, and the value of GCP_WORKER_URL."
  value       = local.worker_task_url
}

output "artifact_registry_repository" {
  description = "Docker repository path. Tag images with this prefix."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.care.repository_id}"
}

output "image" {
  description = "The image reference every role is currently running."
  value       = var.image
}

output "sql_instance_name" {
  value = google_sql_database_instance.this.name
}

output "sql_connection_name" {
  description = "project:region:instance. Used by the Cloud SQL socket path and by `gcloud sql connect`."
  value       = google_sql_database_instance.this.connection_name
}

output "sql_deletion_protection" {
  description = "Whether Cloud SQL will refuse a delete. Surfaced so a review can confirm it without reading the plan."
  value       = google_sql_database_instance.this.deletion_protection
}

output "database_name" {
  value = google_sql_database.care.name
}

output "database_user" {
  description = "Application database user. Created by provision-secrets.sh, not by OpenTofu."
  value       = var.database_user
}

output "buckets" {
  description = "Physical buckets created. The report alias shares the patient bucket unless configured otherwise."
  value       = [for bucket in google_storage_bucket.buckets : bucket.name]
}

output "bucket_aliases" {
  description = "Logical storage alias to physical bucket, as the application resolves them."
  value = {
    patient  = local.patient_bucket
    facility = local.facility_bucket
    report   = local.report_bucket
  }
}

output "tasks_queue_name" {
  value = google_cloud_tasks_queue.default.name
}

output "tasks_queue_id" {
  value = google_cloud_tasks_queue.default.id
}

output "init_job_name" {
  description = "Execute this before promoting an API revision that depends on a new migration."
  value       = google_cloud_run_v2_job.jobs["init"].name
}

output "job_names" {
  description = "Every Cloud Run Job, by logical key."
  value       = { for key, job in google_cloud_run_v2_job.jobs : key => job.name }
}

output "scheduler_job_names" {
  value = { for key, job in google_cloud_scheduler_job.jobs : key => job.name }
}

output "service_accounts" {
  description = "Runtime identities, by responsibility."
  value = {
    api           = google_service_account.api.email
    worker        = google_service_account.worker.email
    init          = google_service_account.init.email
    tasks_invoker = google_service_account.tasks_invoker.email
    scheduler     = google_service_account.scheduler.email
  }
}

output "secret_ids" {
  description = "Secret Manager container names, by environment variable. Names, never values."
  value       = { for env_name, spec in local.all_secrets : env_name => spec.secret_id }
}

output "secret_access_matrix" {
  description = "Which role may read which secret. The reviewable form of least privilege (ES-07 section 125)."
  value       = { for role, secrets in local.secret_env_by_role : role => sort(keys(secrets)) }
}

output "database_connection_budget" {
  description = <<-EOT
    The worst-case Cloud SQL connection count this scaling configuration can
    produce (ES-07 section 120). Gunicorn is sync, so a worker process holds at
    most one Django connection; the bound is processes, not requests.
  EOT
  value = {
    api_worst_case    = var.api_max_instances * var.api_gunicorn_workers
    worker_worst_case = var.worker_max_instances * var.worker_gunicorn_workers
    total_worst_case  = (var.api_max_instances * var.api_gunicorn_workers) + (var.worker_max_instances * var.worker_gunicorn_workers)
  }
}

output "redis_resources" {
  description = "Redis infrastructure in this environment. Empty by construction: the primary composition provisions none (ADR-0007, ES-07 section 140)."
  value       = []
}
