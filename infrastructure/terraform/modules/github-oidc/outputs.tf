# Everything here is a non-secret identifier. These are the values that go into
# GitHub repository or environment *variables*, not secrets: a workload identity
# provider resource name and a service-account email authenticate nobody on
# their own, and classifying them as secrets hides the configuration without
# protecting anything (ES-08 sections 72, 73).

output "workload_identity_provider" {
  description = "Full resource name of the provider. This is the value of the google-github-actions/auth `workload_identity_provider` input."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "pool_name" {
  description = "Full resource name of the workload identity pool."
  value       = local.pool_name
}

output "service_account_emails" {
  description = "Automation identity emails, by purpose. Each one goes into the GitHub environment that is allowed to impersonate it."
  value       = { for key, account in google_service_account.automation : key => account.email }
}

output "deployment_principals" {
  description = "The same emails in IAM member form, ready to be passed to an environment's deployment_principals or image_publisher_principals."
  value       = { for key, account in google_service_account.automation : key => "serviceAccount:${account.email}" }
}

output "trusted_repository" {
  description = "The repository whose Actions tokens this pool accepts."
  value       = var.github_repository
}
