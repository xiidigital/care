"""Carry ADR-0010 patient subjects into ADR-0011 identity rows.

The patient counterpart of `users/0030`. Same expectation -- zero rows -- and
the same refusal to invent an issuer the old column never stored.
"""

from django.conf import settings
from django.db import migrations

from care.utils.external_identity import legacy_identity_rows


def forwards(apps, schema_editor):
    # `apps.get_model` returns historical models, whose `objects` is a plain
    # manager: CARE's soft-delete manager is not `use_in_migrations`. So this
    # sees deleted principals too, which is what a faithful copy requires.
    patient_model = apps.get_model("emr", "Patient")
    identity_model = apps.get_model("emr", "PatientExternalIdentity")

    rows = legacy_identity_rows(
        patient_model.objects.values_list("pk", "keycloak_subject"),
        provider_id=settings.OIDC_LEGACY_PROVIDER_ID,
        issuer=settings.OIDC_LEGACY_ISSUER,
    )
    identity_model.objects.bulk_create(
        identity_model(
            patient_id=row["principal_pk"],
            provider_id=row["provider_id"],
            issuer=row["issuer"],
            subject=row["subject"],
        )
        for row in rows
    )


def backwards(apps, schema_editor):
    patient_model = apps.get_model("emr", "Patient")
    identity_model = apps.get_model("emr", "PatientExternalIdentity")

    legacy = identity_model.objects.filter(
        provider_id=settings.OIDC_LEGACY_PROVIDER_ID, deleted=False
    )
    for identity in legacy:
        patient_model.objects.filter(pk=identity.patient_id).update(
            keycloak_subject=identity.subject
        )
    legacy.delete()


class Migration(migrations.Migration):
    dependencies = [("emr", "0085_external_identities")]

    operations = [migrations.RunPython(forwards, backwards)]
