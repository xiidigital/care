# Cloud Scheduler replaces Celery Beat.
#
# Beat is a container that runs permanently in order to be awake at two moments
# a day. The managed profile does not run one (ADR-0007 "Cloud Scheduler"); these
# two jobs fire the same operations and nothing runs in between.
#
# The mapping, recorded because ES-07 section 68 requires the old and new forms
# to be visible together:
#
#   Beat: crontab(hour="0", minute="0")           name=cleanup_expired_token_slots
#   Here: cron "0 0 * * *", Asia/Kolkata          job  <prefix>-cleanup-token-slots
#
#   Beat: every FILE_UPLOAD_EXPIRY_HOURS*3600 s   name=cleanup_incomplete_file_uploads
#   Here: cron "30 1 * * *", Asia/Kolkata         job  <prefix>-cleanup-uploads
#
# The second is a change of shape, not a translation: Beat fired it on an
# interval measured from whenever beat last started, which has no wall-clock
# meaning. Cloud Scheduler expresses cron only. A fixed daily time is the closest
# equivalent, and the operation is idempotent and time-of-day independent.
#
# The timezone is Asia/Kolkata because that is settings.TIME_ZONE and therefore
# CELERY_TIMEZONE — the timezone these schedules already ran in. Defaulting to
# UTC would have moved both by five and a half hours without saying so.

locals {
  scheduled_jobs = {
    cleanup-expired-token-slots = {
      job_key     = "cleanup-expired-token-slots"
      schedule    = var.schedule_cleanup_expired_token_slots
      description = "Hard-delete expired token slots with no booking. Replaces the Celery Beat crontab(hour=0, minute=0) entry."
    }
    cleanup-incomplete-file-uploads = {
      job_key     = "cleanup-incomplete-file-uploads"
      schedule    = var.schedule_cleanup_incomplete_file_uploads
      description = "Hard-delete file uploads that were never completed. Replaces the Celery Beat FILE_UPLOAD_EXPIRY_HOURS interval entry."
    }
  }
}

# Executing a Cloud Run Job needs run.jobs.run, which roles/run.invoker carries.
# Granted on the two cleanup jobs individually — the scheduler identity has no
# permission over the init Job, which is a deployment operation and not a
# scheduled one.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  for_each = local.scheduled_jobs

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.jobs[each.value.job_key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "jobs" {
  for_each = local.scheduled_jobs

  project     = var.project_id
  region      = var.region
  name        = "${local.name_prefix}-${each.key}"
  description = each.value.description
  schedule    = each.value.schedule
  time_zone   = var.scheduler_timezone

  # Both operations delete rows. An environment holding hand-made test data can
  # turn them off without removing the resource, so the schedule stays visible
  # and reviewable (ES-07 section 69).
  paused = !var.scheduler_jobs_enabled

  attempt_deadline = "320s"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "30s"
    max_backoff_duration = "600s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.jobs[each.value.job_key].name}:run"

    # OAuth, not OIDC: the target is a Google API rather than a Cloud Run
    # service. A managed identity either way — no static token in a query
    # string, which ES-07 section 67 forbids.
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_project_service.required,
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
  ]
}
