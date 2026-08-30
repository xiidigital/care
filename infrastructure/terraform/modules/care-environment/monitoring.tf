# Monitoring.
#
# Cloud Logging needs no resource: the containers write to stdout and stderr and
# Cloud Run collects it. No agent is installed and the application's logging
# configuration is untouched (ADR-0007 "Logging and observability").
#
# Five alert policies, each for a condition an operator would act on. There is
# no policy for latency, none for CPU, none for memory and none for request
# volume — ADR-0007 forbids speculative monitoring created to increase coverage,
# and thresholds guessed before the first real workload would only be retuned or
# ignored.
#
# alerts_enabled is false in dev. Scale-to-zero makes several of these fire on
# normal behaviour when there is no traffic to measure against.

locals {
  alert_policies_enabled = var.alerts_enabled ? 1 : 0
}

resource "google_monitoring_alert_policy" "cloud_run_server_errors" {
  count = local.alert_policies_enabled

  project      = var.project_id
  display_name = "CARE ${var.environment} — Cloud Run 5xx responses"
  combiner     = "OR"

  documentation {
    content   = "The API or the task worker is returning server errors. Check the service logs in Cloud Logging, filtered to the affected revision."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "5xx rate over 5 minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_revision\"",
        "metric.type = \"run.googleapis.com/request_count\"",
        "metric.labels.response_code_class = \"5xx\"",
        "resource.labels.service_name = one_of(\"${local.api_service_name}\",\"${local.worker_service_name}\")",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = var.alert_notification_channels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "job_failures" {
  count = local.alert_policies_enabled

  project      = var.project_id
  display_name = "CARE ${var.environment} — Cloud Run Job failed"
  combiner     = "OR"

  documentation {
    content   = "A Job execution failed. If it is the init Job, the deployment is not initialized and API revisions depending on the migration must not be promoted."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "failed job completions"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_job\"",
        "metric.type = \"run.googleapis.com/job/completed_task_attempt_count\"",
        "metric.labels.result = \"failed\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = var.alert_notification_channels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "task_retries" {
  count = local.alert_policies_enabled

  project      = var.project_id
  display_name = "CARE ${var.environment} — Cloud Tasks retrying"
  combiner     = "OR"

  documentation {
    content   = "Tasks are being retried. Sustained retries mean the worker is rejecting or failing work; a burst during a maintenance window is expected, because maintenance mode answers retryably."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "non-2xx dispatch responses"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_tasks_queue\"",
        "metric.type = \"cloudtasks.googleapis.com/queue/task_attempt_count\"",
        "metric.labels.response_code != \"200\"",
        "resource.labels.queue_id = \"${local.queue_name}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = var.alert_notification_channels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "cloudsql_down" {
  count = local.alert_policies_enabled

  project      = var.project_id
  display_name = "CARE ${var.environment} — Cloud SQL unavailable"
  combiner     = "OR"

  documentation {
    content   = "The database instance is not serving. Everything else in the environment depends on it, including the cache and the rate-limit counters."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "instance reports down"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloudsql_database\"",
        "metric.type = \"cloudsql.googleapis.com/database/up\"",
        "resource.labels.database_id = \"${var.project_id}:${local.sql_instance_name}\"",
      ])
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = var.alert_notification_channels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "scheduler_failures" {
  count = local.alert_policies_enabled

  project      = var.project_id
  display_name = "CARE ${var.environment} — Cloud Scheduler job failed"
  combiner     = "OR"

  documentation {
    content   = "A scheduled cleanup did not start its Job. The cleanups are idempotent, so a single miss is recoverable by running the Job manually."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "failed scheduler attempts"
    condition_matched_log {
      filter = join("\n", [
        "resource.type = \"cloud_scheduler_job\"",
        "log_id(\"cloudscheduler.googleapis.com/executions\")",
        "jsonPayload.@type = \"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\"",
        "severity >= ERROR",
        "resource.labels.job_id =~ \"^${local.name_prefix}-cleanup-\"",
      ])
    }
  }

  alert_strategy {
    auto_close = "604800s"

    notification_rate_limit {
      period = "300s"
    }
  }

  notification_channels = var.alert_notification_channels

  depends_on = [google_project_service.required]
}
