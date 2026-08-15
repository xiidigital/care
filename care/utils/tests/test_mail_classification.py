"""
The mail boundary: which send failures are worth trying again.

Every test here fixes one row of the mapping ``care/utils/mail.py`` declares,
because the mapping is the whole point -- an over-classified permanent failure
loses a message that a second attempt would have delivered, and an
under-classified one is what ES-07 watched retry ten times against a relay that
did not exist (``unresolved-items.md`` N2).

Failures are injected at the backend rather than mocked at ``EmailMessage.send``
wherever the real backend can produce them, so the tests exercise the same code
path a deployed send takes.
"""

import smtplib
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.mail.message import BadHeaderError
from django.test import SimpleTestCase, override_settings

from care.utils.mail import send_email_message
from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError

#: Django 6.0 aliases this to plain ``ValueError`` rather than defining a class.
#: Named here because the classifier's decision *not* to branch on it depends on
#: that being true: if a future Django restores a distinct class, the assertion
#: below fails and the boundary documented in ``care/utils/mail.py`` can be
#: revisited.
BAD_HEADER_ERROR_IS_VALUE_ERROR = BadHeaderError is ValueError

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOCMEM_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def message():
    return EmailMessage("subject", "body", "from@example.com", ("to@example.com",))


class SendFailureClassificationTests(SimpleTestCase):
    """Each failure is raised from the backend and the resulting class asserted."""

    def send_raising(self, exc):
        with patch(
            "django.core.mail.backends.locmem.EmailBackend.send_messages",
            side_effect=exc,
        ):
            send_email_message(message())

    def assert_classified(self, exc, expected):
        with (
            override_settings(EMAIL_BACKEND=LOCMEM_BACKEND),
            self.assertRaises(expected) as caught,
        ):
            self.send_raising(exc)
        # The provider exception is kept as the cause, so the log written by
        # the task view still shows what actually happened.
        self.assertIs(caught.exception.__cause__, exc)

    # --- permanent ------------------------------------------------------

    def test_authentication_failure_is_permanent(self):
        self.assert_classified(
            smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials"),
            PermanentTaskError,
        )

    def test_an_unsupported_capability_is_permanent(self):
        self.assert_classified(
            smtplib.SMTPNotSupportedError("SMTPUTF8 not supported"),
            PermanentTaskError,
        )

    def test_recipients_refused_with_5xx_is_permanent(self):
        self.assert_classified(
            smtplib.SMTPRecipientsRefused({"to@example.com": (550, b"No such user")}),
            PermanentTaskError,
        )

    def test_a_5xx_response_is_permanent(self):
        self.assert_classified(
            smtplib.SMTPSenderRefused(553, b"Sender rejected", "from@example.com"),
            PermanentTaskError,
        )

    def test_an_unusable_backend_is_permanent(self):
        self.assert_classified(
            ImproperlyConfigured("EMAIL_BACKEND is not importable"),
            PermanentTaskError,
        )

    # --- transient ------------------------------------------------------

    def test_recipients_refused_with_4xx_is_retryable(self):
        self.assert_classified(
            smtplib.SMTPRecipientsRefused(
                {"to@example.com": (450, b"Mailbox busy, try again")}
            ),
            RetryableTaskError,
        )

    def test_a_4xx_response_is_retryable(self):
        self.assert_classified(
            smtplib.SMTPDataError(451, b"Local error in processing"),
            RetryableTaskError,
        )

    def test_a_dropped_connection_is_retryable(self):
        self.assert_classified(
            smtplib.SMTPServerDisconnected("connection closed"),
            RetryableTaskError,
        )

    def test_a_timeout_is_retryable(self):
        self.assert_classified(TimeoutError("timed out"), RetryableTaskError)

    # --- deliberately unclassified --------------------------------------

    def test_a_failure_that_is_not_a_mail_failure_is_left_alone(self):
        # A template bug is not a delivery problem and must not be dressed as
        # one: the view's unclassified default is what should handle it.
        with (
            override_settings(EMAIL_BACKEND=LOCMEM_BACKEND),
            self.assertRaises(ValueError),
        ):
            self.send_raising(ValueError("render failed"))

    def test_a_bare_smtp_exception_is_left_unclassified(self):
        # No response code, no socket cause: nothing here justifies a decision,
        # so the safer default (retry, bounded by the queue) is kept. It has to
        # be excluded from the socket case explicitly, because
        # `smtplib.SMTPException` derives from `OSError`.
        with (
            override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="localhost"),
            patch(
                "django.core.mail.backends.smtp.EmailBackend.send_messages",
                side_effect=smtplib.SMTPException("something went wrong"),
            ),
            self.assertRaises(smtplib.SMTPException),
        ):
            send_email_message(message())

    def test_a_malformed_header_cannot_be_told_from_any_other_value_error(self):
        # Django 6.0 made BadHeaderError an alias of ValueError, so branching on
        # it would classify every ValueError in the send path as permanent. The
        # alias is asserted rather than assumed: if it ever stops being one, the
        # classifier can start distinguishing them again.
        self.assertTrue(BAD_HEADER_ERROR_IS_VALUE_ERROR)


