"""End-to-end OIDC login against a standards-faithful double (ES-11 §12).

The double answers discovery, the token endpoint and JWKS exactly as any OIDC
provider must, signing real RS256 identity tokens with a real key pair. It is
deliberately not Keycloak: a test that only passes against one vendor is a test
that has stopped checking the abstraction. Everything on CARE's side is real --
the mounted routes, discovery and issuer pinning, audience and nonce
validation, identity resolution, the staff token pair, the `PatientToken` and
the patient API.

The claim under test is ADR-0011 §3: a principal is resolved from
`(provider_id, issuer, subject)` and from nothing else. Two providers that mint
the same `sub` resolve to different principals, which is exactly what the old
single unique `keycloak_subject` column could not express.
"""

import time
from functools import partial
from unittest.mock import patch

from authlib.jose import JsonWebKey, jwt
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from care.emr.models import Patient, PatientExternalIdentity
from care.users.models import User, UserExternalIdentity
from care.utils.tests.base import CareAPITestBase
from config.oidc import OidcProvider
from config.oidc_service import exchange_oidc_code

ISSUER = "https://identity.example/realms/care"
SECOND_ISSUER = "https://identity-b.example/realms/care"
PUBLIC_BASE_URL = "https://care.example"
WORKFORCE_CALLBACK = f"{PUBLIC_BASE_URL}/auth/oidc/workforce/callback"
PATIENT_CALLBACK = f"{PUBLIC_BASE_URL}/auth/oidc/patient/callback"
NONCE = "browser-nonce-value"
VERIFIER = "v" * 43

WORKFORCE_PROVIDER = OidcProvider(
    id="clinic-sso",
    display_name="Clinic SSO",
    issuer=ISSUER,
    principal_type="workforce",
    client_id="care-workforce",
    client_secret="synthetic-workforce-secret",
)
PATIENT_PROVIDER = OidcProvider(
    id="patient-sso",
    display_name="Patient SSO",
    issuer=ISSUER,
    principal_type="patient",
    client_id="care-patient",
    client_secret="synthetic-patient-secret",
)
#: A second workforce issuer, so the suite can ask the question the old schema
#: could not answer: whose account does a colliding `sub` reach?
SECOND_WORKFORCE_PROVIDER = OidcProvider(
    id="regional-sso",
    display_name="Regional SSO",
    issuer=SECOND_ISSUER,
    principal_type="workforce",
    client_id="care-workforce",
    client_secret="synthetic-regional-secret",
)

PROVIDERS = (WORKFORCE_PROVIDER, PATIENT_PROVIDER, SECOND_WORKFORCE_PROVIDER)

OIDC_SETTINGS = {
    "OIDC_PROVIDERS": PROVIDERS,
    "OIDC_PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    "ROOT_URLCONF": "care.users.tests.test_oidc_flow",
}


