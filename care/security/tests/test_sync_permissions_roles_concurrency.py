"""Consumer-level concurrency coverage for ``sync_permissions_roles``.

`care/utils/tests/test_lock.py` proves the advisory-lock helper contends across
live PostgreSQL connections.  This module proves the property ES-05 actually
requires: two concurrent invocations of the **real management command** cannot
execute its protected mutation at the same time.

Nothing here mocks `care.utils.lock.Lock`, and nothing asserts merely that it
was called.  Both contenders run `call_command("sync_permissions_roles")` on
independent PostgreSQL connections and take the real
`pg_try_advisory_xact_lock` on the real key.  The holder is suspended *inside*
the critical section by a `post_save` receiver that fires only after the
command has already written a `PermissionModel` row inside its transaction --
a test-only listener on a public Django signal, with no production change and
no sleep-based synchronization.

The command's contention semantics are the ones asserted: `Lock.acquire`
raises `ObjectLocked` on a failed `pg_try_advisory_xact_lock`, so the second
invocation aborts before its `transaction.atomic()` block mutates anything.
"""

import threading

from django.core.management import call_command
from django.db import connections
from django.db.models.signals import post_save
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from care.security.models import PermissionModel, RoleModel, RolePermission
from care.security.permissions.base import PermissionController
from care.utils.lock import ObjectLocked, advisory_lock_key

# Only reached when exclusion is broken; the events below are the real
# synchronization, so a passing run never waits on this.
HANDOFF_TIMEOUT = 60

LOCK_KEY = advisory_lock_key("sync_permissions_roles")

MUTATING_STATEMENTS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

# `_meta` is Django's documented Model _meta API, not a private attribute.
PROTECTED_TABLES = (
    PermissionModel._meta.db_table,  # noqa: SLF001
    RoleModel._meta.db_table,  # noqa: SLF001
    RolePermission._meta.db_table,  # noqa: SLF001
)


class SyncPermissionsRolesConcurrencyTests(TransactionTestCase):
    """Two live invocations of the command, one advisory lock, one winner."""

    def setUp(self):
        super().setUp()
        self.entered_critical_section = threading.Event()
        self.contender_finished = threading.Event()
        self.holder_thread_id = None
        self.holder_error = None
        post_save.connect(
            self.hold_inside_critical_section,
            sender=PermissionModel,
            dispatch_uid="es05-consumer-contention",
        )
        self.addCleanup(
            post_save.disconnect,
            self.hold_inside_critical_section,
            sender=PermissionModel,
            dispatch_uid="es05-consumer-contention",
        )
        self.addCleanup(self.contender_finished.set)

    def hold_inside_critical_section(self, sender, instance, **kwargs):
        """Suspend the holder after its first protected write, once."""
        if threading.get_ident() != self.holder_thread_id:
            return
        if self.entered_critical_section.is_set():
            return
        self.entered_critical_section.set()
        self.contender_finished.wait(HANDOFF_TIMEOUT)

    def run_holder(self):
        self.holder_thread_id = threading.get_ident()
        try:
            call_command("sync_permissions_roles")
        except Exception as exc:  # surfaced by the assertions below
            self.holder_error = exc
        finally:
            connections.close_all()

    def test_second_invocation_cannot_enter_the_protected_critical_section(self):
        expected = len(PermissionController.get_permissions())
        self.assertEqual(PermissionModel.objects.count(), 0)

        holder = threading.Thread(target=self.run_holder, name="sync-holder")
        holder.start()
        self.addCleanup(holder.join, HANDOFF_TIMEOUT)

        # 1. invocation A is inside the critical section, holding the advisory
        # lock, with its protected mutation written but not yet committed.
        self.assertTrue(
            self.entered_critical_section.wait(HANDOFF_TIMEOUT),
            f"holder never entered the critical section: {self.holder_error!r}",
        )
        self.assertEqual(PermissionModel.objects.count(), 0, "A committed early")

        # 2. invocation B attempts the same operation on this connection.
        try:
            with (
                CaptureQueriesContext(connections["default"]) as captured,
                self.assertRaises(ObjectLocked),
            ):
                call_command("sync_permissions_roles")
        finally:
            self.contender_finished.set()

        sql = [query["sql"] for query in captured.captured_queries]

        # B really attempted the consumer's own lock, on the consumer's own key.
        self.assertTrue(
            any(
                "pg_try_advisory_xact_lock" in statement and str(LOCK_KEY) in statement
                for statement in sql
            ),
            f"B never attempted the sync_permissions_roles advisory lock: {sql}",
        )

        # 3. B executed no protected mutation while A held the lock.
        self.assertEqual(
            [
                statement
                for statement in sql
                if statement.lstrip().upper().startswith(MUTATING_STATEMENTS)
                and any(table in statement for table in PROTECTED_TABLES)
            ],
            [],
            f"B mutated protected tables while A held the lock: {sql}",
        )

        holder.join(HANDOFF_TIMEOUT)
        self.assertFalse(holder.is_alive(), "holder did not finish")
        self.assertIsNone(self.holder_error)

        # A committed the full sync exactly once.
        self.assertEqual(PermissionModel.objects.count(), expected)

        # 4. the lock is released with A's transaction, so normal execution
        # remains possible and remains idempotent.
        call_command("sync_permissions_roles")
        self.assertEqual(PermissionModel.objects.count(), expected)
