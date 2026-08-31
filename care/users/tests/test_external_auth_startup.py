"""Startup behaviour of the two optional authentication adapters (ADR-0010).

`override_settings` cannot answer these questions. Both adapters are validated
while `config/settings/base.py` is being imported, and the routes they mount are
decided once at URLConf construction. A real process started with a real
environment is the only honest instrument -- the same reasoning as
`care/utils/tests/test_ratelimit_modes.py`.
"""

import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

EXCHANGE_PATHS = (
    "/api/v1/auth/firebase/patient/exchange/",
    "/api/v1/auth/keycloak/workforce/exchange/",
    "/api/v1/auth/keycloak/patient/exchange/",
)

COMPLETE_KEYCLOAK_ENV = {
    "KEYCLOAK_ENABLED": "true",
    "KEYCLOAK_ISSUER_URL": "https://identity.example/realms/care",
    "KEYCLOAK_WORKFORCE_CLIENT_ID": "care-workforce",
    "KEYCLOAK_WORKFORCE_CLIENT_SECRET": "workforce-secret",
    "KEYCLOAK_PATIENT_CLIENT_ID": "care-patient",
    "KEYCLOAK_PATIENT_CLIENT_SECRET": "patient-secret",
    "KEYCLOAK_PUBLIC_BASE_URL": "https://care.example",
}


