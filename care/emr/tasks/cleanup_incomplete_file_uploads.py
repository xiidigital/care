"""
Incomplete file-upload cleanup.

As with expired token slots, the operation is an ordinary function usable from
a management command, from Celery Beat, or directly. The pagination and
error-handling behaviour is deliberately unchanged from before ADR-0003,
including the abort-on-storage-error semantics recorded as B4 in the
unresolved-items inventory: correcting it is a separate decision from making the
operation runnable without a worker.
"""

from datetime import timedelta
from logging import Logger

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.utils import timezone

from care.emr.models import FileUpload

logger: Logger = get_task_logger(__name__)


def cleanup_incomplete_file_uploads() -> int:
    """
    Hard-delete ``FileUpload`` rows whose upload never completed, returning the
    number of rows removed.

    Idempotent: the storage delete tolerates a missing object and the database
    delete is keyed off rows that no longer exist after a successful run.
    """
    threshold = timezone.now() - timedelta(hours=settings.FILE_UPLOAD_EXPIRY_HOURS)
    logger.info("Cleaning up incomplete file uploads")
    page_size = 1000
    total_deleted = 0
    queryset = FileUpload.objects.filter(
        upload_completed=False,
        created_date__lte=threshold,
    )[:page_size]

    file_manager = FileUpload.files_manager
    while queryset.exists():
        ids_to_delete = []
        for file in queryset:
            if file.internal_name:
                try:
                    file_manager.delete_object(file)
                except Exception as e:
                    logger.error(
                        "Failed to delete file upload object %s: %s",
                        file.id,
                        e,
                    )
                    raise e
                ids_to_delete.append(file.id)

        if ids_to_delete:
            deleted_count, _ = FileUpload.objects.filter(id__in=ids_to_delete).delete()
        else:
            deleted_count = 0

        total_deleted += deleted_count
        logger.info("Deleted %d incomplete file uploads", deleted_count)

        # re-fetch the queryset
        queryset = FileUpload.objects.filter(
            upload_completed=False,
            created_date__lte=threshold,
        )[:page_size]

    logger.info("Completed cleanup of incomplete file uploads")
    return total_deleted


@shared_task(
    name="care.emr.tasks.cleanup_incomplete_file_uploads.cleanup_incomplete_file_uploads"
)
def cleanup_incomplete_file_uploads_task() -> int:
    """Celery wrapper. Keeps the pre-ADR-0003 task name."""
    return cleanup_incomplete_file_uploads()
