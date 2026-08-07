"""
The private task worker: what it executes, what it refuses, and which status
each outcome produces.

The status code is the whole retry contract under Cloud Tasks, so the mapping
is asserted outcome by outcome. The view is exercised through ``RequestFactory``
rather than a URL, because the route is registered only under the task-worker
role -- that route separation is asserted separately, at the bottom.

Tests that need to observe execution substitute the *handler* on a stand-in
definition. The registry itself is never modified: an unknown-name request must
be rejected by the real registry, not by a test double.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from care.utils.tasks.envelope import TASK_ENVELOPE_VERSION
from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError
from care.utils.tasks.registry import TaskDefinition, get_task
from care.utils.tasks.views import execute_task

WORKER_PATH = "/internal/tasks/execute/"
REAL_TASK = "send_totp_enabled_email"


def envelope(task=REAL_TASK, payload=None, version=None):
    return {
        "version": TASK_ENVELOPE_VERSION if version is None else version,
        "task": task,
        "payload": {"user_id": 1} if payload is None else payload,
    }


@contextmanager
def handler_stub(handler):
    """Run the worker against ``handler``, keeping the registered payload schema."""
    registered = get_task(REAL_TASK)
    stub = TaskDefinition(
        name=registered.name,
        handler=handler,
        payload_model=registered.payload_model,
    )
    with patch("care.utils.tasks.views.get_task", return_value=stub):
        yield


class TaskWorkerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def post(self, body):
        raw = body if isinstance(body, (bytes, str)) else json.dumps(body)
        request = self.factory.post(
            WORKER_PATH, data=raw, content_type="application/json"
        )
        return execute_task(request)

    def post_to_handler(self, handler, body=None):
        with handler_stub(handler):
            return self.post(envelope() if body is None else body)

    def raising(self, exception):
        def handler(**kwargs):
            raise exception

        return handler

    # --- success --------------------------------------------------------

    def test_a_registered_task_executes_and_returns_no_content(self):
        calls = []
        response = self.post_to_handler(lambda **kwargs: calls.append(kwargs))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(calls), 1)

    def test_the_handler_receives_the_validated_payload_as_keywords(self):
        calls = []
        response = self.post_to_handler(
            lambda **kwargs: calls.append(kwargs),
            envelope(payload={"user_id": 99}),
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(calls, [{"user_id": 99}])

    # --- method ---------------------------------------------------------

    def test_get_is_rejected(self):
        self.assertEqual(execute_task(self.factory.get(WORKER_PATH)).status_code, 405)

    def test_put_is_rejected(self):
        self.assertEqual(execute_task(self.factory.put(WORKER_PATH)).status_code, 405)

    # --- malformed requests, all permanent ------------------------------

    def test_malformed_json_is_permanent(self):
        self.assertEqual(self.post(b"{not json").status_code, 400)

    def test_a_json_array_is_not_an_envelope(self):
        self.assertEqual(self.post([1, 2, 3]).status_code, 400)

    def test_an_unsupported_envelope_version_is_rejected(self):
        self.assertEqual(self.post(envelope(version=99)).status_code, 400)

    def test_a_missing_task_name_is_rejected(self):
        body = {"version": TASK_ENVELOPE_VERSION, "payload": {}}
        self.assertEqual(self.post(body).status_code, 400)

    def test_an_unknown_task_name_is_rejected(self):
        self.assertEqual(self.post(envelope(task="rm_minus_rf")).status_code, 400)

    def test_a_dotted_import_path_executes_nothing(self):
        # There is no path from a request body to an arbitrary callable: the
        # registry maps short names only, so an import path is just unknown.
        response = self.post(
            envelope(task="care.emr.tasks.totp.send_totp_enabled_email")
        )
        self.assertEqual(response.status_code, 400)

    def test_an_invalid_payload_is_rejected_before_the_handler_runs(self):
        calls = []
        response = self.post_to_handler(
            lambda **kwargs: calls.append(kwargs), envelope(payload={"wrong_field": 1})
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    # --- execution outcomes ---------------------------------------------

    def test_a_transient_failure_asks_for_a_retry(self):
        response = self.post_to_handler(
            self.raising(RetryableTaskError("storage unavailable"))
        )
        self.assertEqual(response.status_code, 503)

    def test_a_permanent_failure_does_not_masquerade_as_retriable_work(self):
        response = self.post_to_handler(
            self.raising(PermanentTaskError("user does not exist"))
        )
        self.assertEqual(response.status_code, 400)

    def test_an_unclassified_failure_is_a_server_error(self):
        response = self.post_to_handler(self.raising(RuntimeError("boom")))
        self.assertEqual(response.status_code, 500)

    def test_a_failure_never_reports_success(self):
        # The one outcome that would silently lose work: returning 2xx after a
        # failed operation, to stop Cloud Tasks retrying.
        for exception in (
            RetryableTaskError("x"),
            PermanentTaskError("x"),
            RuntimeError("x"),
        ):
            response = self.post_to_handler(self.raising(exception))
            self.assertGreaterEqual(response.status_code, 400)

    def test_the_response_body_leaks_no_exception_internals(self):
        response = self.post_to_handler(
            self.raising(RuntimeError("connection to 10.1.2.3 refused"))
        )
        self.assertNotIn(b"10.1.2.3", response.content)
        self.assertNotIn(b"Traceback", response.content)

    @override_settings(CARE_TASK_LOG_PAYLOAD=False)
    def test_payloads_are_not_logged_by_default(self):
        with self.assertLogs("care.utils.tasks.views", level="INFO") as logs:
            self.post_to_handler(
                lambda **kwargs: None, envelope(payload={"user_id": 4242})
            )
        self.assertNotIn("4242", "\n".join(logs.output))


class WorkerRouteSeparationTests(SimpleTestCase):
    """The API role should not route the internal endpoint at all."""

    def route_names(self, *, enabled):
        import importlib

        from django.urls import clear_url_caches

        import config.urls

        try:
            with override_settings(CARE_TASK_HANDLER_ENDPOINT_ENABLED=enabled):
                clear_url_caches()
                urlconf = importlib.reload(config.urls)
                return {
                    getattr(pattern, "name", None) for pattern in urlconf.urlpatterns
                }
        finally:
            # Leave the process with the urlconf the settings actually describe.
            clear_url_caches()
            importlib.reload(config.urls)

    def test_the_worker_role_serves_the_route(self):
        self.assertIn("internal_task_execute", self.route_names(enabled=True))

    def test_the_api_role_does_not(self):
        self.assertNotIn("internal_task_execute", self.route_names(enabled=False))

    def test_it_is_disabled_by_default(self):
        from django.conf import settings

        self.assertEqual(settings.CARE_PROCESS_ROLE, "api")
        self.assertFalse(settings.CARE_TASK_HANDLER_ENDPOINT_ENABLED)
