"""Transaction-scoped PostgreSQL advisory locks.

Locks are coordination, not cache entries.  Each lock is held by the current
database transaction and is released by PostgreSQL on commit or rollback.  A
caller must therefore enter ``transaction.atomic()`` before entering a lock.
"""

from hashlib import blake2b

from django.db import connection, transaction
from django.db.utils import DatabaseError
from rest_framework.exceptions import APIException


class ObjectLocked(APIException):
    status_code = 423
    default_detail = "The resource you are trying to access is locked"
    default_code = "object_locked"


class LockConfigurationError(RuntimeError):
    """Raised when a PostgreSQL lock is used outside its transaction scope."""


def advisory_lock_key(name: str) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory-lock key for ``name``.

    BLAKE2b is deterministic across processes.  The ``care.lock.v1`` namespace
    separates this mapping from any future advisory-lock users; names are never
    logged by this module.
    """
    digest = blake2b(f"care.lock.v1:{name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class Lock:
    """A non-blocking, transaction-scoped PostgreSQL advisory lock.

    Acquisition is one attempt.  Contention raises :class:`ObjectLocked`; a
    database failure propagates and never becomes a successful acquisition.
    ``timeout`` is retained only as source-compatible metadata for callers that
    supplied it historically; PostgreSQL transaction lifetime is the lease.
    """

    def __init__(self, key: str, timeout: int | None = None):
        self.key = key
        self.timeout = timeout
        self._acquired = False

    def acquire(self):
        if not transaction.get_connection().in_atomic_block:
            raise LockConfigurationError(
                "PostgreSQL advisory locks require transaction.atomic() before Lock"
            )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(%s)",
                    [advisory_lock_key(self.key)],
                )
                acquired = cursor.fetchone()[0]
        except DatabaseError:
            raise
        if not acquired:
            raise ObjectLocked
        self._acquired = True

    def release(self):
        """Compatibility no-op: PostgreSQL releases xact locks at transaction end."""
        self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False
