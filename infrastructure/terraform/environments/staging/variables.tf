variable "project_id" {
  description = "GCP project for staging. Should not be the dev project: ADR-0007 requires environment separation to include stateful resources and secrets."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  description = "CARE application image. Prefer a digest."
  type        = string
}

# Sizing is a decision, not a default. These have starting values because
# staging is meant to be smaller than production by construction, but they are
# exposed so the decision stays visible in tfvars.

variable "sql_tier" {
  type    = string
  default = "db-g1-small"
}

variable "sql_disk_size_gb" {
  type    = number
  default = 20
}

variable "api_max_instances" {
  type    = number
  default = 4
}

variable "worker_max_instances" {
  type    = number
  default = 3
}

variable "tasks_max_concurrent_dispatches" {
  description = "Must stay within worker_max_instances x worker_concurrency; the module refuses otherwise."
  type        = number
  default     = 8
}

variable "django_allowed_hosts" {
  type    = list(string)
  default = []
}

variable "csrf_trusted_origins" {
  type    = list(string)
  default = []
}

variable "cors_allowed_origins" {
  type    = list(string)
  default = []
}

variable "current_domain" {
  type    = string
  default = ""
}

variable "django_email_backend" {
  description = <<-EOT
    DJANGO_EMAIL_BACKEND. Defaults to Django's console backend, which is a valid
    runtime configuration for staging: email-producing workflows are
    operational, the rendered message reaches Cloud Logging, and the whole
    asynchronous path is verifiable. External delivery is intentionally absent
    and is an optional operational capability (unresolved-items.md N1).

    Set to "" to restore Django's SMTP backend, and then supply EMAIL_HOST,
    EMAIL_PORT, EMAIL_USER and EMAIL_USE_TLS or EMAIL_USE_SSL through extra_env,
    with EMAIL_PASSWORD declared in optional_secrets. No provider is named by
    this repository and none may be committed to it.
  EOT
  type        = string
  default     = "django.core.mail.backends.console.EmailBackend"
}

variable "optional_secrets" {
  description = "Secret Manager containers beyond the required set, as variable name => roles. Containers only; values are written with `gcloud secrets versions add`."
  type        = map(list(string))
  default     = {}
}

variable "extra_env" {
  description = "Additional non-secret environment variables for every role. This is where generic SMTP transport settings go if external email delivery is enabled later. Never a credential."
  type        = map(string)
  default     = {}
}

variable "alerts_enabled" {
  type    = bool
  default = true
}

variable "alert_notification_channels" {
  description = "Notification channel ids. Empty means the policies exist but notify nobody."
  type        = list(string)
  default     = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
