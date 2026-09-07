"""Provider-agnostic OIDC configuration contract (ADR-0011).

CARE speaks standard OpenID Connect and nothing else. A provider is a
configuration record read at startup, never a code path: adding one is a
configuration change, and zero of them is the default and fully supported
state.

Two rules in here are load-bearing rather than tidy.

`client_secret` is declared `repr=False`, so a provider that reaches a log
line, a traceback or an error message by accident carries no secret with it.

`issuer` is normalised once, here, because everything downstream compares it
for equality -- against the discovery document, and against the `issuer` stored
on an enrolled identity. A trailing slash that survives this far becomes a
login that fails for no visible reason.
"""

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

WORKFORCE = "workforce"
PATIENT = "patient"
PRINCIPAL_TYPES = (WORKFORCE, PATIENT)

#: A provider id is stored on every identity row it authenticates, so it is a
#: durable key rather than a label: lowercase, url-safe and short.
PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

REQUIRED_FIELDS = (
    "id",
    "display_name",
    "issuer",
    "principal_type",
    "client_id",
    "client_secret",
)
OPTIONAL_FIELDS = ("enabled", "scopes", "allow_rp_logout")

DEFAULT_SCOPES = ("openid", "profile", "email")

#: Loopback is exempt from the HTTPS rule so a containerised test issuer is
#: usable without weakening the rule for anything an operator would deploy.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class OidcProvider:
    """One configured issuer, serving exactly one CARE principal type."""

    id: str
    display_name: str
    issuer: str
    principal_type: str
    client_id: str
    client_secret: str = field(repr=False)
    enabled: bool = True
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    allow_rp_logout: bool = False


