variable "project_id" {
  description = "GCP project that owns the state bucket. Supplied explicitly; never defaulted to a personal project."
  type        = string

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must be set."
  }
}

variable "region" {
  description = "Region for the state bucket. Aligns with the deployment region so state and infrastructure share a failure domain."
  type        = string
  default     = "us-central1"
}

variable "state_bucket_name" {
  description = "Name of the GCS state bucket. Defaults to care-tfstate-<project_id>, which is globally unique because project IDs are."
  type        = string
  default     = ""
}

variable "noncurrent_version_retention_days" {
  description = "How long superseded state versions are kept. Recovery window for a bad apply; not a backup policy."
  type        = number
  default     = 90
}

variable "create_deployer_service_account" {
  description = <<-EOT
    Create a deployment-automation identity (ADR-0007 "Service accounts and least
    privilege", ES-07 section 16). Off by default: ES-07 applies dev from an
    operator workstation using ADC, and an unused identity with infrastructure
    permissions is a liability rather than a control. Turn it on when CI/CD
    exists, which is ES-08 and out of scope here.
  EOT
  type        = bool
  default     = false
}

variable "github_repository" {
  description = <<-EOT
    GitHub repository, as "owner/name", whose Actions workflows may
    authenticate to this project through workload identity federation (ES-08).

    Empty disables the whole GitHub trust relationship, which is the right
    setting for a project with no CI/CD. Setting it creates a workload identity
    pool, a provider that accepts tokens from this repository and no other, and
    four automation identities that hold no environment permission until an
    environment grants them one.
  EOT
  type        = string
  default     = ""
}

variable "grant_infrastructure_roles" {
  description = <<-EOT
    Grant the infrastructure automation identity the project roles required to
    apply this repository's OpenTofu.

    False by default: applying from an authenticated operator remains supported,
    and an identity holding unused project administration is a standing risk.
  EOT
  type        = bool
  default     = false
}
