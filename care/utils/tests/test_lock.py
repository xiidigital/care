"""Real PostgreSQL tests for ES-05 transaction-scoped advisory locks."""

from django.db import connections, transaction
from django.test import SimpleTestCase, TransactionTestCase

from care.utils.lock import (
    Lock,
    LockConfigurationError,
    advisory_lock_key,
)


class AdvisoryLockKeyTests(SimpleTestCase):
    def test_key_is_stable_and_namespaced(self):
        self.assertEqual(
            advisory_lock_key("sync_permissions_roles"), -2588850649238380186
        )
        self.assertNotEqual(advisory_lock_key("a"), advisory_lock_key("b"))

    def test_requires_an_atomic_transaction(self):
        with self.assertRaises(LockConfigurationError):
            Lock("test").acquire()


class AdvisoryLockPostgreSQLTests(TransactionTestCase):
    """Each contender uses an independent live PostgreSQL connection."""

    def setUp(self):
        super().setUp()
        self.other = connections["default"].copy()
        self.other.ensure_connection()

    def tearDown(self):
        self.other.close()
        super().tearDown()

    def try_other(self, name):
        self.other.set_autocommit(False)
        try:
            with self.other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(%s)", [advisory_lock_key(name)]
                )
                return cursor.fetchone()[0]
        finally:
            self.other.rollback()
            self.other.set_autocommit(True)

    def test_same_lock_contends_but_different_lock_does_not(self):
        with transaction.atomic(), Lock("same-lock"):
            self.assertFalse(self.try_other("same-lock"))
            self.assertTrue(self.try_other("different-lock"))

    def test_commit_releases_lock(self):
        with transaction.atomic(), Lock("commit-release"):
            pass
        self.assertTrue(self.try_other("commit-release"))

    def test_rollback_and_exception_release_lock(self):
        with (
            self.assertRaises(ValueError),
            transaction.atomic(),
            Lock("rollback-release"),
        ):
            raise ValueError("exercise rollback")
        self.assertTrue(self.try_other("rollback-release"))
