# One CARE environment. dev, staging and prod instantiate this module with
# different sizing and protection; the architecture is identical across them
# (ADR-0007 "Environments").

variable "project_id" {
  description = "GCP project. Supplied explicitly by each environment root; never defaulted."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Cloud SQL, Artifact Registry and Cloud Tasks. Keeping them aligned avoids cross-region latency on every database call."
  type        = string
}

variable "environment" {
  description = "Environment name. Part of every resource name, so it is also what keeps environments from colliding."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "image" {
  description = <<-EOT
    The CARE application image, used by the API, the task worker and every Job.
    ADR-0007 requires the same image for all roles: they differ by command and
    environment, never by build.

    Prefer an immutable reference — a digest, or a tag that is never moved.
    Building and publishing is an operational or CI responsibility; OpenTofu
    only selects.
  EOT
  type        = string

  validation {
    condition     = length(trimspace(var.image)) > 0
    error_message = "image must be set. Publish the CARE image to Artifact Registry before creating runtime resources (ES-07 section 131)."
  }
}

variable "labels" {
  type    = map(string)
  default = {}
}

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

variable "name_prefix_override" {
  description = "Overrides the care-<environment> prefix. For the rare case where a name is already taken."
  type        = string
  default     = ""
}

variable "worker_url_override" {
  description = <<-EOT
    The worker's own GCP_WORKER_URL, for the one value that cannot be read back
    from the service that provides it.

    Cloud Run issues a service's URL at creation, and the worker's settings
    validate GCP_WORKER_URL because validation follows the selected task backend
    rather than the role. A service cannot reference its own uri, so the module
    derives one from the service name and project number — correct for projects
    on Cloud Run's newer URL form, wrong for projects still issued the older
    per-service-hash form.

    Leave empty on the first apply. If the check block reports a mismatch, set
    this to the value it prints and apply again. Everything that actually
    dispatches to the worker — the API and the init Job — reads the real uri and
    is unaffected either way.
  EOT
  type        = string
  default     = ""
}

variable "sql_instance_name_suffix" {
  description = <<-EOT
    Appended to the Cloud SQL instance name. Cloud SQL reserves a deleted
    instance name for roughly a week, so recreating a torn-down dev environment
    needs a new one. Empty for the first instance.
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Cloud SQL
# ---------------------------------------------------------------------------

variable "database_version" {
  description = <<-EOT
    Cloud SQL PostgreSQL major version.

    17, because that is what CARE's own stack runs: docker-compose.yaml pins
    postgres:17-alpine, so it is the major the test suite actually executes
    against. Note that CLAUDE.md still says 16; the running configuration is
    authoritative and the discrepancy is recorded in the unresolved items
    inventory.

    Not raised past that merely because a newer major exists (ES-07 section 24).
  EOT
  type        = string
  default     = "POSTGRES_17"
}

variable "sql_tier" {
  description = "Cloud SQL machine type. The principal persistent baseline cost of the Redis-free profile (ADR-0007 \"Scale to zero\")."
  type        = string
}

variable "sql_disk_size_gb" {
  type = number
}

variable "sql_disk_autoresize" {
  type    = bool
  default = true
}

variable "sql_availability_type" {
  description = "ZONAL or REGIONAL. Regional is high availability and roughly doubles instance cost; dev does not get it for architectural symmetry alone (ES-07 section 26)."
  type        = string

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.sql_availability_type)
    error_message = "sql_availability_type must be ZONAL or REGIONAL."
  }
}

variable "sql_backup_enabled" {
  type    = bool
  default = true
}

variable "sql_backup_start_time" {
  description = "HH:MM UTC."
  type        = string
  default     = "03:00"
}

variable "sql_backup_retained_count" {
  type    = number
  default = 7
}

variable "sql_point_in_time_recovery" {
  description = "Retains write-ahead logs. Real recovery capability, and real storage cost."
  type        = bool
  default     = false
}

variable "sql_deletion_protection" {
  description = <<-EOT
    Cloud SQL's own deletion protection, enforced by the service rather than by
    OpenTofu. Production must have it on; the prod root has no way to turn it
    off (see its validation).
  EOT
  type        = bool
}

variable "sql_terraform_deletion_protection" {
  description = "The provider-side guard. Kept consistent with sql_deletion_protection so a plan cannot delete an instance the service would refuse to delete (ES-07 section 27)."
  type        = bool
}

variable "database_name" {
  type    = string
  default = "care"
}

variable "database_user" {
  description = "Application database user. Created out of band with its password so no credential passes through OpenTofu state — see infrastructure/scripts/provision-secrets.sh."
  type        = string
  default     = "care"
}

# ---------------------------------------------------------------------------
# Cloud Storage
# ---------------------------------------------------------------------------

