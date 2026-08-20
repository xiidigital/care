variable "project_id" {
  description = "GCP project that holds the workload identity pool and the automation identities."
  type        = string
}

variable "github_repository" {
  description = <<-EOT
    The one GitHub repository whose Actions tokens this pool accepts, as
    "owner/name".

    This is the trust boundary. A fork of this repository is a different value
    and is rejected at token exchange, which is what makes a pull request from a
    fork unable to reach GCP however the workflow is written
    (ES-08 sections 16, 62).
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^/ ]+/[^/ ]+$", var.github_repository))
    error_message = "github_repository must be owner/name, with no leading https:// and no trailing .git."
  }
}

variable "pool_id" {
  description = "Workload identity pool id. A deleted pool id is reserved for 30 days, so recreating one needs a new value."
  type        = string
  default     = "care-github"
}

variable "provider_id" {
  description = "Workload identity pool provider id."
  type        = string
  default     = "github"
}

variable "identities" {
  description = <<-EOT
    Automation identities, keyed by purpose.

    `environments` names the GitHub Environments whose jobs may impersonate the
    identity. This is the preferred form: the claim exists only when a job
    declares `environment:`, and a protected environment gates the token behind
    approval.

    `refs` names full git refs ("refs/heads/gcp") for an identity used by a
    workflow that declares no environment. Weaker, and empty by default.
  EOT
  type = map(object({
    account_id   = string
    display_name = string
    description  = string
    environments = optional(list(string), [])
    refs         = optional(list(string), [])
  }))

  validation {
    condition = alltrue([
      for key, identity in var.identities :
      length(identity.environments) > 0 || length(identity.refs) > 0
    ])
    error_message = "Every identity must name at least one environment or ref. An identity nothing may impersonate is dead configuration; an identity with no restriction would be worse."
  }
}

variable "infrastructure_identity" {
  description = "Key in `identities` that receives the infrastructure roles below, when they are granted."
  type        = string
  default     = "infrastructure"
}

variable "grant_infrastructure_roles" {
  description = <<-EOT
    Grant the infrastructure identity the project roles needed to apply this
    repository's OpenTofu.

    False by default. ES-07 applied every environment from an authenticated
    operator workstation, and that remains a supported model; an identity
    holding project administration it does not yet use is a standing risk. Turn
    it on when infrastructure apply actually runs in CI, and review the role
    list when you do.
  EOT
  type        = bool
  default     = false
}

variable "infrastructure_roles" {
  description = <<-EOT
    The roles an infrastructure apply needs, enumerated.

    Each one corresponds to resources this repository declares: Cloud Run, Cloud
    SQL, Cloud Storage, Secret Manager containers, Cloud Tasks, Cloud Scheduler,
    Artifact Registry, the runtime service accounts, the IAM bindings between
    them, the enabled API set and the alert policies.

    Listed rather than replaced by roles/editor deliberately. Editor would be
    shorter and would also grant everything nobody reviewed (ES-08 section 114).
  EOT
  type        = list(string)
  default = [
    # Read the project's own metadata. `data "google_project" "this"` needs
    # resourcemanager.projects.get, which none of the admin roles below carry --
    # projectIamAdmin grants get/setIamPolicy on the project, not get on the
    # project itself. Without it a plan fails before it reads any resource:
    # "the user does not have permission to access Project ... or it may not
    # exist", which reads like a missing project rather than a missing role.
    # roles/browser is the smallest predefined role that supplies it, and it is
    # read-only (ES-08 section 114: name the permission, do not reach for
    # roles/editor).
    "roles/browser",
    "roles/run.admin",
    "roles/cloudsql.admin",
    "roles/storage.admin",
    "roles/secretmanager.admin",
    "roles/cloudtasks.admin",
    "roles/cloudscheduler.admin",
    "roles/artifactregistry.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/monitoring.admin",
  ]
}
