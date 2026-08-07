"""The dispatch-backend interface."""

from abc import ABC, abstractmethod

from care.utils.tasks.registry import TaskDefinition


class TaskBackend(ABC):
    """
    Enqueues a validated task for later execution.

    The contract is deliberately narrow. ADR-0003 restricts it to CARE's
    verified requirements, so there is no chain, group, chord, callback or
    workflow concept here, and none should be added without a call site that
    needs it.
    """

    #: Value of ``CARE_TASK_BACKEND`` that selects this implementation.
    name: str

    @abstractmethod
    def enqueue(
        self,
        definition: TaskDefinition,
        payload: dict,
        *,
        delay_seconds: int | None = None,
        task_id: str | None = None,
    ) -> str:
        """
        Enqueue ``definition`` with ``payload`` and return an external task id.

        ``payload`` has already been validated against the task's schema and
        contains only JSON-serializable primitives.

        The returned identifier is for logging and correlation. It is *not* a
        handle onto a result: ADR-0003 keeps meaningful task outcomes in
        PostgreSQL, not in the transport.
        """
