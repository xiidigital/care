"""
Tests for the dispatch boundary: backend selection, the registry, payload
validation and the two configuration-level guards.

None of these require GCP credentials or a broker. The Cloud Tasks client is
mocked throughout; the request it would build is asserted in
``test_cloud_tasks_backend.py``.
"""

from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from pydantic import BaseModel

from care.utils.tasks import enqueue_task
from care.utils.tasks.backends.celery import CeleryTaskBackend
from care.utils.tasks.backends.cloud_tasks import CloudTasksBackend
from care.utils.tasks.dispatcher import get_backend
from care.utils.tasks.exceptions import (
    InvalidTaskPayloadError,
    TaskDispatchError,
    UnknownTaskError,
)
from care.utils.tasks.registry import (
    TaskDefinition,
    registered_task_names,
    validate_payload,
)
from config.tasks import (
    SUPPORTED_TASK_BACKENDS,
    validate_cloud_tasks_settings,
    validate_process_role,
    validate_task_backend,
)

CLOUD_TASKS_SETTINGS = {
    "CARE_TASK_BACKEND": "cloud_tasks",
    "GCP_TASKS_PROJECT_ID": "care-test",
    "GCP_TASKS_LOCATION": "us-central1",
    "GCP_TASKS_QUEUE": "care-default",
    "GCP_WORKER_URL": "https://worker.example.run.app/internal/tasks/execute/",
    "GCP_TASKS_SERVICE_ACCOUNT": "invoker@care-test.iam.gserviceaccount.com",
    "GCP_TASKS_OIDC_AUDIENCE": "https://worker.example.run.app",
}


class BackendSelectionTests(SimpleTestCase):
    @override_settings(CARE_TASK_BACKEND="celery")
    def test_celery_is_selected_by_name(self):
        self.assertIsInstance(get_backend(), CeleryTaskBackend)

    @override_settings(**CLOUD_TASKS_SETTINGS)
    def test_cloud_tasks_is_selected_by_name(self):
        self.assertIsInstance(get_backend(), CloudTasksBackend)

    @override_settings(CARE_TASK_BACKEND="rabbitmq")
    def test_an_unknown_backend_fails_rather_than_falling_back(self):
        with self.assertRaises(ValueError):
            get_backend()

    def test_celery_is_the_default_so_existing_deployments_are_unaffected(self):
        # Guards the promise made to local Compose and traditional installs.
        from django.conf import settings

        self.assertEqual(settings.CARE_TASK_BACKEND, "celery")


