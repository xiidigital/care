"""
Celery Beat registration for CARE's periodic operations.

Retained for local Docker Compose and traditional deployments. The schedule
still lives in code and still fires the same two operations, but it now fires
them through thin wrappers: the operations themselves are ordinary functions
that a Cloud Run Job or an operator can run through the equivalent management
commands without a broker, a worker or a beat process.

The GCP profile does not run beat. ADR-0003 requires that the same operation is
never scheduled by Beat and by Cloud Scheduler at once, so a deployment picks
one.
"""

from celery import Celery, current_app
from celery.schedules import crontab
from django.conf import settings

from care.emr.tasks.cleanup_expired_token_slots import cleanup_expired_token_slots_task
from care.emr.tasks.cleanup_incomplete_file_uploads import (
    cleanup_incomplete_file_uploads_task,
)

# `autodiscover_tasks` imports this package and no deeper, so a wrapper in a
# submodule this package does not import would never reach the worker. Imported
# for its registration side effect; nothing here calls it.
from care.emr.tasks.resource_category import (  # noqa: F401
    summarise_monetary_components_task,
)


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        crontab(hour="0", minute="0"),
        cleanup_expired_token_slots_task.s(),
        name="cleanup_expired_token_slots",
    )

    if cleanup_file_upload_hours := settings.FILE_UPLOAD_EXPIRY_HOURS:
        sender.add_periodic_task(
            cleanup_file_upload_hours * 3600,  # convert hours to seconds
            cleanup_incomplete_file_uploads_task.s(),
            name="cleanup_incomplete_file_uploads",
        )