class UnreachableRelayTests(SimpleTestCase):
    """
    A refused connection means opposite things depending on where it was refused.

    This is the ES-07 failure and the one distinction the exception alone cannot
    make, so it is asserted from both sides.
    """

    def send_refused(self):
        with patch(
            "django.core.mail.backends.smtp.EmailBackend.open",
            side_effect=ConnectionRefusedError(111, "Connection refused"),
        ):
            send_email_message(message())

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="localhost")
    def test_the_django_default_host_is_a_missing_configuration(self):
        with self.assertRaises(PermanentTaskError) as caught:
            self.send_refused()
        self.assertIn("No mail relay is configured", str(caught.exception))

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="127.0.0.1")
    def test_a_loopback_address_is_the_same_fault(self):
        with self.assertRaises(PermanentTaskError):
            self.send_refused()

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="")
    def test_an_empty_host_is_the_same_fault(self):
        with self.assertRaises(PermanentTaskError):
            self.send_refused()

    @override_settings(EMAIL_BACKEND=SMTP_BACKEND, EMAIL_HOST="smtp.relay.example")
    def test_a_real_relay_that_is_down_stays_retryable(self):
        # The load-bearing negative case. A provider outage must not consume a
        # message; only the configuration fault is permanent.
        with self.assertRaises(RetryableTaskError):
            self.send_refused()

    @override_settings(EMAIL_BACKEND=CONSOLE_BACKEND, EMAIL_HOST="localhost")
    def test_the_host_is_ignored_when_the_backend_does_not_connect(self):
        # EMAIL_HOST is still `localhost` under the console backend and means
        # nothing there, so it must not colour an unrelated failure.
        with (
            patch(
                "django.core.mail.backends.console.EmailBackend.send_messages",
                side_effect=TimeoutError("timed out"),
            ),
            self.assertRaises(RetryableTaskError),
        ):
            send_email_message(message())


class CeleryRetryPolicyTests(SimpleTestCase):
    """
    The Celery half of the same mapping.

    ADR-0003 requires the classification to be expressed on every backend that
    can retry. The task view expresses it as an HTTP status; the Celery wrappers
    express it as ``autoretry_for``, and the two must not disagree about which
    failures come back.
    """

    def tasks(self):
        from care.emr.tasks.totp import (
            send_totp_disabled_email_task,
            send_totp_enabled_email_task,
        )

        return (send_totp_enabled_email_task, send_totp_disabled_email_task)

    def test_transient_failures_are_retried(self):
        for task in self.tasks():
            with self.subTest(task=task.name):
                self.assertIn(RetryableTaskError, task.autoretry_for)

    def test_permanent_failures_are_not(self):
        # Asserted by subclass rather than by identity: listing any superclass
        # of PermanentTaskError would retry it just as effectively as listing it.
        for task in self.tasks():
            for retried in task.autoretry_for:
                with self.subTest(task=task.name, retried=retried.__name__):
                    self.assertFalse(issubclass(PermanentTaskError, retried))

    def test_os_error_is_not_retried_wholesale(self):
        # The specific regression: `smtplib.SMTPException` derives from
        # `OSError`, so retrying OSError retries every SMTP fault including the
        # unconfigured relay this task exists to stop retrying.
        for task in self.tasks():
            with self.subTest(task=task.name):
                self.assertNotIn(OSError, task.autoretry_for)

    def test_retries_stay_bounded(self):
        # ADR-0003: "Infinite or uncontrolled retries are prohibited."
        for task in self.tasks():
            with self.subTest(task=task.name):
                self.assertGreater(task.retry_kwargs["max_retries"], 0)


class ConsoleBackendTests(SimpleTestCase):
    """
    The dev environment's backend has to keep working, unclassified and unraised.

    ``DJANGO_EMAIL_BACKEND=console`` is what lets Cloud Run dev complete email
    tasks with no relay at all (``unresolved-items.md`` N1), and this task must
    not have made a successful send look like a failure.
    """

    @override_settings(EMAIL_BACKEND=CONSOLE_BACKEND, EMAIL_HOST="localhost")
    def test_a_console_send_succeeds(self):
        self.assertEqual(send_email_message(message()), 1)

    @override_settings(EMAIL_BACKEND=LOCMEM_BACKEND)
    def test_a_locmem_send_succeeds_and_is_delivered(self):
        from django.core import mail

        mail.outbox.clear()
        self.assertEqual(send_email_message(message()), 1)
        self.assertEqual(len(mail.outbox), 1)
