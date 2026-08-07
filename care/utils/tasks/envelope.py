"""
The task request envelope.

The shape a dispatch backend writes and the worker reads. It lives in its own
module because both sides need it and neither should have to import the other:
the worker must not pull in a provider client, and the Cloud Tasks backend must
not pull in Django views.
"""

#: Bumped only when the shape changes in a way a deployed worker cannot read.
#: The worker rejects versions it does not recognise rather than guessing.
TASK_ENVELOPE_VERSION = 1


def build_envelope(task_name: str, payload: dict) -> dict:
    """
    The request body delivered to the worker.

    Deliberately minimal: a task name, its validated payload and a version. It
    carries no credentials, no session, no user object and no complete domain
    record -- handlers reload state from PostgreSQL by identifier.
    """
    return {
        "version": TASK_ENVELOPE_VERSION,
        "task": task_name,
        "payload": payload,
    }
