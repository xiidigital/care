terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Partial configuration; see dev/backend.hcl.example.
  #
  #   tofu init -backend-config=backend.hcl
  backend "gcs" {
    prefix = "environments/prod"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Production.
#
# Structure, not sizing. ES-07 section 86 forbids guessing production capacity
# before measurements exist, so every quantity that costs money is a variable
# with **no default**: `tofu plan` fails until someone decides it, which is the
# documented required decision rather than a number nobody chose.
#
# What is not left open is protection. Deletion protection, backups and bucket
# destroy semantics are fixed here and the variables that could weaken them are
# validated in variables.tf. A production environment cannot be applied from
# this root in a state where a routine plan could destroy a stateful resource
# (ES-07 sections 86, 87 and 106).
#
# Not applied by ES-07.
module "care" {
  source = "../../modules/care-environment"

  project_id  = var.project_id
  region      = var.region
  environment = "prod"
  image       = var.image

  # --- Cloud SQL -----------------------------------------------------------
  sql_tier              = var.sql_tier
  sql_disk_size_gb      = var.sql_disk_size_gb
  sql_availability_type = var.sql_availability_type

  sql_backup_enabled         = true
  sql_backup_retained_count  = var.sql_backup_retained_count
  sql_point_in_time_recovery = true

  # Not variables. Production deletion protection is a property of this root,
  # so there is no tfvars value that turns it off (ADR-0007 "Resource
  # protection", ES-07 section 105).
  sql_deletion_protection           = true
  sql_terraform_deletion_protection = true

  # --- Storage -------------------------------------------------------------
  # force_destroy false is required by ES-07 section 89: destroy must not
  # silently empty a bucket holding patient files.
  bucket_force_destroy = false
  bucket_versioning    = true

  # --- Cloud Run -----------------------------------------------------------
  api_allow_unauthenticated = var.api_allow_unauthenticated

  api_min_instances    = var.api_min_instances
  api_max_instances    = var.api_max_instances
  api_concurrency      = var.api_concurrency
  api_gunicorn_workers = var.api_gunicorn_workers

  worker_min_instances    = 0
  worker_max_instances    = var.worker_max_instances
  worker_concurrency      = var.worker_concurrency
  worker_gunicorn_workers = var.worker_gunicorn_workers

  # --- Cloud Tasks ---------------------------------------------------------
  tasks_max_dispatches_per_second = var.tasks_max_dispatches_per_second
  tasks_max_concurrent_dispatches = var.tasks_max_concurrent_dispatches

  # --- Cloud Scheduler -----------------------------------------------------
  scheduler_jobs_enabled = true

  # --- Application ---------------------------------------------------------
  # django_debug is not a variable here. There is no production reason for it.
  django_debug               = false
  django_secure_ssl_redirect = true

  # Console email is permitted in production, and is the default here.
  #
  # Not a compromise and not a placeholder. CARE separates application email
  # generation from external email delivery; the first is architecture and is
  # present, the second is an operational capability an operator enables when
  # they have a relay to enable it against (unresolved-items.md N1). Nothing in
  # this root requires SMTP credentials, and no validation rule may be added
  # that does.
  #
  # It is a default rather than a constant precisely because production is the
  # environment most likely to want delivery. Clearing it restores Django's SMTP
  # backend; the transport settings then come through extra_env and the password
  # through optional_secrets. The default is the console backend rather than
  # empty because empty means SMTP to localhost:587, which no Cloud Run
  # container answers — a broken configuration is a worse default than an
  # honest one.
  django_email_backend = var.django_email_backend

  django_allowed_hosts = var.django_allowed_hosts
  csrf_trusted_origins = var.csrf_trusted_origins
  cors_allowed_origins = var.cors_allowed_origins
  current_domain       = var.current_domain

  optional_secrets = var.optional_secrets
  extra_env        = var.extra_env

  # --- Monitoring ----------------------------------------------------------
  alerts_enabled              = true
  alert_notification_channels = var.alert_notification_channels

  # --- Delivery ------------------------------------------------------------
  # Who may publish images here and who may deploy them. Empty until the
  # bootstrap root has created the automation identities (ES-08 section 17).
  deployment_principals      = var.deployment_principals
  image_publisher_principals = var.image_publisher_principals

  labels = var.labels
}
