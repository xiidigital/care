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
handler failed permanently    200     recorded and not redelivered: no attempt
                                     can succeed
malformed or unknown request 400     the dispatcher is wrong, not the work;
                                     retried until the queue's attempt limit,
                                     then dead-lettered
retryable failure            503     transient; redelivery is wanted
unclassified exception       500     treated as retryable, being unclassified
===========================  ======  ===============================================

Two of those deserve their reasons written down.

**A permanently failed handler answers 2xx.** Cloud Tasks has exactly one
question -- redeliver or not -- and expresses it as 2xx versus everything else;
it has no status meaning "this failed and will keep failing". Answering 400
therefore did not mark the task permanent, it just spelled the retries
differently: ES-07 watched an email that could not be sent under any
configuration retry ten times over the queue's 24-hour window
(``unresolved-items.md`` N2). The 2xx here does not claim the operation
succeeded. It ends the delivery, having recorded the failure at ERROR with its
full traceback, and it is a distinct status from the 204 that means it worked,
so the two are still told apart in access logs and in the queue's own record.
ADR-0003 requires the transient and permanent cases to be mapped explicitly onto
each backend's retry mechanism; for this transport, that mapping is the status.

**A rejected request still answers 4xx**, even though an identical redelivery
would be rejected identically. That failure is not a task that cannot be done,
it is a caller sending something CARE never registered -- a broken dispatcher,
a stale queue entry, or an unauthorized attempt to reach the endpoint -- and it
should stay visible as a client error rather than be absorbed with a 2xx.

Returning 2xx for a failure that has *not* been characterised is never done: an
unclassified exception is retryable by default.
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
        # 2xx, and not 204: the delivery is finished, the work is not. See the
        # module docstring.
        return JsonResponse({"detail": "Task failed permanently"}, status=200)
    except Exception:
        # Unclassified. Retrying is the safer default for a failure nobody has
        # characterised yet, and the queue's attempt limit bounds it.
        logger.exception("Task %s raised an unexpected error", task_name)
        return _error(500, "Task failed")

    return HttpResponse(status=204)
