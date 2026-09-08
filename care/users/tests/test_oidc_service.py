import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace

import requests
from authlib.jose import JsonWebKey, jwt
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from config.oidc import OidcProvider
from config.oidc_service import OidcExchangeError, exchange_oidc_code

WORKFORCE_PROVIDER = OidcProvider(
    id="clinic-sso",
    display_name="Clinic SSO",
    issuer="https://identity.example/realms/care",
    principal_type="workforce",
    client_id="care-workforce",
    client_secret="workforce-secret",
)
PATIENT_PROVIDER = OidcProvider(
    id="patient-sso",
    display_name="Patient SSO",
    issuer="https://identity.example/realms/care",
    principal_type="patient",
    client_id="care-patient",
    client_secret="patient-secret",
)

OIDC_SETTINGS = {"OIDC_PUBLIC_BASE_URL": "https://care.example"}


@override_settings(**OIDC_SETTINGS)
class KeycloakServiceTests(SimpleTestCase):
    def setUp(self):
        # Discovery and JWKS are cached per issuer now, and these tests drive
        # the exchange with an ordered list of canned responses. A warm cache
        # would skip the discovery GET and hand the token POST the wrong one.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_pkce_code_is_exchanged_and_strict_claims_are_validated(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        claims = exchange_oidc_code(
            provider=PATIENT_PROVIDER,
            code="single-use-code",
            code_verifier="v" * 43,
            nonce="browser-nonce-value",
            redirect_uri="https://care.example/auth/oidc/patient/callback",
            session=session,
        )

        self.assertEqual(claims["sub"], "patient-subject")
        self.assertEqual(session.post_calls[0]["data"]["code_verifier"], "v" * 43)
        self.assertEqual(
            session.post_calls[0]["auth"], ("care-patient", "patient-secret")
        )

    def test_redirect_uri_must_match_the_configured_principal_callback(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://attacker.example/callback",
                session=session,
            )

        self.assertEqual(session.get_calls, [])

    def test_discovery_issuer_must_match_configuration_exactly(self):
        session = _valid_session(client_id="care-workforce", subject="staff-subject")
        session.responses[0]._payload["issuer"] = (  # noqa: SLF001
            "https://identity.example/realms/other"
        )

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=WORKFORCE_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/workforce/callback",
                session=session,
            )

    def test_id_token_nonce_must_match_browser_nonce(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="different-browser-nonce",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

    def test_a_patient_audience_token_is_refused_by_the_workforce_exchange(self):
        session = _valid_session(client_id="care-patient", subject="shared-subject")

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=WORKFORCE_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/workforce/callback",
                session=session,
            )

    def test_a_workforce_audience_token_is_refused_by_the_patient_exchange(self):
        session = _valid_session(client_id="care-workforce", subject="shared-subject")

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

    def test_expired_id_token_is_refused(self):
        session = _valid_session(
            client_id="care-patient", subject="patient-subject", lifetime=-300
        )

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

    def test_id_token_signed_by_an_unpublished_key_is_refused(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")
        foreign_key = JsonWebKey.generate_key(
            "RSA", 2048, is_private=True, options={"kid": "care-test-key"}
        )
        session.responses[2]._payload = {  # noqa: SLF001
            "keys": [foreign_key.as_dict(is_private=False)]
        }

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

    def test_an_unreachable_issuer_fails_closed(self):
        session = _FailingSession()

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

    def test_an_insecure_issuer_is_never_contacted(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        insecure = replace(
            PATIENT_PROVIDER, issuer="http://identity.example/realms/care"
        )

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=insecure,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

        self.assertEqual(session.get_calls, [])

    def test_discovery_cannot_redirect_token_exchange_to_another_origin(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")
        session.responses[0]._payload["token_endpoint"] = (  # noqa: SLF001
            "https://attacker.example/token"
        )

        with self.assertRaises(OidcExchangeError):
            exchange_oidc_code(
                provider=PATIENT_PROVIDER,
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/oidc/patient/callback",
                session=session,
            )

        self.assertEqual(session.post_calls, [])


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FailingSession:
    def get(self, url, **kwargs):
        raise requests.RequestException

    def post(self, url, **kwargs):
        raise requests.RequestException


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


ISSUER = "https://identity.example/realms/care"

DISCOVERY = {
    "issuer": ISSUER,
    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
}


def _generate_key(kid="care-test-key"):
    return JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": kid})


def _sign(
    key,
    *,
    client_id,
    subject,
    lifetime=300,
    audience=None,
    azp=None,
    kid="care-test-key",
    alg="RS256",
):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": client_id if audience is None else audience,
        "sub": subject,
        "nonce": "browser-nonce-value",
        "iat": now if lifetime > 0 else now + lifetime,
        "exp": now + lifetime,
    }
    if azp is not None:
        claims["azp"] = azp
    header = {"alg": alg}
    if kid is not None:
        header["kid"] = kid
    return jwt.encode(header, claims, key).decode()


def _valid_session(*, client_id, subject, lifetime=300, audience=None, azp=None):
    key = _generate_key()
    token = _sign(
        key,
        client_id=client_id,
        subject=subject,
        lifetime=lifetime,
        audience=audience,
        azp=azp,
    )
    session = _Session(
        [
            _Response(dict(DISCOVERY)),
            _Response({"id_token": token}),
            _Response({"keys": [key.as_dict(is_private=False)]}),
        ]
    )
    session.signing_key = key
    return session


def _cached_followup_session(*, client_id, subject, key):
    """A second login once discovery and JWKS are warm: only the token call."""
    token = _sign(key, client_id=client_id, subject=subject)
    return _Session([_Response({"id_token": token})])


def _rotated_key_session(*, client_id, subject, publish_new_key=True):
    """The issuer signs with a key the cached JWKS does not contain.

    With `publish_new_key`, the refreshed JWKS carries it and the login should
    succeed. Without, the kid stays unknown and the login must fail closed --
    and the refresh must not repeat on the next attempt.
    """
    key = _generate_key(kid="rotated-key")
    token = _sign(key, client_id=client_id, subject=subject, kid="rotated-key")
    responses = [_Response({"id_token": token})]
    if publish_new_key:
        responses.append(_Response({"keys": [key.as_dict(is_private=False)]}))
    else:
        responses.append(_Response({"keys": []}))
    return _Session(responses)


def _unsigned_token_session(*, client_id):
    """`alg: none` with an empty signature, assembled by hand.

    No library will produce this, which is the point: the verifier must reject
    it before it ever looks at the claims.
    """
    key = _generate_key()
    header = _b64url(json.dumps({"alg": "none", "kid": "care-test-key"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": client_id,
                "sub": "patient-subject",
                "nonce": "browser-nonce-value",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            }
        ).encode()
    )
    return _Session(
        [
            _Response(dict(DISCOVERY)),
            _Response({"id_token": f"{header}.{payload}."}),
            _Response({"keys": [key.as_dict(is_private=False)]}),
        ]
    )


def _hmac_token_session(*, client_id):
    """Algorithm confusion: the published public key used as an HMAC secret.

    Assembled by hand, because authlib refuses to import a PEM as a symmetric
    key -- and a test that stopped there would be proving authlib's encoder is
    careful, not that CARE's verifier is. If the verifier honoured the token's
    own `alg`, this would validate against material the issuer publishes to
    everyone.
    """
    key = _generate_key()
    public_pem = key.as_pem(is_private=False)
    header = _b64url(json.dumps({"alg": "HS256", "kid": "care-test-key"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": client_id,
                "sub": "patient-subject",
                "nonce": "browser-nonce-value",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            }
        ).encode()
    )
    signature = _b64url(
        hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return _Session(
        [
            _Response(dict(DISCOVERY)),
            _Response({"id_token": f"{header}.{payload}.{signature}"}),
            _Response({"keys": [key.as_dict(is_private=False)]}),
        ]
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@override_settings(**OIDC_SETTINGS)
class OidcDiscoveryCacheTests(SimpleTestCase):
    """T17: an issuer must not be re-fetched twice on every single login.

    Before this, one exchange meant three round trips to the issuer -- and a
    slow or flapping IdP was felt on every attempt by every user.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def exchange(self, session, **overrides):
        return exchange_oidc_code(
            **{
                "provider": PATIENT_PROVIDER,
                "code": "single-use-code",
                "code_verifier": "v" * 43,
                "nonce": "browser-nonce-value",
                "redirect_uri": "https://care.example/auth/oidc/patient/callback",
                "session": session,
                **overrides,
            }
        )

    def test_discovery_and_jwks_are_fetched_once_across_two_logins(self):
        first = _valid_session(client_id="care-patient", subject="patient-subject")
        self.exchange(first)

        second = _cached_followup_session(
            client_id="care-patient", subject="patient-subject", key=first.signing_key
        )
        claims = self.exchange(second)

        self.assertEqual(claims["sub"], "patient-subject")
        self.assertEqual(second.get_calls, [], "issuer was contacted again")
        self.assertEqual(len(second.post_calls), 1, "only the token call is per-login")

    def test_a_rotated_key_is_picked_up_by_exactly_one_refresh(self):
        """A cached JWKS must not turn key rotation into an outage."""
        first = _valid_session(client_id="care-patient", subject="patient-subject")
        self.exchange(first)

        rotated = _rotated_key_session(
            client_id="care-patient", subject="patient-subject"
        )
        claims = self.exchange(rotated)

        self.assertEqual(claims["sub"], "patient-subject")
        self.assertEqual(
            len(rotated.get_calls), 1, "exactly one JWKS refresh, not a re-discovery"
        )

    def test_a_repeated_unknown_key_does_not_refetch_within_the_window(self):
        """Otherwise an attacker picks the refresh rate by inventing a kid."""
        first = _valid_session(client_id="care-patient", subject="patient-subject")
        self.exchange(first)

        unknown = _rotated_key_session(
            client_id="care-patient", subject="patient-subject", publish_new_key=False
        )
        with self.assertRaises(OidcExchangeError):
            self.exchange(unknown)

        again = _rotated_key_session(
            client_id="care-patient", subject="patient-subject", publish_new_key=False
        )
        with self.assertRaises(OidcExchangeError):
            self.exchange(again)

        self.assertEqual(again.get_calls, [], "second unknown kid refetched anyway")


@override_settings(**OIDC_SETTINGS)
class OidcTokenHardeningTests(SimpleTestCase):
    """T4 and T5: what the token itself is allowed to claim about its own proof."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def exchange(self, session):
        return exchange_oidc_code(
            provider=PATIENT_PROVIDER,
            code="single-use-code",
            code_verifier="v" * 43,
            nonce="browser-nonce-value",
            redirect_uri="https://care.example/auth/oidc/patient/callback",
            session=session,
        )

    def test_an_unsigned_token_is_refused(self):
        """`alg: none` is the oldest JWT attack and must never reach validation."""
        session = _unsigned_token_session(client_id="care-patient")

        with self.assertRaises(OidcExchangeError):
            self.exchange(session)

    def test_a_token_signed_with_the_published_key_as_an_hmac_secret_is_refused(self):
        """Algorithm confusion: the verifier must not accept a symmetric alg."""
        session = _hmac_token_session(client_id="care-patient")

        with self.assertRaises(OidcExchangeError):
            self.exchange(session)

    def test_a_multi_valued_audience_without_azp_is_refused(self):
        """RFC 9700: with several audiences, `azp` is what names the party."""
        session = _valid_session(
            client_id="care-patient",
            subject="patient-subject",
            audience=["care-patient", "another-client"],
        )

        with self.assertRaises(OidcExchangeError):
            self.exchange(session)

    def test_a_multi_valued_audience_with_a_foreign_azp_is_refused(self):
        session = _valid_session(
            client_id="care-patient",
            subject="patient-subject",
            audience=["care-patient", "another-client"],
            azp="another-client",
        )

        with self.assertRaises(OidcExchangeError):
            self.exchange(session)

    def test_a_multi_valued_audience_naming_care_in_azp_is_accepted(self):
        session = _valid_session(
            client_id="care-patient",
            subject="patient-subject",
            audience=["care-patient", "another-client"],
            azp="care-patient",
        )

        self.assertEqual(self.exchange(session)["sub"], "patient-subject")