def _build_urlconf():
    """Mount the exchanges the way `config/urls.py` does with providers set."""
    from config.oidc_urls import build_oidc_urlpatterns
    from config.urls import urlpatterns as base_urlpatterns

    return [*base_urlpatterns, *build_oidc_urlpatterns(PROVIDERS)]


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

    def __init__(
        self,
        *,
        audience,
        subject,
        key=REALM_KEY,
        nonce=NONCE,
        lifetime=300,
        issuer=ISSUER,
    ):
        self.issuer = issuer
        self.audience = audience
        self.subject = subject
        self.key = key
        self.nonce = nonce
        self.lifetime = lifetime
        self.token_requests = []
        self.discovery = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        }

    def id_token(self):
        now = int(time.time())
        return jwt.encode(
            {"alg": "RS256", "kid": "realm-key"},
            {
                "iss": self.issuer,
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


@override_settings(**OIDC_SETTINGS)
class KeycloakFlowTests(CareAPITestBase):
    workforce_url = "/api/v1/auth/oidc/workforce/exchange/"
    patient_url = "/api/v1/auth/oidc/patient/exchange/"

    def setUp(self):
        super().setUp()
        # Discovery and JWKS are cached per issuer. Several of these tests
        # tamper with the discovery document and expect CARE to notice, which
        # it can only do on a cold cache -- a real deployment re-reads the
        # document when the TTL expires, not on every login.
        cache.clear()
        self.addCleanup(cache.clear)

    def enrol_user(self, subject, *, provider=WORKFORCE_PROVIDER, is_active=True):
        """Enrolment is a deliberate act that writes a row (ADR-0011 §5)."""
        user = self.create_user(is_active=is_active)
        UserExternalIdentity.objects.create(
            user=user,
            provider_id=provider.id,
            issuer=provider.issuer,
            subject=subject,
        )
        return user

    def enrol_patient(self, subject, *, provider=PATIENT_PROVIDER):
        patient = self.create_patient()
        PatientExternalIdentity.objects.create(
            patient=patient,
            provider_id=provider.id,
            issuer=provider.issuer,
            subject=subject,
        )
        return patient

    def payload(self, redirect_uri, provider_id="clinic-sso", **overrides):
        return {
            "provider_id": provider_id,
            "code": "single-use-authorization-code",
            "code_verifier": VERIFIER,
            "nonce": NONCE,
            "redirect_uri": redirect_uri,
            **overrides,
        }

    def exchange(self, url, provider, payload):
        """Drive the real exchange, with the double as its HTTP session.

        `exchange_oidc_code` itself is untouched -- discovery, issuer
        pinning, the token request, JWKS retrieval and every claim check run
        for real. Only the transport is redirected at the seam the function
        already exposes for exactly this purpose.
        """
        with patch(
            "config.oidc_views.exchange_oidc_code",
            partial(exchange_oidc_code, session=provider),
        ):
            return self.client.post(url, payload, format="json")

    # -- workforce ----------------------------------------------------------

    def test_an_enrolled_workforce_subject_receives_the_care_token_pair(self):
        user = self.enrol_user("staff-subject")
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
        self.enrol_user("staff-subject")
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
        self.enrol_user("staff-subject", is_active=False)
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
        patient = self.enrol_patient("patient-subject")
        other = self.enrol_patient("another-subject")
        provider = _OidcProviderDouble(
            audience="care-patient", subject="patient-subject"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK, "patient-sso")
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
            self.patient_url, provider, self.payload(PATIENT_CALLBACK, "patient-sso")
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Patient.objects.count(), before)

    # -- the triple, end to end ----------------------------------------------

    def test_two_issuers_minting_the_same_subject_reach_different_accounts(self):
        """T1, through the real HTTP route.

        This is the defect ADR-0011 was written for. Under the old schema both
        of these were one unique `keycloak_subject`, so whichever issuer was
        configured could authenticate as the account the other had enrolled.
        """
        alice = self.enrol_user("shared-subject", provider=WORKFORCE_PROVIDER)
        mallory = self.enrol_user("shared-subject", provider=SECOND_WORKFORCE_PROVIDER)

        first = self.exchange(
            self.workforce_url,
            _OidcProviderDouble(audience="care-workforce", subject="shared-subject"),
            self.payload(WORKFORCE_CALLBACK, "clinic-sso"),
        )
        second = self.exchange(
            self.workforce_url,
            _OidcProviderDouble(
                audience="care-workforce",
                subject="shared-subject",
                issuer=SECOND_ISSUER,
            ),
            self.payload(WORKFORCE_CALLBACK, "regional-sso"),
        )

        self.assertEqual(self._username_for(first), alice.username)
        self.assertEqual(self._username_for(second), mallory.username)
        self.assertNotEqual(alice.username, mallory.username)

    def test_a_subject_enrolled_elsewhere_is_not_reachable_from_this_provider(self):
        """The other half of the same claim: the issuer has to match too."""
        self.enrol_user("shared-subject", provider=SECOND_WORKFORCE_PROVIDER)

        response = self.exchange(
            self.workforce_url,
            _OidcProviderDouble(audience="care-workforce", subject="shared-subject"),
            self.payload(WORKFORCE_CALLBACK, "clinic-sso"),
        )

        self.assertEqual(response.status_code, 401)

    def test_an_unconfigured_provider_id_is_refused(self):
        self.enrol_user("staff-subject")

        response = self.exchange(
            self.workforce_url,
            _OidcProviderDouble(audience="care-workforce", subject="staff-subject"),
            self.payload(WORKFORCE_CALLBACK, "no-such-provider"),
        )

        self.assertEqual(response.status_code, 401)

    def test_a_patient_provider_cannot_be_named_at_the_workforce_exchange(self):
        """A provider serves one principal type, and the route enforces it."""
        self.enrol_user("staff-subject")

        response = self.exchange(
            self.workforce_url,
            _OidcProviderDouble(audience="care-workforce", subject="staff-subject"),
            self.payload(WORKFORCE_CALLBACK, "patient-sso"),
        )

        self.assertEqual(response.status_code, 401)

    def _username_for(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        me = self.client.get(
            "/api/v1/users/getcurrentuser/",
            headers={
                "authorization": f"Bearer {response.json()['access']}",
                "accept": "application/json",
            },
        )
        self.assertEqual(me.status_code, 200, me.content)
        return me.json()["username"]

    # -- the two audiences never cross ---------------------------------------

    def test_a_patient_token_cannot_buy_a_staff_session(self):
        self.enrol_user("shared-subject")
        provider = _OidcProviderDouble(
            audience="care-patient", subject="shared-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_workforce_token_cannot_buy_a_patient_session(self):
        self.enrol_patient("shared-subject")
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="shared-subject"
        )

        response = self.exchange(
            self.patient_url, provider, self.payload(PATIENT_CALLBACK, "patient-sso")
        )

        self.assertEqual(response.status_code, 401)

    def test_the_patient_callback_is_not_accepted_by_the_workforce_exchange(self):
        self.enrol_user("staff-subject")
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject"
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(PATIENT_CALLBACK, "patient-sso")
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(provider.token_requests, [])

    # -- every other check fails closed --------------------------------------

    def test_a_replayed_nonce_mismatch_is_refused(self):
        self.enrol_user("staff-subject")
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
        self.enrol_user("staff-subject")
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject", lifetime=-600
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_token_signed_outside_the_realm_is_refused(self):
        self.enrol_user("staff-subject")
        provider = _OidcProviderDouble(
            audience="care-workforce", subject="staff-subject", key=FOREIGN_KEY
        )

        response = self.exchange(
            self.workforce_url, provider, self.payload(WORKFORCE_CALLBACK)
        )

        self.assertEqual(response.status_code, 401)

    def test_a_discovery_document_for_another_issuer_is_refused(self):
        self.enrol_user("staff-subject")
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
            self.patient_url, provider, self.payload(PATIENT_CALLBACK, "patient-sso")
        )

        body = response.content.decode()
        self.assertNotIn("synthetic-patient-secret", body)
        self.assertNotIn("synthetic-workforce-secret", body)
        self.assertNotIn("single-use-authorization-code", body)
        self.assertNotIn(VERIFIER, body)
