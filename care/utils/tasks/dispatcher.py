"""
CARE's asynchronous dispatch boundary.

This is the whole producer-side API. A call site that wants work done later
calls :func:`enqueue_task` with a registered task name and a JSON-serializable
payload; which transport carries it is a configuration question answered by
``CARE_TASK_BACKEND``.

Being decorated as a Celery task has never meant a function is dispatched
asynchronously in this repository -- most task-decorated functions are called
inline -- so the rule is the reverse of the obvious one: a call site becomes
asynchronous only because it was already asynchronous, never because the target
carries a decorator.
"""

import json
import logging

from django.conf import settings
from django.db import transaction

from care.utils.tasks.backends.base import TaskBackend
from care.utils.tasks.exceptions import InvalidTaskPayloadError
from care.utils.tasks.registry import get_task, validate_payload

logger = logging.getLogger(__name__)

CELERY_BACKEND = "celery"
CLOUD_TASKS_BACKEND = "cloud_tasks"


def _build_backend(name: str) -> TaskBackend:
    if name == CELERY_BACKEND:
        from care.utils.tasks.backends.celery import CeleryTaskBackend

        return CeleryTaskBackend()
    if name == CLOUD_TASKS_BACKEND:
        from care.utils.tasks.backends.cloud_tasks import CloudTasksBackend

        return CloudTasksBackend()
    # config.tasks.validate_task_backend rejects unsupported values at startup,
    # so reaching this means the setting was overridden after settings loaded.
    msg = f"Unsupported CARE_TASK_BACKEND: {name!r}"
    raise ValueError(msg)


def get_backend() -> TaskBackend:
    """The dispatch backend selected by ``CARE_TASK_BACKEND``."""
    return _build_backend(settings.CARE_TASK_BACKEND)


def enqueue_task(
    task_name: str,
    payload: dict | None = None,
    *,
    delay_seconds: int | None = None,
    task_id: str | None = None,
) -> str:
    """
    Enqueue ``task_name`` for asynchronous execution and return its external id.

    ``payload`` is validated against the task's registered schema before it
    leaves the process, so an unserializable or malformed payload fails at the
    call site rather than in a worker minutes later.

    Callers that depend on database state written in the current transaction
    must use :func:`enqueue_task_on_commit` instead -- a task dispatched inside
    an open transaction can be executed before that transaction commits, or
    after it rolls back.
    """
    definition = get_task(task_name)
    validated = validate_payload(definition, payload)

    encoded = json.dumps(validated)
    max_bytes = settings.CARE_TASK_MAX_PAYLOAD_BYTES
    if len(encoded.encode()) > max_bytes:
        msg = (
            f"Payload for task {task_name!r} exceeds "
            f"CARE_TASK_MAX_PAYLOAD_BYTES ({max_bytes})"
        )
        raise InvalidTaskPayloadError(msg)

    backend = get_backend()
    external_id = backend.enqueue(
        definition,
        validated,
        delay_seconds=delay_seconds,
        task_id=task_id,
    )
    # Name and transport only. The payload may reference clinical records, and
    # logging it is prohibited in production.
    logger.info(
        "Enqueued task %s via %s as %s", definition.name, backend.name, external_id
    )
    return external_id


def enqueue_task_on_commit(
    task_name: str,
    payload: dict | None = None,
    *,
    delay_seconds: int | None = None,
) -> None:
    """
    Enqueue ``task_name`` once the current transaction commits successfully.

    ``ATOMIC_REQUESTS`` is enabled, so every request runs inside a transaction
    and an immediate dispatch would let a worker read rows the request has not
    committed -- or rows it is about to roll back. Under Cloud Tasks that race
    is real: the worker is a separate service reaching the database directly.

    Validation still happens now rather than at commit time, so a bad payload
    surfaces as an error in the request that caused it. Nothing is returned:
    the external id does not exist yet.
    """
    definition = get_task(task_name)
    validated = validate_payload(definition, payload)
    transaction.on_commit(
        lambda: enqueue_task(definition.name, validated, delay_seconds=delay_seconds)
    )
