"""
TOTP notification emails as reusable operations plus thin Celery wrappers.

The email bodies, templates and provider are unchanged by ADR-0003. What
changed is that sending no longer requires Celery: each operation is an
ordinary function, and the Celery task is a delegator that exists so the
traditional worker keeps serving the same task names.

Both operations take a ``user_id`` and reload the user from PostgreSQL. The
address and display name used to travel in the payload; carrying an identifier
instead keeps personal data out of the queue and out of Cloud Tasks request
bodies, and means a task executed after a rename sends to the current address.
"""

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from care.users.models import User
from care.utils.tasks.exceptions import PermanentTaskError

TOTP_ENABLED_SUBJECT = "Two-Factor Authentication Enabled"
TOTP_DISABLED_SUBJECT = "Two-Factor Authentication Disabled"


def _get_user(user_id: int) -> User:
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist as e:
        # A deleted user cannot be notified by any number of retries.
        msg = f"User {user_id} does not exist"
        raise PermanentTaskError(msg) from e


def _send(user: User, subject: str, template_path: str, timestamp_key: str) -> None:
    context = {
        "username": user.username,
        "email": user.email,
        timestamp_key: timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    message = EmailMessage(
        subject,
        render_to_string(template_path, context),
        settings.DEFAULT_FROM_EMAIL,
        (user.email,),
    )
    message.content_subtype = "html"
    message.send()


def send_totp_enabled_email(user_id: int) -> None:
    """Notify a user that TOTP was enabled on their account."""
    user = _get_user(user_id)
    _send(
        user,
        TOTP_ENABLED_SUBJECT,
        settings.TOTP_ENABLED_EMAIL_TEMPLATE_PATH,
        "enabled_at",
    )


def send_totp_disabled_email(user_id: int) -> None:
    """Notify a user that TOTP was disabled on their account."""
    user = _get_user(user_id)
    _send(
        user,
        TOTP_DISABLED_SUBJECT,
        settings.TOTP_DISABLED_EMAIL_TEMPLATE_PATH,
        "disabled_at",
    )


# `autoretry_for` was previously `(Exception,)`, which re-sent the email when
# anything failed after the SMTP handoff (recorded as B7). It is narrowed to
# transient failures only: a permanent failure, such as a missing user, is not
# improved by retrying and a duplicate email is a real user-visible cost.
_RETRY = {
    "autoretry_for": (ConnectionError, TimeoutError, OSError),
    "retry_kwargs": {"max_retries": 3},
    "expires": 10 * 60,
}


@shared_task(name="care.emr.tasks.totp.send_totp_enabled_email", **_RETRY)
def send_totp_enabled_email_task(user_id: int) -> None:
    """Celery wrapper. Keeps the pre-ADR-0003 task name."""
    send_totp_enabled_email(user_id)


@shared_task(name="care.emr.tasks.totp.send_totp_disabled_email", **_RETRY)
def send_totp_disabled_email_task(user_id: int) -> None:
    """Celery wrapper. Keeps the pre-ADR-0003 task name."""
    send_totp_disabled_email(user_id)
