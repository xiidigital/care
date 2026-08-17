"""
Email configuration is generic, and console delivery is valid everywhere.

CARE separates *application email generation* -- rendering a message,
dispatching the work, executing the task, reporting the failure -- from
*external email delivery*, which is an environment-specific operational choice.
These tests assert the first half is complete and portable, and that the second
is genuinely optional: no environment requires a relay, no environment guard
rejects the console backend, and this repository names no provider
(``unresolved-items.md`` N1).

Most of this is not observable from inside a test process. ``EMAIL_BACKEND``,
``EMAIL_USE_TLS`` and the rest are read from the environment while the settings
module imports, so ``override_settings`` would assert the override rather than
the parsing. Each test that cares therefore starts a real process under
``DJANGO_SETTINGS_MODULE=config.settings.deployment`` -- the module staging and
production both derive from -- and reads what it resolved.

Nothing here sends mail to anyone. The console backend writes to stdout, and the
SMTP assertions read configuration without opening a socket.
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.mail import EmailMessage
from django.test import SimpleTestCase

from care.utils.mail import send_email_message

#: Parsed by `env.db()` and never connected to.
PLACEHOLDER_DATABASE_URL = "postgres://probe:probe@127.0.0.1:5432/probe"

CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def run_probe(body, **extra_env):
    """Run ``body`` under the deployment settings; return (stdout, stderr)."""
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.deployment",
        "DATABASE_URL": PLACEHOLDER_DATABASE_URL,
        # The probe must see the settings module, not a developer's .env.
        "DJANGO_READ_DOT_ENV_FILE": "false",
        "PYTHONPATH": str(settings.BASE_DIR),
        **extra_env,
    }
    # Inherited values would silently satisfy assertions about defaults.
    for name in (
        "DJANGO_EMAIL_BACKEND",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_USER",
        "EMAIL_PASSWORD",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
        "EMAIL_FROM",
    ):
        if name not in extra_env:
            env.pop(name, None)

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            "import django; django.setup()\n" + textwrap.dedent(body),
        ],
        capture_output=True,
        text=True,
        cwd=str(settings.BASE_DIR),
        env=env,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        msg = f"probe failed ({completed.returncode}):\n{completed.stderr}"
        raise AssertionError(msg)
    return completed.stdout, completed.stderr


def probe_email_settings(**extra_env):
    """Resolved email settings under the deployment module, as a dict."""
    stdout, _ = run_probe(
        """
        from django.conf import settings

        for name in (
            "EMAIL_BACKEND",
            "EMAIL_HOST",
            "EMAIL_PORT",
            "EMAIL_HOST_USER",
            "EMAIL_USE_TLS",
            "EMAIL_USE_SSL",
            "DEFAULT_FROM_EMAIL",
        ):
            print(f"{name}={getattr(settings, name)!r}")
        """,
        **extra_env,
    )
    return dict(
        line.partition("=")[::2] for line in stdout.strip().splitlines() if "=" in line
    )


class ConsoleBackendIsValidInDeployedEnvironmentsTests(SimpleTestCase):
    """
    The console backend is a configuration, not a stopgap.

    ``config.settings.deployment`` is the module both staging and production
    load. If either were going to reject console mode -- with a system check, an
    ``ImproperlyConfigured``, or a startup guard -- this is where it would
    happen, so it is asserted against that module rather than against the test
    settings.
    """

    def test_the_deployment_settings_accept_the_console_backend(self):
        resolved = probe_email_settings(DJANGO_EMAIL_BACKEND=CONSOLE_BACKEND)
        self.assertEqual(resolved["EMAIL_BACKEND"], repr(CONSOLE_BACKEND))

    def test_no_system_check_rejects_the_console_backend(self):
        # A deployment fails on a check error, so a check that disliked console
        # mode would make it a blocker by another route.
        stdout, _ = run_probe(
            """
            from django.core.checks import run_checks

            errors = [m for m in run_checks() if m.is_serious()]
            print(f"serious={len(errors)}")
            for message in errors:
                print(message)
            """,
            DJANGO_EMAIL_BACKEND=CONSOLE_BACKEND,
        )
        self.assertIn("serious=0", stdout)

    def test_console_mode_needs_no_relay_and_no_credential(self):
        resolved = probe_email_settings(DJANGO_EMAIL_BACKEND=CONSOLE_BACKEND)
        # EMAIL_HOST keeps its unreachable default and nothing objects, because
        # nothing consults it: this is the whole point of the reclassification.
        self.assertEqual(resolved["EMAIL_HOST"], repr("localhost"))
        self.assertEqual(resolved["EMAIL_HOST_USER"], repr(""))

    def test_a_message_is_rendered_and_written_to_stdout(self):
        # Generation end to end under the deployed settings: the message is
        # built, handed to the backend and emitted where a log sink collects it.
        stdout, _ = run_probe(
            """
            from django.core.mail import EmailMessage

            sent = EmailMessage(
                subject="PROBE-SUBJECT",
                body="PROBE-BODY",
                to=["probe@example.invalid"],
            ).send()
            print(f"sent={sent}")
            """,
            DJANGO_EMAIL_BACKEND=CONSOLE_BACKEND,
        )
        self.assertIn("sent=1", stdout)
        self.assertIn("PROBE-SUBJECT", stdout)
        self.assertIn("PROBE-BODY", stdout)
        self.assertIn("probe@example.invalid", stdout)

    def test_the_mail_boundary_reports_success_rather_than_classifying_it(self):
        # In-process counterpart of the probe above, through the boundary a task
        # actually calls. A send that does not reach a relay must still be a
        # success to the caller -- not a PermanentTaskError, not a retry.
        locmem = "django.core.mail.backends.locmem.EmailBackend"
        with self.settings(EMAIL_BACKEND=locmem):
            sent = send_email_message(
                EmailMessage(
                    subject="operational",
                    body="rendered",
                    to=["probe@example.invalid"],
                )
            )
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "operational")


class GenericSmtpSettingsTests(SimpleTestCase):
    """
    The settings an operator would use later, read from generic names only.

    No value below is a real one, and none is committed anywhere: they exist for
    the length of one subprocess to prove the variables are wired.
    """

    def test_transport_settings_come_from_generic_environment_variables(self):
        # DJANGO_EMAIL_BACKEND is absent, not empty. `django_email_backend = ""`
        # in the infrastructure means *omit the variable* -- config.tf sets it
        # only when non-empty -- and that distinction matters: django-environ
        # returns the empty string for a variable that is set to one, which is
        # not a usable backend path. The module never emits that; the test
        # reproduces what the module actually does.
        resolved = probe_email_settings(
            EMAIL_HOST="relay.example.invalid",
            EMAIL_PORT="2525",
            EMAIL_USER="probe-user",
            EMAIL_FROM="CARE <no-reply@example.invalid>",
        )
        self.assertEqual(resolved["EMAIL_BACKEND"], repr(SMTP_BACKEND))
        self.assertEqual(resolved["EMAIL_HOST"], repr("relay.example.invalid"))
        self.assertEqual(resolved["EMAIL_HOST_USER"], repr("probe-user"))
        self.assertEqual(
            resolved["DEFAULT_FROM_EMAIL"], repr("CARE <no-reply@example.invalid>")
        )

    def test_the_port_is_an_integer(self):
        resolved = probe_email_settings(EMAIL_PORT="2525")
        self.assertEqual(resolved["EMAIL_PORT"], "2525")

    def test_tls_can_be_turned_off(self):
        # The regression this asserts: read with `env(...)` the value arrives as
        # the string "false", and every non-empty string is truthy, so TLS could
        # not be disabled and implicit SSL could not be reached.
        resolved = probe_email_settings(EMAIL_USE_TLS="false")
        self.assertEqual(resolved["EMAIL_USE_TLS"], "False")

    def test_implicit_tls_is_selectable(self):
        resolved = probe_email_settings(EMAIL_USE_TLS="false", EMAIL_USE_SSL="true")
        self.assertEqual(resolved["EMAIL_USE_TLS"], "False")
        self.assertEqual(resolved["EMAIL_USE_SSL"], "True")

    def test_a_deployed_environment_defaults_to_starttls(self):
        # Unset, not off: a deployed environment that does talk to a relay
        # should do so over TLS. The default is what changed shape, not the
        # policy.
        resolved = probe_email_settings()
        self.assertEqual(resolved["EMAIL_USE_TLS"], "True")
        self.assertEqual(resolved["EMAIL_USE_SSL"], "False")


class NoProviderIsEncodedTests(SimpleTestCase):
    """
    CARE is a public repository, and the runtime configuration must stay
    provider-neutral. A provider is an operator's deployment decision; encoding
    one here would make it everyone's.

    Scoped to the two trees that would actually configure delivery -- the
    settings modules and the infrastructure. Prose elsewhere may discuss
    providers as options, and the Pipfile carries an upstream Anymail extra that
    no settings module activates.
    """

    #: Substrings, matched case-insensitively. Deliberately narrow: each names a
    #: provider or a provider's relay, so a match is a real selection.
    PROVIDER_MARKERS = (
        "sendgrid",
        "mailgun",
        "amazon_ses",
        "amazon-ses",
        "anymail",
        "postmark",
        "sparkpost",
        "mandrill",
        "brevo",
        "sendinblue",
        "mailjet",
        "smtp.gmail.com",
        "email-smtp.",
    )

    SCANNED = (
        ("config/settings", ("*.py",)),
        ("infrastructure", ("*.tf", "*.tfvars.example", "*.sh", "*.md")),
    )

    def scanned_files(self):
        for subdir, patterns in self.SCANNED:
            root = Path(settings.BASE_DIR) / subdir
            for pattern in patterns:
                yield from root.rglob(pattern)

    def test_no_email_provider_is_named(self):
        for path in self.scanned_files():
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for marker in self.PROVIDER_MARKERS:
                with self.subTest(path=str(path), marker=marker):
                    self.assertNotIn(
                        marker,
                        text,
                        f"{path} names an email provider. A provider is an "
                        f"operational choice and belongs outside this "
                        f"repository (unresolved-items.md N1).",
                    )

    #: A value that is visibly not one. Documentation has to be able to show the
    #: shape of a setting without the scan reading the illustration as a leak.
    PLACEHOLDER = re.compile(
        r"""^(\.{2,}|<.*>|)$|REPLACE_ME|example|your-|probe-""", re.IGNORECASE
    )

    def test_no_email_credential_is_committed(self):
        # An assignment of a literal to a credential-shaped name. A reference to
        # the *variable* -- EMAIL_PASSWORD in an optional_secrets list, or in a
        # comment -- is what the infrastructure is supposed to contain, so only
        # a value being given is a finding.
        assignment = re.compile(
            r"""(EMAIL_PASSWORD|EMAIL_HOST_PASSWORD|EMAIL_USER|EMAIL_HOST_USER)"""
            r"""\s*[:=]\s*["']([^"']*)["']""",
            re.IGNORECASE,
        )
        for path in self.scanned_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in assignment.finditer(text):
                if self.PLACEHOLDER.search(match.group(2)):
                    continue
                # `env("EMAIL_PASSWORD", default="")` reads one; it does not set
                # one. The quoted group there is the variable name itself.
                line = text[text.rfind("\n", 0, match.start()) + 1 : match.end()]
                if "env(" in line or "env.str(" in line:
                    continue
                self.fail(
                    f"{path} assigns an email credential: {match.group(0)!r}. "
                    f"Credentials belong in the platform secret store."
                )

    def test_the_infrastructure_requires_no_email_secret(self):
        # `required_secrets` is the set every environment must have provisioned
        # before it can start. An email secret in it would make external
        # delivery a deployment precondition again.
        secrets_tf = (
            Path(settings.BASE_DIR)
            / "infrastructure/terraform/modules/care-environment/secrets.tf"
        ).read_text(encoding="utf-8")
        required = secrets_tf.partition("required_secrets = {")[2].partition(
            "optional_secrets = {"
        )[0]
        self.assertNotIn("EMAIL", required.upper())