def is_safe_oidc_url(url: str) -> bool:
    """Whether CARE may fetch this URL server-side.

    T16: the issuer is operator input that the backend then requests, so it is
    an SSRF surface. HTTPS everywhere, plain HTTP for loopback only, and no
    credentials, query or fragment to smuggle anything into a derived URL.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    is_local_http = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS
    return bool(
        parsed.hostname
        and (parsed.scheme == "https" or is_local_http)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def load_oidc_providers(
    *, raw: str | None, path: str | None
) -> tuple[OidcProvider, ...]:
    """Read the provider set from an inline value or a file, never both.

    Both empty is the default and returns no providers. A configured source
    that cannot be read raises instead of returning nothing: starting with no
    providers because a secret mount failed is a silent authentication outage.
    """
    raw = (raw or "").strip()
    path = (path or "").strip()

    if raw and path:
        msg = (
            "Set OIDC_PROVIDERS or OIDC_PROVIDERS_FILE, not both. "
            "Two sources of provider truth cannot be reconciled at startup."
        )
        raise ImproperlyConfigured(msg)

    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"OIDC_PROVIDERS_FILE could not be read: {exc.strerror or exc}."
            raise ImproperlyConfigured(msg) from exc

    if not raw.strip():
        return ()

    source = "OIDC_PROVIDERS_FILE" if path else "OIDC_PROVIDERS"
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{source} is not valid JSON: {exc.msg} at line {exc.lineno}."
        raise ImproperlyConfigured(msg) from exc

    if not isinstance(entries, list):
        msg = f"{source} must be a JSON array of provider objects."
        raise ImproperlyConfigured(msg)

    return tuple(
        _build_provider(entry, index=index, source=source)
        for index, entry in enumerate(entries)
    )


def _build_provider(entry: object, *, index: int, source: str) -> OidcProvider:
    if not isinstance(entry, dict):
        msg = f"{source} entry #{index} must be a JSON object."
        raise ImproperlyConfigured(msg)

    label = entry.get("id") if isinstance(entry.get("id"), str) else f"#{index}"

    unknown = sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        # A dropped typo is a dropped control: `clientsecret` would leave the
        # provider silently unauthenticated rather than obviously misconfigured.
        msg = f"{source} provider '{label}' has unknown field(s): {', '.join(unknown)}."
        raise ImproperlyConfigured(msg)

    missing = [name for name in REQUIRED_FIELDS if name not in entry]
    if missing:
        msg = (
            f"{source} provider '{label}' is missing required field(s): "
            f"{', '.join(missing)}."
        )
        raise ImproperlyConfigured(msg)

    scopes = entry.get("scopes", DEFAULT_SCOPES)
    if isinstance(scopes, str) or not isinstance(scopes, list | tuple):
        msg = f"{source} provider '{label}' field 'scopes' must be a list."
        raise ImproperlyConfigured(msg)

    return OidcProvider(
        id=str(entry["id"]).strip(),
        display_name=str(entry["display_name"]).strip(),
        issuer=normalize_issuer(str(entry["issuer"])),
        principal_type=str(entry["principal_type"]).strip().lower(),
        client_id=str(entry["client_id"]).strip(),
        client_secret=str(entry["client_secret"]),
        enabled=bool(entry.get("enabled", True)),
        scopes=tuple(str(scope).strip() for scope in scopes),
        allow_rp_logout=bool(entry.get("allow_rp_logout", False)),
    )


def normalize_issuer(issuer: str) -> str:
    """Strip whitespace and one trailing slash. Compared for equality later."""
    return issuer.strip().rstrip("/")


def validate_oidc_providers(
    providers: tuple[OidcProvider, ...],
    *,
    public_base_url: str,
    enforce: bool,
) -> None:
    """Refuse to start on a provider set that could not work, or is unsafe.

    `enforce` follows the role split ADR-0006 established and ADR-0010 already
    applies: every role assembles the same environment, but only the API serves
    an authentication route or needs a client secret.

    Enabled and disabled providers are held to different contracts. An id is
    checked either way, because it keys stored identities and a collision is
    not a problem that starts when the provider is switched on. Everything else
    is checked only while enabled, so a provider parked at `enabled: false` is
    inert rather than a startup failure.
    """
    if not enforce:
        return

    seen: set[str] = set()
    for provider in providers:
        if not PROVIDER_ID_PATTERN.match(provider.id):
            msg = (
                f"OIDC provider id '{provider.id}' is invalid: use 1-32 "
                "lowercase letters, digits, hyphen or underscore, starting "
                "with a letter or digit."
            )
            raise ImproperlyConfigured(msg)
        if provider.id in seen:
            msg = f"OIDC provider id '{provider.id}' is configured more than once."
            raise ImproperlyConfigured(msg)
        seen.add(provider.id)

    enabled = [provider for provider in providers if provider.enabled]
    for provider in enabled:
        _validate_enabled_provider(provider)

    if enabled:
        _validate_public_base_url(public_base_url)


def _validate_enabled_provider(provider: OidcProvider) -> None:
    def refuse(field_name: str, detail: str) -> None:
        # Only the id and the field name. Never the record, never a value that
        # could be a secret.
        msg = f"OIDC provider '{provider.id}' field '{field_name}' {detail}"
        raise ImproperlyConfigured(msg)

    if provider.principal_type not in PRINCIPAL_TYPES:
        refuse("principal_type", f"must be one of {', '.join(PRINCIPAL_TYPES)}.")
    if not provider.display_name:
        refuse("display_name", "must not be empty.")
    if not is_safe_oidc_url(provider.issuer):
        refuse(
            "issuer",
            "must be an https URL (http is accepted for localhost only) with "
            "no credentials, query or fragment.",
        )
    if not provider.client_id:
        refuse("client_id", "must not be empty.")
    if not provider.client_secret:
        refuse("client_secret", "must not be empty.")
    if "openid" not in provider.scopes:
        refuse("scopes", "must include 'openid'.")


def _validate_public_base_url(public_base_url: str) -> None:
    url = (public_base_url or "").strip()
    if not url:
        msg = (
            "OIDC_PUBLIC_BASE_URL is required when an OIDC provider is enabled: "
            "callback URLs are derived from it and matched byte-for-byte."
        )
        raise ImproperlyConfigured(msg)

    parsed = urlparse(url)
    if not is_safe_oidc_url(url) or parsed.path not in {"", "/"}:
        msg = (
            "OIDC_PUBLIC_BASE_URL must be a bare https origin such as "
            "https://care.example, with no path, query or fragment."
        )
        raise ImproperlyConfigured(msg)


def callback_url(public_base_url: str, principal_type: str) -> str:
    """The one redirect URI CARE will accept for a principal type.

    Vendor-free by construction (ADR-0011 §2): the path names the protocol and
    the principal, never the provider on the other end.
    """
    if principal_type not in PRINCIPAL_TYPES:
        msg = f"Unsupported OIDC principal type: {principal_type}"
        raise ValueError(msg)
    return f"{public_base_url.rstrip('/')}/auth/oidc/{principal_type}/callback"


def providers_for(
    providers: tuple[OidcProvider, ...], principal_type: str
) -> tuple[OidcProvider, ...]:
    """Enabled providers serving one principal type, in configuration order."""
    return tuple(
        provider
        for provider in providers
        if provider.enabled and provider.principal_type == principal_type
    )


def without_secrets(provider: OidcProvider) -> OidcProvider:
    """A copy safe to hand to anything that might serialise it."""
    return replace(provider, client_secret="")
