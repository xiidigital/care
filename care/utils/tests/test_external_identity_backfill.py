"""The rule the ADR-0010 -> ADR-0011 data migration must not get wrong.

A `keycloak_subject` column records a subject and no issuer. The triple it has
to become needs one. There is exactly one honest thing to do when rows exist
and no issuer has been supplied, and it is to stop: inventing an issuer would
bind every enrolled account to a value nobody chose, and skipping the rows
would drop enrolled identities silently.

In every environment CARE has today this function returns an empty list,
because no subject was ever enrolled. It is written and tested for the one
where that is not true.
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from care.utils.external_identity import legacy_identity_rows

ISSUER = "https://identity.example/realms/care"


class LegacyIdentityRowTests(SimpleTestCase):
    def test_no_enrolled_subjects_needs_no_issuer(self):
        """The expected case everywhere: nothing to migrate, nothing required."""
        self.assertEqual(
            legacy_identity_rows([], provider_id="keycloak", issuer=""), []
        )

    def test_subjects_are_mapped_onto_the_triple(self):
        rows = legacy_identity_rows(
            [(1, "sub-a"), (2, "sub-b")], provider_id="keycloak", issuer=ISSUER
        )

        self.assertEqual(
            rows,
            [
                {
                    "principal_pk": 1,
                    "provider_id": "keycloak",
                    "issuer": ISSUER,
                    "subject": "sub-a",
                },
                {
                    "principal_pk": 2,
                    "provider_id": "keycloak",
                    "issuer": ISSUER,
                    "subject": "sub-b",
                },
            ],
        )

    def test_rows_without_an_issuer_refuse_rather_than_guess(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            legacy_identity_rows([(1, "sub-a")], provider_id="keycloak", issuer="")

        self.assertIn("OIDC_LEGACY_ISSUER", str(caught.exception))

    def test_rows_without_an_issuer_are_never_silently_dropped(self):
        """The failure mode that matters: a migration that 'succeeds' empty."""
        with self.assertRaises(ImproperlyConfigured):
            legacy_identity_rows([(1, "sub-a")], provider_id="keycloak", issuer="   ")

    def test_blank_and_null_subjects_are_not_identities(self):
        rows = legacy_identity_rows(
            [(1, None), (2, ""), (3, "  "), (4, "sub-d")],
            provider_id="keycloak",
            issuer=ISSUER,
        )

        self.assertEqual([row["subject"] for row in rows], ["sub-d"])

    def test_only_blank_subjects_still_needs_no_issuer(self):
        self.assertEqual(
            legacy_identity_rows(
                [(1, ""), (2, None)], provider_id="keycloak", issuer=""
            ),
            [],
        )

    def test_the_issuer_is_normalised_the_same_way_as_configuration(self):
        """It will be compared for equality against a provider record."""
        rows = legacy_identity_rows(
            [(1, "sub-a")], provider_id="keycloak", issuer=f"{ISSUER}/  "
        )

        self.assertEqual(rows[0]["issuer"], ISSUER)

    def test_a_malformed_provider_id_refuses(self):
        """The id keys every row it writes; a bad one is not recoverable later."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            legacy_identity_rows(
                [(1, "sub-a")], provider_id="Legacy Keycloak", issuer=ISSUER
            )

        self.assertIn("OIDC_LEGACY_PROVIDER_ID", str(caught.exception))

    def test_duplicate_subjects_refuse_rather_than_violate_the_constraint(self):
        """Impossible under the old unique column, but the migration is the
        last place a duplicate could still be caught with a readable message."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            legacy_identity_rows(
                [(1, "sub-a"), (2, "sub-a")], provider_id="keycloak", issuer=ISSUER
            )

        self.assertIn("sub-a", str(caught.exception))
