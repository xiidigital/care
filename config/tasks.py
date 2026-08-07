"""
Validation of CARE's asynchronous-execution configuration.

ADR-0003 makes the task transport a configuration choice. This module is the
settings-level half of that: it validates the selected backend name and checks
that the variables *that backend* needs are present. It deliberately imports no
application code and constructs no client, so importing settings never opens a
network connection.

Only the selected backend is validated. Choosing Celery must not require any
Cloud Tasks variable, and choosing Cloud Tasks must not require a broker.
"""

from django.core.exceptions import ImproperlyConfigured

CELERY_BACKEND = "celery"
CLOUD_TASKS_BACKEND = "cloud_tasks"

#: Values accepted by ``CARE_TASK_BACKEND``. ``postgres`` is described in the
#: target-runtime document as a future option; it is not implemented, so it is
#: rejected rather than silently accepted.
SUPPORTED_TASK_BACKENDS = (CELERY_BACKEND, CLOUD_TASKS_BACKEND)

#: Values accepted by ``CARE_PROCESS_ROLE``. The role selects startup command,
#: exposed routes and logging metadata; it never alters clinical behaviour.
SUPPORTED_PROCESS_ROLES = ("api", "task_worker", "job", "celery_worker")

#: Required when ``CARE_TASK_BACKEND=cloud_tasks``.
CLOUD_TASKS_REQUIRED_SETTINGS = (
    "GCP_TASKS_PROJECT_ID",
    "GCP_TASKS_LOCATION",
    "GCP_TASKS_QUEUE",
    "GCP_WORKER_URL",
    "GCP_TASKS_SERVICE_ACCOUNT",
    "GCP_TASKS_OIDC_AUDIENCE",
)


def validate_task_backend(backend: str) -> str:
    """Return ``backend`` if supported, otherwise raise ``ImproperlyConfigured``."""
    if backend not in SUPPORTED_TASK_BACKENDS:
        supported = ", ".join(SUPPORTED_TASK_BACKENDS)
        msg = (
            f"Invalid CARE_TASK_BACKEND: {backend!r}. "
            f"Supported values are: {supported}."
        )
        raise ImproperlyConfigured(msg)
    return backend


def validate_process_role(role: str) -> str:
    """Return ``role`` if supported, otherwise raise ``ImproperlyConfigured``."""
    if role not in SUPPORTED_PROCESS_ROLES:
        supported = ", ".join(SUPPORTED_PROCESS_ROLES)
        msg = f"Invalid CARE_PROCESS_ROLE: {role!r}. Supported values are: {supported}."
        raise ImproperlyConfigured(msg)
    return role


def validate_cloud_tasks_settings(values: dict[str, str | None]) -> None:
    """
    Fail clearly when Cloud Tasks is selected but incompletely configured.

    ``values`` maps each name in :data:`CLOUD_TASKS_REQUIRED_SETTINGS` to its
    resolved value. Nothing is logged: some of these identify infrastructure and
    none should appear in an error message beyond its own name.
    """
    missing = [name for name in CLOUD_TASKS_REQUIRED_SETTINGS if not values.get(name)]
    if missing:
        msg = (
            f"CARE_TASK_BACKEND={CLOUD_TASKS_BACKEND!r} requires "
            f"{', '.join(missing)} to be set."
        )
        raise ImproperlyConfigured(msg)
