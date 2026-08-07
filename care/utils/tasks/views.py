"""
CARE's private task-execution endpoint.

Cloud Tasks delivers work by making an authenticated HTTP request. This view is
the receiving end: it validates an envelope, resolves a *registered* task name,
validates the payload against that task's schema and runs the handler.

It is not a public API operation. Authentication is the platform's job -- the
worker runs as a private Cloud Run service and Cloud Run IAM rejects any caller
without the invoker role, before a request reaches Django. Nothing here
attempts to reimplement that; the route is simply not served by the API role at
all (see ``CARE_TASK_HANDLER_ENDPOINT_ENABLED``), so there is no public surface
to protect.

The status code is the retry signal. Cloud Tasks retries anything that is not
2xx, bounded by the queue's own policy, so the mapping is deliberate:

===========================  ======  ===============================================
Outcome                      Status  Effect
===========================  ======  ===============================================
handler returned             204     done, never redelivered
malformed or unknown request 400     permanent; retried only until the queue's
                                     attempt limit, then dead-lettered
retryable failure            503     transient; redelivery is wanted
unclassified exception       500     treated as retryable, being unclassified
===========================  ======  ===============================================

Returning 2xx for a failed operation would silence retries by lying about the
outcome, and is never done.
"""

import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from care.utils.tasks.envelope import TASK_ENVELOPE_VERSION
from care.utils.tasks.exceptions import (
    InvalidTaskPayloadError,
    PermanentTaskError,
    RetryableTaskError,
)
from care.utils.tasks.registry import TaskDefinition, get_task, validate_payload

logger = logging.getLogger(__name__)

HTTP_RETRYABLE = 503


def _error(status: int, detail: str) -> JsonResponse:
    # Deliberately terse. A task request comes from infrastructure, not from a
    # user, so a response body has no diagnostic audience -- and exception
    # internals in it would leak into Cloud Tasks logs.
    return JsonResponse({"detail": detail}, status=status)


def _resolve(body: bytes) -> tuple[TaskDefinition, dict]:
    """
    Parse and validate a task request, returning what to run and with what.

    Every rejection raises :class:`InvalidTaskPayloadError` or, from
    :func:`get_task`, :class:`UnknownTaskError`. Both are permanent: an
    identical redelivery would be rejected identically, so the caller reports
    them as a client error rather than asking for a retry.
    """
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidTaskPayloadError("Body is not valid JSON") from e

    if not isinstance(envelope, dict):
        raise InvalidTaskPayloadError("Envelope is not an object")

    version = envelope.get("version")
    if version != TASK_ENVELOPE_VERSION:
        msg = f"Unsupported envelope version: {version!r}"
        raise InvalidTaskPayloadError(msg)

    task_name = envelope.get("task")
    if not isinstance(task_name, str) or not task_name:
        raise InvalidTaskPayloadError("Missing task name")

    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise InvalidTaskPayloadError("Payload is not an object")

    definition = get_task(task_name)
    return definition, validate_payload(definition, payload)


@csrf_exempt
@require_POST
def execute_task(request: HttpRequest) -> HttpResponse:
    """Execute one registered task. ``POST`` only; anything else gets 405."""
    try:
        definition, validated = _resolve(request.body)
    except PermanentTaskError as e:
        logger.warning("Task request rejected: %s", e)
        return _error(400, "Unprocessable task request")

    task_name = definition.name
    if settings.CARE_TASK_LOG_PAYLOAD:
        logger.debug("Executing task %s with payload %s", task_name, validated)
    else:
        logger.info("Executing task %s", task_name)

    try:
        definition.handler(**validated)
    except RetryableTaskError:
        logger.warning("Task %s failed transiently; requesting retry", task_name)
        return _error(HTTP_RETRYABLE, "Task failed, retry")
    except PermanentTaskError:
        logger.exception("Task %s failed permanently", task_name)
        return _error(400, "Task failed")
    except Exception:
        # Unclassified. Retrying is the safer default for a failure nobody has
        # characterised yet, and the queue's attempt limit bounds it.
        logger.exception("Task %s raised an unexpected error", task_name)
        return _error(500, "Task failed")

    return HttpResponse(status=204)
