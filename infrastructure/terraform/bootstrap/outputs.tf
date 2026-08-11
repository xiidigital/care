output "state_bucket" {
  description = "Name of the remote state bucket. Goes in each environment's backend block."
  value       = google_storage_bucket.state.name
}

output "state_bucket_url" {
  description = "gs:// URL of the state bucket."
  value       = google_storage_bucket.state.url
}

output "deployer_service_account_email" {
  description = "Deployment automation identity, when created. Empty otherwise."
  value       = var.create_deployer_service_account ? google_service_account.deployer[0].email : ""
}
