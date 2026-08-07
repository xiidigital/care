"""
The Google Cloud Tasks dispatch backend.

Cloud Tasks is a deployment implementation of ADR-0003's dispatch contract, not
part of CARE's architecture: nothing outside this module imports the client, and
no domain code learns that it exists. Selecting ``CARE_TASK_BACKEND=celery``
means this module is never imported, so the dependency is not required for a
local or traditional deployment.

The backend enqueues authenticated HTTP tasks aimed at CARE's private worker
(:mod:`care.utils.tasks.views`). Credentials come from Application Default
Credentials -- the Cloud Run service identity -- so no service-account JSON file
is read, shipped or configured.
"""

import json
from datetime import UTC, datetime, timedelta
from functools import cache

from django.conf import settings

from care.utils.tasks.backends.base import TaskBackend
from care.utils.tasks.envelope import build_envelope
from care.utils.tasks.exceptions import TaskDispatchError
from care.utils.tasks.registry import TaskDefinition


@cache
def get_client():
    """
    The shared Cloud Tasks client.

    Cached per process: the client owns a gRPC channel and is safe to reuse.
    Imported here rather than at module level so that importing this module
    under a non-GCP profile does not require the package.
    """
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


class CloudTasksBackend(TaskBackend):
    name = "cloud_tasks"

    def enqueue(
        self,
        definition: TaskDefinition,
        payload: dict,
        *,
        delay_seconds: int | None = None,
        task_id: str | None = None,
    ) -> str:
        from google.cloud import tasks_v2

        client = get_client()
        project = settings.GCP_TASKS_PROJECT_ID
        location = settings.GCP_TASKS_LOCATION
        queue = settings.GCP_TASKS_QUEUE

        request_task: dict = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": settings.GCP_WORKER_URL,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(build_envelope(definition.name, payload)).encode(),
                # Platform authentication. Cloud Run IAM on the worker service
                # is what actually enforces this; the token is how the caller
                # proves the invoker identity.
                "oidc_token": {
                    "service_account_email": settings.GCP_TASKS_SERVICE_ACCOUNT,
                    "audience": settings.GCP_TASKS_OIDC_AUDIENCE,
                },
            },
        }

        if delay_seconds:
            request_task["schedule_time"] = datetime.now(UTC) + timedelta(
                seconds=delay_seconds
            )

        if task_id is not None:
            # A named task gives Cloud Tasks server-side de-duplication for the
            # queue's retention window. It is a useful narrowing, not a
            # correctness guarantee: handlers still assume at-least-once.
            request_task["name"] = client.task_path(project, location, queue, task_id)

        try:
            response = client.create_task(
                parent=client.queue_path(project, location, queue),
                task=request_task,
            )
        except Exception as e:
            # Never surface the provider exception to domain code: callers see
            # one CARE-level dispatch failure regardless of backend.
            msg = f"Could not enqueue task {definition.name!r}"
            raise TaskDispatchError(msg) from e

        return str(response.name)
