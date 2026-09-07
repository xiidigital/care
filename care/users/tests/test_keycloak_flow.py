"""End-to-end Keycloak login against a standards-faithful OIDC double (ES-10 §12).

No Keycloak service exists and none is started here. What stands in is a double
that answers discovery, the token endpoint and JWKS exactly as an OIDC provider
must, signing real RS256 identity tokens with a real key pair. Everything on
CARE's side is real: the mounted HTTP routes, discovery and issuer pinning,
audience and nonce validation, subject resolution, the staff token pair, the
`PatientToken` and the patient API.

This is the pre-activation contract test ADR-0010 asks for: it must keep
passing so the dormant adapter does not rot before its first real deployment.
"""

import time
from functools import partial
from unittest.mock import patch

from authlib.jose import JsonWebKey, jwt
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from care.emr.models import Patient
from care.users.models import User
from care.utils.tests.base import CareAPITestBase
from config.keycloak_service import exchange_keycloak_code

ISSUER = "https://identity.example/realms/care"
PUBLIC_BASE_URL = "https://care.example"
WORKFORCE_CALLBACK = f"{PUBLIC_BASE_URL}/auth/keycloak/workforce/callback"
PATIENT_CALLBACK = f"{PUBLIC_BASE_URL}/auth/keycloak/patient/callback"
NONCE = "browser-nonce-value"
VERIFIER = "v" * 43

KEYCLOAK_SETTINGS = {
    "KEYCLOAK_ENABLED": True,
    "KEYCLOAK_ISSUER_URL": ISSUER,
    "KEYCLOAK_WORKFORCE_CLIENT_ID": "care-workforce",
    "KEYCLOAK_WORKFORCE_CLIENT_SECRET": "synthetic-workforce-secret",
    "KEYCLOAK_PATIENT_CLIENT_ID": "care-patient",
    "KEYCLOAK_PATIENT_CLIENT_SECRET": "synthetic-patient-secret",
    "KEYCLOAK_PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    "ROOT_URLCONF": "care.users.tests.test_keycloak_flow",
}


def _build_urlconf():
    """Mount the exchanges the way `config/urls.py` does when the flag is on."""
    from config.keycloak_urls import build_keycloak_urlpatterns
    from config.urls import urlpatterns as base_urlpatterns

    return [*base_urlpatterns, *build_keycloak_urlpatterns(enabled=True)]


urlpatterns = _build_urlconf()

