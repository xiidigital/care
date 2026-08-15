"""
The TOTP notification operations under a mail backend that cannot deliver.

``care/utils/tests/test_mail_classification.py`` fixes the boundary's mapping.
This fixes what the *operations* do with it, through the registered handler that
Cloud Tasks and Celery both call -- because the classification is only worth
anything if it survives the call chain the deployment actually uses.

The ES-07 failure is the first test: a Cloud Run container with Django's default
``EMAIL_HOST=localhost`` and no relay anywhere, which retried ten times over the
queue's 24-hour window for a send that could never succeed
(``unresolved-items.md`` N2).
"""

from unittest.mock import patch

from django.core import mail
from django.test import override_settings

from care.emr.tasks.totp import send_totp_enabled_email
from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError
from care.utils.tasks.registry import get_task
from care.utils.tests.base import CareAPITestBase

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"


def refuse_connection():
    """Patch the SMTP backend so opening a connection is refused, as it is on GCP."""
    return patch(
        "django.core.mail.backends.smtp.EmailBackend.open",
        side_effect=ConnectionRefusedError(111, "Connection refused"),
    )


class TotpEmailFailureTests(CareAPITestBase):
    def setUp(self):
        self.user = self.create_user(email="totp-probe@example.com")

    # --- the ES-07 failure ----------------------------------------------

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="localhost")
    def test_an_unconfigured_relay_fails_permanently(self):
        with refuse_connection(), self.assertRaises(PermanentTaskError) as caught:
            send_totp_enabled_email(self.user.id)
        self.assertIn("No mail relay is configured", str(caught.exception))

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="localhost")
    def test_the_registered_handler_reaches_the_same_verdict(self):
        # The path a Cloud Tasks delivery takes: name -> registry -> handler.
        # A classification the registered entry point loses is no classification.
        handler = get_task("send_totp_enabled_email").handler
        with refuse_connection(), self.assertRaises(PermanentTaskError):
            handler(user_id=self.user.id)

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="localhost")
    def test_the_worker_ends_the_delivery_instead_of_asking_again(self):
        from django.test import RequestFactory

        from care.utils.tasks.envelope import TASK_ENVELOPE_VERSION
        from care.utils.tasks.views import execute_task

        body = {
            "version": TASK_ENVELOPE_VERSION,
            "task": "send_totp_enabled_email",
            "payload": {"user_id": self.user.id},
        }
        request = RequestFactory().post(
            "/internal/tasks/execute/",
            data=body,
            content_type="application/json",
        )
        with refuse_connection():
            response = execute_task(request)

        # 2xx is the only answer Cloud Tasks reads as "do not redeliver". This
        # assertion is the whole of N2: before it, this was a 500 and the queue
        # kept coming back.
        self.assertLess(response.status_code, 300)
        self.assertNotEqual(response.status_code, 204)

    # --- the case that must still come back ------------------------------

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="smtp.relay.example")
    def test_a_real_relay_that_is_down_stays_retryable(self):
        # The negative control. If this ever becomes permanent, an outage at the
        # provider silently discards every notification raised during it.
        with refuse_connection(), self.assertRaises(RetryableTaskError):
            send_totp_enabled_email(self.user.id)

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="smtp.relay.example")
    def test_the_worker_asks_for_a_retry_for_a_relay_outage(self):
        from django.test import RequestFactory

        from care.utils.tasks.envelope import TASK_ENVELOPE_VERSION
        from care.utils.tasks.views import execute_task

        request = RequestFactory().post(
            "/internal/tasks/execute/",
            data={
                "version": TASK_ENVELOPE_VERSION,
                "task": "send_totp_enabled_email",
                "payload": {"user_id": self.user.id},
            },
            content_type="application/json",
        )
        with refuse_connection():
            response = execute_task(request)
        self.assertEqual(response.status_code, 503)

    # --- dev, which has no relay and must keep working -------------------

    @override_settings(EMAIL_BACKEND=CONSOLE_BACKEND, EMAIL_HOST="localhost")
    def test_the_console_backend_completes_the_task(self):
        # What Cloud Run dev runs (`DJANGO_EMAIL_BACKEND=console`): no relay, no
        # failure, the rendered message written to stdout for Cloud Logging.
        send_totp_enabled_email(self.user.id)

    def test_a_deliverable_send_still_delivers(self):
        # The locmem backend of the test settings: nothing about classifying
        # failures may change what a successful send does.
        mail.outbox.clear()
        send_totp_enabled_email(self.user.id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_a_missing_user_is_still_permanent(self):
        # Unchanged by this task, asserted because the send boundary now sits
        # between this failure and the caller.
        with self.assertRaises(PermanentTaskError):
            send_totp_enabled_email(self.user.id + 10_000)
