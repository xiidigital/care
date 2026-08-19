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

# --- GitHub Actions identity (ES-08) ---------------------------------------
#
# These are the values a repository administrator copies into GitHub. All of
# them are identifiers; none is a credential.

output "github_workload_identity_provider" {
  description = "Resource name for the google-github-actions/auth workload_identity_provider input. Empty when GitHub federation is not configured."
  value       = var.github_repository != "" ? module.github_oidc[0].workload_identity_provider : ""
}

output "github_service_accounts" {
  description = "Automation identity emails by purpose: publisher, deploy_staging, deploy_production, infrastructure."
  value       = var.github_repository != "" ? module.github_oidc[0].service_account_emails : {}
}

output "github_deployment_principals" {
  description = "The same identities in IAM member form, for an environment's deployment_principals and image_publisher_principals."
  value       = var.github_repository != "" ? module.github_oidc[0].deployment_principals : {}
}
