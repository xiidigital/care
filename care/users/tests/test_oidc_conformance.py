"""One contract, two issuers (ES-11 §9).

The claim ADR-0011 makes is that CARE speaks standard OpenID Connect and
cannot tell which product is answering. A claim like that is not settled by
reading a specification, and it is not settled by a test double either -- a
double agrees with whatever the code that wrote it believes.

So the same assertions run twice. Once against the standards-faithful double,
which is always available and keeps CI hermetic. Once against a real Keycloak
in a container, which is available only when a maintainer starts it. A test
that passes against one and fails against the other has found the thing this
file exists to catch: an assumption about a vendor that the specification does
not license.

Keycloak is here as the *reference implementation* and nothing more. Its name
appears in this file and in the fixture; it appears nowhere in CARE.

The live half performs the real browser dance with `requests` -- authorization
request, login form, redirect -- because the authorization code is the one
piece a token endpoint will not hand out any other way. Everything it uses is
a local-only literal from `scripts/oidc/care-test-realm.json`.
"""

import base64
import hashlib
import html
import os
import re
import time
import unittest
from urllib.parse import parse_qs, urlparse

import requests
from authlib.jose import JsonWebKey, jwt
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from config.oidc import OidcProvider
from config.oidc_service import (
    OidcExchangeError,
    discovery_for_provider,
    exchange_oidc_code,
)

#: Set by `make test-oidc`. Absent means the container is not running, and the
#: live half skips rather than fails: CI must not depend on a service CARE does
#: not require.
LIVE_ISSUER = os.environ.get("CARE_OIDC_LIVE_ISSUER", "").rstrip("/")

PUBLIC_BASE_URL = "http://localhost:9000"
NONCE = "browser-nonce-value-0001"
VERIFIER = "v" * 43
#: Derived, not written down. A hardcoded challenge that drifts from its
#: verifier fails as "the issuer rejected PKCE", which is the wrong diagnosis.
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)

LOCAL_REALM_PASSWORD = "local-only-password"


#: This machine's Docker VM boots the issuer slowly, and a real one may be
#: across a network. Generous, because a timeout here reads as a conformance
#: failure when it is only impatience.
LIVE_TIMEOUT_SECONDS = 60


def forward_cookies(session: requests.Session) -> str:
    """Return the session's cookies as a browser would send them.

    Keycloak sets its session cookies `SameSite=None`, which obliges `Secure`,
    and `requests` then withholds them over `http://` -- so the login POST
    arrives with no session and the server answers `cookie_not_found`.

    A browser does send them: `http://localhost` is a secure context by
    specification, precisely so local development works. Rather than bend a
    cookie policy to say that, the browser stand-in states the header outright.
    Nothing in CARE relaxes anything.
    """
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in session.cookies)


def live_provider(principal_type: str) -> OidcProvider:
    return OidcProvider(
        id=f"{principal_type}-sso",
        display_name=f"Local {principal_type} issuer",
        issuer=LIVE_ISSUER,
        principal_type=principal_type,
        client_id=f"care-{principal_type}",
        client_secret=f"local-only-{principal_type}-secret",
    )


