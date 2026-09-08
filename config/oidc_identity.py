"""Creating and removing external identity links (ADR-0011 §5).

Authentication asks which external subject is present. Linking decides whose
CARE account that subject reaches, and the exchange will honour the answer
indefinitely -- which makes this, not the token validation, the operation an
attacker would rather reach.

So everything here is written as a refusal. A triple that is already linked is
never moved to another principal, because "link my subject to their account"
is the whole attack. A subject is never edited, because editing one is
indistinguishable from moving it. A patient link always names an
administrator, because CARE's existing custody rules decide who may attach an
identity to a clinical record -- a patient's browser does not.

Every outcome is recorded on the `audit_log` logger the rest of CARE already
uses, and never with the subject: it is the credential half of the triple, and
anyone who can read it and control a provider record could enrol it elsewhere.
"""

import logging

from django.db import IntegrityError, transaction

from care.emr.models import PatientExternalIdentity
from care.users.models import UserExternalIdentity
from config.oidc import PATIENT, WORKFORCE, OidcProvider

logger = logging.getLogger("audit_log")


class IdentityLinkError(Exception):
    """A link that CARE will not create."""


class IdentityAlreadyLinkedError(IdentityLinkError):
    """Rule §5.4: the first writer of a triple keeps it."""


def _record(event: str, *, provider: OidcProvider, principal_id, outcome: str) -> None:
    logger.info(
        "oidc-identity %s principal_type=%s principal=%s provider=%s outcome=%s",
        event,
        provider.principal_type,
        principal_id,
        provider.id,
        outcome,
    )


def _require_principal_type(provider: OidcProvider, expected: str) -> None:
    if provider.principal_type != expected:
        msg = (
            f"Provider '{provider.id}' serves {provider.principal_type} principals, "
            f"not {expected}."
        )
        raise ValueError(msg)


def link_workforce_identity(
    *, user, provider: OidcProvider, subject: str, linked_by=None
) -> UserExternalIdentity:
    """Link an external subject to a CARE user.

    `linked_by` is the administrator who acted, or None when the principal
    linked its own identity while already authenticated to CARE. Both are
    legitimate under rule §5.2; the null is the record that nobody else was
    involved.
    """
    _require_principal_type(provider, WORKFORCE)
    try:
        with transaction.atomic():
            identity = UserExternalIdentity.objects.create(
                user=user,
                provider_id=provider.id,
                issuer=provider.issuer,
                subject=subject,
                linked_by=linked_by,
            )
    except IntegrityError as exc:
        # The database constraint is the arbiter, not a prior read: two
        # concurrent links to the same triple must not both believe they won.
        _record(
            "relink_refused",
            provider=provider,
            principal_id=user.external_id,
            outcome="conflict",
        )
        raise IdentityAlreadyLinkedError from exc

    _record(
        "link",
        provider=provider,
        principal_id=user.external_id,
        outcome="administrative" if linked_by else "self_service",
    )
    return identity


def link_patient_identity(
    *, patient, provider: OidcProvider, subject: str, linked_by
) -> PatientExternalIdentity:
    """Link an external subject to a patient record. Administrative only."""
    _require_principal_type(provider, PATIENT)
    if linked_by is None:
        msg = (
            "A patient identity link requires the administrator who created it "
            "(ADR-0011 §5.3): there is no self-service path to a clinical record."
        )
        raise ValueError(msg)

    try:
        with transaction.atomic():
            identity = PatientExternalIdentity.objects.create(
                patient=patient,
                provider_id=provider.id,
                issuer=provider.issuer,
                subject=subject,
                linked_by=linked_by,
            )
    except IntegrityError as exc:
        _record(
            "relink_refused",
            provider=provider,
            principal_id=patient.external_id,
            outcome="conflict",
        )
        raise IdentityAlreadyLinkedError from exc

    _record(
        "link",
        provider=provider,
        principal_id=patient.external_id,
        outcome="administrative",
    )
    return identity


def unlink_workforce_identity(*, user, external_id) -> bool:
    """Remove one of this user's own identities. Returns whether it existed.

    Scoped to the caller's own principal on purpose: an unlink endpoint that
    took an arbitrary identity id would let any authenticated user revoke
    anyone's access.

    Rule §5.6 -- the principal, its records and its other login methods are
    untouched, including when this was its last identity. It falls back to
    whatever else is enabled.
    """
    identity = UserExternalIdentity.objects.filter(
        user=user, external_id=external_id
    ).first()
    if identity is None:
        return False

    provider_id = identity.provider_id
    principal_type = WORKFORCE
    identity.delete()

    logger.info(
        "oidc-identity unlink principal_type=%s principal=%s provider=%s outcome=%s",
        principal_type,
        user.external_id,
        provider_id,
        "removed",
    )
    return True
