variable "project_id" {
  description = "GCP project for production. Must be its own project: environment separation covers stateful resources and secrets (ADR-0007 \"Environments\")."
  type        = string
}

variable "region" {
  type = string
}

variable "image" {
  description = "CARE application image. A digest in production — a tag can be moved, and then nobody can say what is running."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "Production must deploy an immutable image digest, not a tag. Publish the image, read its digest, and reference <repo>/care@sha256:<digest> (ES-07 section 101)."
  }
}

# ---------------------------------------------------------------------------
# Sizing — no defaults, by requirement
#
# ES-07 section 86: "Do not guess expensive production sizing. Use
# variables/placeholders with documented required decisions." A default here
# would be a guess that outlives the person who made it, so a plan fails until
# each of these is decided against measurements from staging.
# ---------------------------------------------------------------------------

variable "sql_tier" {
  description = "REQUIRED DECISION. Cloud SQL machine type, from observed staging load. This is the largest standing cost in the environment."
  type        = string
}

variable "sql_disk_size_gb" {
  description = "REQUIRED DECISION. Initial disk. Autoresize is on, so this is a floor rather than a ceiling."
  type        = number
}

variable "sql_availability_type" {
  description = "REQUIRED DECISION. REGIONAL for high availability, roughly doubling instance cost. ZONAL is permitted but must be chosen knowingly."
  type        = string

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.sql_availability_type)
    error_message = "sql_availability_type must be ZONAL or REGIONAL."
  }
}

variable "sql_backup_retained_count" {
  description = "REQUIRED DECISION. How many automated backups to keep. Production backup behaviour must not be accidental (ES-07 section 28)."
  type        = number

  validation {
    condition     = var.sql_backup_retained_count >= 7
    error_message = "Production must retain at least 7 automated backups."
  }
}

variable "api_min_instances" {
  description = "REQUIRED DECISION. Above zero removes cold starts and adds a standing cost that never scales down."
  type        = number
}

variable "api_max_instances" {
  description = "REQUIRED DECISION. Bounded against the Cloud SQL connection ceiling; see the database_connection_budget output."
  type        = number
}

variable "api_concurrency" {
  description = "REQUIRED DECISION. Requests per instance."
  type        = number
}

variable "api_gunicorn_workers" {
  description = "REQUIRED DECISION. Processes per instance. max_instances x this is the API's share of the connection budget."
  type        = number
}

variable "worker_max_instances" {
  description = "REQUIRED DECISION."
  type        = number
}

variable "worker_concurrency" {
  description = "REQUIRED DECISION. Tasks per instance."
  type        = number
}

variable "worker_gunicorn_workers" {
  description = "REQUIRED DECISION."
  type        = number
}

variable "tasks_max_dispatches_per_second" {
  description = "REQUIRED DECISION."
  type        = number
}

variable "tasks_max_concurrent_dispatches" {
  description = "REQUIRED DECISION. Must stay within worker_max_instances x worker_concurrency."
  type        = number
}

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

variable "api_allow_unauthenticated" {
  description = "Whether Cloud Run answers anonymous requests to the API. Separate from CARE's own authentication. The worker is unaffected and cannot be made public."
  type        = bool
  default     = true
}

variable "django_allowed_hosts" {
  description = "REQUIRED. Production serves a real hostname; the .run.app wildcard the module adds is not an adequate production ALLOWED_HOSTS on its own."
  type        = list(string)

  validation {
    condition     = length(var.django_allowed_hosts) > 0
    error_message = "Production must name its own hostnames. Relying on the .run.app wildcard alone means any Cloud Run URL in the project is an accepted Host header."
  }
}

variable "csrf_trusted_origins" {
  type    = list(string)
  default = []
}

variable "cors_allowed_origins" {
  description = "Frontend origins. An empty list means no cross-origin browser access, which is correct when the frontend is same-origin."
  type        = list(string)
  default     = []
}

variable "current_domain" {
  type    = string
  default = ""
}

variable "django_email_backend" {
  description = <<-EOT
    DJANGO_EMAIL_BACKEND. Defaults to Django's console backend, which is a valid
    production configuration: email-producing workflows are operational and the
    rendered message reaches Cloud Logging, while external delivery is
    intentionally absent (unresolved-items.md N1).

    Nothing here requires SMTP credentials. To enable external delivery, set
    this to "" — restoring Django's SMTP backend — and supply EMAIL_HOST,
    EMAIL_PORT, EMAIL_USER and EMAIL_USE_TLS or EMAIL_USE_SSL through extra_env,
    with EMAIL_PASSWORD declared in optional_secrets and its value written
    straight to Secret Manager. The repository names no provider, and no
    credential belongs in any tracked file.
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

variable "alert_notification_channels" {
  description = "REQUIRED. Alert policies that notify nobody are not monitoring."
  type        = list(string)

  validation {
    condition     = length(var.alert_notification_channels) > 0
    error_message = "Production must route alerts somewhere. Create a notification channel and reference it here."
  }
}

variable "labels" {
  type    = map(string)
  default = {}
}
