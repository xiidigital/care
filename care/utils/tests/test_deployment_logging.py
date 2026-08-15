"""
The deployed logging configuration, asserted where it actually takes effect.

``LOGGING`` is not a value the application reads; it is a dictConfig applied
once, inside ``django.setup()``, to a process-global registry. Its defect was of
exactly that kind: ``disable_existing_loggers: True`` set ``disabled = True`` on
every logger object that existed when Django was set up, which silenced
``django.request`` -- the logger through which Django reports every unhandled
exception -- along with Celery's loggers and every logger built while the
settings modules imported. Records were dropped at ``Logger.handle()``, before
any handler ran, so nothing reached the root handler either and a 500 arrived in
Cloud Logging with no type, no message and no traceback
(``unresolved-items.md`` L2).

Nothing about that is observable from inside a test process configured by
``config.settings.test``: the suite's own logging is already applied, and
re-applying a different dictConfig would reconfigure the running test runner.
So each test starts a real Python process under
``DJANGO_SETTINGS_MODULE=config.settings.deployment`` and reads what it emitted.
That is the only place the answer is truthful.

These tests assert Python's logging behaviour under the deployed settings. They
do not test Cloud Logging, which is verified against the real environment and
recorded in ``docs/xii/architecture/inventory/runtime-and-deployment.md``.
"""

import os
import subprocess
import sys
import textwrap

from django.conf import settings
from django.test import SimpleTestCase

#: Parsed by `env.db()` and never connected to. Importing the deployment
#: settings requires DATABASE_URL and nothing else that a build or a test does
#: not already have -- the same property the image build relies on.
PLACEHOLDER_DATABASE_URL = "postgres://probe:probe@127.0.0.1:5432/probe"

PROBE_PRELUDE = """
import logging, sys

# Created before django.setup(), the way Celery's loggers and every logger built
# during settings import are.
before_setup = logging.getLogger("probe.before_setup")

import django
django.setup()
"""


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
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", PROBE_PRELUDE + textwrap.dedent(body)],
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


class ExistingLoggersSurviveSetupTests(SimpleTestCase):
    def test_no_logger_is_disabled_by_setup(self):
        stdout, _ = run_probe("""
            names = [
                "probe.before_setup",
                "config.settings.base",
                "celery",
                "celery.worker",
                "celery.beat",
                "django",
                "django.request",
            ]
            for name in names:
                print(f"{name}={logging.getLogger(name).disabled}")
        """)
        for line in stdout.strip().splitlines():
            name, _, disabled = line.partition("=")
            with self.subTest(logger=name):
                self.assertEqual(
                    disabled,
                    "False",
                    f"{name} was disabled by django.setup(); every record it "
                    f"receives is dropped before any handler sees it.",
                )

    def test_a_logger_created_before_setup_still_reaches_stderr(self):
        # The scheduler role's whole diagnostic surface: Celery builds its
        # loggers on import, long before Django is set up.
        _, stderr = run_probe("""
            before_setup.error("PROBE-BEFORE-SETUP")
            logging.getLogger("celery.worker").error("PROBE-CELERY")
        """)
        self.assertIn("PROBE-BEFORE-SETUP", stderr)
        self.assertIn("PROBE-CELERY", stderr)


class ExceptionsReachTheHandlerIntactTests(SimpleTestCase):
    def test_an_exception_carries_type_message_and_traceback(self):
        _, stderr = run_probe("""
            def raise_it():
                raise ConnectionRefusedError(111, "no mail relay is configured")

            try:
                raise_it()
            except ConnectionRefusedError:
                logging.getLogger("care.probe").exception("PROBE-HANDLER-FAILED")
        """)
        self.assertIn("PROBE-HANDLER-FAILED", stderr)
        self.assertIn("Traceback (most recent call last):", stderr)
        self.assertIn("in raise_it", stderr)
        self.assertIn("ConnectionRefusedError", stderr)
        self.assertIn("no mail relay is configured", stderr)

    def test_djangos_own_request_logger_reports_unhandled_exceptions(self):
        # The path a 500 actually takes. `django.request` was the logger the old
        # configuration disabled, so this is the regression that mattered most:
        # an unhandled view exception produced no log line at all.
        _, stderr = run_probe("""
            from django.core.handlers.exception import response_for_exception
            from django.test import RequestFactory

            request = RequestFactory().get("/probe/")
            try:
                raise RuntimeError("probe: handler exploded")
            except RuntimeError as exc:
                response = response_for_exception(request, exc)
            print(f"status={response.status_code}")
        """)
        self.assertIn("Internal Server Error: /probe/", stderr)
        self.assertIn("RuntimeError", stderr)
        self.assertIn("probe: handler exploded", stderr)
        self.assertIn("Traceback (most recent call last):", stderr)


class NoDuplicateOutputTests(SimpleTestCase):
    """
    Re-enabling the existing loggers must not also restore Django's own handlers.

    ``DEFAULT_LOGGING`` attaches two handlers to the ``django`` logger: a console
    handler gated on ``DEBUG`` being on, which would print a second copy of every
    record the root handler already printed, and ``mail_admins``, gated on DEBUG
    being off, which would make every 500 in a deployed environment attempt an
    SMTP connection there is no relay to answer (``unresolved-items.md`` N1).
    """

    def test_a_django_record_is_emitted_exactly_once(self):
        _, stderr = run_probe("""
            logging.getLogger("django.request").error("PROBE-ONCE")
            logging.getLogger("care.probe").error("PROBE-APP-ONCE")
        """)
        self.assertEqual(stderr.count("PROBE-ONCE"), 1)
        self.assertEqual(stderr.count("PROBE-APP-ONCE"), 1)

    def test_it_is_still_emitted_exactly_once_with_debug_on(self):
        # The local Celery and Beat containers run these settings with
        # DJANGO_DEBUG=true (docker/.local.env), which is precisely where
        # Django's DEBUG-gated console handler would double every line.
        _, stderr = run_probe(
            """
            logging.getLogger("django.request").error("PROBE-DEBUG-ONCE")
            """,
            DJANGO_DEBUG="true",
        )
        self.assertEqual(stderr.count("PROBE-DEBUG-ONCE"), 1)

    def test_no_admin_email_handler_is_attached_to_the_django_logger(self):
        # Asserted structurally as well as by output count: with ADMINS empty
        # the handler is a silent no-op, so counting lines would not catch it
        # being attached, and it would start sending the day ADMINS is set.
        stdout, _ = run_probe("""
            handlers = logging.getLogger("django").handlers
            print(",".join(type(h).__name__ for h in handlers))
            print(f"propagate={logging.getLogger('django').propagate}")
        """)
        self.assertNotIn("AdminEmailHandler", stdout)
        self.assertIn("StreamHandler", stdout)
        self.assertIn("propagate=False", stdout)


class RuntimeSummarySurvivesTests(SimpleTestCase):
    def test_the_startup_role_summary_is_emitted(self):
        # ES-06's startup line, which had to resolve its logger lazily to
        # survive the old configuration. It must still appear now that the
        # workaround is no longer load-bearing.
        _, stderr = run_probe("""
            from config.runtime import log_runtime_summary
            log_runtime_summary()
        """)
        self.assertIn("CARE runtime:", stderr)
        self.assertEqual(stderr.count("CARE runtime:"), 1)
