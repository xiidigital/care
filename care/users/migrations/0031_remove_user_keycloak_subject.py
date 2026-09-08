"""Retire the ADR-0010 subject column.

Its replacement is `users.UserExternalIdentity`, keyed on
`(provider_id, issuer, subject)`. A subject with no issuer cannot resolve a
principal safely once more than one issuer can be configured, which is the
defect ADR-0011 exists to remove.

Reversible: `users.0030_backfill_external_identities` writes the
subject back onto this column before it is dropped again.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("users", "0030_backfill_external_identities")]

    operations = [
        migrations.RemoveField(model_name="user", name="keycloak_subject"),
    ]
