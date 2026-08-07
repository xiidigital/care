"""
CARE's asynchronous execution runtime (ADR-0003).

Reusable operations are ordinary functions living with the domain they serve.
This package holds only the transport: a narrow dispatch contract, an explicit
registry of executable task names, the Celery and Cloud Tasks backends, and the
private HTTP worker that Cloud Tasks calls back into.

Producers should import from here rather than from the submodules::

    from care.utils.tasks import enqueue_task_on_commit

    enqueue_task_on_commit("send_totp_enabled_email", {"user_id": user.id})
"""

from care.utils.tasks.dispatcher import enqueue_task, enqueue_task_on_commit
from care.utils.tasks.exceptions import (
    InvalidTaskPayloadError,
    PermanentTaskError,
    RetryableTaskError,
    TaskDispatchError,
    TaskError,
    UnknownTaskError,
)

__all__ = [
    "InvalidTaskPayloadError",
    "PermanentTaskError",
    "RetryableTaskError",
    "TaskDispatchError",
    "TaskError",
    "UnknownTaskError",
    "enqueue_task",
    "enqueue_task_on_commit",
]
