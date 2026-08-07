"""
Expired token-slot cleanup.

Periodic scheduling is a separate concern from asynchronous dispatch (ADR-0003).
The operation below is an ordinary function with no scheduler dependency; it is
reachable three ways, all running the same code:

- ``python manage.py cleanup_expired_token_slots`` -- a Cloud Run Job, a cron
  entry, or an operator at a shell;
- the Celery Beat schedule in ``care.emr.tasks``, retained for local Compose;
- a direct call.
"""

from logging import Logger

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from care.emr.models import TokenSlot

logger: Logger = get_task_logger(__name__)


def cleanup_expired_token_slots() -> int:
    """
    Hard-delete expired ``TokenSlot`` rows that have no booking, returning the
    number of rows removed.

    Idempotent: a second run over the same state deletes nothing further, so
    at-least-once delivery is harmless.
    """
    logger.info("Cleaning up expired TokenSlot objects")
    queryset = TokenSlot.objects.filter(
        tokenbooking__isnull=True, end_datetime__lte=timezone.now()
    )
    deleted_count, _ = queryset.delete()
    logger.info("Deleted %d expired token slots", deleted_count)
    return deleted_count


@shared_task(
    name="care.emr.tasks.cleanup_expired_token_slots.cleanup_expired_token_slots"
)
def cleanup_expired_token_slots_task() -> int:
    """Celery wrapper. Keeps the pre-ADR-0003 task name."""
    return cleanup_expired_token_slots()
