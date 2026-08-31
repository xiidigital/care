import time

import requests
from authlib.jose import JsonWebKey, jwt
from django.test import SimpleTestCase, override_settings

from config.keycloak_service import KeycloakExchangeError, exchange_keycloak_code

KEYCLOAK_SETTINGS = {
    "KEYCLOAK_ISSUER_URL": "https://identity.example/realms/care",
    "KEYCLOAK_WORKFORCE_CLIENT_ID": "care-workforce",
    "KEYCLOAK_WORKFORCE_CLIENT_SECRET": "workforce-secret",
    "KEYCLOAK_PATIENT_CLIENT_ID": "care-patient",
    "KEYCLOAK_PATIENT_CLIENT_SECRET": "patient-secret",
    "KEYCLOAK_PUBLIC_BASE_URL": "https://care.example",
}


@override_settings(**KEYCLOAK_SETTINGS)
class KeycloakServiceTests(SimpleTestCase):
    def test_pkce_code_is_exchanged_and_strict_claims_are_validated(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        claims = exchange_keycloak_code(
            principal_type="patient",
            code="single-use-code",
            code_verifier="v" * 43,
            nonce="browser-nonce-value",
            redirect_uri="https://care.example/auth/keycloak/patient/callback",
            session=session,
        )

        self.assertEqual(claims["sub"], "patient-subject")
        self.assertEqual(session.post_calls[0]["data"]["code_verifier"], "v" * 43)
        self.assertEqual(
            session.post_calls[0]["auth"], ("care-patient", "patient-secret")
        )

    def test_redirect_uri_must_match_the_configured_principal_callback(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
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

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="workforce",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/workforce/callback",
                session=session,
            )

    def test_id_token_nonce_must_match_browser_nonce(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="different-browser-nonce",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
                session=session,
            )

    def test_a_patient_audience_token_is_refused_by_the_workforce_exchange(self):
        session = _valid_session(client_id="care-patient", subject="shared-subject")

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="workforce",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/workforce/callback",
                session=session,
            )

    def test_a_workforce_audience_token_is_refused_by_the_patient_exchange(self):
        session = _valid_session(client_id="care-workforce", subject="shared-subject")

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
                session=session,
            )

    def test_expired_id_token_is_refused(self):
        session = _valid_session(
            client_id="care-patient", subject="patient-subject", lifetime=-300
        )

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
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

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
                session=session,
            )

    def test_an_unreachable_issuer_fails_closed(self):
        session = _FailingSession()

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
                session=session,
            )

    def test_an_insecure_issuer_is_never_contacted(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")

        with (
            override_settings(
                KEYCLOAK_ISSUER_URL="http://identity.example/realms/care"
            ),
            self.assertRaises(KeycloakExchangeError),
        ):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
                session=session,
            )

        self.assertEqual(session.get_calls, [])

    def test_discovery_cannot_redirect_token_exchange_to_another_origin(self):
        session = _valid_session(client_id="care-patient", subject="patient-subject")
        session.responses[0]._payload["token_endpoint"] = (  # noqa: SLF001
            "https://attacker.example/token"
        )

        with self.assertRaises(KeycloakExchangeError):
            exchange_keycloak_code(
                principal_type="patient",
                code="single-use-code",
                code_verifier="v" * 43,
                nonce="browser-nonce-value",
                redirect_uri="https://care.example/auth/keycloak/patient/callback",
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


def _valid_session(*, client_id, subject, lifetime=300):
    issuer = "https://identity.example/realms/care"
    key = JsonWebKey.generate_key(
        "RSA", 2048, is_private=True, options={"kid": "care-test-key"}
    )
    now = int(time.time())
    token = jwt.encode(
        {"alg": "RS256", "kid": "care-test-key"},
        {
            "iss": issuer,
            "aud": client_id,
            "sub": subject,
            "nonce": "browser-nonce-value",
            "iat": now if lifetime > 0 else now + lifetime,
            "exp": now + lifetime,
        },
        key,
    ).decode()
    return _Session(
        [
            _Response(
                {
                    "issuer": issuer,
                    "token_endpoint": f"{issuer}/protocol/openid-connect/token",
                    "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
                }
            ),
            _Response({"id_token": token}),
            _Response({"keys": [key.as_dict(is_private=False)]}),
        ]
    )
