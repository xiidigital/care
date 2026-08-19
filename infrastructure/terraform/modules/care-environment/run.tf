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

# The one value that cannot be read back from the resource that provides it:
# the worker's own copy of GCP_WORKER_URL. See the note in main.tf.
#
# Cloud Run has two URL forms in circulation — the newer
# <service>-<project-number>.<region>.run.app and the older
# <service>-<hash>-<region-code>.a.run.app — and which one a service reports as
# its canonical uri is not derivable. So this asserts rather than assumes, and
# names the fix.
#
# A warning here does not mean the deployment is broken, for two independent
# reasons. Task dispatch reads module.worker.uri directly, so the API and the
# init Job are unaffected either way; and Cloud Run answers on both hostnames
# for the same service, verified during ES-07, so the derived value addresses
# the right revision regardless. The warning is about exactness, not reachability.
check "worker_self_url_matches" {
  assert {
    condition     = module.worker.uri == local.worker_self_url
    error_message = "The worker's own GCP_WORKER_URL is built from ${local.worker_self_url}, but the deployed service URL is ${module.worker.uri}. Nothing dispatches with this value today -- the API and init Job read the service URL directly -- so the deployment is functional. To make it exact, set worker_url_override = \"${module.worker.uri}\" and apply again."
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
  # What the three deployment Jobs share: the deployment image, the init
  # identity, the init environment. Only the command differs between them.
  deployment_job = {
    image           = var.image
    service_account = google_service_account.init.email
    process_role    = "init"
    env             = local.init_env
    secret_env      = local.secret_env_by_role["init"]
  }

  # Development only, and not part of any deployment. Seeds synthetic accounts
  # and clinical data so that a greenfield environment has something to
  # authenticate as — which ES-07 section 93 requires in order to prove the GCS
  # transport through CARE rather than against the bucket.
  #
  # It differs from the deployment Jobs in three ways, all consequences of
  # load_fixtures driving CARE's viewsets through an in-process DRF client:
  #
  #   image            carries Faker, which the runtime image correctly omits
  #   service account  api, not init: it writes objects to the buckets, which
  #                    the init identity has no storage permission for
  #   env              fixture_env; see the note in config.tf
  #
  # CARE_PROCESS_ROLE=api follows from the same thing. The command exercises the
  # API's own code paths, so it must be configured as the API is.
  fixture_jobs = var.enable_fixture_loader ? {
    load-fixtures = merge(local.deployment_job, {
      name            = "${local.name_prefix}-load-fixtures"
      command         = ["python"]
      args            = ["manage.py", "load_fixtures"]
      image           = local.fixture_image
      service_account = google_service_account.api.email
      process_role    = "api"
      env             = local.fixture_env
    })
  } : {}

  jobs = merge(
    {
      init = merge(local.deployment_job, {
        name    = local.init_job_name
        command = ["./initialize.sh"]
        args    = []
      })
      cleanup-expired-token-slots = merge(local.deployment_job, {
        name    = "${local.name_prefix}-cleanup-token-slots"
        command = ["python"]
        args    = ["manage.py", "cleanup_expired_token_slots"]
      })
      cleanup-incomplete-file-uploads = merge(local.deployment_job, {
        name    = "${local.name_prefix}-cleanup-uploads"
        command = ["python"]
        args    = ["manage.py", "cleanup_incomplete_file_uploads"]
      })
    },
    local.fixture_jobs,
  )
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
      service_account = each.value.service_account
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
        image   = each.value.image
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
          value = each.value.process_role
        }

        dynamic "env" {
          for_each = each.value.env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = each.value.secret_env
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

  # `load_fixtures` creates users whose passwords are published in the fixture
  # documentation and writes synthetic patients through the real viewsets. The
  # application refuses to run it without DEBUG; this refuses to build the Job
  # that would carry DEBUG anywhere but dev, so neither guard stands alone.
  lifecycle {
    # Same ownership boundary as the services: application delivery moves the
    # image, OpenTofu owns everything else about the Job (ES-08 sections 144,
    # 145). The init Job especially — a deployment must be able to point it at
    # the digest it is about to roll out without an infrastructure apply, and
    # the next plan must not propose putting it back.
    # `client` and `client_version` for the same reason as the services: they
    # record which tool last wrote the Job, and a deployment writes it with
    # gcloud (ES-08 section 139).
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]

    precondition {
      condition     = !var.enable_fixture_loader || var.environment == "dev"
      error_message = "enable_fixture_loader is true for environment '${var.environment}'. The fixture Job seeds known-credential accounts and synthetic clinical data, and runs with DJANGO_DEBUG=true. It is a development tool and cannot be created outside dev."
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.accessors,
    google_project_iam_member.cloudsql_client,
    google_sql_database.care,
  ]
}