class OidcContractMixin:
    """What CARE requires of any issuer. Neither half may override these."""

    def provider(self, principal_type="workforce") -> OidcProvider:
        raise NotImplementedError

    def authorization_code(self, provider) -> str:
        """One fresh, single-use authorization code for this provider."""
        raise NotImplementedError

    def session(self):
        return requests

    def redirect_uri(self, provider) -> str:
        return f"{PUBLIC_BASE_URL}/auth/oidc/{provider.principal_type}/callback"

    def exchange(self, provider, code, **overrides):
        payload = {
            "provider": provider,
            "code": code,
            "code_verifier": VERIFIER,
            "nonce": NONCE,
            "redirect_uri": self.redirect_uri(provider),
            "session": self.session(),
            **overrides,
        }
        return exchange_oidc_code(**payload)

    # -- discovery ----------------------------------------------------------

    def test_discovery_advertises_everything_care_requires(self):
        discovery = discovery_for_provider(self.provider(), self.session())

        self.assertIsNotNone(discovery, "issuer discovery could not be read")
        for endpoint in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            self.assertIn(endpoint, discovery)

    def test_every_discovered_endpoint_shares_the_issuer_origin(self):
        """CARE will fetch these. An issuer that names another origin here
        would be naming CARE's token endpoint too."""
        discovery = discovery_for_provider(self.provider(), self.session())
        issuer = urlparse(self.provider().issuer)

        for url in discovery.values():
            parsed = urlparse(url)
            self.assertEqual(
                (parsed.scheme, parsed.netloc), (issuer.scheme, issuer.netloc)
            )

    def test_the_jwks_publishes_an_asymmetric_key(self):
        discovery = discovery_for_provider(self.provider(), self.session())
        jwks = (
            self.session()
            .get(discovery["jwks_uri"], timeout=LIVE_TIMEOUT_SECONDS)
            .json()
        )

        algorithms = {key.get("alg") for key in jwks["keys"] if key.get("alg")}
        self.assertTrue(algorithms, "issuer published no signing algorithm")
        self.assertTrue(
            all(alg.startswith(("RS", "ES", "PS")) for alg in algorithms),
            f"issuer offers a non-asymmetric algorithm: {algorithms}",
        )

    # -- the exchange -------------------------------------------------------

    def test_a_pkce_code_is_exchanged_for_a_validated_identity(self):
        provider = self.provider()

        claims = self.exchange(provider, self.authorization_code(provider))

        self.assertEqual(claims["iss"].rstrip("/"), provider.issuer)
        self.assertTrue(claims["sub"])
        self.assertEqual(claims["nonce"], NONCE)

    def test_a_code_is_single_use(self):
        """Replaying it must not produce a second identity."""
        provider = self.provider()
        code = self.authorization_code(provider)
        self.exchange(provider, code)

        with self.assertRaises(OidcExchangeError):
            self.exchange(provider, code)

    def test_a_redirect_uri_that_does_not_match_is_refused(self):
        provider = self.provider()

        with self.assertRaises(OidcExchangeError):
            self.exchange(
                provider,
                self.authorization_code(provider),
                redirect_uri=f"{PUBLIC_BASE_URL}/auth/oidc/patient/callback",
            )

    def test_a_wrong_verifier_is_refused(self):
        """PKCE is what binds the code to this browser (T8)."""
        provider = self.provider()

        with self.assertRaises(OidcExchangeError):
            self.exchange(
                provider, self.authorization_code(provider), code_verifier="w" * 43
            )

    def test_a_nonce_that_does_not_match_is_refused(self):
        provider = self.provider()

        with self.assertRaises(OidcExchangeError):
            self.exchange(
                provider, self.authorization_code(provider), nonce="another-nonce"
            )

    def test_an_unknown_code_is_refused(self):
        with self.assertRaises(OidcExchangeError):
            self.exchange(self.provider(), "not-a-code-this-issuer-ever-issued")

    def test_a_token_for_another_client_is_refused(self):
        """T3/T4: the two audiences never cross, whoever the issuer is."""
        patient_code = self.authorization_code(self.provider("patient"))

        with self.assertRaises(OidcExchangeError):
            self.exchange(self.provider("workforce"), patient_code)


