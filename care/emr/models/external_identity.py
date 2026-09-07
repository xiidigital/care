"""External identity for patient principals (ADR-0011 §3).

One row per `(provider_id, issuer, subject)`. The issuer is stored rather than
read from live configuration on purpose: if a provider record is later pointed
at a different issuer, enrolled identities stop resolving and login fails
closed, instead of every enrolled account silently re-binding to whatever the
new issuer says.
"""

from django.db import models

from care.utils.models.base import BaseModel


class PatientExternalIdentity(BaseModel):
    patient = models.ForeignKey(
        "emr.Patient",
        on_delete=models.CASCADE,
        related_name="external_identities",
    )
    provider_id = models.CharField(max_length=32, db_index=True)
    issuer = models.CharField(max_length=512)
    subject = models.CharField(max_length=255)

    #: Rule §5.3 makes a patient link administrative, so the actor is part of
    #: the record. SET_NULL because an audit trail that disappears with the
    #: administrator is not an audit trail.
    linked_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Patient External Identity"
        verbose_name_plural = "Patient External Identities"
        constraints = [
            # Partial, matching the soft delete this codebase uses everywhere:
            # an unlinked identity stops resolving and frees its triple for a
            # deliberate re-enrolment (rules §5.5 and §5.6).
            models.UniqueConstraint(
                fields=["provider_id", "issuer", "subject"],
                condition=models.Q(deleted=False),
                name="unique_patient_external_identity",
            )
        ]

    def __str__(self):
        return f"{self.provider_id}:{self.subject[:8]}… -> patient {self.patient_id}"
