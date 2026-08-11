# Cloud Run: one image, three shapes.
#
# The API and the worker are services; initialization and the periodic cleanups
# are Jobs. All four run the same var.image and differ only in the command they
# start and the environment they carry (ADR-0007 "Runtime roles").

# ---------------------------------------------------------------------------
# Task worker — private
#
# Created before the API so the API can be given its URL. The worker is not
# given its own by reference; see the note in main.tf.
# ---------------------------------------------------------------------------

module "worker" {
  source = "../cloud-run-service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.worker_service_name
  image                 = var.image
  command               = ["./start-worker.sh"]
  process_role          = "task_worker"
  service_account_email = google_service_account.worker.email

  # Not negotiable, and not merely a default: the module refuses to build a
  # task_worker with unauthenticated invocation at all. The task endpoint
  # carries no application-layer authentication by design (ES-06 section 14),
  # so Cloud Run IAM is the whole boundary.
  allow_unauthenticated = false

  # Exactly one member may invoke it. Not allUsers, not allAuthenticatedUsers,
  # not the API, not the worker itself.
  invoker_members = ["serviceAccount:${google_service_account.tasks_invoker.email}"]

  # INGRESS_TRAFFIC_ALL with no unauthenticated invoker is intentional: an
  # anonymous request reaches Cloud Run's IAM check and is rejected there,
  # before Django. Internal-only ingress would additionally require the caller
  # to be inside the VPC, which Cloud Tasks is not, and would break the queue
  # without adding a boundary that IAM does not already provide.
  ingress = "INGRESS_TRAFFIC_ALL"

  env        = local.worker_env
  secret_env = local.secret_env_by_role["worker"]

  cloudsql_instance_connection_name = google_sql_database_instance.this.connection_name

  cpu                     = var.worker_cpu
  memory                  = var.worker_memory
  min_instances           = var.worker_min_instances
  max_instances           = var.worker_max_instances
  concurrency             = var.worker_concurrency
  request_timeout_seconds = var.worker_request_timeout_seconds

  deletion_protection = false
  labels              = local.labels

  depends_on = [
    google_secret_manager_secret_iam_member.accessors,
    google_project_iam_member.cloudsql_client,
    google_sql_database.care,
  ]
}

# The worker's environment is built from a derived URL rather than read back
# from the service, because the service cannot reference itself. This asserts
# the derivation was right. It runs after apply and on every plan; a failure
# means Cloud Run's URL format changed and GCP_WORKER_URL is pointing at
# nothing.
check "worker_url_derivation" {
  assert {
    condition     = module.worker.uri == local.worker_base_url
    error_message = "Derived worker URL ${local.worker_base_url} does not match the deployed service URL ${module.worker.uri}. GCP_WORKER_URL and GCP_TASKS_OIDC_AUDIENCE are built from the derived value, so task dispatch and OIDC audience validation are both wrong until this is reconciled."
  }
}

# ---------------------------------------------------------------------------
# API — public entry point
# ---------------------------------------------------------------------------

module "api" {
  source = "../cloud-run-service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.api_service_name
  image                 = var.image
  command               = ["./start.sh"]
  process_role          = "api"
  service_account_email = google_service_account.api.email

  # A separate question from CARE's own authentication, which still guards
  # every protected route. This decides only whether Cloud Run answers an
  # anonymous request at all (ADR-0007 "Cloud Run API").
  allow_unauthenticated = var.api_allow_unauthenticated

  ingress = "INGRESS_TRAFFIC_ALL"

  env        = local.api_env
  secret_env = local.secret_env_by_role["api"]

  cloudsql_instance_connection_name = google_sql_database_instance.this.connection_name

  cpu                     = var.api_cpu
  memory                  = var.api_memory
  min_instances           = var.api_min_instances
  max_instances           = var.api_max_instances
  concurrency             = var.api_concurrency
  request_timeout_seconds = var.api_request_timeout_seconds

  deletion_protection = false
  labels              = local.labels

  depends_on = [
    google_secret_manager_secret_iam_member.accessors,
    google_project_iam_member.cloudsql_client,
    google_sql_database.care,
  ]
}

# ---------------------------------------------------------------------------
# Jobs
#
# Initialization plus the two operations Celery Beat used to fire. All three are
# ephemeral processes whose health is their exit status, all three run
# CARE_PROCESS_ROLE=init, and all three invoke a committed application
# entrypoint or management command rather than a sequence rebuilt here
# (ADR-0007 "Initialization").
#
# Using the init role for the cleanups is a deliberate approximation: ADR-0006
# offers api, task_worker, scheduler and init, and a periodic batch command is
# none of them exactly. init is the only ephemeral, non-serving role, so it is
# the closest correct fit. Recorded in the unresolved items inventory.
# ---------------------------------------------------------------------------

locals {
  jobs = {
    init = {
      name    = local.init_job_name
      command = ["./initialize.sh"]
      args    = []
    }
    cleanup-expired-token-slots = {
      name    = "${local.name_prefix}-cleanup-token-slots"
      command = ["python"]
      args    = ["manage.py", "cleanup_expired_token_slots"]
    }
    cleanup-incomplete-file-uploads = {
      name    = "${local.name_prefix}-cleanup-uploads"
      command = ["python"]
      args    = ["manage.py", "cleanup_incomplete_file_uploads"]
    }
  }
}

resource "google_cloud_run_v2_job" "jobs" {
  for_each = local.jobs

  project             = var.project_id
  name                = each.value.name
  location            = var.region
  labels              = local.labels
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.init.email
      timeout         = "${var.job_timeout_seconds}s"

      # No retries. A failed initialization must fail the deployment
      # procedure, and a retry would turn a hard failure into a slow one and
      # then possibly into an apparent success (ADR-0007 "Initialization").
      max_retries = 0

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.this.connection_name]
        }
      }

      containers {
        image   = var.image
        command = each.value.command
        args    = each.value.args

        resources {
          limits = {
            cpu    = var.job_cpu
            memory = var.job_memory
          }
        }

        env {
          name  = "CARE_PROCESS_ROLE"
          value = "init"
        }

        dynamic "env" {
          for_each = local.init_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.secret_env_by_role["init"]
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.accessors,
    google_project_iam_member.cloudsql_client,
    google_sql_database.care,
  ]
}
