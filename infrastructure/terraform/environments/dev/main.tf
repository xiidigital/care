terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Partial configuration. The bucket is supplied at init time from backend.hcl
  # so that no project-specific value is committed (ES-07 section 8):
  #
  #   tofu init -backend-config=backend.hcl
  #
  # The prefix is fixed here, not passed in. It is what keeps dev state from
  # ever being written over staging's, and an operator should not be able to
  # get it wrong from the command line (ES-07 section 13).
  backend "gcs" {
    prefix = "environments/dev"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Development.
#
# Deliberately the cheapest configuration that is still a real environment
# (ES-07 section 84). Everything scales to zero except Cloud SQL, which cannot;
# it is the whole persistent baseline, and it is the smallest instance offered.
#
# No Redis. Not a Memorystore instance, not a Redis VM, not a managed
# Redis-compatible service, and no Redis variable in the runtime environment.
# The application selects PostgreSQL for cache and rate limiting and Cloud Tasks
# for asynchronous work, so there is nothing for one to do.
module "care" {
  source = "../../modules/care-environment"

  project_id  = var.project_id
  region      = var.region
  environment = "dev"
  image       = var.image

  # --- Cloud SQL -----------------------------------------------------------
  # The smallest tier Cloud SQL offers: 614 MiB, shared core, roughly USD 8 a
  # month before storage. Its connection ceiling is about 25, which the scaling
  # limits below stay well inside — see the database_connection_budget output.
  sql_tier              = "db-f1-micro"
  sql_disk_size_gb      = 10
  sql_availability_type = "ZONAL" # HA doubles the cost and dev does not need it

  sql_backup_enabled         = true
  sql_backup_retained_count  = 7
  sql_point_in_time_recovery = false # write-ahead log retention is storage dev need not buy

  # Dev is meant to be destroyable, and the teardown procedure is documented.
  # Both guards are off together; the module refuses to let them disagree.
  sql_deletion_protection           = false
  sql_terraform_deletion_protection = false

  # --- Storage -------------------------------------------------------------
  # Dev buckets may be emptied by destroy. Production may not.
  bucket_force_destroy = true
  bucket_versioning    = false

  # --- Cloud Run -----------------------------------------------------------
  # The API is public at the platform level; CARE still authenticates every
  # protected route itself. The worker is private and cannot be made otherwise.
  api_allow_unauthenticated = true

  api_min_instances    = 0
  api_max_instances    = 2
  api_concurrency      = 20
  api_gunicorn_workers = 2

  worker_min_instances    = 0
  worker_max_instances    = 2
  worker_concurrency      = 4
  worker_gunicorn_workers = 2

  # --- Cloud Tasks ---------------------------------------------------------
  # Bounded by what two worker instances at concurrency 4 can absorb.
  tasks_max_dispatches_per_second = 5
  tasks_max_concurrent_dispatches = 5

  # --- Cloud Scheduler -----------------------------------------------------
  # Enabled: both cleanups are idempotent and dev holds no data worth keeping.
  # Set false before loading fixtures you intend to keep.
  scheduler_jobs_enabled = true

  # --- Application ---------------------------------------------------------
  django_debug               = false # never true, even here: this is a real deployment
  django_secure_ssl_redirect = true

  django_allowed_hosts = var.django_allowed_hosts
  csrf_trusted_origins = var.csrf_trusted_origins
  cors_allowed_origins = var.cors_allowed_origins
  current_domain       = var.current_domain

  optional_secrets = var.optional_secrets

  # --- Monitoring ----------------------------------------------------------
  # Policies exist in the module but are not created here. In an environment
  # that scales to zero and has no traffic, every one of them would fire on
  # normal behaviour (ES-07 section 81).
  alerts_enabled = false

  labels = var.labels
}
