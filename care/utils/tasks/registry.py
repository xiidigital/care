"""
The explicit CARE task registry.

ADR-0003 forbids executing arbitrary Python paths supplied in a task payload.
A Cloud Tasks request therefore carries a *task name* -- a short, stable,
server-defined string -- and nothing else identifying code. This module maps
those names onto handlers.

There is no ``eval``, no ``import_string`` of caller-supplied text and no
dynamic callable resolution. The only import performed here is of the modules
listed in :data:`HANDLER_MODULES`, a constant of this module; payload content
never influences what is imported.

Registration is deterministic: :func:`load_registry` imports every handler
module exactly once, in declaration order, before any lookup succeeds. The
indirection exists so that ``care.utils`` does not import ``care.emr`` models at
module-import time, which would tie the dispatcher to app-loading order.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from pydantic import BaseModel, ValidationError

from care.utils.tasks.exceptions import InvalidTaskPayloadError, UnknownTaskError

#: Modules that call :func:`register_task` at import time. Server-defined and
#: constant -- never derived from configuration or from a task payload.
HANDLER_MODULES: tuple[str, ...] = ("care.emr.tasks.handlers",)


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """One registered task."""

    #: Stable external name. It travels in Cloud Tasks request bodies and in
    #: logs, so it must not change when the handler is renamed or moved.
    name: str

    #: The reusable operation. An ordinary callable taking keyword arguments
    #: matching :attr:`payload_model`; it knows nothing about Celery or Cloud
    #: Tasks and can be called directly.
    handler: Callable[..., Any]

    #: Validates and normalises the payload on both the dispatch and the
    #: execution side, so a malformed payload is rejected before it is queued
    #: *and* again before a handler sees it.
    payload_model: type[BaseModel]

    #: The thin Celery wrapper, used only by the Celery dispatch backend.
    #: ``None`` for tasks that have no Celery representation.
    celery_task: Any | None = None


_REGISTRY: dict[str, TaskDefinition] = {}
_LOAD_LOCK = threading.Lock()
_loaded = False


def register_task(
    name: str,
    *,
    handler: Callable[..., Any],
    payload_model: type[BaseModel],
    celery_task: Any | None = None,
) -> TaskDefinition:
    """Register one task. Re-registering the same name is an error."""
    if name in _REGISTRY:
        msg = f"Task {name!r} is already registered"
        raise ValueError(msg)
    definition = TaskDefinition(
        name=name,
        handler=handler,
        payload_model=payload_model,
        celery_task=celery_task,
    )
    _REGISTRY[name] = definition
    return definition


def load_registry() -> None:
    """Import every handler module once. Idempotent and thread-safe."""
    global _loaded  # noqa: PLW0603
    if _loaded:
        return
    with _LOAD_LOCK:
        if _loaded:
            return
        for module in HANDLER_MODULES:
            import_module(module)
        _loaded = True


def get_task(name: str) -> TaskDefinition:
    """
    Return the definition registered under ``name``.

    Raises :class:`UnknownTaskError` -- a permanent failure -- for any name that
    is not registered, so an unrecognised request is never retried forever.
    """
    load_registry()
    try:
        return _REGISTRY[name]
    except KeyError as e:
        msg = f"Unknown task: {name!r}"
        raise UnknownTaskError(msg) from e


def registered_task_names() -> tuple[str, ...]:
    """Every registered task name, sorted. Useful for diagnostics and tests."""
    load_registry()
    return tuple(sorted(_REGISTRY))


def validate_payload(definition: TaskDefinition, payload: dict | None) -> dict:
    """
    Validate ``payload`` against the task's schema and return it JSON-ready.

    The returned mapping contains only JSON-serializable primitives, so the
    same value can be handed to Celery (``json`` serializer) or placed in a
    Cloud Tasks request body unchanged.
    """
    try:
        model = definition.payload_model.model_validate(payload or {})
    except ValidationError as e:
        # The message names fields, not values: a payload may reference
        # clinical records and must not be echoed into logs or responses.
        msg = (
            f"Invalid payload for task {definition.name!r}: {e.error_count()} error(s)"
        )
        raise InvalidTaskPayloadError(msg) from e
    return model.model_dump(mode="json")
