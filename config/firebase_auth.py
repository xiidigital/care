"""Configuration contract for optional direct Firebase patient login."""

from django.core.exceptions import ImproperlyConfigured

#: ADR-0010 §6 limits the first Firebase SMS rollout to Mexico. The policy is
#: expressed as E.164 calling-code prefixes so an operator can widen it later
#: through configuration instead of a code change.
DEFAULT_SMS_COUNTRY_CODES = ("+52",)

#: E.164 assigns country calling codes of one to three digits.
MAX_CALLING_CODE_DIGITS = 3


def validate_firebase_auth_settings(
    *,
    enabled: bool,
    project_id: str | None,
    sms_country_codes: tuple[str, ...] | list[str] | None = None,
) -> None:
    if not enabled:
        return

    if not project_id:
        msg = "FIREBASE_AUTH_ENABLED=true requires FIREBASE_AUTH_PROJECT_ID to be set."
        raise ImproperlyConfigured(msg)

    codes = (
        DEFAULT_SMS_COUNTRY_CODES if sms_country_codes is None else sms_country_codes
    )
    if not codes or any(not is_calling_code(code) for code in codes):
        msg = (
            "FIREBASE_AUTH_SMS_COUNTRY_CODES must be a non-empty list of E.164 "
            "calling codes such as +52."
        )
        raise ImproperlyConfigured(msg)


def is_calling_code(value: str) -> bool:
    """A calling code is a plus sign followed by one to three digits."""
    return (
        isinstance(value, str)
        and value.startswith("+")
        and value[1:].isdigit()
        and 1 <= len(value) - 1 <= MAX_CALLING_CODE_DIGITS
    )
