import logging
import time
from uuid import uuid4

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone

from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.report.template import Template
from care.emr.reports.context_builder import SingleUserIdContextBuilder
from care.emr.reports.context_builder.data_point_registry import DataPointRegistry
from care.emr.reports.renderer.generators import GeneratorRegistry
from care.emr.reports.renderer.renderer import Renderer
from care.emr.reports.renderer.template_engine import TemplateEngine
from care.emr.reports.report_type_registry import ReportTypeRegistry
from care.emr.reports.report_type_utils import validate_associating_id
from care.users.models import User
from care.utils.tasks.exceptions import RetryableTaskError

logger = logging.getLogger(__name__)

#: How long a progress value survives without an update. Generous enough to
#: outlast a slow render, short enough that an abandoned run stops blocking the
#: next attempt. Unrelated to ``settings.LOCK_TIMEOUT``.
PROGRESS_TIMEOUT = 2 * 60


def get_progress_key(report_type: str, associating_id: str) -> str:
    return f"{report_type}_{associating_id}"


def _progress_cache_key(key: str) -> str:
    return f"report_generation_progress:{key}"


def set_progress(key: str, progress: int, timeout: int = PROGRESS_TIMEOUT) -> None:
    """
    Publish generation progress for ``key``.

    ADR-0004 required this to be classified. It is **shared cache**, not a lock
    and not durable state (ES-04 section 16): the value is a percentage written
    by the worker and read by the API purely to render "already in progress".
    Losing it costs a duplicate render, never data -- the report itself is a
    ``ReportUpload`` row plus an object in storage, both written independently
    of this value.

    It must be *shared*, because the writer and the reader are different
    processes and, under Cloud Run, different containers. That rules out LocMem
    but is satisfied by the PostgreSQL and Redis cache profiles alike.

    Until ES-04 these functions were named ``set_lock``/``clear_lock``. They
    never passed ``nx``, so they never excluded anything: two concurrent
    requests can still both pass the 409 check. The names promised mutual
    exclusion the code did not implement, which is exactly what ADR-0004
    forbids progress values from doing.
    """
    cache.set(_progress_cache_key(key), progress, timeout)


def get_progress(key: str) -> int | None:
    """
    Return the published progress, or ``None`` if there is none.

    Cache failure reads as "no generation in progress" and costs at worst a
    duplicate render, so this fails open by design (ES-04 section 20).
    """
    return cache.get(_progress_cache_key(key))


def clear_progress(key: str) -> None:
    cache.delete(_progress_cache_key(key))


def generate_and_upload_report(  # noqa:PLR0915
    template: Template,
    report_type: str,
    associating_id: str,
    output_format: str = "pdf",
    user_id: int | None = None,
) -> ReportUpload:
    context_class = DataPointRegistry.get(template.context)
    if not context_class:
        error_msg = f"Context '{template.context}' not found in DataPointRegistry"
        raise ValueError(error_msg)

    try:
        report_type_config = ReportTypeRegistry.get(report_type)
    except KeyError as e:
        error_msg = f"Report Type '{report_type}' not found in ReportTypeRegistry"
        raise ValueError(error_msg) from e

    associating_object = validate_associating_id(
        associating_model=report_type_config.associating_model,
        associating_id=associating_id,
        report_type_key=report_type,
    )

    context_key = context_class.context_key or template.context
    context = {context_key: context_class(context=associating_object)}

    user = None
    if user_id:
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.warning(
                "User with id %s not found, report will have no current_user", user_id
            )

    if user:
        context["current_user"] = SingleUserIdContextBuilder(context=user)
    else:
        context["current_user"] = SingleUserIdContextBuilder(is_preview=True)

    template_engine = TemplateEngine()
    format_lower = output_format.lower()

    generator_class = GeneratorRegistry.get(format_lower)
    generator = generator_class()
    format_config = GeneratorRegistry.get_format_config(format_lower)
    file_extension = format_config["file_extension"]
    mime_type = format_config["mime_type"]

    renderer = Renderer(generator, template_engine)

    validated_options = generator.options_model.model_validate(template.options)
    output_bytes = renderer.render(template.template_data, context, validated_options)

    current_date = timezone.now()
    timestamp = int(current_date.timestamp() * 1000)

    report_name = f"{template.name}-{associating_id}-{timestamp}"
    internal_name = f"{uuid4()}{int(time.time())}{file_extension}"

    report_upload = ReportUpload(
        template=template,
        name=report_name,
        internal_name=internal_name,
        associating_id=associating_id,
        report_type=report_type,
        upload_completed=False,
    )

    if user:
        report_upload.created_by = user

    report_upload.meta["mime_type"] = mime_type
    report_upload.meta["generated_at"] = current_date.isoformat()
    report_upload.meta["template_id"] = str(template.external_id)
    report_upload.meta["output_format"] = output_format

    report_upload.save(skip_internal_name=True)

    try:
        # output_bytes is already fully materialised by the renderer above; see
        # docs/xii/architecture/inventory/storage-call-sites.md section 5.
        report_upload.files_manager.put_object(
            report_upload, ContentFile(output_bytes), content_type=mime_type
        )
        report_upload.upload_completed = True
        report_upload.save()
    except Exception as e:
        report_upload.delete()
        # This is the storage-provider boundary, and the only place report
        # generation can see a provider exception. Translating here is what
        # makes retry portable: the caller decides whether to retry from
        # CARE's own classification rather than from `botocore.ClientError`,
        # which does not exist under GCS. Writing an object is I/O against an
        # external service, so the failure is treated as transient; the bounded
        # retry count keeps an over-classified permanent failure cheap.
        msg = f"Storing report object failed for {report_upload.internal_name}"
        raise RetryableTaskError(msg) from e

    return report_upload
