"""
The registered CARE tasks: name, payload schema, handler and Celery wrapper.

This module is the complete list of what a task request may ask CARE to
execute. A Cloud Tasks body names one of these strings and nothing else; there
is no import path, no dotted callable and no way to reach code that is not
listed here.

Payload schemas are part of the contract and are documented in
``docs/xii/architecture/inventory/task-call-sites.md``. Each carries opaque
identifiers rather than records: handlers reload current state from PostgreSQL,
so a task queued before an edit acts on the edited row rather than on a stale
copy, and no clinical data sits in a queue.

Adding a task means adding an entry here. That is deliberate friction --
``autodiscover_tasks`` registers any ``tasks.py`` in any installed app with
Celery, and this registry does not, so a plugin task remains Celery-only until
it is registered explicitly. See ``inventory/plugin-impact.md``.
"""

from pydantic import UUID4, BaseModel

from care.emr.models.resource_category import summarise_monetary_components
from care.emr.tasks.cleanup_expired_token_slots import (
    cleanup_expired_token_slots,
    cleanup_expired_token_slots_task,
)
from care.emr.tasks.cleanup_incomplete_file_uploads import (
    cleanup_incomplete_file_uploads,
    cleanup_incomplete_file_uploads_task,
)
from care.emr.tasks.report_generation import generate_report, generate_report_task
from care.emr.tasks.resource_category import summarise_monetary_components_task
from care.emr.tasks.totp import (
    send_totp_disabled_email,
    send_totp_disabled_email_task,
    send_totp_enabled_email,
    send_totp_enabled_email_task,
)
from care.utils.tasks.registry import register_task


class EmptyPayload(BaseModel):
    """No arguments. Used by the two maintenance operations."""


class UserNotificationPayload(BaseModel):
    """The recipient's database id. The address is read from the user row."""

    user_id: int


class GenerateReportPayload(BaseModel):
    """
    Identifiers for one report render.

    ``template_id`` and ``associating_id`` are external UUIDs, serialized as
    strings; ``user_id`` names the requesting user so the rendered document can
    show them, and is optional because a report may be generated without one.
    """

    template_id: UUID4
    report_type: str
    associating_id: UUID4
    output_format: str = "pdf"
    user_id: int | None = None


class SummariseMonetaryComponentsPayload(BaseModel):
    """The child category to recompute, by primary key."""

    category_id: int


def _summarise_monetary_components(category_id: int) -> None:
    # The reusable operation accepts an instance or a pk; the task contract
    # accepts only a pk, so the adapter keeps the payload unambiguous.
    summarise_monetary_components(category_id)


register_task(
    "generate_report",
    handler=generate_report,
    payload_model=GenerateReportPayload,
    celery_task=generate_report_task,
)

register_task(
    "send_totp_enabled_email",
    handler=send_totp_enabled_email,
    payload_model=UserNotificationPayload,
    celery_task=send_totp_enabled_email_task,
)

register_task(
    "send_totp_disabled_email",
    handler=send_totp_disabled_email,
    payload_model=UserNotificationPayload,
    celery_task=send_totp_disabled_email_task,
)

register_task(
    "summarise_monetary_components",
    handler=_summarise_monetary_components,
    payload_model=SummariseMonetaryComponentsPayload,
    celery_task=summarise_monetary_components_task,
)

# Registered so that a deployment without Celery Beat can trigger maintenance
# through the task backend if it chooses to. The supported path remains the
# management command run as a Cloud Run Job: both operations are unbounded scans
# and can outlast a request deadline.
register_task(
    "cleanup_expired_token_slots",
    handler=cleanup_expired_token_slots,
    payload_model=EmptyPayload,
    celery_task=cleanup_expired_token_slots_task,
)

register_task(
    "cleanup_incomplete_file_uploads",
    handler=cleanup_incomplete_file_uploads,
    payload_model=EmptyPayload,
    celery_task=cleanup_incomplete_file_uploads_task,
)
