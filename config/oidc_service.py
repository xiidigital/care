"""The one place CARE accepts an external token (ADR-0011 §4).

Everything here is standard OpenID Connect. The function takes a configured
provider record and knows nothing about who implements it: Keycloak, Entra ID,
Authentik, Zitadel and the test double all reach this code identically, and a
branch on which one is answering would mean the abstraction had leaked.

Its only output is a set of validated claims. Deciding which CARE principal
those claims belong to -- and whether that principal may do anything -- happens
in `config/oidc_views.py`, against an enrolled identity row.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from authlib.jose import JoseError, JsonWebKey, JsonWebToken
from django.conf import settings
from django.core.cache import cache

from config.oidc import OidcProvider, callback_url, is_safe_oidc_url

OIDC_TIMEOUT_SECONDS = 5

#: Asymmetric only. `none` needs no explanation; HMAC is excluded because the
#: verifying key is published to the world at `jwks_uri`, so honouring the
#: token's own choice of a symmetric algorithm would let anyone who can read
#: the JWKS sign a token CARE accepts (T5).
ALLOWED_SIGNING_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

#: Constructed with the allowlist, so an algorithm outside it is refused at the
#: JWS layer -- before any claim is read.
_jwt = JsonWebToken(list(ALLOWED_SIGNING_ALGORITHMS))

#: An unknown `kid` means either a key rotation or a forged token, and CARE
#: cannot tell which without asking. Asking is rate-limited so that a forged
#: `kid` cannot be used to drive traffic at the issuer (T6, T17).
JWKS_REFRESH_MIN_INTERVAL_SECONDS = 60


def _cache_key(kind: str, issuer: str) -> str:
    """Hash the issuer: it is a URL, and cache keys have length limits."""
    digest = hashlib.sha256(issuer.encode()).hexdigest()[:32]
    return f"oidc:{kind}:{digest}"


def _fetch_discovery(client, session) -> dict:
    """Read the issuer's own description of itself, then pin it to the issuer.

    Everything CARE subsequently talks to comes from this document, so an
    issuer that could name another origin here would be naming CARE's token
    endpoint too.
    """
    discovery_url = _require_safe_oidc_url(
        f"{client.issuer}/.well-known/openid-configuration"
    )
    response = session.get(discovery_url, timeout=OIDC_TIMEOUT_SECONDS)
    response.raise_for_status()
    discovery = response.json()

    if discovery.get("issuer", "").rstrip("/") != client.issuer:
        raise OidcExchangeError

    return {
        "token_endpoint": _require_issuer_origin(
            discovery["token_endpoint"], client.issuer
        ),
        "jwks_uri": _require_issuer_origin(discovery["jwks_uri"], client.issuer),
    }


def _discovery_for(client, session) -> dict:
    key = _cache_key("discovery", client.issuer)
    cached = cache.get(key)
    if cached is not None:
        return cached

    discovery = _fetch_discovery(client, session)
    cache.set(key, discovery, settings.OIDC_DISCOVERY_CACHE_SECONDS)
    return discovery


def _fetch_jwks(jwks_uri: str, issuer: str, session) -> dict:
    response = session.get(jwks_uri, timeout=OIDC_TIMEOUT_SECONDS)
    response.raise_for_status()
    jwks = response.json()
    cache.set(_cache_key("jwks", issuer), jwks, settings.OIDC_JWKS_CACHE_SECONDS)
    return jwks


def _unverified_kid(id_token: str) -> str | None:
    """Read the `kid` from an unverified header, to choose a key -- nothing else.

    The header is attacker-controlled until the signature checks out, so it is
    used only to decide which published key to try, never to decide whether the
    token is good.
    """
    try:
        raw = id_token.split(".", 1)[0]
        header = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def _has_kid(jwks: dict, kid: str) -> bool:
    return any(key.get("kid") == kid for key in jwks.get("keys", []))


def _key_set_for(client, jwks_uri: str, id_token: str, session):
    jwks = cache.get(_cache_key("jwks", client.issuer))
    if jwks is None:
        jwks = _fetch_jwks(jwks_uri, client.issuer, session)

    kid = _unverified_kid(id_token)
    if (
        kid
        and not _has_kid(jwks, kid)
        # `add` succeeds only if no refresh has happened in the window, so a
        # rotation costs one fetch and a stream of forged kids costs the same.
        and cache.add(
            _cache_key("jwks-refresh", client.issuer),
            "1",
            JWKS_REFRESH_MIN_INTERVAL_SECONDS,
        )
    ):
        jwks = _fetch_jwks(jwks_uri, client.issuer, session)

    return JsonWebKey.import_key_set(jwks)


def _require_authorized_party(claims: dict, client_id: str) -> None:
    """RFC 9700: with several audiences, `azp` is what names the recipient.

    A token minted for another client of the same issuer already fails the
    audience check when `aud` is a single value. When it is not, `aud` alone no
    longer identifies who the token is for.
    """
    audience = claims.get("aud")
    is_multi_valued = isinstance(audience, list | tuple) and len(audience) > 1
    if is_multi_valued and claims.get("azp") != client_id:
        raise OidcExchangeError


class OidcExchangeError(Exception):
    """A deliberately detail-free external authentication failure."""


@dataclass(frozen=True)
class OidcClient:
    """A provider record joined to the callback this deployment registered."""

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str


def client_for(provider: OidcProvider) -> OidcClient:
    return OidcClient(
        issuer=provider.issuer,
        client_id=provider.client_id,
        client_secret=provider.client_secret,
        redirect_uri=callback_url(
            settings.OIDC_PUBLIC_BASE_URL, provider.principal_type
        ),
    )


def _require_safe_oidc_url(url: str) -> str:
    if not is_safe_oidc_url(url):
        raise OidcExchangeError
    return url


def _require_issuer_origin(url: str, issuer: str) -> str:
    safe_url = _require_safe_oidc_url(url)
    parsed = urlparse(safe_url)
    issuer_parsed = urlparse(issuer)
    if (parsed.scheme, parsed.netloc) != (issuer_parsed.scheme, issuer_parsed.netloc):
        raise OidcExchangeError
    return safe_url


def exchange_oidc_code(
    *,
    provider: OidcProvider,
    code: str,
    code_verifier: str,
    nonce: str,
    redirect_uri: str,
    session=requests,
) -> dict:
    """Exchange one PKCE code and validate the returned OIDC identity token."""
    client = client_for(provider)
    # Byte-for-byte. A redirect URI that merely looks equivalent is the seam an
    # authorization-code injection needs (T8).
    if redirect_uri != client.redirect_uri:
        raise OidcExchangeError

    try:
        discovery = _discovery_for(client, session)
        token_response = session.post(
            discovery["token_endpoint"],
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

        key_set = _key_set_for(client, discovery["jwks_uri"], id_token, session)
        claims = _jwt.decode(
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
            raise OidcExchangeError
        _require_authorized_party(claims, client.client_id)
    except (
        JoseError,
        KeyError,
        TypeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        raise OidcExchangeError from exc

    return dict(claims)
