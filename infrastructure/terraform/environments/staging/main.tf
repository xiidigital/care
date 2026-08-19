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
    prefix = "environments/staging"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Staging.
#
# Production's architecture at a smaller size (ES-07 section 85). The point of
# it is that a change which behaves here will behave in production, so the
# things that differ are quantities — instance size, instance counts, backup
# depth — and not the composition.
#
# Not applied by ES-07. The structure exists and validates; creating it needs a
# target project and an explicit decision.
module "care" {
  source = "../../modules/care-environment"

  project_id  = var.project_id
  region      = var.region
  environment = "staging"
  image       = var.image

  # --- Cloud SQL -----------------------------------------------------------
  # A real instance rather than a shared core, because staging is where a
  # migration's cost is meant to become visible before production sees it.
  sql_tier              = var.sql_tier
  sql_disk_size_gb      = var.sql_disk_size_gb
  sql_availability_type = "ZONAL" # single zone; production is where HA is bought

  sql_backup_enabled         = true
  sql_backup_retained_count  = 7
  sql_point_in_time_recovery = true # exercises the same recovery path as production

  # Protected. Staging holds data people rely on for release decisions, and a
  # deliberate teardown can flip these together.
  sql_deletion_protection           = true
  sql_terraform_deletion_protection = true

  # --- Storage -------------------------------------------------------------
  bucket_force_destroy = false
  bucket_versioning    = true

  # --- Cloud Run -----------------------------------------------------------
  api_allow_unauthenticated = true

  api_min_instances    = 0
  api_max_instances    = var.api_max_instances
  api_concurrency      = 40
  api_gunicorn_workers = 2

  worker_min_instances    = 0
  worker_max_instances    = var.worker_max_instances
  worker_concurrency      = 4
  worker_gunicorn_workers = 2

  # --- Cloud Tasks ---------------------------------------------------------
  tasks_max_dispatches_per_second = 10
  tasks_max_concurrent_dispatches = var.tasks_max_concurrent_dispatches

  # --- Cloud Scheduler -----------------------------------------------------
  scheduler_jobs_enabled = true

  # --- Application ---------------------------------------------------------
  django_debug               = false
  django_secure_ssl_redirect = true

  # Console email. A valid runtime configuration for staging, not a placeholder
  # for one (unresolved-items.md N1).
  #
  # CARE separates application email generation from external email delivery.
  # Generation is exercised in full here — the API enqueues, Cloud Tasks
  # delivers, the worker renders and the message lands in Cloud Logging — and
  # only the relay hop is absent. Staging is where that path is verified;
  # whether the bytes then reach a mailbox is an operational choice this
  # repository does not make for anyone.
  #
  # Overridable in tfvars. An operator who wants delivery clears it and supplies
  # the generic SMTP settings through extra_env plus EMAIL_PASSWORD through
  # optional_secrets. No provider is named here or anywhere else.
  django_email_backend = var.django_email_backend

  django_allowed_hosts = var.django_allowed_hosts
  csrf_trusted_origins = var.csrf_trusted_origins
  cors_allowed_origins = var.cors_allowed_origins
  current_domain       = var.current_domain

  optional_secrets = var.optional_secrets
  extra_env        = var.extra_env

  # --- Monitoring ----------------------------------------------------------
  alerts_enabled              = var.alerts_enabled
  alert_notification_channels = var.alert_notification_channels

  # --- Delivery ------------------------------------------------------------
  # Who may publish images here and who may deploy them. Empty until the
  # bootstrap root has created the automation identities (ES-08 section 17).
  deployment_principals      = var.deployment_principals
  image_publisher_principals = var.image_publisher_principals

  labels = var.labels
}
