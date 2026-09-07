"""Configuration contract for CARE's optional Keycloak integration."""

from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

from config.oidc import is_safe_oidc_url

KEYCLOAK_REQUIRED_SETTINGS = (
    "KEYCLOAK_ISSUER_URL",
    "KEYCLOAK_WORKFORCE_CLIENT_ID",
    "KEYCLOAK_WORKFORCE_CLIENT_SECRET",
    "KEYCLOAK_PATIENT_CLIENT_ID",
    "KEYCLOAK_PATIENT_CLIENT_SECRET",
    "KEYCLOAK_PUBLIC_BASE_URL",
)


def validate_keycloak_settings(*, enabled: bool, values: dict[str, str | None]) -> None:
    """Require Keycloak settings only when the dormant adapter is enabled."""
    if not enabled:
        return

    missing = [name for name in KEYCLOAK_REQUIRED_SETTINGS if not values.get(name)]
    if missing:
        msg = f"KEYCLOAK_ENABLED=true requires {', '.join(missing)} to be set."
        raise ImproperlyConfigured(msg)

    invalid_urls = [
        name
        for name in ("KEYCLOAK_ISSUER_URL", "KEYCLOAK_PUBLIC_BASE_URL")
        if not is_safe_oidc_url(values[name] or "")
    ]
    public_base = urlparse(values["KEYCLOAK_PUBLIC_BASE_URL"] or "")
    if public_base.path not in {"", "/"} or public_base.query or public_base.fragment:
        invalid_urls.append("KEYCLOAK_PUBLIC_BASE_URL")
    if invalid_urls:
        msg = f"Invalid Keycloak URL setting: {', '.join(sorted(set(invalid_urls)))}."
        raise ImproperlyConfigured(msg)
