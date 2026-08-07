"""
Behavioural guarantees of the ADR-0003 task runtime.

Four things this file exists to prevent regressing:

1. Calls that are synchronous today stay synchronous. Most task-decorated
   functions in CARE were never dispatched, and the modernization must not have
   quietly made any of them eventually consistent.
2. Tasks that depend on rows the request writes are dispatched only after that
   transaction commits.
3. Report-generation retry classification does not depend on the storage
   provider, so it behaves identically under ``s3`` and ``gcs``.
4. Duplicate delivery of each migrated task does what the inventory says it
   does.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from model_bakery import baker

from care.emr.models.resource_category import (
    ResourceCategory,
    summarise_monetary_components,
)
from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError
from care.utils.tests.base import CareAPITestBase

DISPATCHER = "care.utils.tasks.dispatcher.enqueue_task"

S3_STORAGE = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {"bucket_name": "reports", "file_overwrite": True},
}
GCS_STORAGE = {
    "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
    "OPTIONS": {"bucket_name": "reports", "file_overwrite": True},
}


class SynchronousCallSitesTests(CareAPITestBase):
    """
    The mandatory regression: what ran inline before still runs inline.

    ``rebalance_account`` is the most important of the sixteen, and the reason
    it is: it recalculates financial balances at twelve call sites in the
    request path. Making it asynchronous would expose intermediate totals to any
    read landing between the write and the recalculation.
    """

    def test_rebalance_account_is_an_ordinary_function(self):
        from care.emr.resources.account.sync_items import rebalance_account

        self.assertFalse(hasattr(rebalance_account, "delay"))
        self.assertFalse(hasattr(rebalance_account, "apply_async"))

    def test_rebalance_account_runs_inline_and_dispatches_nothing(self):
        from care.emr.models.account import Account
        from care.emr.resources.account.sync_items import rebalance_account

        with (
            patch(DISPATCHER) as dispatch,
            patch("care.emr.resources.account.sync_items.sync_account_items") as sync,
            patch.object(Account.objects, "get") as get_account,
        ):
            rebalance_account(1)

        # Everything happened before the call returned, and nothing was queued.
        get_account.assert_called_once_with(id=1)
        sync.assert_called_once()
        get_account.return_value.save.assert_called_once()
        dispatch.assert_not_called()

    def test_handle_cascade_is_an_ordinary_function(self):
        from care.emr.models.location import handle_cascade

        self.assertFalse(hasattr(handle_cascade, "delay"))
        self.assertFalse(hasattr(handle_cascade, "apply_async"))

    def test_no_model_or_resource_module_imports_the_celery_app(self):
        from care.emr.models import location
        from care.emr.resources.account import sync_items

        self.assertFalse(hasattr(location, "app"))
        self.assertFalse(hasattr(sync_items, "app"))

    def test_summarise_monetary_components_updates_its_own_category_inline(self):
        # The caller's own category is recomputed within the request,
        # transactionally. Only the fan-out over children is asynchronous, and
        # that was asynchronous before ADR-0003 too.
        category = self.make_category("parent-inline")

        with patch(DISPATCHER) as dispatch:
            summarise_monetary_components(category.id)

        category.refresh_from_db()
        self.assertEqual(
            category.calculated_monetary_components,
            [{"monetary_component_type": "surcharge"}],
        )
        dispatch.assert_not_called()  # no children, nothing to fan out to

    def test_only_the_child_fan_out_is_dispatched(self):
        parent = self.make_category("parent-fanout")
        child = ResourceCategory.objects.create(
            facility=parent.facility,
            resource_type="charge_item_definition",
            resource_sub_type="test",
            title="Child",
            slug="child-fanout",
            parent=parent,
            configured_monetary_components=[],
        )

        with patch(DISPATCHER) as dispatch, self.captureOnCommitCallbacks(execute=True):
            summarise_monetary_components(parent.id)

        dispatch.assert_called_once_with(
            "summarise_monetary_components",
            {"category_id": child.id},
            delay_seconds=None,
        )

    def make_category(self, slug):
        facility = self.create_facility(user=self.create_user())
        return ResourceCategory.objects.create(
            facility=facility,
            resource_type="charge_item_definition",
            resource_sub_type="test",
            title="Parent",
            slug=slug,
            configured_monetary_components=[{"monetary_component_type": "surcharge"}],
        )


class TransactionDispatchTests(TransactionTestCase):
    """
    ``ATOMIC_REQUESTS`` is enabled, so an immediate dispatch would let a worker
    read uncommitted rows. These use ``TransactionTestCase`` so the transaction
    really commits: under the plain ``TestCase`` the outer atomic block never
    does, and every assertion here would be vacuous.
    """

    def test_a_committed_transaction_dispatches(self):
        from care.utils.tasks import enqueue_task_on_commit

        with patch(DISPATCHER) as dispatch:
            with transaction.atomic():
                enqueue_task_on_commit("send_totp_enabled_email", {"user_id": 5})
                dispatch.assert_not_called()  # transaction still open
            dispatch.assert_called_once()

        self.assertEqual(dispatch.call_args.args[0], "send_totp_enabled_email")
        self.assertEqual(dispatch.call_args.args[1], {"user_id": 5})

    def test_a_rolled_back_transaction_dispatches_nothing(self):
        from care.utils.tasks import enqueue_task_on_commit

        class RollbackError(Exception):
            pass

        with patch(DISPATCHER) as dispatch:
            with self.assertRaises(RollbackError), transaction.atomic():
                enqueue_task_on_commit("send_totp_enabled_email", {"user_id": 5})
                raise RollbackError

            dispatch.assert_not_called()

    def test_an_invalid_payload_fails_at_the_call_site_not_at_commit(self):
        # Validation happens immediately, so the error surfaces in the request
        # that caused it rather than inside a commit hook where nobody sees it.
        from care.utils.tasks import enqueue_task_on_commit
        from care.utils.tasks.exceptions import InvalidTaskPayloadError

        with transaction.atomic(), self.assertRaises(InvalidTaskPayloadError):
            enqueue_task_on_commit("send_totp_enabled_email", {"nope": 1})


@contextmanager
def stubbed_rendering(output=b"%PDF-1.4 stub"):
    """
    Drive ``generate_and_upload_report`` to its storage write without rendering.

    Everything stubbed here is upstream of the boundary under test. The
    ``try``/``except`` around the object write, which is what these tests are
    about, runs for real.
    """
    module = "care.emr.reports.report_utils"
    with (
        patch(f"{module}.DataPointRegistry") as data_points,
        patch(f"{module}.ReportTypeRegistry") as report_types,
        patch(f"{module}.validate_associating_id") as associating,
        patch(f"{module}.GeneratorRegistry") as generators,
        patch(f"{module}.TemplateEngine"),
        patch(f"{module}.Renderer") as renderer,
    ):
        data_points.get.return_value = MagicMock(context_key="encounter")
        report_types.get.return_value = MagicMock()
        associating.return_value = MagicMock()
        generators.get_format_config.return_value = {
            "file_extension": ".pdf",
            "mime_type": "application/pdf",
        }
        renderer.return_value.render.return_value = output
        yield


class ReportRetryPortabilityTests(CareAPITestBase):
    """
    Resolves S2.

    The task previously declared ``autoretry_for=(botocore ClientError,)``,
    which fired under ``s3`` and never under ``gcs`` -- so the GCS profile had
    no retry at all. Classification now happens at the storage boundary, and
    these assertions hold for either provider because nothing in the path names
    one.
    """

    def test_the_task_does_not_retry_on_a_provider_exception_type(self):
        from care.emr.tasks.report_generation import generate_report_task

        self.assertEqual(
            tuple(generate_report_task.autoretry_for), (RetryableTaskError,)
        )

    def test_a_permanent_failure_is_not_retried(self):
        from care.emr.tasks.report_generation import generate_report_task

        self.assertNotIn(PermanentTaskError, tuple(generate_report_task.autoretry_for))

    def test_the_task_module_imports_no_provider_library(self):
        import ast
        import inspect
        from pathlib import Path

        from care.emr.tasks import report_generation

        tree = ast.parse(Path(inspect.getfile(report_generation)).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for module in sorted(imported):
            self.assertFalse(
                module.startswith(("botocore", "boto3", "google.")),
                f"{module} is a provider library; retry classification must not "
                f"depend on one",
            )

    def assert_storage_failure_is_retryable(self, exception):
        """The heart of S2: whatever the provider raises, CARE says 'retry'."""
        from care.emr.reports import report_utils
        from care.emr.utils.file_manager import FilesManager

        template = baker.make("emr.Template", name="T", options={}, template_data="")
        with (
            stubbed_rendering(),
            patch.object(FilesManager, "put_object", side_effect=exception),
            self.assertRaises(RetryableTaskError),
        ):
            report_utils.generate_and_upload_report(
                template=template,
                report_type="encounter",
                associating_id="00000000-0000-0000-0000-000000000000",
            )

    @override_settings(STORAGES={"report": S3_STORAGE})
    def test_an_s3_style_write_failure_is_retryable(self):
        # Stands in for botocore.exceptions.ClientError without importing it.
        self.assert_storage_failure_is_retryable(OSError("An error occurred (500)"))

    @override_settings(STORAGES={"report": GCS_STORAGE})
    def test_a_gcs_style_write_failure_is_retryable(self):
        # Stands in for google.api_core.exceptions.ServiceUnavailable. Under the
        # previous autoretry_for this produced no retry whatsoever.
        self.assert_storage_failure_is_retryable(
            ConnectionError("503 Service Unavailable")
        )

    def test_the_orphan_row_is_removed_when_the_write_fails(self):
        from care.emr.models.report.report_upload import ReportUpload
        from care.emr.reports import report_utils
        from care.emr.utils.file_manager import FilesManager

        template = baker.make("emr.Template", name="T", options={}, template_data="")
        before = ReportUpload.objects.count()

        with (
            stubbed_rendering(),
            patch.object(FilesManager, "put_object", side_effect=OSError("nope")),
            self.assertRaises(RetryableTaskError),
        ):
            report_utils.generate_and_upload_report(
                template=template,
                report_type="encounter",
                associating_id="00000000-0000-0000-0000-000000000000",
            )

        self.assertEqual(ReportUpload.objects.count(), before)

    def test_a_missing_template_is_classified_permanent(self):
        from uuid import uuid4

        from care.emr.tasks.report_generation import generate_report

        with self.assertRaises(PermanentTaskError):
            generate_report(
                template_id=str(uuid4()),
                report_type="encounter",
                associating_id=str(uuid4()),
            )


class CeleryRegistrationTests(SimpleTestCase):
    """
    What a Celery worker actually registers at startup.

    Two failures this guards against, both silent until a task is dispatched:

    *Missing* -- `autodiscover_tasks` imports `care.emr.tasks` and no deeper, so
    a wrapper in a submodule the package does not import never reaches the
    worker. This regressed during ADR-0003 and was caught only by inspecting a
    running worker, because in this test process other imports had already
    pulled the modules in. Hence the subprocess: it is the one way to observe
    what a cold worker sees.

    *Renamed* -- a worker draining a queue written by an older revision must
    still recognise every name in it, so the set below is exact rather than a
    subset.
    """

    EXPECTED = {
        "care.emr.models.resource_category.summarise_monetary_components",
        "care.emr.tasks.cleanup_expired_token_slots.cleanup_expired_token_slots",
        "care.emr.tasks.cleanup_incomplete_file_uploads.cleanup_incomplete_file_uploads",
        "care.emr.tasks.report_generation.generate_report_task",
        "care.emr.tasks.totp.send_totp_disabled_email",
        "care.emr.tasks.totp.send_totp_enabled_email",
    }

    def test_a_cold_worker_registers_exactly_the_expected_task_names(self):
        import json
        import subprocess
        import sys

        program = (
            "import django, os;"
            "os.environ.setdefault("
            "'DJANGO_SETTINGS_MODULE', 'config.settings.test');"
            "django.setup();"
            "from config.celery_app import app;"
            "app.loader.import_default_modules();"
            "import json;"
            "print(json.dumps(sorted("
            "n for n in app.tasks if not n.startswith('celery.'))))"
        )
        completed = subprocess.run(  # noqa: S603  # a literal program, no input
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
        )
        registered = set(json.loads(completed.stdout.strip().splitlines()[-1]))
        self.assertEqual(registered, self.EXPECTED)


class IdempotencyTests(CareAPITestBase):
    """
    Every backend can redeliver, so each migrated task is executed twice here
    and the outcome recorded. See ``inventory/task-call-sites.md`` for the
    per-task disposition.
    """

    def test_expired_token_slot_cleanup_is_idempotent(self):
        from care.emr.tasks.cleanup_expired_token_slots import (
            cleanup_expired_token_slots,
        )

        self.assertEqual(cleanup_expired_token_slots(), 0)
        self.assertEqual(cleanup_expired_token_slots(), 0)

    def test_incomplete_upload_cleanup_is_idempotent(self):
        from care.emr.tasks.cleanup_incomplete_file_uploads import (
            cleanup_incomplete_file_uploads,
        )

        self.assertEqual(cleanup_incomplete_file_uploads(), 0)
        self.assertEqual(cleanup_incomplete_file_uploads(), 0)

    def test_a_totp_email_is_re_sent_on_redelivery(self):
        from django.core import mail

        from care.emr.tasks.totp import send_totp_enabled_email

        user = self.create_user(email="totp@example.com")
        send_totp_enabled_email(user.id)
        send_totp_enabled_email(user.id)

        # Recorded rather than prevented. A duplicate notification is visible
        # but not destructive, and suppressing it needs an execution record --
        # which ADR-0003 says to add only where duplication actually harms.
        self.assertEqual(len(mail.outbox), 2)

    def test_a_deleted_user_is_permanent_rather_than_an_endless_retry(self):
        from care.emr.tasks.totp import send_totp_enabled_email

        user = self.create_user(email="gone@example.com")
        user_id = user.id
        user.delete()

        with self.assertRaises(PermanentTaskError):
            send_totp_enabled_email(user_id)

    def test_summarising_the_same_category_twice_converges(self):
        facility = self.create_facility(user=self.create_user())
        category = ResourceCategory.objects.create(
            facility=facility,
            resource_type="charge_item_definition",
            resource_sub_type="test",
            title="Parent",
            slug="idempotent-cat",
            configured_monetary_components=[{"monetary_component_type": "tax"}],
        )

        with patch(DISPATCHER):
            summarise_monetary_components(category.id)
            category.refresh_from_db()
            first = category.calculated_monetary_components

            summarise_monetary_components(category.id)
            category.refresh_from_db()

        self.assertEqual(category.calculated_monetary_components, first)