class ExternalAuthStartupTests(SimpleTestCase):
    def run_check(self, **extra_env):
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.local",
            **extra_env,
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, "manage.py", "check"],
            cwd=str(settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return result, result.stdout + result.stderr

    # -- disabled -----------------------------------------------------------

    def test_startup_needs_no_provider_configuration_at_all(self):
        result, output = self.run_check(
            KEYCLOAK_ENABLED="false", FIREBASE_AUTH_ENABLED="false"
        )

        self.assertEqual(result.returncode, 0, output)

    def test_disabled_startup_contacts_no_provider(self):
        """A hostile issuer URL is inert while the flag is off."""
        result, output = self.run_check(
            KEYCLOAK_ENABLED="false",
            KEYCLOAK_ISSUER_URL="https://127.0.0.1:1/realms/unreachable",
            FIREBASE_AUTH_ENABLED="false",
        )

        self.assertEqual(result.returncode, 0, output)

    # -- enabled but incomplete ---------------------------------------------

    def test_enabled_keycloak_without_configuration_refuses_to_start(self):
        result, output = self.run_check(KEYCLOAK_ENABLED="true")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KEYCLOAK_ISSUER_URL", output)

    def test_enabled_keycloak_missing_one_secret_refuses_to_start(self):
        env = dict(COMPLETE_KEYCLOAK_ENV, KEYCLOAK_PATIENT_CLIENT_SECRET="")
        result, output = self.run_check(**env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KEYCLOAK_PATIENT_CLIENT_SECRET", output)

    def test_startup_failure_never_prints_a_configured_secret(self):
        env = dict(COMPLETE_KEYCLOAK_ENV, KEYCLOAK_ISSUER_URL="")
        result, output = self.run_check(**env)

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("workforce-secret", output)
        self.assertNotIn("patient-secret", output)

    def test_enabled_keycloak_rejects_an_insecure_issuer(self):
        env = dict(
            COMPLETE_KEYCLOAK_ENV,
            KEYCLOAK_ISSUER_URL="http://identity.example/realms/care",
        )
        result, output = self.run_check(**env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KEYCLOAK_ISSUER_URL", output)

    def test_enabled_firebase_without_a_project_refuses_to_start(self):
        result, output = self.run_check(FIREBASE_AUTH_ENABLED="true")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIREBASE_AUTH_PROJECT_ID", output)

    def test_enabled_firebase_rejects_an_empty_sms_policy(self):
        result, output = self.run_check(
            FIREBASE_AUTH_ENABLED="true",
            FIREBASE_AUTH_PROJECT_ID="care-dev",
            FIREBASE_AUTH_SMS_COUNTRY_CODES="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIREBASE_AUTH_SMS_COUNTRY_CODES", output)

    # -- enabled and complete -----------------------------------------------

    def test_complete_keycloak_configuration_starts_without_a_runtime(self):
        """Dormant-to-active is configuration only; no server is contacted."""
        result, output = self.run_check(**COMPLETE_KEYCLOAK_ENV)

        self.assertEqual(result.returncode, 0, output)

    def test_complete_firebase_configuration_starts(self):
        result, output = self.run_check(
            FIREBASE_AUTH_ENABLED="true", FIREBASE_AUTH_PROJECT_ID="care-dev"
        )

        self.assertEqual(result.returncode, 0, output)

    # -- route gating -------------------------------------------------------

    def run_route_probe(self, *paths, **extra_env):
        """Resolve paths inside a freshly started process.

        The URLConf is built once, at import, from the flags the process was
        started with. `override_settings` cannot move a route in or out of it,
        so the question can only be asked of a real process.
        """
        script = (
            "import django;django.setup()\n"
            "from django.urls import Resolver404, resolve\n"
            f"for path in {list(paths)!r}:\n"
            "    try:\n"
            "        resolve(path)\n"
            "        print('MOUNTED', path)\n"
            "    except Resolver404:\n"
            "        print('ABSENT', path)\n"
        )
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.local",
            **extra_env,
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=str(settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_disabled_providers_mount_no_exchange_routes(self):
        output = self.run_route_probe(
            *EXCHANGE_PATHS, KEYCLOAK_ENABLED="false", FIREBASE_AUTH_ENABLED="false"
        )

        self.assertNotIn("MOUNTED", output)
        self.assertEqual(output.count("ABSENT"), len(EXCHANGE_PATHS))

    def test_enabled_providers_mount_exactly_their_own_routes(self):
        output = self.run_route_probe(
            *EXCHANGE_PATHS,
            **COMPLETE_KEYCLOAK_ENV,
            FIREBASE_AUTH_ENABLED="true",
            FIREBASE_AUTH_PROJECT_ID="care-dev",
        )

        for path in EXCHANGE_PATHS:
            self.assertIn(f"MOUNTED {path}", output)

    def test_enabling_firebase_alone_leaves_keycloak_absent(self):
        output = self.run_route_probe(
            *EXCHANGE_PATHS,
            KEYCLOAK_ENABLED="false",
            FIREBASE_AUTH_ENABLED="true",
            FIREBASE_AUTH_PROJECT_ID="care-dev",
        )

        self.assertIn("MOUNTED /api/v1/auth/firebase/patient/exchange/", output)
        self.assertIn("ABSENT /api/v1/auth/keycloak/workforce/exchange/", output)
        self.assertIn("ABSENT /api/v1/auth/keycloak/patient/exchange/", output)

    def test_legacy_patient_otp_login_survives_both_adapters_being_off(self):
        output = self.run_route_probe(
            "/api/v1/otp/login/",
            "/api/v1/otp/send/",
            KEYCLOAK_ENABLED="false",
            FIREBASE_AUTH_ENABLED="false",
        )

        self.assertNotIn("ABSENT", output)

    def test_enabling_a_provider_never_removes_an_existing_login_path(self):
        """A provider is one more way in, never a replacement.

        ADR-0010 §7 removes a legacy path only once its replacement is proven
        and clients have migrated -- a separate, deliberate decision. Switching
        Firebase or Keycloak on is not that decision, so every existing CARE
        login route must still be mounted next to the new ones.
        """
        legacy_paths = (
            "/api/v1/auth/login/",
            "/api/v1/otp/send/",
            "/api/v1/otp/login/",
        )

        output = self.run_route_probe(
            *legacy_paths,
            *EXCHANGE_PATHS,
            **COMPLETE_KEYCLOAK_ENV,
            FIREBASE_AUTH_ENABLED="true",
            FIREBASE_AUTH_PROJECT_ID="care-dev",
        )

        for path in (*legacy_paths, *EXCHANGE_PATHS):
            self.assertIn(f"MOUNTED {path}", output)
        self.assertNotIn("ABSENT", output)

    def test_firebase_alone_leaves_the_care_phone_otp_in_place(self):
        output = self.run_route_probe(
            "/api/v1/otp/send/",
            "/api/v1/otp/login/",
            "/api/v1/auth/login/",
            KEYCLOAK_ENABLED="false",
            FIREBASE_AUTH_ENABLED="true",
            FIREBASE_AUTH_PROJECT_ID="care-dev",
        )

        self.assertNotIn("ABSENT", output)

    # -- non-API roles ------------------------------------------------------

    def test_a_worker_role_needs_no_provider_configuration(self):
        result, output = self.run_check(
            CARE_PROCESS_ROLE="task_worker",
            KEYCLOAK_ENABLED="true",
            FIREBASE_AUTH_ENABLED="true",
        )

        self.assertEqual(result.returncode, 0, output)
