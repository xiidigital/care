"""ES-11 phase 9: a real login, end to end, against a real issuer.

Everything up to here stops one step short. The conformance suite validates
claims but issues no CARE credential. The flow tests issue one but drive a
double. Neither answers the question an operator actually has: if I connect a
real identity provider and enrol someone, can they log in, and does the
credential they receive work on a real CARE endpoint?

This does the whole loop against the containerised Keycloak, in the order the
security story requires:

1. an unenrolled subject is refused, and creates nothing;
2. an administrator enrols it;
3. the same person logs in and reaches a real API with the CARE token.

Step 1 comes first on purpose. "Auto-provisioning is off" is the claim that
matters most and the one easiest to believe without checking.

Skipped unless `CARE_OIDC_LIVE_ISSUER` is set -- CI must never depend on a
service CARE does not require.
"""

import base64
import hashlib
import html
import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

import requests
from django.test import override_settings
from django.urls import reverse

from care.emr.models import Patient, PatientExternalIdentity
from care.users.models import User, UserExternalIdentity
from care.utils.tests.base import CareAPITestBase
from config.oidc import OidcProvider
from config.oidc_service import (
    discovery_for_provider,
    exchange_oidc_code,
)

LIVE_ISSUER = os.environ.get("CARE_OIDC_LIVE_ISSUER", "").rstrip("/")
PUBLIC_BASE_URL = "http://localhost:9000"
NONCE = "acceptance-nonce-value-01"
VERIFIER = "a" * 43
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)
TIMEOUT = 60

WORKFORCE_PROVIDER = OidcProvider(
    id="clinic-sso",
    display_name="Clinic SSO",
    issuer=LIVE_ISSUER,
    principal_type="workforce",
    client_id="care-workforce",
    client_secret="local-only-workforce-secret",
)
PATIENT_PROVIDER = OidcProvider(
    id="patient-sso",
    display_name="Patient SSO",
    issuer=LIVE_ISSUER,
    principal_type="patient",
    client_id="care-patient",
    client_secret="local-only-patient-secret",
)
PROVIDERS = (WORKFORCE_PROVIDER, PATIENT_PROVIDER)

ACCEPTANCE_SETTINGS = {
    "OIDC_PROVIDERS": PROVIDERS,
    "OIDC_PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    "ROOT_URLCONF": "care.users.tests.test_oidc_dev_acceptance",
}


def _build_urlconf():
    from config.oidc_urls import build_oidc_urlpatterns
    from config.urls import urlpatterns as base_urlpatterns

    return [*base_urlpatterns, *build_oidc_urlpatterns(PROVIDERS)]


urlpatterns = _build_urlconf()

USERNAMES = {"workforce": "synthetic-clinician", "patient": "synthetic-patient"}


