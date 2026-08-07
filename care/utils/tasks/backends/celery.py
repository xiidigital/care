"""
The Celery dispatch backend.

This is the default and preserves CARE's traditional runtime: local Docker
Compose, upstream-compatible development and conventional server deployments
keep working with an unchanged broker, worker and set of task names.

It dispatches through the thin ``*_task`` wrappers registered alongside each
reusable operation, so the wire-level task names Celery sees are the ones it
saw before ADR-0003.
"""

from care.utils.tasks.backends.base import TaskBackend
from care.utils.tasks.exceptions import TaskDispatchError
from care.utils.tasks.registry import TaskDefinition


class CeleryTaskBackend(TaskBackend):
    name = "celery"

    def enqueue(
        self,
        definition: TaskDefinition,
        payload: dict,
        *,
        delay_seconds: int | None = None,
        task_id: str | None = None,
    ) -> str:
        if definition.celery_task is None:
            msg = f"Task {definition.name!r} has no Celery wrapper"
            raise TaskDispatchError(msg)

        options: dict = {"kwargs": payload}
        if delay_seconds:
            # Celery expresses delay as a countdown in seconds, which is what
            # the previous `.delay()` call sites never used -- the parameter
            # exists because Cloud Tasks needs it, not because Celery does.
            options["countdown"] = delay_seconds
        if task_id is not None:
            options["task_id"] = task_id

        result = definition.celery_task.apply_async(**options)
        return str(result.id)
