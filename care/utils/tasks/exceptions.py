"""
Provider-neutral task failure classification.

ADR-0003 requires retry policy to follow *operation* failure semantics rather
than the exception names a particular storage or queue provider happens to
raise. Every asynchronous backend can retry work, but each expresses that
differently -- Celery through ``autoretry_for``, Cloud Tasks through the HTTP
status the worker returns -- so the classification has to live in CARE.

Two categories are enough, and ADR-0003 explicitly warns against building more:

``RetryableTaskError``
    The operation failed for a reason that may not recur: an object-storage
    write timed out, an external service was briefly unavailable. Retrying the
    same payload is worthwhile.

``PermanentTaskError``
    The operation cannot succeed with this payload: the task name is unknown,
    the payload is malformed, or a referenced record does not exist. Retrying
    changes nothing.

Provider exceptions are translated at the operation boundary -- see
``care.emr.reports.report_utils`` for the storage-write case -- so no task
definition imports ``botocore`` or ``google.api_core``.
"""


class TaskError(Exception):
    """Base class for CARE task failures."""


class RetryableTaskError(TaskError):
    """A transient failure. The same payload may succeed on a later attempt."""


class PermanentTaskError(TaskError):
    """A failure that retrying cannot fix."""


class UnknownTaskError(PermanentTaskError):
    """The requested task name is not registered."""


class InvalidTaskPayloadError(PermanentTaskError):
    """The payload is absent, unserializable, oversized or fails validation."""


class TaskDispatchError(TaskError):
    """Enqueueing failed. The task was never accepted by the backend."""
