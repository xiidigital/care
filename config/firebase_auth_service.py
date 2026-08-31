"""Server-side validation of Firebase ID tokens for patient login."""

import re

import jwt
import requests
from cryptography.x509 import load_pem_x509_certificate
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

from care.utils.models.validators import mobile_validator

FIREBASE_CERTIFICATES_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
FIREBASE_CERTIFICATES_CACHE_KEY = "firebase-auth-public-certificates"
FIREBASE_HTTP_TIMEOUT_SECONDS = 5
DEFAULT_CERTIFICATE_TTL_SECONDS = 3600
FIREBASE_SUBJECT_MAX_LENGTH = 128


class FirebaseTokenError(Exception):
    """A deliberately detail-free Firebase authentication failure."""


def normalize_verified_phone_number(phone_number: str | None) -> str:
    """Accept a Firebase-verified phone number only inside the SMS policy.

    ADR-0010 §6 limits the first rollout to Mexico. Firebase project settings
    and the frontend country selector express the same policy, but neither is
    trustworthy at this boundary: the ID token is the only evidence CARE has,
    so the allowed calling codes are re-checked here before any CARE token is
    issued.
    """
    number = (phone_number or "").strip()
    try:
        mobile_validator(number)
    except ValidationError as exc:
        raise FirebaseTokenError from exc

    allowed = settings.FIREBASE_AUTH_SMS_COUNTRY_CODES
    if not any(number.startswith(code) for code in allowed):
        raise FirebaseTokenError

    return number


def _certificate_ttl(cache_control: str) -> int:
    match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control)
    if not match:
        return DEFAULT_CERTIFICATE_TTL_SECONDS
    return max(60, int(match.group(1)))


def _certificates(*, session, force_refresh: bool = False) -> dict:
    certificates = None if force_refresh else cache.get(FIREBASE_CERTIFICATES_CACHE_KEY)
    if certificates is not None:
        return certificates

    response = session.get(
        FIREBASE_CERTIFICATES_URL,
        timeout=FIREBASE_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    certificates = response.json()
    cache.set(
        FIREBASE_CERTIFICATES_CACHE_KEY,
        certificates,
        timeout=_certificate_ttl(response.headers.get("Cache-Control", "")),
    )
    return certificates


def _signing_key(certificate_pem: str):
    """Take the public key out of Google's X.509 certificate.

    `securetoken@system.gserviceaccount.com` publishes PEM *certificates*, not
    bare public keys, and PyJWT cannot parse a certificate as a key. Handing the
    certificate straight to `jwt.decode` fails every real Firebase token with
    `InvalidKeyError` -- a failure no mocked test can see, because a mock
    supplies whatever shape the test invented.
    """
    return load_pem_x509_certificate(certificate_pem.encode()).public_key()


def verify_firebase_id_token(
    id_token: str, *, project_id: str | None = None, session=requests
) -> dict:
    project_id = project_id or settings.FIREBASE_AUTH_PROJECT_ID
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise FirebaseTokenError

        certificates = _certificates(session=session)
        certificate = certificates.get(header["kid"])
        if certificate is None:
            certificates = _certificates(session=session, force_refresh=True)
            certificate = certificates[header["kid"]]
        claims = jwt.decode(
            id_token,
            _signing_key(certificate),
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            leeway=30,
            options={"require": ["aud", "auth_time", "exp", "iat", "iss", "sub"]},
        )
        if not claims["sub"] or len(claims["sub"]) > FIREBASE_SUBJECT_MAX_LENGTH:
            raise FirebaseTokenError
    except (
        KeyError,
        TypeError,
        ValueError,
        jwt.PyJWTError,
        requests.RequestException,
    ) as exc:
        raise FirebaseTokenError from exc

    return claims
