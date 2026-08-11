output "api_url" {
  description = "Public API endpoint."
  value       = module.care.api_url
}

output "worker_url" {
  description = "Private worker endpoint. Not reachable without roles/run.invoker."
  value       = module.care.worker_url
}

output "worker_task_endpoint" {
  value = module.care.worker_task_endpoint
}

output "artifact_registry_repository" {
  value = module.care.artifact_registry_repository
}

output "image" {
  value = module.care.image
}

output "sql_instance_name" {
  value = module.care.sql_instance_name
}

output "sql_connection_name" {
  value = module.care.sql_connection_name
}

output "sql_deletion_protection" {
  value = module.care.sql_deletion_protection
}

output "database_name" {
  value = module.care.database_name
}

output "database_user" {
  value = module.care.database_user
}

output "buckets" {
  value = module.care.buckets
}

output "bucket_aliases" {
  value = module.care.bucket_aliases
}

output "tasks_queue_name" {
  value = module.care.tasks_queue_name
}

output "init_job_name" {
  value = module.care.init_job_name
}

output "job_names" {
  value = module.care.job_names
}

output "scheduler_job_names" {
  value = module.care.scheduler_job_names
}

output "service_accounts" {
  value = module.care.service_accounts
}

output "secret_ids" {
  description = "Secret Manager container names. Names only; values live in Secret Manager and never in state."
  value       = module.care.secret_ids
}

output "secret_access_matrix" {
  value = module.care.secret_access_matrix
}

output "database_connection_budget" {
  value = module.care.database_connection_budget
}
