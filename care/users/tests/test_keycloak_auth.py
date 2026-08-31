from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from model_bakery import baker
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from care.emr.models import Patient
from care.users.admin import UserAdmin
from care.users.models import User
from config.keycloak import KEYCLOAK_REQUIRED_SETTINGS, validate_keycloak_settings
from config.keycloak_urls import build_keycloak_urlpatterns
from config.keycloak_views import (
    PatientKeycloakExchangeView,
    WorkforceKeycloakExchangeView,
)
from config.patient_otp_token import PatientToken

ENABLED_KEYCLOAK_SETTINGS = {
    "KEYCLOAK_ENABLED": True,
    "KEYCLOAK_ISSUER_URL": "https://identity.example/realms/care",
    "KEYCLOAK_WORKFORCE_CLIENT_ID": "care-workforce",
    "KEYCLOAK_WORKFORCE_CLIENT_SECRET": "workforce-secret",
    "KEYCLOAK_PATIENT_CLIENT_ID": "care-patient",
    "KEYCLOAK_PATIENT_CLIENT_SECRET": "patient-secret",
    "KEYCLOAK_PUBLIC_BASE_URL": "https://care.example",
}


class KeycloakConfigurationTests(SimpleTestCase):
    def test_disabled_keycloak_requires_no_configuration(self):
        validate_keycloak_settings(enabled=False, values={})

    def test_enabled_keycloak_accepts_complete_configuration(self):
        values = dict.fromkeys(KEYCLOAK_REQUIRED_SETTINGS, "configured")
        values["KEYCLOAK_ISSUER_URL"] = "https://identity.example/realms/care"
        values["KEYCLOAK_PUBLIC_BASE_URL"] = "https://care.example"
        validate_keycloak_settings(
            enabled=True,
            values=values,
        )

    def test_enabled_keycloak_names_every_missing_setting(self):
        values = dict.fromkeys(KEYCLOAK_REQUIRED_SETTINGS, "configured")
        values["KEYCLOAK_ISSUER_URL"] = ""
        values["KEYCLOAK_PATIENT_CLIENT_SECRET"] = None

        with self.assertRaises(ImproperlyConfigured) as caught:
            validate_keycloak_settings(enabled=True, values=values)

        message = str(caught.exception)
        self.assertIn("KEYCLOAK_ISSUER_URL", message)
        self.assertIn("KEYCLOAK_PATIENT_CLIENT_SECRET", message)

    def test_enabled_keycloak_rejects_insecure_remote_urls(self):
        values = dict.fromkeys(KEYCLOAK_REQUIRED_SETTINGS, "configured")
        values["KEYCLOAK_ISSUER_URL"] = "http://identity.example/realms/care"
        values["KEYCLOAK_PUBLIC_BASE_URL"] = "https://care.example"

        with self.assertRaises(ImproperlyConfigured) as caught:
            validate_keycloak_settings(enabled=True, values=values)

        self.assertIn("KEYCLOAK_ISSUER_URL", str(caught.exception))

    def test_disabled_keycloak_mounts_no_routes(self):
        self.assertEqual(build_keycloak_urlpatterns(enabled=False), [])
        with self.assertRaises(NoReverseMatch):
            reverse("keycloak_workforce_exchange")

    def test_enabled_keycloak_mounts_separate_principal_routes(self):
        patterns = build_keycloak_urlpatterns(enabled=True)

        self.assertEqual(
            {pattern.name for pattern in patterns},
            {"keycloak_patient_exchange", "keycloak_workforce_exchange"},
        )


class KeycloakIdentityFieldTests(SimpleTestCase):
    def test_user_keycloak_subject_is_optional_and_unique(self):
        field = User._meta.get_field("keycloak_subject")  # noqa: SLF001

        self.assertIsInstance(field, models.CharField)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertTrue(field.unique)

    def test_patient_email_is_optional_and_indexed(self):
        field = Patient._meta.get_field("email")  # noqa: SLF001

        self.assertIsInstance(field, models.EmailField)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertTrue(field.db_index)

    def test_patient_keycloak_subject_is_optional_and_unique(self):
        field = Patient._meta.get_field("keycloak_subject")  # noqa: SLF001

        self.assertIsInstance(field, models.CharField)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertTrue(field.unique)

    def test_workforce_subject_can_be_enrolled_in_django_admin(self):
        user_fields = UserAdmin.fieldsets[0][1]["fields"]

        self.assertIn("keycloak_subject", user_fields)


@override_settings(**ENABLED_KEYCLOAK_SETTINGS)
class KeycloakExchangeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _request(self):
        return self.factory.post(
            "/exchange/",
            {
                "code": "single-use-code",
                "code_verifier": "v" * 43,
                "nonce": "browser-nonce-value",
                "redirect_uri": "https://care.example/auth/keycloak/callback",
            },
            format="json",
        )

    @patch("config.keycloak_views.exchange_keycloak_code")
    def test_workforce_exchange_issues_existing_staff_token_pair(self, exchange):
        user = baker.make(User, keycloak_subject="staff-subject", is_active=True)
        exchange.return_value = {"sub": user.keycloak_subject}

        response = WorkforceKeycloakExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RefreshToken(response.data["refresh"])["user_id"], str(user.external_id)
        )
        self.assertIn("access", response.data)

    @patch("config.keycloak_views.exchange_keycloak_code")
    def test_patient_exchange_issues_patient_token_only(self, exchange):
        patient = baker.make(Patient, keycloak_subject="patient-subject")
        exchange.return_value = {"sub": patient.keycloak_subject}

        response = PatientKeycloakExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 200)
        token = PatientToken(response.data["access"])
        self.assertEqual(token["patient_id"], str(patient.external_id))
        self.assertEqual(token["auth_provider"], "keycloak")

    @patch("config.keycloak_views.exchange_keycloak_code")
    def test_workforce_exchange_never_resolves_a_patient_subject(self, exchange):
        baker.make(Patient, keycloak_subject="shared-subject")
        exchange.return_value = {"sub": "shared-subject"}

        response = WorkforceKeycloakExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.keycloak_views.exchange_keycloak_code")
    def test_patient_exchange_never_resolves_a_user_subject(self, exchange):
        baker.make(User, keycloak_subject="shared-subject", is_active=True)
        exchange.return_value = {"sub": "shared-subject"}

        response = PatientKeycloakExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.keycloak_views.exchange_keycloak_code")
    @patch("config.keycloak_views.ratelimit", return_value=True)
    def test_exchange_is_rate_limited_before_provider_call(self, limited, exchange):
        response = WorkforceKeycloakExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 429)
        exchange.assert_not_called()
