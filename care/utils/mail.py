"""
The mail-provider boundary: one send, classified.

ADR-0003 requires retry policy to follow *operation* failure semantics rather
than the exception names a provider happens to raise, and requires the mapping
to be explicit. ``care.emr.reports.report_utils`` does this for object storage.
This module does it for email, and for the same reason: a task definition should
not import ``smtplib``, and the decision to retry should not depend on which
backend ``EMAIL_BACKEND`` names.

Why it exists at all. Cloud Run containers run no SMTP server, and
``EMAIL_HOST`` defaults to ``localhost``, so a deployed environment that has not
been given a relay raises ``ConnectionRefusedError`` on every send. Before this
module that reached the task view unclassified, which means retryable: ES-07
watched two task names retry ten times each over a queue window measured in
hours, for a send that could not have succeeded on any attempt
(``unresolved-items.md`` N2). The send still fails, and is still logged in full;
it is simply not asked for again.

What is deliberately *not* claimed. Django's SMTP backend surfaces the same
exception types for faults with opposite retry semantics, and where the
exception alone cannot separate them this module leaves the failure
unclassified rather than guessing:

``ConnectionRefusedError`` from a real relay
    A relay that is down refuses connections exactly as an absent one does.
    Only a connection failure against a host that cannot be a relay for this
    process -- an empty ``EMAIL_HOST``, or a loopback address, in a
    configuration where CARE ships no MTA -- is read as a configuration fault.
    A deployment that really does run a local submission agent is unaffected
    while that agent answers, and loses only the retries of a send it was going
    to fail anyway.

``SMTPException`` carrying no response code
    Left to propagate. The view treats an unclassified exception as retryable,
    which is the safer default, and the queue's attempt limit bounds it. Note
    that ``smtplib.SMTPException`` derives from ``OSError``, so it has to be
    excluded from the socket case below rather than merely ordered after it.

A malformed message
    Django 6.0 made ``BadHeaderError`` an alias of ``ValueError`` rather than a
    class of its own, so a header injection is no longer distinguishable from
    any other ``ValueError`` a caller might raise. Classifying on it would have
    marked every ``ValueError`` in the send path permanent, which is how this
    module's own tests caught it. Left unclassified instead.

Anything that is not an SMTP fault
    A template that fails to render, or a bug in the caller, is not a mail
    failure and is not reclassified here.
"""

import logging
import smtplib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage

from care.utils.tasks.exceptions import PermanentTaskError, RetryableTaskError

logger = logging.getLogger(__name__)

#: ``EMAIL_HOST`` values that cannot name a mail relay reachable by this
#: process. CARE's images run no MTA and its deployments provision none, so a
#: connection failure against one of these is a missing configuration rather
#: than an unavailable service. An empty host is included: ``smtplib`` resolves
#: it to the local host.
UNCONFIGURED_SMTP_HOSTS = frozenset(
    {
        "",
        "localhost",
        "localhost.localdomain",
        "127.0.0.1",
        "::1",
        "[::1]",
        "0.0.0.0",  # noqa: S104 -- compared against, never bound
    }
)

#: The first response digit that means "and do not try this again". SMTP uses
#: 4xx for temporary negative completion and 5xx for permanent (RFC 5321 4.2.1),
#: which is exactly the distinction ADR-0003 asks for, so it is used rather than
#: reinvented.
_PERMANENT_SMTP_STATUS = 500


def send_email_message(message: EmailMessage) -> int:
    """
    Send ``message``, translating provider failures into CARE task errors.

    Returns the number of messages sent, as ``EmailMessage.send`` does. Raises
    :class:`~care.utils.tasks.exceptions.PermanentTaskError` for a failure that
    a later attempt cannot fix, :class:`RetryableTaskError` for one that it may,
    and re-raises anything it cannot honestly place.
    """
    try:
        return message.send()
    except Exception as exc:  # translated, never swallowed
        classified = _classify(exc)
        if classified is None:
            raise
        raise classified from exc


def _classify(  # noqa: PLR0911 -- one return per row of the mapping, by design
    exc: Exception,
) -> PermanentTaskError | RetryableTaskError | None:
    """
    Return the CARE error for ``exc``, or ``None`` to leave it unclassified.

    Written as a flat sequence of guarded returns rather than collapsed into a
    lookup: the order is part of the meaning -- ``smtplib`` puts specific faults
    underneath general ones, and ``SMTPException`` derives from ``OSError`` --
    and each branch's comment is the argument for the row it decides.
    """
    # Configuration that cannot produce a working backend at all. Django raises
    # these while building the connection, before any network operation.
    if isinstance(exc, ImproperlyConfigured | ImportError):
        return PermanentTaskError(f"Email backend is not usable: {exc}")

    # Every recipient was rejected. Each entry is (code, response); a 5xx means
    # the address is refused outright, a 4xx means "not now".
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        if codes and all(code >= _PERMANENT_SMTP_STATUS for code in codes):
            return PermanentTaskError(f"Recipients refused permanently: {codes}")
        return RetryableTaskError(f"Recipients refused temporarily: {codes}")

    # Credentials, or an authentication mechanism the relay will not accept.
    # Retrying with the same configuration reproduces it exactly.
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return PermanentTaskError(f"Mail relay rejected authentication: {exc}")

    # The relay does not implement something this send requires -- SMTPUTF8 for
    # an internationalized address, for instance. A capability does not appear
    # on retry.
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return PermanentTaskError(f"Mail relay does not support this message: {exc}")

    # Everything else that carries an SMTP response code, including
    # SMTPSenderRefused, SMTPDataError, SMTPHeloError and SMTPConnectError:
    # split on the code, which is what the code is for.
    if isinstance(exc, smtplib.SMTPResponseException):
        if exc.smtp_code >= _PERMANENT_SMTP_STATUS:
            return PermanentTaskError(f"Mail relay refused the message: {exc}")
        return RetryableTaskError(f"Mail relay deferred the message: {exc}")

    # The connection dropped mid-conversation. Nothing about the message is
    # implicated.
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return RetryableTaskError(f"Mail relay disconnected: {exc}")

    # Any remaining SMTP fault carries neither a response code nor a socket
    # cause, so there is nothing here to decide on. `smtplib.SMTPException`
    # derives from `OSError`, so it must be excluded explicitly: ordering alone
    # would let the socket case below claim it and read the configured host as
    # evidence about a failure that never reached a socket.
    if isinstance(exc, smtplib.SMTPException):
        return None

    # Socket-level failure, and by here it can only be one: refused,
    # unreachable, timed out, unresolvable.
    if isinstance(exc, OSError):
        host = _configured_smtp_host()
        if host is not None and host in UNCONFIGURED_SMTP_HOSTS:
            return PermanentTaskError(
                f"No mail relay is configured: EMAIL_HOST={host!r} cannot be "
                f"reached by this process and no attempt will succeed until "
                f"the environment is given one ({exc})"
            )
        return RetryableTaskError(f"Mail relay is unreachable: {exc}")

    return None


def _configured_smtp_host() -> str | None:
    """
    The SMTP host CARE is configured to use, or ``None`` if it is not using SMTP.

    Both values are read at call time through the lazy settings object rather
    than captured at import, so an ``override_settings`` in a test, or a
    management command that selects a different backend, is seen.
    """
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if not backend.endswith("smtp.EmailBackend"):
        # console, filebased, locmem, dummy and third-party backends do not
        # connect to EMAIL_HOST, so its value says nothing about them.
        return None
    return str(getattr(settings, "EMAIL_HOST", "") or "").strip().lower()
