"""
Report generation as a reusable operation plus a thin Celery wrapper.

The operation below is an ordinary function. It can be called directly, invoked
by the Celery wrapper, or invoked by the Cloud Tasks worker through the task
registry, and it behaves identically in all three cases.

Retry classification is provider-neutral. Before ADR-0003 this task declared
``autoretry_for=(botocore.exceptions.ClientError,)``, which retried transient
object-storage failures under S3 and silently retried nothing under Google
Cloud Storage, where the same failures arrive as ``google.api_core`` exceptions
(recorded as S2 in the unresolved-items inventory). The translation now happens
where the storage write happens -- see ``report_utils.generate_and_upload_report``
-- and this module names no provider at all.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

from care.emr.models.report.template import Template
from care.emr.reports import report_utils
from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError

logger = get_task_logger(__name__)


def generate_report(
    template_id: str,
    report_type: str,
    associating_id: str,
    output_format: str = "pdf",
    user_id: int | None = None,
) -> str:
    """
    Render a report from a template and store it. Returns the ``ReportUpload``
    external id.

    Raises :class:`RetryableTaskError` when the failure was transient -- in
    practice an object-storage write -- and :class:`PermanentTaskError` when the
    request itself cannot succeed, such as a template that does not exist.
    """
    progress_key = report_utils.get_progress_key(report_type, associating_id)

    logger.info(
        "Starting report generation - report_type: %s, "
        "associating_id: %s, template_id: %s, output_format: %s",
        report_type,
        associating_id,
        template_id,
        output_format,
    )

    try:
        logger.debug("Publishing initial progress for %s at 10%%", progress_key)
        report_utils.set_progress(progress_key, 10)

        try:
            logger.debug("Fetching template with external_id: %s", template_id)
            template = Template.objects.get(external_id=template_id)
        except Template.DoesNotExist as e:
            logger.error("Template not found: %s", template_id)
            msg = f"Template {template_id} does not exist"
            raise PermanentTaskError(msg) from e

        logger.debug("Updating progress for %s to 30%%", progress_key)
        report_utils.set_progress(progress_key, 30)

        report_upload = report_utils.generate_and_upload_report(
            template=template,
            output_format=output_format,
            report_type=report_type,
            associating_id=associating_id,
            user_id=user_id,
        )

        if not report_upload:
            logger.error(
                "Report generation failed - generate_and_upload_report returned None"
            )
            raise PermanentTaskError("Unable to generate report")

        logger.info(
            "Report generation completed - external_id: %s",
            report_upload.external_id,
        )
        return str(report_upload.external_id)

    except (PermanentTaskError, RetryableTaskError):
        logger.exception("Report generation failed for %s", progress_key)
        raise
    except Exception:
        logger.exception("Unexpected error in report generation for %s", progress_key)
        raise
    finally:
        # Always cleared, so a failed run does not keep reporting progress
        # and blocking the next attempt until the timeout expires.
        logger.debug("Clearing progress for %s", progress_key)
        report_utils.clear_progress(progress_key)


@shared_task(
    autoretry_for=(RetryableTaskError,),
    retry_kwargs={"max_retries": 3},
    expires=10 * 60,
)
def generate_report_task(
    template_id: str,
    report_type: str,
    associating_id: str,
    output_format: str = "pdf",
    user_id: int | None = None,
) -> str:
    """Celery wrapper. The task name is unchanged from before ADR-0003."""
    return generate_report(
        template_id=template_id,
        report_type=report_type,
        associating_id=associating_id,
        output_format=output_format,
        user_id=user_id,
    )
