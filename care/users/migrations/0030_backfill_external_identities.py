"""Carry ADR-0010 workforce subjects into ADR-0011 identity rows.

Expected to move zero rows: `KEYCLOAK_ENABLED` was false in every environment
and no subject was ever enrolled. It exists for the environment where that is
not true, and it refuses rather than guess -- see
`care/utils/external_identity.py`.
"""

from django.conf import settings
from django.db import migrations

from care.utils.external_identity import legacy_identity_rows


def forwards(apps, schema_editor):
    # `apps.get_model` returns historical models, whose `objects` is a plain
    # manager: CARE's soft-delete manager is not `use_in_migrations`. So this
    # sees deleted principals too, which is what a faithful copy requires.
    user_model = apps.get_model("users", "User")
    identity_model = apps.get_model("users", "UserExternalIdentity")

    rows = legacy_identity_rows(
        user_model.objects.values_list("pk", "keycloak_subject"),
        provider_id=settings.OIDC_LEGACY_PROVIDER_ID,
        issuer=settings.OIDC_LEGACY_ISSUER,
    )
    identity_model.objects.bulk_create(
        identity_model(
            user_id=row["principal_pk"],
            provider_id=row["provider_id"],
            issuer=row["issuer"],
            subject=row["subject"],
        )
        for row in rows
    )


def backwards(apps, schema_editor):
    """Write the subject back onto the column and drop the rows we created.

    Only rows under the legacy provider id are touched: identities enrolled
    after the migration belong to providers the old column cannot represent,
    and silently flattening them into it would lose the issuer.
    """
    user_model = apps.get_model("users", "User")
    identity_model = apps.get_model("users", "UserExternalIdentity")

    legacy = identity_model.objects.filter(
        provider_id=settings.OIDC_LEGACY_PROVIDER_ID, deleted=False
    )
    for identity in legacy:
        user_model.objects.filter(pk=identity.user_id).update(
            keycloak_subject=identity.subject
        )
    legacy.delete()


class Migration(migrations.Migration):
    dependencies = [("users", "0029_external_identities")]

    operations = [migrations.RunPython(forwards, backwards)]