@override_settings(OIDC_PUBLIC_BASE_URL=PUBLIC_BASE_URL)
class DoubleConformanceTests(OidcContractMixin, SimpleTestCase):
    """The contract against a standards-faithful double. Always runs."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.issuer = "https://identity.example/realms/care"
        self.key = JsonWebKey.generate_key(
            "RSA", 2048, is_private=True, options={"kid": "double-key"}
        )
        self.issued = {}

    def provider(self, principal_type="workforce") -> OidcProvider:
        return OidcProvider(
            id=f"{principal_type}-sso",
            display_name=f"Double {principal_type}",
            issuer=self.issuer,
            principal_type=principal_type,
            client_id=f"care-{principal_type}",
            client_secret=f"double-{principal_type}-secret",
        )

    def authorization_code(self, provider) -> str:
        code = f"code-{provider.principal_type}-{len(self.issued)}"
        self.issued[code] = {"provider": provider, "used": False}
        return code

    def session(self):
        return _DoubleSession(self)


class _DoubleSession:
    """An issuer that behaves the way the specification says, and no better."""

    def __init__(self, owner):
        self.owner = owner

    def get(self, url, **kwargs):
        issuer = self.owner.issuer
        if url.endswith("/.well-known/openid-configuration"):
            return _Response(
                {
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
                    "token_endpoint": f"{issuer}/protocol/openid-connect/token",
                    "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
                }
            )
        if url.endswith("/certs"):
            key = self.owner.key.as_dict(is_private=False)
            key["alg"] = "RS256"
            return _Response({"keys": [key]})
        msg = f"Unexpected GET {url}"
        raise AssertionError(msg)

    def post(self, url, **kwargs):
        data = kwargs["data"]
        record = self.owner.issued.get(data["code"])
        # Everything below is what a conforming token endpoint refuses.
        if record is None or record["used"]:
            raise requests.RequestException
        if data["code_verifier"] != VERIFIER:
            raise requests.RequestException
        if data["redirect_uri"] != self.owner.redirect_uri(record["provider"]):
            raise requests.RequestException
        if kwargs["auth"][0] != record["provider"].client_id:
            raise requests.RequestException

        record["used"] = True
        now = int(time.time())
        token = jwt.encode(
            {"alg": "RS256", "kid": "double-key"},
            {
                "iss": self.owner.issuer,
                "aud": record["provider"].client_id,
                "sub": f"subject-of-{record['provider'].principal_type}",
                "nonce": NONCE,
                "iat": now,
                "exp": now + 300,
            },
            self.owner.key,
        ).decode()
        return _Response({"id_token": token})


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@override_settings(OIDC_PUBLIC_BASE_URL=PUBLIC_BASE_URL)
class LiveIssuerConformanceTests(OidcContractMixin, SimpleTestCase):
    """The same contract against a real Keycloak. Skipped when absent.

    Reaching an authorization code without a browser means doing what a browser
    does: request authorization, post the login form, and read the redirect
    rather than following it. It is more machinery than a double, and it is the
    only way to learn something a double cannot tell us.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not LIVE_ISSUER:
            raise unittest.SkipTest(
                "CARE_OIDC_LIVE_ISSUER is unset. Start the fixture with "
                "`make oidc-up` and run `make test-oidc`."
            )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def provider(self, principal_type="workforce") -> OidcProvider:
        return live_provider(principal_type)

    def authorization_code(self, provider) -> str:
        browser = requests.Session()
        discovery = discovery_for_provider(provider, requests)
        self.assertIsNotNone(discovery, "live issuer discovery failed")

        page = browser.get(
            discovery["authorization_endpoint"],
            params={
                "response_type": "code",
                "client_id": provider.client_id,
                "redirect_uri": self.redirect_uri(provider),
                "scope": "openid",
                "state": "conformance-state",
                "nonce": NONCE,
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
            timeout=LIVE_TIMEOUT_SECONDS,
        )
        page.raise_for_status()

        form_action = re.search(r'action="([^"]+)"', page.text)
        self.assertIsNotNone(form_action, "no login form on the issuer's page")
        action = html.unescape(form_action.group(1))

        submitted = browser.post(
            action,
            data={
                "username": (
                    "synthetic-clinician"
                    if provider.principal_type == "workforce"
                    else "synthetic-patient"
                ),
                "password": LOCAL_REALM_PASSWORD,
                "credentialId": "",
            },
            headers={
                "Cookie": forward_cookies(browser),
                "Referer": page.url,
            },
            allow_redirects=False,
            timeout=LIVE_TIMEOUT_SECONDS,
        )
        location = submitted.headers.get("Location", "")
        self.assertTrue(
            location.startswith(self.redirect_uri(provider)),
            f"issuer did not redirect to the registered callback: {location!r}",
        )

        code = parse_qs(urlparse(location).query).get("code", [None])[0]
        self.assertIsNotNone(code, "issuer returned no authorization code")
        return code