REALM_KEY = JsonWebKey.generate_key(
    "RSA", 2048, is_private=True, options={"kid": "realm-key"}
)
FOREIGN_KEY = JsonWebKey.generate_key(
    "RSA", 2048, is_private=True, options={"kid": "realm-key"}
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _OidcProviderDouble:
    """A minimal, standards-faithful OIDC provider."""

    def __init__(self, *, audience, subject, key=REALM_KEY, nonce=NONCE, lifetime=300):
        self.audience = audience
        self.subject = subject
        self.key = key
        self.nonce = nonce
        self.lifetime = lifetime
        self.token_requests = []
        self.discovery = {
            "issuer": ISSUER,
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
        }

    def id_token(self):
        now = int(time.time())
        return jwt.encode(
            {"alg": "RS256", "kid": "realm-key"},
            {
                "iss": ISSUER,
                "aud": self.audience,
                "sub": self.subject,
                "nonce": self.nonce,
                "iat": now if self.lifetime > 0 else now + self.lifetime,
                "exp": now + self.lifetime,
            },
            self.key,
        ).decode()

    def get(self, url, **kwargs):
        if url.endswith("/.well-known/openid-configuration"):
            return _Response(self.discovery)
        if url == self.discovery["jwks_uri"]:
            return _Response({"keys": [REALM_KEY.as_dict(is_private=False)]})
        msg = f"Unexpected GET {url}"
        raise AssertionError(msg)

    def post(self, url, **kwargs):
        self.token_requests.append(kwargs)
        return _Response({"id_token": self.id_token()})


@override_settings(**KEYCLOAK_SETTINGS)
class KeycloakFlowTests(CareAPITestBase):
    workforce_url = "/api/v1/auth/keycloak/workforce/exchange/"
    patient_url = "/api/v1/auth/keycloak/patient/exchange/"

    def setUp(self):
        super().setUp()
        # Discovery and JWKS are cached per issuer. Several of these tests
        # tamper with the discovery document and expect CARE to notice, which
        # it can only do on a cold cache -- a real deployment re-reads the
        # document when the TTL expires, not on every login.
        cache.clear()
        self.addCleanup(cache.clear)

    def payload(self, redirect_uri, **overrides):
        return {
            "code": "single-use-authorization-code",
            "code_verifier": VERIFIER,
            "nonce": NONCE,
            "redirect_uri": redirect_uri,
            **overrides,
        }

    def exchange(self, url, provider, payload):
        """Drive the real exchange, with the double as its HTTP session.

        `exchange_keycloak_code` itself is untouched -- discovery, issuer
        pinning, the token request, JWKS retrieval and every claim check run
        for real. Only the transport is redirected at the seam the function
        already exposes for exactly this purpose.
        """
        with patch(
            "config.keycloak_views.exchange_keycloak_code",
            partial(exchange_keycloak_code, session=provider),
        ):
            return self.client.post(url, payload, format="json")

    # -- workforce ----------------------------------------------------------

    def test_an_enrolled_workforce_subject_receives_the_care_token_pair(self):
        user = self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"access", "refresh"})

        # The pair is an ordinary CARE staff credential.
        me = self.client.get(
            "/api/v1/users/getcurrentuser/",
            headers={
                "authorization": f"Bearer {body['access']}",
                "accept": "application/json",
            },
        )
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["username"], user.username)

    def test_the_client_secret_authenticates_the_token_request(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        self.exchange(self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK))

        request = provider.token_requests[0]
        self.assertEqual(
            request["auth"], ("care-workforce", "synthetic-workforce-secret")
        )
        self.assertEqual(request["data"]["code_verifier"], VERIFIER)
        self.assertEqual(request["data"]["grant_type"], "authorization_code")
        self.assertEqual(request["data"]["redirect_uri"], WORKFORCE_CALLBACK)

    def test_a_deactivated_workforce_account_cannot_log_in(self):
        self.create_user(keycloak_subject="staff-subject", is_active=False)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_an_unenrolled_subject_is_never_created(self):
        before = User.objects.count()
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="never-enrolled"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), before)

    # -- patient ------------------------------------------------------------

    def test_an_enrolled_patient_subject_reaches_only_its_own_record(self):
        patient = self.create_patient(keycloak_subject="patient-subject")
        other = self.create_patient(keycloak_subject="another-subject")
        provider = _OidcProviderDouble(
            audience="care-patient", subject="patient-subject"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK)
        )

        self.assertEqual(response.status_code, 200, response.content)
        listed = self.client.get(
            reverse("otp-patient-list"),
            headers={
                "authorization": f"Bearer {response.json()['access']}",
                "accept": "application/json",
            },
        )
        self.assertEqual(listed.status_code, 200, listed.content)
        ids = {result["id"] for result in listed.json()["results"]}
        self.assertEqual(ids, {str(patient.external_id)})
        self.assertNotIn(str(other.external_id), ids)

    def test_an_unenrolled_patient_subject_is_never_created(self):
        before = Patient.objects.count()
        provider = _OidcProviderDouble(
            audience="care-patient", subject="never-enrolled"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Patient.objects.count(), before)

    # -- the two audiences never cross ---------------------------------------

    def test_a_patient_token_cannot_buy_a_staff_session(self):
        self.create_user(keycloak_subject="shared-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-patient", subject="shared-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_workforce_token_cannot_buy_a_patient_session(self):
        self.create_patient(keycloak_subject="shared-subject")
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="shared-subject"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_the_patient_callback_is_not_accepted_by_the_workforce_exchange(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(PATIENT_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(provider.token_requests, [])

    # -- every other check fails closed --------------------------------------

    def test_a_replayed_nonce_mismatch_is_refused(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce",
            subject="staff-subject",
            nonce="a-different-nonce",
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_an_expired_identity_token_is_refused(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject", lifetime=-600
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_token_signed_outside_the_realm_is_refused(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject", key=FOREIGN_KEY
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_discovery_document_for_another_issuer_is_refused(self):
        self.create_user(keycloak_subject="staff-subject", is_active=True)
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )
        provider.discovery["issuer"] = "https://identity.example/realms/other"

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(provider.token_requests, [])

    def test_a_short_code_verifier_is_rejected_before_the_provider_is_called(self):
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        response = self.exchange(
            self.workforce_url,
            provider,
            self.payload(WORKFORCE_CALLBACK, code_verifier="too-short"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(provider.token_requests, [])

    def test_no_failure_ever_echoes_the_client_secret_or_the_code(self):
        provider = _OidcProviderDouble(
            audience="care-patient", subject="never-enrolled"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK)
        )

        body = response.content.decode()
        self.assertNotIn("synthetic-patient-secret", body)
        self.assertNotIn("synthetic-workforce-secret", body)
        self.assertNotIn("single-use-authorization-code", body)
        self.assertNotIn(VERIFIER, body)
