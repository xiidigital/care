"""
What the Cloud Tasks backend actually asks Google to create.

The client is mocked, so these run without credentials, without a network and
without a queue. They assert the parts of the request CARE is responsible for
-- target, method, body, authentication, schedule -- and deliberately not the
protobuf shapes the client library owns.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from care.utils.tasks import enqueue_task
from care.utils.tasks.envelope import TASK_ENVELOPE_VERSION

PROJECT = "care-test"
LOCATION = "us-central1"
QUEUE = "care-default"
WORKER_URL = "https://worker.example.run.app/internal/tasks/execute/"
SERVICE_ACCOUNT = "invoker@care-test.iam.gserviceaccount.com"
AUDIENCE = "https://worker.example.run.app"

CLOUD_TASKS_SETTINGS = {
    "CARE_TASK_BACKEND": "cloud_tasks",
    "GCP_TASKS_PROJECT_ID": PROJECT,
    "GCP_TASKS_LOCATION": LOCATION,
    "GCP_TASKS_QUEUE": QUEUE,
    "GCP_WORKER_URL": WORKER_URL,
    "GCP_TASKS_SERVICE_ACCOUNT": SERVICE_ACCOUNT,
    "GCP_TASKS_OIDC_AUDIENCE": AUDIENCE,
}


@override_settings(**CLOUD_TASKS_SETTINGS)
class CloudTasksRequestTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("care.utils.tasks.backends.cloud_tasks.get_client")
        self.get_client = patcher.start()
        self.addCleanup(patcher.stop)

        self.client = MagicMock()
        self.client.queue_path.return_value = (
            f"projects/{PROJECT}/locations/{LOCATION}/queues/{QUEUE}"
        )
        self.client.task_path.side_effect = (
            lambda project, region, queue, task: (
                f"projects/{project}/locations/{region}/queues/{queue}/tasks/{task}"
            )
        )
        self.client.create_task.return_value.name = "projects/care-test/.../tasks/abc"
        self.get_client.return_value = self.client

    def enqueue(self, **kwargs):
        result = enqueue_task("send_totp_enabled_email", {"user_id": 42}, **kwargs)
        return result, self.client.create_task.call_args.kwargs

    def http_request(self):
        _, call = self.enqueue()
        return call["task"]["http_request"]

    def test_it_targets_the_configured_queue(self):
        _, call = self.enqueue()
        self.client.queue_path.assert_called_once_with(PROJECT, LOCATION, QUEUE)
        self.assertEqual(
            call["parent"], f"projects/{PROJECT}/locations/{LOCATION}/queues/{QUEUE}"
        )

    def test_it_posts_to_the_private_worker(self):
        from google.cloud import tasks_v2

        request = self.http_request()
        self.assertEqual(request["url"], WORKER_URL)
        self.assertEqual(request["http_method"], tasks_v2.HttpMethod.POST)

    def test_it_sends_json(self):
        request = self.http_request()
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        json.loads(request["body"])

    def test_the_body_is_the_task_envelope(self):
        body = json.loads(self.http_request()["body"])
        self.assertEqual(
            body,
            {
                "version": TASK_ENVELOPE_VERSION,
                "task": "send_totp_enabled_email",
                "payload": {"user_id": 42},
            },
        )

    def test_the_body_carries_no_credentials_or_records(self):
        # The whole request should be identifiers. If a payload ever grows a
        # record or a secret, this is where it should be noticed.
        body = json.loads(self.http_request()["body"])
        self.assertEqual(set(body), {"version", "task", "payload"})
        self.assertEqual(body["payload"], {"user_id": 42})

    def test_it_authenticates_with_an_oidc_token(self):
        token = self.http_request()["oidc_token"]
        self.assertEqual(token["service_account_email"], SERVICE_ACCOUNT)
        self.assertEqual(token["audience"], AUDIENCE)

    def test_it_uses_application_default_credentials(self):
        # No credentials argument anywhere: the Cloud Run service identity is
        # the only credential, and no JSON key file is ever read.
        self.enqueue()
        self.get_client.assert_called_once_with()

    def test_no_schedule_time_without_a_delay(self):
        _, call = self.enqueue()
        self.assertNotIn("schedule_time", call["task"])

    def test_a_delay_becomes_a_future_schedule_time(self):
        from datetime import UTC, datetime

        before = datetime.now(UTC)
        _, call = self.enqueue(delay_seconds=120)
        scheduled = call["task"]["schedule_time"]
        self.assertGreater((scheduled - before).total_seconds(), 60)

    def test_no_task_name_unless_one_is_requested(self):
        _, call = self.enqueue()
        self.assertNotIn("name", call["task"])

    def test_an_explicit_task_id_becomes_a_named_task(self):
        _, call = self.enqueue(task_id="totp-42")
        self.assertEqual(
            call["task"]["name"],
            f"projects/{PROJECT}/locations/{LOCATION}/queues/{QUEUE}/tasks/totp-42",
        )

    def test_it_returns_the_cloud_tasks_name(self):
        result, _ = self.enqueue()
        self.assertEqual(result, "projects/care-test/.../tasks/abc")
