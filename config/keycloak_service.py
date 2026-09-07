"""Narrow OIDC code-exchange boundary for the optional Keycloak adapter."""

from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from authlib.jose import JoseError, JsonWebKey, jwt
from django.conf import settings

from config.oidc import is_safe_oidc_url

OIDC_TIMEOUT_SECONDS = 5


class KeycloakExchangeError(Exception):
    """A deliberately detail-free external authentication failure."""


@dataclass(frozen=True)
class KeycloakClient:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str


def _client_for(principal_type: str) -> KeycloakClient:
    base_url = settings.KEYCLOAK_PUBLIC_BASE_URL.rstrip("/")
    if principal_type == "workforce":
        return KeycloakClient(
            issuer=settings.KEYCLOAK_ISSUER_URL.rstrip("/"),
            client_id=settings.KEYCLOAK_WORKFORCE_CLIENT_ID,
            client_secret=settings.KEYCLOAK_WORKFORCE_CLIENT_SECRET,
            redirect_uri=f"{base_url}/auth/keycloak/workforce/callback",
        )
    if principal_type == "patient":
        return KeycloakClient(
            issuer=settings.KEYCLOAK_ISSUER_URL.rstrip("/"),
            client_id=settings.KEYCLOAK_PATIENT_CLIENT_ID,
            client_secret=settings.KEYCLOAK_PATIENT_CLIENT_SECRET,
            redirect_uri=f"{base_url}/auth/keycloak/patient/callback",
        )
    msg = "Unsupported Keycloak principal type"
    raise ValueError(msg)


def _require_safe_oidc_url(url: str) -> str:
    if not is_safe_oidc_url(url):
        raise KeycloakExchangeError
    return url


def _require_issuer_origin(url: str, issuer: str) -> str:
    safe_url = _require_safe_oidc_url(url)
    parsed = urlparse(safe_url)
    issuer_parsed = urlparse(issuer)
    if (parsed.scheme, parsed.netloc) != (issuer_parsed.scheme, issuer_parsed.netloc):
        raise KeycloakExchangeError
    return safe_url


def exchange_keycloak_code(
    *,
    principal_type: str,
    code: str,
    code_verifier: str,
    nonce: str,
    redirect_uri: str,
    session=requests,
) -> dict:
    """Exchange one PKCE code and validate the returned OIDC identity token."""
    client = _client_for(principal_type)
    if redirect_uri != client.redirect_uri:
        raise KeycloakExchangeError

    discovery_url = _require_safe_oidc_url(
        f"{client.issuer}/.well-known/openid-configuration"
    )
    try:
        discovery_response = session.get(discovery_url, timeout=OIDC_TIMEOUT_SECONDS)
        discovery_response.raise_for_status()
        discovery = discovery_response.json()

        if discovery.get("issuer", "").rstrip("/") != client.issuer:
            raise KeycloakExchangeError

        token_endpoint = _require_issuer_origin(
            discovery["token_endpoint"], client.issuer
        )
        jwks_uri = _require_issuer_origin(discovery["jwks_uri"], client.issuer)
        token_response = session.post(
            token_endpoint,
            auth=(client.client_id, client.client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": client.redirect_uri,
            },
            timeout=OIDC_TIMEOUT_SECONDS,
        )
        token_response.raise_for_status()
        id_token = token_response.json()["id_token"]

        jwks_response = session.get(jwks_uri, timeout=OIDC_TIMEOUT_SECONDS)
        jwks_response.raise_for_status()
        key_set = JsonWebKey.import_key_set(jwks_response.json())
        claims = jwt.decode(
            id_token,
            key_set,
            claims_options={
                "iss": {"essential": True, "value": client.issuer},
                "aud": {"essential": True, "value": client.client_id},
                "exp": {"essential": True},
                "iat": {"essential": True},
                "nonce": {"essential": True},
                "sub": {"essential": True},
            },
        )
        claims.validate(leeway=30)
        if claims.get("nonce") != nonce:
            raise KeycloakExchangeError
    except (
        JoseError,
        KeyError,
        TypeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        raise KeycloakExchangeError from exc

    return dict(claims)