variable "bucket_location" {
  description = "Bucket location. Defaults to the deployment region; a multi-region value changes residency and cost and must be chosen deliberately (ES-07 section 41)."
  type        = string
  default     = ""
}

variable "bucket_force_destroy" {
  description = "Whether `tofu destroy` may delete a bucket that still holds objects. Never true in production (ES-07 section 89)."
  type        = bool
  default     = false
}

variable "bucket_versioning" {
  type    = bool
  default = false
}

variable "share_report_bucket_with_patient" {
  description = <<-EOT
    Whether the `report` alias points at the patient bucket.

    True by default because that is what the application already does:
    CARE_REPORT_STORAGE_BUCKET falls back to FILE_UPLOAD_BUCKET, the same value
    the patient alias falls back to. The aliases stay separate in configuration
    so one can be repointed without touching application code; only the physical
    bucket is shared (ES-07 section 37).
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Cloud Run — API
# ---------------------------------------------------------------------------

variable "api_allow_unauthenticated" {
  description = "Whether Cloud Run lets anyone reach the API. Separate from CARE's own authentication, which still guards protected routes."
  type        = bool
  default     = true
}

variable "api_cpu" {
  type    = string
  default = "1"
}

variable "api_memory" {
  type    = string
  default = "1Gi"
}

variable "api_min_instances" {
  type    = number
  default = 0
}

variable "api_max_instances" {
  type = number
}

variable "api_concurrency" {
  type = number
}

variable "api_request_timeout_seconds" {
  type    = number
  default = 300
}

variable "api_gunicorn_workers" {
  description = "Gunicorn processes per instance. Multiplied by max instances and by concurrency, this is what sizes the Cloud SQL connection budget."
  type        = number
  default     = 2
}

# ---------------------------------------------------------------------------
# Cloud Run — task worker
# ---------------------------------------------------------------------------

variable "worker_cpu" {
  type    = string
  default = "1"
}

variable "worker_memory" {
  description = "Report rendering loads templates and images; the worker is not sized like the API by default."
  type        = string
  default     = "2Gi"
}

variable "worker_min_instances" {
  type    = number
  default = 0
}

variable "worker_max_instances" {
  type = number
}

variable "worker_concurrency" {
  description = "Deliberately low. A task is a unit of work holding a database connection for its whole duration, which is not how an API request behaves (ES-07 section 51)."
  type        = number
  default     = 4
}

variable "worker_request_timeout_seconds" {
  description = "Must exceed the longest valid task. Report generation is the long one; see the operations guide for the measurement this should be revisited against."
  type        = number
  default     = 900
}

variable "worker_gunicorn_workers" {
  type    = number
  default = 2
}

# ---------------------------------------------------------------------------
# Cloud Run Jobs
# ---------------------------------------------------------------------------

variable "job_cpu" {
  type    = string
  default = "1"
}

variable "job_memory" {
  description = "migrate and compilemessages are the memory-hungry steps of initialization."
  type        = string
  default     = "2Gi"
}

variable "job_timeout_seconds" {
  type    = number
  default = 1800
}

variable "enable_fixture_loader" {
  description = <<-EOT
    Create a manually invoked Job that runs `manage.py load_fixtures`, seeding
    the database with synthetic accounts and clinical data.

    Development tooling, and the one Job here that is not part of a deployment.
    It exists because ES-07 section 93 requires authenticating to CARE "through
    an appropriate dev path" to prove the GCS transport end to end, and a
    greenfield environment has no account to authenticate as.

    Never true outside dev. The fixtures create known-credential users, and the
    command is destructive to existing data. The module refuses it elsewhere.
  EOT
  type        = bool
  default     = false
}

variable "fixture_image" {
  description = <<-EOT
    Image for the fixture Job, built by `docker/fixtures.Dockerfile` FROM the
    runtime image.

    This is the single deliberate exception to the same-image rule, and it does
    not weaken it: no runtime role uses this image, and the deployment does not
    contain it. `load_fixtures` imports Faker, a development dependency the
    production image correctly omits, so a fixture Job on the runtime image
    fails at import.

    Empty means "use var.image", which works only if that image carries Faker.
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Cloud Tasks
# ---------------------------------------------------------------------------

variable "tasks_max_attempts" {
  description = "Finite by requirement. Maintenance mode returns a retryable response, so the queue must be able to ride out a maintenance window without retrying forever (ADR-0007 \"Cloud Tasks\")."
  type        = number
  default     = 10
}

variable "tasks_max_retry_duration_seconds" {
  type    = number
  default = 86400
}

variable "tasks_min_backoff_seconds" {
  type    = number
  default = 5
}

variable "tasks_max_backoff_seconds" {
  type    = number
  default = 600
}

variable "tasks_max_doublings" {
  type    = number
  default = 5
}

variable "tasks_max_dispatches_per_second" {
  type = number
}

variable "tasks_max_concurrent_dispatches" {
  description = "Bounded by what the worker and Cloud SQL can absorb: worker_max_instances x worker_concurrency is the ceiling this must stay under."
  type        = number
}

# ---------------------------------------------------------------------------
# Cloud Scheduler
# ---------------------------------------------------------------------------

variable "scheduler_timezone" {
  description = <<-EOT
    Timezone for every schedule. Defaults to Asia/Kolkata because that is
    settings.TIME_ZONE and therefore CELERY_TIMEZONE — the timezone the Beat
    schedules being replaced actually ran in. Changing it silently would move
    every cleanup to a different local hour (ES-07 section 68).
  EOT
  type        = string
  default     = "Asia/Kolkata"
}

variable "scheduler_jobs_enabled" {
  description = "Whether the periodic cleanups run. Both delete rows, so an environment holding hand-made test data may want them off (ES-07 section 69)."
  type        = bool
  default     = true
}

variable "schedule_cleanup_expired_token_slots" {
  description = "Replaces crontab(hour=0, minute=0) from care/emr/tasks/__init__.py."
  type        = string
  default     = "0 0 * * *"
}

variable "schedule_cleanup_incomplete_file_uploads" {
  description = <<-EOT
    Replaces a Beat *interval* of FILE_UPLOAD_EXPIRY_HOURS hours (24 by
    default), which had no fixed wall-clock time — it fired relative to when
    beat started. Cloud Scheduler expresses only cron, so this becomes a daily
    run at a fixed hour. That is a deliberate, recorded change of schedule shape,
    not a silent one; the cleanup is idempotent and time-of-day independent.
  EOT
  type        = string
  default     = "30 1 * * *"
}

# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------

variable "django_allowed_hosts" {
  description = "DJANGO_ALLOWED_HOSTS, as a JSON list. Cloud Run hostnames are appended automatically, so this holds only custom domains."
  type        = list(string)
  default     = []
}

variable "csrf_trusted_origins" {
  description = "CSRF_TRUSTED_ORIGINS. The API's own Cloud Run origin is appended automatically."
  type        = list(string)
  default     = []
}

variable "cors_allowed_origins" {
  description = "CORS_ALLOWED_ORIGINS. Frontend origins; production must not be left open."
  type        = list(string)
  default     = []
}

variable "current_domain" {
  description = "CURRENT_DOMAIN — the frontend host used when building links in outbound email."
  type        = string
  default     = ""
}

variable "django_debug" {
  description = "Never true outside a controlled environment. The prod root rejects true."
  type        = bool
  default     = false
}

variable "django_secure_ssl_redirect" {
  type    = bool
  default = true
}

variable "django_email_backend" {
  description = <<-EOT
    DJANGO_EMAIL_BACKEND. Empty leaves the application default, which is SMTP
    to EMAIL_HOST — and that defaults to localhost:587.

    A Cloud Run container runs no SMTP server, so leaving this empty without
    also setting EMAIL_HOST makes every email-sending task fail with a refused
    connection. The failure is in the handler, after Cloud Tasks has delivered
    the request, so the queue retries it to exhaustion.

    dev uses the console backend: the message is written to stdout and arrives
    in Cloud Logging, which is the email test sink ES-07 section 99 accepts as
    observable evidence. staging and prod need a real relay configured through
    EMAIL_HOST and the optional EMAIL_PASSWORD secret.
  EOT
  type        = string
  default     = ""
}

variable "conn_max_age" {
  description = "Django persistent connection lifetime. Longer reduces setup cost and holds more Cloud SQL connections open; see the connection budget in the operations guide."
  type        = number
  default     = 60
}

variable "extra_env" {
  description = "Additional non-secret environment variables applied to every role. An escape hatch for integrations, not a place for credentials."
  type        = map(string)
  default     = {}
}

variable "optional_secrets" {
  description = <<-EOT
    Secret Manager containers to create beyond the required set, as
    environment variable name => role list. Roles are api, worker and init.

    Example: { EMAIL_PASSWORD = ["api", "worker"], SENTRY_DSN = ["api", "worker", "init"] }

    Only the named roles get read access; nothing grants every identity access
    to every secret (ES-07 section 23).
  EOT
  type        = map(list(string))
  default     = {}

  validation {
    condition = alltrue([
      for roles in values(var.optional_secrets) :
      alltrue([for role in roles : contains(["api", "worker", "init"], role)])
    ])
    error_message = "optional_secrets roles must be drawn from api, worker, init."
  }
}

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

variable "alert_notification_channels" {
  description = "Notification channel ids for alert policies. Empty means the policies are created but notify nobody, which is the sane dev default."
  type        = list(string)
  default     = []
}

variable "alerts_enabled" {
  description = "Whether alert policies are created at all. Off in dev: paging on scale-to-zero behaviour is noise (ES-07 section 81)."
  type        = bool
  default     = false
}
