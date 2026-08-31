from django.test import SimpleTestCase, TestCase
from model_bakery import baker

from care.emr.models import Patient
from config.patient_otp_authentication import (
    JWTTokenPatientAuthentication,
    OTPAuthenticatedPermission,
    contact_fingerprint,
    patient_access_queryset,
    patient_principal_label,
)
from config.patient_otp_token import PatientToken


class PatientIdentityTokenTests(SimpleTestCase):
    def test_legacy_phone_token_remains_supported(self):
        token = PatientToken()
        token["phone_number"] = "+5215555555555"

        principal = JWTTokenPatientAuthentication().get_user(token)

        self.assertEqual(principal.phone_number, "+5215555555555")
        self.assertIsNone(principal.patient_id)
        self.assertTrue(
            OTPAuthenticatedPermission().has_permission(_request(principal), None)
        )

    def test_keycloak_patient_token_uses_exact_patient_identity(self):
        token = PatientToken()
        token["patient_id"] = "550e8400-e29b-41d4-a716-446655440000"
        token["auth_provider"] = "keycloak"

        principal = JWTTokenPatientAuthentication().get_user(token)

        self.assertEqual(principal.patient_id, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(principal.auth_provider, "keycloak")
        self.assertTrue(
            OTPAuthenticatedPermission().has_permission(_request(principal), None)
        )


class PatientPrincipalAuditLabelTests(SimpleTestCase):
    """ADR-0010 §6: record the principal opaquely, never the raw contact."""

    def test_a_phone_identity_is_masked_to_its_last_four_digits(self):
        label = patient_principal_label(_principal(phone_number="+5215555555555"))

        self.assertEqual(label, "patient|otp|phone:5555")
        self.assertNotIn("+5215555555555", label)

    def test_an_email_identity_is_fingerprinted_rather_than_recorded(self):
        principal = _principal(email="patient@example.com")
        principal.auth_provider = "firebase"

        label = patient_principal_label(principal)

        self.assertNotIn("patient@example.com", label)
        self.assertNotIn("example.com", label)
        self.assertEqual(
            label,
            f"patient|firebase|email:{contact_fingerprint('patient@example.com')}",
        )

    def test_the_email_fingerprint_is_stable_and_case_insensitive(self):
        self.assertEqual(
            contact_fingerprint(" Patient@Example.COM "),
            contact_fingerprint("patient@example.com"),
        )

    def test_a_resolved_patient_is_recorded_by_its_care_identifier(self):
        principal = _principal(patient_id="550e8400-e29b-41d4-a716-446655440000")
        principal.auth_provider = "keycloak"

        self.assertEqual(
            patient_principal_label(principal),
            "patient|keycloak|id:550e8400-e29b-41d4-a716-446655440000",
        )

    def test_an_identity_without_any_contact_does_not_raise(self):
        self.assertEqual(
            patient_principal_label(_principal()), "patient|otp|unresolved"
        )


class PatientIdentityQueryTests(TestCase):
    def test_patient_id_takes_precedence_over_shared_contact(self):
        selected = baker.make(Patient, phone_number="+5215555555555")
        baker.make(Patient, phone_number=selected.phone_number)
        principal = _principal(
            patient_id=str(selected.external_id), phone_number=selected.phone_number
        )

        self.assertEqual(list(patient_access_queryset(principal)), [selected])

    def test_email_is_normalized_and_can_select_a_household(self):
        first = baker.make(Patient, email=" Patient@Example.COM ")
        second = baker.make(Patient, email="patient@example.com")
        principal = _principal(email="PATIENT@example.com")

        first.refresh_from_db()
        self.assertEqual(first.email, "patient@example.com")
        self.assertCountEqual(patient_access_queryset(principal), [first, second])


def _principal(**identifiers):
    principal = type("PatientPrincipal", (), {})()
    principal.patient_id = identifiers.get("patient_id")
    principal.phone_number = identifiers.get("phone_number")
    principal.email = identifiers.get("email")
    principal.auth_provider = identifiers.get("auth_provider")
    return principal


def _request(user):
    return type("Request", (), {"user": user})()
