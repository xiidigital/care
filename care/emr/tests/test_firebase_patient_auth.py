from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIRequestFactory

from config.firebase_auth import (
    DEFAULT_SMS_COUNTRY_CODES,
    is_calling_code,
    validate_firebase_auth_settings,
)
from config.firebase_auth_service import (
    FIREBASE_CERTIFICATES_URL,
    FirebaseTokenError,
    _signing_key,
    normalize_verified_phone_number,
    verify_firebase_id_token,
)
from config.firebase_auth_urls import build_firebase_auth_urlpatterns
from config.firebase_auth_views import FirebasePatientExchangeView
from config.patient_otp_token import PatientToken


class FirebaseAuthConfigurationTests(SimpleTestCase):
    def test_disabled_firebase_auth_requires_no_project(self):
        validate_firebase_auth_settings(enabled=False, project_id="")

    def test_enabled_firebase_auth_requires_a_project(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_firebase_auth_settings(enabled=True, project_id="")

    def test_enabled_firebase_auth_requires_an_sms_country_policy(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_firebase_auth_settings(
                enabled=True, project_id="care-dev", sms_country_codes=[]
            )

    def test_enabled_firebase_auth_rejects_a_malformed_calling_code(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            validate_firebase_auth_settings(
                enabled=True, project_id="care-dev", sms_country_codes=["52"]
            )

        self.assertIn("FIREBASE_AUTH_SMS_COUNTRY_CODES", str(caught.exception))

    def test_default_sms_policy_is_mexico_only(self):
        self.assertEqual(DEFAULT_SMS_COUNTRY_CODES, ("+52",))
        self.assertTrue(is_calling_code("+52"))
        self.assertFalse(is_calling_code("+"))
        self.assertFalse(is_calling_code("0052"))

    def test_disabled_firebase_auth_mounts_no_route(self):
        self.assertEqual(build_firebase_auth_urlpatterns(enabled=False), [])
        with self.assertRaises(NoReverseMatch):
            reverse("firebase_patient_exchange")

    def test_enabled_firebase_auth_mounts_one_patient_exchange(self):
        patterns = build_firebase_auth_urlpatterns(enabled=True)

        self.assertEqual(
            [pattern.name for pattern in patterns], ["firebase_patient_exchange"]
        )


@override_settings(FIREBASE_AUTH_ENABLED=True, FIREBASE_AUTH_PROJECT_ID="care-dev")
class FirebasePatientExchangeTests(SimpleTestCase):
    def setUp(self):
        self.request = APIRequestFactory().post(
            "/exchange/", {"id_token": "firebase-id-token"}, format="json"
        )

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_verified_phone_issues_legacy_compatible_patient_token(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "phone_number": "+5215555555555",
            "firebase": {"sign_in_provider": "phone"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 200)
        token = PatientToken(response.data["access"])
        self.assertEqual(token["phone_number"], "+5215555555555")
        self.assertEqual(token["auth_provider"], "firebase")

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_verified_email_issues_normalized_patient_token(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "email": "Patient@Example.COM",
            "email_verified": True,
            "firebase": {"sign_in_provider": "password"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 200)
        token = PatientToken(response.data["access"])
        self.assertEqual(token["email"], "patient@example.com")

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_unverified_email_is_rejected_generically(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "email": "patient@example.com",
            "email_verified": False,
            "firebase": {"sign_in_provider": "password"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("email", str(response.data).lower())

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_token_without_verified_contact_is_rejected(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "firebase": {"sign_in_provider": "custom"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_custom_provider_cannot_claim_the_direct_email_path(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "email": "patient@example.com",
            "email_verified": True,
            "firebase": {"sign_in_provider": "custom"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_out_of_policy_phone_identity_cannot_obtain_a_patient_token(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "phone_number": "+919999999999",
            "firebase": {"sign_in_provider": "phone"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("919999999999", str(response.data))

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_malformed_verified_phone_number_is_rejected(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "phone_number": "+52-not-a-number",
            "firebase": {"sign_in_provider": "phone"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)

    @override_settings(FIREBASE_AUTH_SMS_COUNTRY_CODES=["+52", "+1"])
    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_widening_the_policy_is_configuration_only(self, verify):
        verify.return_value = {
            "sub": "firebase-user",
            "phone_number": "+15555555555",
            "firebase": {"sign_in_provider": "phone"},
        }

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 200)

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    def test_failed_exchange_never_echoes_the_provider_token(self, verify):
        verify.side_effect = FirebaseTokenError

        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("firebase-id-token", str(response.data))

    @patch("config.firebase_auth_views.verify_firebase_id_token")
    @patch("config.firebase_auth_views.ratelimit", return_value=True)
    def test_exchange_is_rate_limited_before_token_verification(self, limited, verify):
        response = FirebasePatientExchangeView.as_view()(self.request)

        self.assertEqual(response.status_code, 429)
        verify.assert_not_called()


class GoogleSigningKeyTests(SimpleTestCase):
    """Google publishes X.509 certificates, not bare public keys.

    Handing the certificate straight to PyJWT raises `InvalidKeyError` on every
    real Firebase token. No mocked verifier can catch that, because a mock
    supplies whatever shape the test invented -- so this asserts against a real
    certificate.
    """

    def test_a_certificate_yields_a_usable_public_key(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        certificate = _self_signed_certificate(key)

        public_key = _signing_key(certificate)

        self.assertEqual(public_key.public_numbers(), key.public_key().public_numbers())

    def test_a_bare_public_key_is_not_accepted_as_a_certificate(self):
        """The shape Google never sends must not silently pass either."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

        with self.assertRaises(ValueError):
            _signing_key(public_pem)


def _self_signed_certificate(key) -> str:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    name = x509.Name(
        [
            x509.NameAttribute(
                NameOID.COMMON_NAME, "securetoken.system.gserviceaccount.com"
            )
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


@override_settings(FIREBASE_AUTH_SMS_COUNTRY_CODES=["+52"])
class FirebaseSmsPolicyTests(SimpleTestCase):
    def test_mexican_mobile_number_is_accepted(self):
        self.assertEqual(
            normalize_verified_phone_number(" +5215555555555 "), "+5215555555555"
        )

    def test_number_outside_the_policy_is_refused(self):
        with self.assertRaises(FirebaseTokenError):
            normalize_verified_phone_number("+919999999999")

    def test_missing_number_is_refused(self):
        with self.assertRaises(FirebaseTokenError):
            normalize_verified_phone_number(None)

    def test_prefix_policy_does_not_accept_a_national_format(self):
        with self.assertRaises(FirebaseTokenError):
            normalize_verified_phone_number("5215555555555")


@override_settings(FIREBASE_AUTH_PROJECT_ID="care-dev")
class FirebaseTokenVerificationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("config.firebase_auth_service.jwt.decode")
    @patch("config.firebase_auth_service.jwt.get_unverified_header")
    def test_verifier_pins_google_certificates_issuer_and_audience(
        self, get_header, decode
    ):
        from cryptography.hazmat.primitives.asymmetric import rsa

        get_header.return_value = {"alg": "RS256", "kid": "test-key"}
        decode.return_value = {
            "sub": "firebase-user",
            "aud": "care-dev",
            "iss": "https://securetoken.google.com/care-dev",
        }
        # A real certificate, because that is what Google serves. The earlier
        # placeholder string let the verifier hand PyJWT something it cannot
        # parse and still pass.
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        session = _CertificateSession({"test-key": _self_signed_certificate(key)})

        claims = verify_firebase_id_token("id-token", session=session)

        self.assertEqual(claims["sub"], "firebase-user")
        self.assertEqual(session.requested_url, FIREBASE_CERTIFICATES_URL)
        _, signing_key = decode.call_args.args
        # The public key extracted from the certificate, not the certificate.
        self.assertEqual(
            signing_key.public_numbers(), key.public_key().public_numbers()
        )
        self.assertEqual(decode.call_args.kwargs["algorithms"], ["RS256"])
        self.assertEqual(decode.call_args.kwargs["audience"], "care-dev")
        self.assertEqual(
            decode.call_args.kwargs["issuer"],
            "https://securetoken.google.com/care-dev",
        )

    @patch("config.firebase_auth_service.jwt.get_unverified_header")
    def test_unknown_signing_key_is_rejected(self, get_header):
        get_header.return_value = {"alg": "RS256", "kid": "unknown-key"}
        session = _CertificateSession({"current-key": "public-certificate"})

        with self.assertRaises(FirebaseTokenError):
            verify_firebase_id_token("id-token", session=session)

        self.assertEqual(session.request_count, 2)


class _CertificateResponse:
    headers = {"Cache-Control": "public, max-age=300"}

    def __init__(self, certificates):
        self.certificates = certificates

    def raise_for_status(self):
        return None

    def json(self):
        return self.certificates


class _CertificateSession:
    def __init__(self, certificates):
        self.certificates = certificates
        self.requested_url = None
        self.request_count = 0

    def get(self, url, **kwargs):
        self.requested_url = url
        self.request_count += 1
        return _CertificateResponse(self.certificates)