class ConfigurationValidationTests(SimpleTestCase):
    def test_supported_backends_are_accepted(self):
        for backend in SUPPORTED_TASK_BACKENDS:
            self.assertEqual(validate_task_backend(backend), backend)

    def test_an_invalid_backend_names_the_supported_values(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_task_backend("sqs")
        message = str(ctx.exception)
        self.assertIn("sqs", message)
        self.assertIn("celery", message)
        self.assertIn("cloud_tasks", message)

    def test_postgres_is_rejected_until_that_backend_exists(self):
        # Named in the target-runtime document as a future option. Accepting it
        # silently would select a backend that cannot execute anything.
        with self.assertRaises(ImproperlyConfigured):
            validate_task_backend("postgres")

    def test_an_invalid_process_role_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_process_role("worker")

    def test_cloud_tasks_settings_are_complete(self):
        validate_cloud_tasks_settings(
            {name: "value" for name in CLOUD_TASKS_SETTINGS if name.startswith("GCP_")}
        )

    def test_missing_cloud_tasks_settings_are_named(self):
        values = {
            name: "value" for name in CLOUD_TASKS_SETTINGS if name.startswith("GCP_")
        }
        values["GCP_TASKS_QUEUE"] = ""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_cloud_tasks_settings(values)
        self.assertIn("GCP_TASKS_QUEUE", str(ctx.exception))

    def test_celery_does_not_require_any_cloud_tasks_variable(self):
        # The Celery profile must start with none of these set, which is what
        # the default settings already demonstrate.
        from django.conf import settings

        self.assertEqual(settings.CARE_TASK_BACKEND, "celery")
        self.assertEqual(settings.GCP_TASKS_QUEUE, "")
        self.assertEqual(settings.GCP_WORKER_URL, "")


class RegistryTests(SimpleTestCase):
    def test_every_verified_async_task_is_registered(self):
        self.assertEqual(
            registered_task_names(),
            (
                "cleanup_expired_token_slots",
                "cleanup_incomplete_file_uploads",
                "generate_report",
                "send_totp_disabled_email",
                "send_totp_enabled_email",
                "summarise_monetary_components",
            ),
        )

    def test_an_unknown_task_name_is_rejected(self):
        with self.assertRaises(UnknownTaskError):
            enqueue_task("drop_all_the_tables", {})

    def test_a_dotted_import_path_is_not_a_task_name(self):
        # The whole point of the registry: no payload can name arbitrary code.
        with self.assertRaises(UnknownTaskError):
            enqueue_task("care.emr.tasks.totp.send_totp_enabled_email", {"user_id": 1})

    def test_registered_handlers_are_plain_callables(self):
        from care.utils.tasks.registry import get_task

        handler = get_task("generate_report").handler
        self.assertFalse(hasattr(handler, "delay"))
        self.assertFalse(hasattr(handler, "apply_async"))


class PayloadValidationTests(SimpleTestCase):
    class Payload(BaseModel):
        user_id: int

    def definition(self):
        return TaskDefinition(
            name="example", handler=lambda **kw: None, payload_model=self.Payload
        )

    def test_a_valid_payload_survives_round_tripping(self):
        self.assertEqual(
            validate_payload(self.definition(), {"user_id": 7}), {"user_id": 7}
        )

    def test_a_missing_field_is_a_permanent_failure(self):
        with self.assertRaises(InvalidTaskPayloadError):
            validate_payload(self.definition(), {})

    def test_the_error_does_not_echo_the_payload(self):
        # Payloads reference clinical records; a validation message must not
        # become the route by which one reaches a log.
        with self.assertRaises(InvalidTaskPayloadError) as ctx:
            validate_payload(self.definition(), {"user_id": "sensitive-value"})
        self.assertNotIn("sensitive-value", str(ctx.exception))

    def test_uuids_are_serialized_as_strings(self):
        import json
        from uuid import uuid4

        from care.utils.tasks.registry import get_task

        template_id, associating_id = uuid4(), uuid4()
        payload = validate_payload(
            get_task("generate_report"),
            {
                "template_id": template_id,
                "report_type": "encounter",
                "associating_id": associating_id,
                "user_id": 3,
            },
        )
        self.assertEqual(payload["template_id"], str(template_id))
        # The decisive assertion: plain json.dumps must accept it, because a
        # Cloud Tasks body is built with exactly that and not with kombu's
        # extended encoder.
        self.assertIn(str(template_id), json.dumps(payload))

    @override_settings(CARE_TASK_MAX_PAYLOAD_BYTES=10)
    def test_an_oversized_payload_is_refused(self):
        with self.assertRaises(InvalidTaskPayloadError):
            enqueue_task("send_totp_enabled_email", {"user_id": 123456789})


class CeleryBackendTests(SimpleTestCase):
    def definition(self, celery_task=None):
        class Payload(BaseModel):
            user_id: int

        return TaskDefinition(
            name="example",
            handler=lambda **kw: None,
            payload_model=Payload,
            celery_task=celery_task,
        )

    def test_it_dispatches_through_the_celery_wrapper(self):
        celery_task = MagicMock()
        celery_task.apply_async.return_value.id = "celery-id"

        result = CeleryTaskBackend().enqueue(
            self.definition(celery_task), {"user_id": 4}
        )

        self.assertEqual(result, "celery-id")
        celery_task.apply_async.assert_called_once_with(kwargs={"user_id": 4})

    def test_a_delay_becomes_a_countdown(self):
        celery_task = MagicMock()
        CeleryTaskBackend().enqueue(
            self.definition(celery_task), {"user_id": 4}, delay_seconds=30
        )
        self.assertEqual(
            celery_task.apply_async.call_args.kwargs["countdown"],
            30,
        )

    def test_a_task_id_is_passed_through(self):
        celery_task = MagicMock()
        CeleryTaskBackend().enqueue(
            self.definition(celery_task), {"user_id": 4}, task_id="fixed-id"
        )
        self.assertEqual(
            celery_task.apply_async.call_args.kwargs["task_id"],
            "fixed-id",
        )

    def test_a_task_with_no_celery_wrapper_fails_clearly(self):
        with self.assertRaises(TaskDispatchError):
            CeleryTaskBackend().enqueue(self.definition(None), {"user_id": 4})


class DispatchFailureTests(SimpleTestCase):
    @override_settings(**CLOUD_TASKS_SETTINGS)
    def test_a_backend_error_surfaces_as_a_care_level_failure(self):
        # Callers must never have to catch a provider exception type.
        with patch("care.utils.tasks.backends.cloud_tasks.get_client") as get_client:
            get_client.return_value.create_task.side_effect = RuntimeError("gRPC down")
            with self.assertRaises(TaskDispatchError):
                enqueue_task("send_totp_enabled_email", {"user_id": 1})
