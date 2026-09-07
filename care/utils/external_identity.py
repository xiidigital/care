"""Turning ADR-0010's `keycloak_subject` column into ADR-0011's triple.

A `keycloak_subject` records a subject and no issuer, because only one issuer
could ever be configured. The triple it has to become needs one, and there is
no way to recover it from the column.

So this function refuses. When rows exist and no issuer has been supplied it
stops the migration rather than inventing a value nobody chose or skipping the
rows silently -- and a migration that "succeeds" having dropped enrolled
identities is the worse of those two outcomes, because nothing surfaces it
until someone cannot log in.

In every CARE environment that exists today it returns an empty list: the
Keycloak adapter was never enabled and no subject was ever enrolled. It is
written for the environment where that is not true.
"""

from collections.abc import Iterable

from django.core.exceptions import ImproperlyConfigured

from config.oidc import PROVIDER_ID_PATTERN, normalize_issuer

#: Settings an operator supplies only if they actually enrolled subjects under
#: ADR-0010. Named here so the error message can point at them.
LEGACY_PROVIDER_ID_SETTING = "OIDC_LEGACY_PROVIDER_ID"
LEGACY_ISSUER_SETTING = "OIDC_LEGACY_ISSUER"


def legacy_identity_rows(
    subjects: Iterable[tuple[int, str | None]],
    *,
    provider_id: str,
    issuer: str,
) -> list[dict]:
    """Map `(principal pk, keycloak_subject)` pairs onto identity rows.

    Returns the rows to create, in input order. Raises rather than produce a
    row it cannot stand behind.
    """
    enrolled = [
        (pk, subject.strip()) for pk, subject in subjects if subject and subject.strip()
    ]
    if not enrolled:
        return []

    issuer = normalize_issuer(issuer or "")
    if not issuer:
        msg = (
            f"{len(enrolled)} enrolled Keycloak subject(s) cannot be migrated "
            f"without {LEGACY_ISSUER_SETTING}. The old column stored a subject "
            "and no issuer, and ADR-0011 resolves a principal from "
            "(provider_id, issuer, subject). Set it to the exact issuer those "
            "subjects were enrolled against."
        )
        raise ImproperlyConfigured(msg)

    if not PROVIDER_ID_PATTERN.match(provider_id or ""):
        msg = (
            f"{LEGACY_PROVIDER_ID_SETTING} '{provider_id}' is not a valid "
            "provider id, and it keys every identity row this migration "
            "writes."
        )
        raise ImproperlyConfigured(msg)

    seen: dict[str, int] = {}
    for pk, subject in enrolled:
        if subject in seen:
            msg = (
                f"Subject '{subject}' is enrolled on more than one principal "
                f"({seen[subject]} and {pk}). Resolve the duplicate before "
                "migrating; the new constraint would reject it without saying "
                "which rows collided."
            )
            raise ImproperlyConfigured(msg)
        seen[subject] = pk

    return [
        {
            "principal_pk": pk,
            "provider_id": provider_id,
            "issuer": issuer,
            "subject": subject,
        }
        for pk, subject in enrolled
    ]
