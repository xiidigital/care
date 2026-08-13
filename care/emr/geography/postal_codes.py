"""Postal-code presentation and validation by ISO 3166-1 alpha-2 country."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PostalCodeRule:
    label: str
    placeholder: str
    pattern: re.Pattern[str]
    error: str


DEFAULT_POSTAL_CODE_RULE = PostalCodeRule(
    label="Postal code",
    placeholder="Enter postal code",
    pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{1,11}$"),
    error="Enter a valid postal code",
)

POSTAL_CODE_RULES = {
    "IN": PostalCodeRule(
        label="PIN code",
        placeholder="Enter PIN code",
        pattern=re.compile(r"^\d{6}$"),
        error="PIN code must be 6 digits",
    ),
    "MX": PostalCodeRule(
        label="Código postal",
        placeholder="Ingresa el código postal",
        pattern=re.compile(r"^\d{5}$"),
        error="El código postal debe tener 5 dígitos",
    ),
    "US": PostalCodeRule(
        label="ZIP code",
        placeholder="Enter ZIP code",
        pattern=re.compile(r"^\d{5}(?:-\d{4})?$"),
        error="ZIP code must be 5 digits or ZIP+4",
    ),
}


def get_postal_code_rule(country_code: str | None) -> PostalCodeRule:
    """Return the display and validation rule for an ISO country code."""
    return POSTAL_CODE_RULES.get((country_code or "").upper(), DEFAULT_POSTAL_CODE_RULE)


def validate_postal_code(value: str | None, country_code: str | None) -> str | None:
    """Normalize and validate an optional postal code for a country."""
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized:
        return None
    rule = get_postal_code_rule(country_code)
    if not rule.pattern.fullmatch(normalized):
        raise ValueError(rule.error)
    return normalized