def authorization_code(provider: OidcProvider) -> str:
    """Do what a browser does, because a code cannot be had any other way."""
    browser = requests.Session()
    discovery = discovery_for_provider(provider, requests)
    if discovery is None:
        msg = "live issuer discovery failed"
        raise AssertionError(msg)

    redirect_uri = f"{PUBLIC_BASE_URL}/auth/oidc/{provider.principal_type}/callback"
    page = browser.get(
        discovery["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid",
            "state": "acceptance-state",
            "nonce": NONCE,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
        timeout=TIMEOUT,
    )
    page.raise_for_status()
    action = html.unescape(re.search(r'action="([^"]+)"', page.text).group(1))

    # Keycloak marks its session cookies Secure; a browser sends them anyway
    # because http://localhost is a secure context. `requests` does not, so the
    # header is stated outright.
    cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies)
    submitted = requests.post(
        action,
        data={
            "username": USERNAMES[provider.principal_type],
            "password": "local-only-password",
            "credentialId": "",
        },
        headers={"Cookie": cookies, "Referer": page.url},
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    location = submitted.headers.get("Location", "")
    if not location.startswith(redirect_uri):
        msg = f"issuer did not redirect to the callback: {location!r}"
        raise AssertionError(msg)
    return parse_qs(urlparse(location).query)["code"][0]


@unittest.skipUnless(LIVE_ISSUER, "Set CARE_OIDC_LIVE_ISSUER (see `make oidc-up`).")
@override_settings(**ACCEPTANCE_SETTINGS)
class WorkforceLoginAcceptanceTests(CareAPITestBase):
    workforce_url = "/api/v1/auth/oidc/workforce/exchange/"

    def payload(self, code):
        return {
            "provider_id": WORKFORCE_PROVIDER.id,
            "code": code,
            "code_verifier": VERIFIER,
            "nonce": NONCE,
            "redirect_uri": f"{PUBLIC_BASE_URL}/auth/oidc/workforce/callback",
        }

    def test_the_whole_loop_in_the_order_that_matters(self):
        # 1. A real, valid identity nobody has enrolled reaches nothing.
        users_before = User.objects.count()
        refused = self.client.post(
            self.workforce_url,
            self.payload(authorization_code(WORKFORCE_PROVIDER)),
            format="json",
        )

        self.assertEqual(refused.status_code, 401, refused.content)
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(UserExternalIdentity.objects.count(), 0)

        # 2. Learn the subject the way an administrator would -- from the
        #    provider -- and enrol it deliberately.
        claims = exchange_oidc_code(
            provider=WORKFORCE_PROVIDER,
            code=authorization_code(WORKFORCE_PROVIDER),
            code_verifier=VERIFIER,
            nonce=NONCE,
            redirect_uri=f"{PUBLIC_BASE_URL}/auth/oidc/workforce/callback",
        )
        subject = claims["sub"]
        user = self.create_user(is_active=True)
        UserExternalIdentity.objects.create(
            user=user,
            provider_id=WORKFORCE_PROVIDER.id,
            issuer=WORKFORCE_PROVIDER.issuer,
            subject=subject,
        )

        # 3. The same person now logs in, and the credential is an ordinary
        #    CARE one -- nothing downstream knows a provider was involved.
        accepted = self.client.post(
            self.workforce_url,
            self.payload(authorization_code(WORKFORCE_PROVIDER)),
            format="json",
        )

        self.assertEqual(accepted.status_code, 200, accepted.content)
        tokens = accepted.json()
        self.assertEqual(set(tokens), {"access", "refresh"})

        me = self.client.get(
            "/api/v1/users/getcurrentuser/",
            headers={
                "authorization": f"Bearer {tokens['access']}",
                "accept": "application/json",
            },
        )
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["username"], user.username)

        # And the login is recorded on the identity, not invented elsewhere.
        identity = UserExternalIdentity.objects.get()
        self.assertIsNotNone(identity.last_login_at)

    def test_a_real_token_is_still_refused_at_the_wrong_exchange(self):
        """T3, with a genuine issuer-signed token rather than a crafted one."""
        code = authorization_code(PATIENT_PROVIDER)

        response = self.client.post(
            self.workforce_url,
            {
                **self.payload(code),
                "redirect_uri": f"{PUBLIC_BASE_URL}/auth/oidc/workforce/callback",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_an_unlinked_identity_stops_working_immediately(self):
        """Rule §5.6 is how access is withdrawn, so it has to be immediate."""
        claims = exchange_oidc_code(
            provider=WORKFORCE_PROVIDER,
            code=authorization_code(WORKFORCE_PROVIDER),
            code_verifier=VERIFIER,
            nonce=NONCE,
            redirect_uri=f"{PUBLIC_BASE_URL}/auth/oidc/workforce/callback",
        )
        user = self.create_user(is_active=True)
        identity = UserExternalIdentity.objects.create(
            user=user,
            provider_id=WORKFORCE_PROVIDER.id,
            issuer=WORKFORCE_PROVIDER.issuer,
            subject=claims["sub"],
        )
        identity.delete()

        response = self.client.post(
            self.workforce_url,
            self.payload(authorization_code(WORKFORCE_PROVIDER)),
            format="json",
        )

        self.assertEqual(response.status_code, 401)


@unittest.skipUnless(LIVE_ISSUER, "Set CARE_OIDC_LIVE_ISSUER (see `make oidc-up`).")
@override_settings(**ACCEPTANCE_SETTINGS)
class PatientLoginAcceptanceTests(CareAPITestBase):
    patient_url = "/api/v1/auth/oidc/patient/exchange/"

    def test_an_enrolled_patient_reaches_only_its_own_record(self):
        claims = exchange_oidc_code(
            provider=PATIENT_PROVIDER,
            code=authorization_code(PATIENT_PROVIDER),
            code_verifier=VERIFIER,
            nonce=NONCE,
            redirect_uri=f"{PUBLIC_BASE_URL}/auth/oidc/patient/callback",
        )
        patient = self.create_patient()
        other = self.create_patient()
        PatientExternalIdentity.objects.create(
            patient=patient,
            provider_id=PATIENT_PROVIDER.id,
            issuer=PATIENT_PROVIDER.issuer,
            subject=claims["sub"],
            linked_by=self.create_user(),
        )

        response = self.client.post(
            self.patient_url,
            {
                "provider_id": PATIENT_PROVIDER.id,
                "code": authorization_code(PATIENT_PROVIDER),
                "code_verifier": VERIFIER,
                "nonce": NONCE,
                "redirect_uri": f"{PUBLIC_BASE_URL}/auth/oidc/patient/callback",
            },
            format="json",
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

    def test_no_patient_is_created_for_an_unenrolled_subject(self):
        before = Patient.objects.count()

        response = self.client.post(
            self.patient_url,
            {
                "provider_id": PATIENT_PROVIDER.id,
                "code": authorization_code(PATIENT_PROVIDER),
                "code_verifier": VERIFIER,
                "nonce": NONCE,
                "redirect_uri": f"{PUBLIC_BASE_URL}/auth/oidc/patient/callback",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Patient.objects.count(), before)
        self.assertEqual(PatientExternalIdentity.objects.count(), 0)
