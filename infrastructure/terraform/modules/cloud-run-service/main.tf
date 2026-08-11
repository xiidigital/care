terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

locals {
  # allUsers and allAuthenticatedUsers are the two members ADR-0007 names as
  # forbidden on the worker. Checked for every service rather than only the
  # worker: public access has one expression here, and it is
  # allow_unauthenticated.
  public_members = ["allUsers", "allAuthenticatedUsers"]

  explicit_public_members = [
    for member in var.invoker_members : member
    if contains(local.public_members, member)
  ]

  invoker_members = var.allow_unauthenticated ? distinct(concat(var.invoker_members, ["allUsers"])) : var.invoker_members

  mount_cloudsql = var.cloudsql_instance_connection_name != ""
}

resource "google_cloud_run_v2_service" "this" {
  project  = var.project_id
  name     = var.name
  location = var.region
  ingress  = var.ingress
  labels   = var.labels

  deletion_protection = var.deletion_protection

  template {
    service_account                  = var.service_account_email
    timeout                          = "${var.request_timeout_seconds}s"
    max_instance_request_concurrency = var.concurrency

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    # Native Cloud Run to Cloud SQL integration: the instance appears as a unix
    # socket under /cloudsql. No Serverless VPC Access connector is involved,
    # which is why none is declared anywhere in this configuration
    # (ADR-0007 "Networking", ES-07 section 31).
    dynamic "volumes" {
      for_each = local.mount_cloudsql ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloudsql_instance_connection_name]
        }
      }
    }

    containers {
      image = var.image

      # The committed role script, not a command assembled here. Terraform
      # invokes the application's entrypoint; it does not reimplement it
      # (ADR-0007 "Initialization").
      command = var.command

      ports {
        container_port = var.port
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        # CPU is released between requests. Always-allocated CPU is a standing
        # cost and ES-07 section 124 requires a reason for it.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "CARE_PROCESS_ROLE"
        value = var.process_role
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env
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

      dynamic "volume_mounts" {
        for_each = local.mount_cloudsql ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      # Startup and liveness both use the cheap per-role diagnostic. The
      # dependency check at /health/ is not probed: a liveness probe that opens
      # a database connection turns a slow query into a container restart.
      startup_probe {
        initial_delay_seconds = 10
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = var.startup_probe_failure_threshold

        http_get {
          path = var.probe_path
          port = var.port
        }
      }

      liveness_probe {
        initial_delay_seconds = 0
        period_seconds        = 60
        timeout_seconds       = 5
        failure_threshold     = 3

        http_get {
          path = var.probe_path
          port = var.port
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = !(var.process_role == "task_worker" && var.allow_unauthenticated)
      error_message = "The task worker must not permit unauthenticated invocation. ADR-0007 treats a deployment that exposes the worker without its IAM boundary as invalid, and the worker's task endpoint carries no application-layer authentication by design (ES-06 section 14)."
    }

    precondition {
      condition     = length(local.explicit_public_members) == 0
      error_message = "invoker_members must not contain allUsers or allAuthenticatedUsers; set allow_unauthenticated instead, which the task worker cannot set."
    }
  }
}

# Invoker bindings are authoritative per member rather than per policy: an
# _iam_binding would silently drop any binding added outside this module, and a
# lost run.invoker on the worker is a broken queue rather than a visible error.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(local.invoker_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
