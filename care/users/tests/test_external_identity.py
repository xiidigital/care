"""External identity for workforce principals (ADR-0011 §3).

The subject of these tests is one claim: CARE resolves a principal from
`(provider_id, issuer, subject)` and from nothing else. The defect they exist
to prevent is the one ADR-0011 was written for -- a subject minted by one
issuer resolving a principal enrolled against another.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase
from model_bakery import baker

from care.users.models import User, UserExternalIdentity

ISSUER_A = "https://identity-a.example/realms/care"
ISSUER_B = "https://identity-b.example/realms/care"
SHARED_SUBJECT = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"


class UserExternalIdentityConstraintTests(TestCase):
    def setUp(self):
        self.alice = baker.make(User, username="alice", deleted=False)
        self.mallory = baker.make(User, username="mallory", deleted=False)

    def link(self, user, *, provider_id="clinic-sso", issuer=ISSUER_A, subject="sub-1"):
        return UserExternalIdentity.objects.create(
            user=user, provider_id=provider_id, issuer=issuer, subject=subject
        )

    def test_the_same_subject_at_two_issuers_is_two_identities(self):
        """T1. The whole reason the triple exists rather than a subject column.

        Before ADR-0011 these two rows were one unique `keycloak_subject`, and
        whichever issuer got there second could authenticate as the first.
        """
        self.link(
            self.alice, provider_id="issuer-a", issuer=ISSUER_A, subject=SHARED_SUBJECT
        )
        self.link(
            self.mallory,
            provider_id="issuer-b",
            issuer=ISSUER_B,
            subject=SHARED_SUBJECT,
        )

        self.assertEqual(
            UserExternalIdentity.objects.get(
                issuer=ISSUER_A, subject=SHARED_SUBJECT
            ).user,
            self.alice,
        )
        self.assertEqual(
            UserExternalIdentity.objects.get(
                issuer=ISSUER_B, subject=SHARED_SUBJECT
            ).user,
            self.mallory,
        )

    def test_the_same_triple_cannot_be_linked_twice(self):
        """Rule §5.4, first writer wins -- enforced by the database."""
        self.link(self.alice)

        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.link(self.mallory)

    def test_the_same_triple_cannot_be_relinked_to_the_same_user_either(self):
        self.link(self.alice)

        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.link(self.alice)

    def test_one_user_may_hold_several_identities(self):
        """A clinician arriving through two providers is one User, two rows."""
        self.link(self.alice, provider_id="clinic-sso", issuer=ISSUER_A, subject="a")
        self.link(self.alice, provider_id="regional-sso", issuer=ISSUER_B, subject="b")

        self.assertEqual(self.alice.external_identities.count(), 2)

    def test_the_same_subject_under_two_providers_at_one_issuer_is_distinct(self):
        """Two clients of one issuer are still two providers to CARE."""
        self.link(self.alice, provider_id="clinic-sso", subject=SHARED_SUBJECT)
        self.link(self.alice, provider_id="clinic-sso-legacy", subject=SHARED_SUBJECT)

        self.assertEqual(self.alice.external_identities.count(), 2)

    def test_unlinking_frees_the_triple_for_re_enrolment(self):
        """Rules §5.5 and §5.6: subject is immutable, so re-enrol after unlink."""
        identity = self.link(self.alice)
        identity.delete()

        replacement = self.link(self.mallory)

        self.assertEqual(replacement.user, self.mallory)

    def test_an_unlinked_identity_no_longer_resolves(self):
        """Soft delete must not leave a row that can still authenticate."""
        identity = self.link(self.alice)
        identity.delete()

        self.assertFalse(
            UserExternalIdentity.objects.filter(
                provider_id="clinic-sso", issuer=ISSUER_A, subject="sub-1"
            ).exists()
        )

    def test_deleting_the_user_removes_its_identities(self):
        """A real foreign key, not a generic one: the database enforces this."""
        self.link(self.alice)

        User.objects.filter(pk=self.alice.pk).delete()

        self.assertEqual(UserExternalIdentity.objects.count(), 0)

    def test_an_identity_records_who_linked_it(self):
        identity = UserExternalIdentity.objects.create(
            user=self.alice,
            provider_id="clinic-sso",
            issuer=ISSUER_A,
            subject="sub-1",
            linked_by=self.mallory,
        )

        self.assertEqual(identity.linked_by, self.mallory)

    def test_removing_the_administrator_keeps_the_identity(self):
        """An audit trail that deletes the fact along with the actor is useless."""
        identity = UserExternalIdentity.objects.create(
            user=self.alice,
            provider_id="clinic-sso",
            issuer=ISSUER_A,
            subject="sub-1",
            linked_by=self.mallory,
        )

        User.objects.filter(pk=self.mallory.pk).delete()
        identity.refresh_from_db()

        self.assertIsNone(identity.linked_by)
        self.assertEqual(identity.user, self.alice)
