"""
Celery task discovery and Beat registration.

`autodiscover_tasks` imports each app's `tasks` module and goes no deeper, so
for a package like this one only `__init__` runs. Every Celery wrapper in a
submodule must therefore be imported here or the worker will not register it,
and dispatching it fails with `NotRegistered` at runtime rather than at import.

That is not hypothetical: it happened while implementing ADR-0003. The report
and TOTP wrappers used to be registered by accident, because the viewsets that
dispatched them imported the task functions directly and the worker happened to
load those viewsets. Once the call sites moved to `enqueue_task`, nothing
imported them and they silently vanished from the worker's registry. The
imports below are what makes registration deterministic instead of incidental;
`test_task_runtime.TaskNameStabilityTests` checks it in a fresh process.

Beat registration is retained for local Docker Compose and traditional
deployments. The schedule still lives in code and still fires the same two
operations, but through thin wrappers: the operations themselves are ordinary
functions that a Cloud Run Job or an operator runs through the equivalent
management commands, with no broker, worker or beat process involved.

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

# Imported for their registration side effect; nothing here calls them.
from care.emr.tasks.report_generation import generate_report_task
from care.emr.tasks.resource_category import (
    summarise_monetary_components_task,
)
from care.emr.tasks.totp import (
    send_totp_disabled_email_task,
    send_totp_enabled_email_task,
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
