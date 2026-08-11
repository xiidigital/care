"""
What this CARE process is responsible for.

ADR-0006 separates two things that CARE historically conflated: the
*responsibility* a process carries, and the *topology* it happens to run in.
This module owns the first and knows nothing about the second. There is no
value here that means "Cloud Run", "Docker Compose", "Kubernetes" or "GCP", and
introducing one -- ``CARE_RUNTIME_PROFILE``, ``IS_GCP``, ``CLOUD_MODE`` -- is
forbidden by the ADR: a deployment is a composition of a role and independently
selected backends, and the application validates the concrete capabilities
rather than a platform label.

Four roles exist:

``api``
    Serves the public application API.
``task_worker``
    Executes asynchronous CARE work. Says nothing about the transport that
    delivers it -- Cloud Tasks over HTTP and a Celery worker are the same role.
``scheduler``
    Decides when periodic work runs. Holds no business logic.
``init``
    Deployment-time initialization. Ephemeral: it runs, it exits.

The role selects routing, startup, health and configuration validation. It must
not reach into domain logic; a clinical decision that differs between the API
and a worker is a bug, not a role difference.

This module imports no application code and opens no connection, so importing
settings stays free of side effects.
"""

import logging

from django.core.exceptions import ImproperlyConfigured

API_ROLE = "api"
TASK_WORKER_ROLE = "task_worker"
SCHEDULER_ROLE = "scheduler"
INIT_ROLE = "init"

#: Values accepted by ``CARE_PROCESS_ROLE``, in the order ADR-0006 lists them.
SUPPORTED_PROCESS_ROLES = (API_ROLE, TASK_WORKER_ROLE, SCHEDULER_ROLE, INIT_ROLE)

#: Kept because the local and traditional entrypoints predate the variable and
#: an unset value has always meant "serve the API". Every entrypoint in this
#: repository now sets the role explicitly; the default exists for a checkout
#: that runs ``manage.py`` directly and for third-party deployment scripts.
DEFAULT_PROCESS_ROLE = API_ROLE

#: Roles whose process serves HTTP. ``scheduler`` and ``init`` do not: their
#: health is the process itself, not an endpoint.
HTTP_SERVING_ROLES = (API_ROLE, TASK_WORKER_ROLE)

#: Name of the logger the startup summary is emitted on. Resolved when the
#: summary is logged, never at import: the deployment settings apply
#: ``disable_existing_loggers: True``, which permanently disables every logger
#: object that existed before ``django.setup()`` ran. A module-level
#: ``getLogger`` here would be created while settings are still importing and
#: would therefore be silenced in exactly the deployments the line matters in.
#: (The same mechanism is why Celery's own startup lines are missing from those
#: processes -- see inventory/unresolved-items.md.)
RUNTIME_LOGGER_NAME = "care.runtime"

_summary_logged = False


def validate_process_role(role: str) -> str:
    """Return ``role`` if supported, otherwise raise ``ImproperlyConfigured``.

    An unknown role fails the process rather than falling back to ``api``.
    Silently serving the public API because ``task_wroker`` was misspelled is
    exactly the accident ADR-0006 exists to prevent.
    """
    if role not in SUPPORTED_PROCESS_ROLES:
        supported = ", ".join(SUPPORTED_PROCESS_ROLES)
        msg = f"Invalid CARE_PROCESS_ROLE: {role!r}. Supported values are: {supported}."
        raise ImproperlyConfigured(msg)
    return role


def current_process_role() -> str:
    """The validated role of this process."""
    from django.conf import settings

    return settings.CARE_PROCESS_ROLE


def is_api_process() -> bool:
    return current_process_role() == API_ROLE


def is_task_worker() -> bool:
    return current_process_role() == TASK_WORKER_ROLE


def is_scheduler() -> bool:
    return current_process_role() == SCHEDULER_ROLE


def is_init_process() -> bool:
    return current_process_role() == INIT_ROLE


def runtime_summary() -> dict[str, str]:
    """
    The non-sensitive description of what this process is and what it selected.

    Every value is a backend *name* chosen by configuration -- never a URL,
    credential or connection string. Accidental deployment misconfiguration is
    usually visible in these few values alone.

    ``rate_limit_semantics`` is the exception to "these are just names": it
    states the guarantee rather than the technology, because the technology does
    not imply it to a reader. RF2 permits a PostgreSQL rate-limit store whose
    increments are not atomic, and a deployment that ends up there by accident
    should be able to see it in one startup line rather than infer it from a
    backend name. Absent under ``disabled``, where there is no counter to
    characterise.
    """
    from django.conf import settings

    from config.caches import rate_limit_semantics

    summary = {
        "process_role": settings.CARE_PROCESS_ROLE,
        "storage_backend": settings.CARE_STORAGE_BACKEND,
        "task_backend": settings.CARE_TASK_BACKEND,
        "cache_backend": settings.CARE_CACHE_BACKEND,
        "rate_limit_backend": settings.CARE_RATE_LIMIT_BACKEND,
    }
    semantics = rate_limit_semantics(settings.CARE_RATE_LIMIT_BACKEND)
    if semantics is not None:
        summary["rate_limit_semantics"] = semantics
    return summary


def log_runtime_summary() -> None:
    """Emit :func:`runtime_summary` once per process, at startup."""
    global _summary_logged  # noqa: PLW0603
    if _summary_logged:
        return
    _summary_logged = True
    logging.getLogger(RUNTIME_LOGGER_NAME).info(
        "CARE runtime: %s",
        " ".join(f"{key}={value}" for key, value in runtime_summary().items()),
    )
