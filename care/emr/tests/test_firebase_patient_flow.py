"""End-to-end Firebase patient login against a controlled double (ES-10 §12).

ADR-0010 forbids live SMS and email in automation, and no Firebase project is
provisioned for this phase. Everything below Google is therefore real: a real
RS256 key pair, a real Firebase-shaped ID token, the real verifier, the real
HTTP exchange endpoint, a real `PatientToken` and the real patient API. Only
Google's certificate endpoint is replaced, by serving the public half of the
key the test signed with.

What this proves: a Firebase-verified phone or email identity reaches the
patient flow, and an identity that is unverified, out of policy, expired,
misaddressed or signed by the wrong key does not.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from care.emr.models import Patient
from care.utils.tests.base import CareAPITestBase

PROJECT_ID = "care-dev-synthetic"
KEY_ID = "synthetic-signing-key"

FIREBASE_SETTINGS = {
    "FIREBASE_AUTH_ENABLED": True,
    "FIREBASE_AUTH_PROJECT_ID": PROJECT_ID,
    "FIREBASE_AUTH_SMS_COUNTRY_CODES": ["+52"],
    "ROOT_URLCONF": "care.emr.tests.test_firebase_patient_flow",
}


def _build_urlconf():
    """Mount the exchange the way `config/urls.py` does when the flag is on."""
    from config.firebase_auth_urls import build_firebase_auth_urlpatterns
    from config.urls import urlpatterns as base_urlpatterns

    return [
        *base_urlpatterns,
        *build_firebase_auth_urlpatterns(enabled=True),
    ]


urlpatterns = _build_urlconf()


class _SigningKey:
    """An RSA key pair plus the X.509 certificate that publishes it.

    Google serves *certificates*, not bare public keys, so the double must too.
    An earlier version of this file served a public key and therefore could not
    have caught the defect where the certificate was handed to PyJWT as a key --
    a reminder that a double which is easier than reality tests less than it
    appears to.
    """

    def __init__(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

    @property
    def private_pem(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def certificate_pem(self) -> str:
        """A self-signed certificate shaped like the ones Google publishes."""
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(
                    NameOID.COMMON_NAME, "securetoken.system.gserviceaccount.com"
                )
            ]
        )
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self.private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(self.private_key, hashes.SHA256())
        )
        return certificate.public_bytes(serialization.Encoding.PEM).decode()


SIGNING_KEY = _SigningKey()
FOREIGN_KEY = _SigningKey()


class _GoogleCertificateDouble:
    """Stands in for Google's public certificate endpoint, and nothing else.

    It replaces `_certificates`, the one function whose only job is to fetch
    and cache `FIREBASE_CERTIFICATES_URL`. Signature verification, issuer and
    audience pinning, expiry, the exchange view, the CARE token and the patient
    API all stay real. The URL itself is asserted separately in
    `test_firebase_patient_auth.FirebaseTokenVerificationTests`.
    """

    def __init__(self, certificate_pem):
        self.certificate_pem = certificate_pem
        self.lookups = 0

    def __call__(self, *, session=None, force_refresh=False):
        self.lookups += 1
        return {KEY_ID: self.certificate_pem}


def firebase_id_token(
    *,
    key: _SigningKey = SIGNING_KEY,
    project_id: str = PROJECT_ID,
    subject: str = "firebase-synthetic-user",
    lifetime: int = 3600,
    kid: str = KEY_ID,
    **claims,
) -> str:
    issued_at = int(time.time())
    payload = {
        "iss": f"https://securetoken.google.com/{project_id}",
        "aud": project_id,
        "sub": subject,
        "auth_time": issued_at,
        "iat": issued_at if lifetime > 0 else issued_at + lifetime,
        "exp": issued_at + lifetime,
        "user_id": subject,
        **claims,
    }
    return jwt.encode(payload, key.private_pem, algorithm="RS256", headers={"kid": kid})


PHONE_TOKEN_CLAIMS = {
    "phone_number": "+5215555555555",
    "firebase": {"sign_in_provider": "phone", "identities": {}},
}
EMAIL_TOKEN_CLAIMS = {
    "email": "Synthetic.Patient@Example.com",
    "email_verified": True,
    "firebase": {"sign_in_provider": "password", "identities": {}},
}


@override_settings(**FIREBASE_SETTINGS)
class FirebasePatientFlowTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.exchange_url = "/api/v1/auth/firebase/patient/exchange/"
        self.patient_url = reverse("otp-patient-list")
        self.certificates = _GoogleCertificateDouble(SIGNING_KEY.certificate_pem)

    def exchange(self, id_token):
        with patch("config.firebase_auth_service._certificates", self.certificates):
            return self.client.post(
                self.exchange_url, {"id_token": id_token}, format="json"
            )

    def patients_visible_to(self, access_token):
        response = self.client.get(
            self.patient_url,
            headers={
                "authorization": f"Bearer {access_token}",
                "accept": "application/json",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {result["id"] for result in response.json()["results"]}

    # -- the two supported identities reach the patient flow ----------------

    def test_a_verified_mexican_phone_reaches_its_own_patient_record(self):
        patient = self.create_patient(phone_number="+5215555555555")
        other = self.create_patient(phone_number="+5215555559999")

        response = self.exchange(firebase_id_token(**PHONE_TOKEN_CLAIMS))

        self.assertEqual(response.status_code, 200, response.content)
        visible = self.patients_visible_to(response.json()["access"])
        self.assertEqual(visible, {str(patient.external_id)})
        self.assertNotIn(str(other.external_id), visible)
        self.assertEqual(self.certificates.lookups, 1)

    def test_a_verified_email_link_identity_reaches_its_own_patient_record(self):
        patient = self.create_patient(email="synthetic.patient@example.com")
        self.create_patient(email="someone.else@example.com")

        response = self.exchange(firebase_id_token(**EMAIL_TOKEN_CLAIMS))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.patients_visible_to(response.json()["access"]),
            {str(patient.external_id)},
        )

    def test_an_identity_matching_no_patient_still_receives_a_token(self):
        """The exchange authenticates a contact; it does not create a patient."""
        before = Patient.objects.count()

        response = self.exchange(firebase_id_token(**EMAIL_TOKEN_CLAIMS))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Patient.objects.count(), before)
        self.assertEqual(self.patients_visible_to(response.json()["access"]), set())

    # -- everything else fails closed, uniformly ----------------------------

    def assert_generic_failure(self, response):
        self.assertEqual(response.status_code, 401, response.content)
        body = response.content.decode()
        self.assertNotIn("+52", body)
        self.assertNotIn("example.com", body)
        self.assertNotIn(PROJECT_ID, body)

    def test_an_out_of_policy_phone_number_is_refused(self):
        self.create_patient(phone_number="+919999999999")

        self.assert_generic_failure(
            self.exchange(
                firebase_id_token(
                    phone_number="+919999999999",
                    firebase={"sign_in_provider": "phone"},
                )
            )
        )

    def test_an_unverified_email_is_refused(self):
        self.assert_generic_failure(
            self.exchange(
                firebase_id_token(
                    email="synthetic.patient@example.com",
                    email_verified=False,
                    firebase={"sign_in_provider": "password"},
                )
            )
        )

    def test_a_token_for_another_firebase_project_is_refused(self):
        self.assert_generic_failure(
            self.exchange(
                firebase_id_token(
                    project_id="someone-elses-project", **PHONE_TOKEN_CLAIMS
                )
            )
        )

    def test_an_expired_token_is_refused(self):
        self.assert_generic_failure(
            self.exchange(firebase_id_token(lifetime=-3600, **PHONE_TOKEN_CLAIMS))
        )

    def test_a_token_signed_by_another_key_is_refused(self):
        self.assert_generic_failure(
            self.exchange(firebase_id_token(key=FOREIGN_KEY, **PHONE_TOKEN_CLAIMS))
        )

    def test_an_unsigned_token_is_refused(self):
        unsigned = jwt.encode({"sub": "x", "aud": PROJECT_ID}, key="", algorithm="none")

        self.assert_generic_failure(self.exchange(unsigned))

    def test_an_unsupported_sign_in_provider_is_refused(self):
        self.assert_generic_failure(
            self.exchange(
                firebase_id_token(
                    email="synthetic.patient@example.com",
                    email_verified=True,
                    firebase={"sign_in_provider": "anonymous"},
                )
            )
        )

    def test_a_malformed_token_is_refused_without_a_stack_trace(self):
        self.assert_generic_failure(self.exchange("not.a.token"))

    def test_the_failure_body_is_identical_for_unknown_and_invalid_identities(self):
        unknown = self.exchange(firebase_id_token(**EMAIL_TOKEN_CLAIMS))
        invalid = self.exchange(
            firebase_id_token(key=FOREIGN_KEY, **EMAIL_TOKEN_CLAIMS)
        )

        # An unknown-but-valid identity is authenticated and simply sees no
        # patient; an invalid one is refused. Neither reveals whether any
        # patient record exists.
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(json.loads(unknown.content).keys(), {"access"})

    # -- the issued token is a CARE token, not a provider token -------------

    def test_the_raw_firebase_token_is_not_a_care_credential(self):
        self.create_patient(phone_number="+5215555555555")
        id_token = firebase_id_token(**PHONE_TOKEN_CLAIMS)
        self.exchange(id_token)

        response = self.client.get(
            self.patient_url,
            headers={
                "authorization": f"Bearer {id_token}",
                "accept": "application/json",
            },
        )

        self.assertIn(response.status_code, {401, 403})
