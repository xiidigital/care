# Cloud Tasks.
#
# The queue the application's cloud_tasks backend dispatches to. Its name,
# location and project are wired into the runtime environment from these
# resources rather than being written twice (ES-07 section 54).
#
# Authentication is OIDC against a dedicated invoker identity. No service-account
# JSON key exists anywhere in this configuration.

resource "google_cloud_tasks_queue" "default" {
  project  = var.project_id
  name     = local.queue_name
  location = var.region

  rate_limits {
    max_dispatches_per_second = var.tasks_max_dispatches_per_second

    # The real backpressure control. It must stay under what the worker can
    # absorb — worker_max_instances x worker_concurrency — or the queue will
    # push more concurrent work than there are containers to run it, and the
    # overflow becomes retries rather than throughput.
    max_concurrent_dispatches = var.tasks_max_concurrent_dispatches
  }

  retry_config {
    # Finite. CARE's maintenance mode answers with a retryable status, so a task
    # enqueued during a maintenance window will legitimately fail for as long as
    # the window lasts; the queue has to outlast that without retrying forever
    # (ADR-0007 "Cloud Tasks").
    max_attempts       = var.tasks_max_attempts
    max_retry_duration = "${var.tasks_max_retry_duration_seconds}s"
    min_backoff        = "${var.tasks_min_backoff_seconds}s"
    max_backoff        = "${var.tasks_max_backoff_seconds}s"
    max_doublings      = var.tasks_max_doublings
  }

  lifecycle {
    precondition {
      condition     = var.tasks_max_concurrent_dispatches <= var.worker_max_instances * var.worker_concurrency
      error_message = "tasks_max_concurrent_dispatches exceeds what the worker can serve (worker_max_instances x worker_concurrency). The excess does not become throughput; it becomes dispatch failures and retries against a queue that is already saturated."
    }

    precondition {
      condition     = var.tasks_max_attempts > 0
      error_message = "tasks_max_attempts must be finite. Cloud Tasks treats -1 as unlimited, and an unlimited retry against a permanently failing task is a queue that never drains."
    }
  }

  depends_on = [google_project_service.required]
}
