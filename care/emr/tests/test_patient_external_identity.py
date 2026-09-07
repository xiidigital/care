"""External identity for patient principals (ADR-0011 §3).

The same contract as the workforce table, on a separate table with its own
foreign key. Two tables rather than one polymorphic table is the point: the
join that decides who someone is stays enforceable by the database.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase
from model_bakery import baker

from care.emr.models import Patient, PatientExternalIdentity
from care.users.models import User

ISSUER_A = "https://identity-a.example/realms/care-patients"
ISSUER_B = "https://identity-b.example/realms/care-patients"
SHARED_SUBJECT = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"


class PatientExternalIdentityConstraintTests(TestCase):
    def setUp(self):
        self.patient = baker.make(Patient, name="Patient One", deleted=False)
        self.other = baker.make(Patient, name="Patient Two", deleted=False)

    def link(
        self, patient, *, provider_id="patient-sso", issuer=ISSUER_A, subject="sub-1"
    ):
        return PatientExternalIdentity.objects.create(
            patient=patient, provider_id=provider_id, issuer=issuer, subject=subject
        )

    def test_the_same_subject_at_two_issuers_is_two_identities(self):
        """T1, on the side where the pre-ADR-0011 lookup was subject-only."""
        self.link(
            self.patient,
            provider_id="issuer-a",
            issuer=ISSUER_A,
            subject=SHARED_SUBJECT,
        )
        self.link(
            self.other, provider_id="issuer-b", issuer=ISSUER_B, subject=SHARED_SUBJECT
        )

        self.assertEqual(
            PatientExternalIdentity.objects.get(
                issuer=ISSUER_A, subject=SHARED_SUBJECT
            ).patient,
            self.patient,
        )
        self.assertEqual(
            PatientExternalIdentity.objects.get(
                issuer=ISSUER_B, subject=SHARED_SUBJECT
            ).patient,
            self.other,
        )

    def test_the_same_triple_cannot_be_linked_twice(self):
        self.link(self.patient)

        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.link(self.other)

    def test_one_patient_may_hold_several_identities(self):
        self.link(self.patient, provider_id="patient-sso", issuer=ISSUER_A, subject="a")
        self.link(
            self.patient, provider_id="regional-sso", issuer=ISSUER_B, subject="b"
        )

        self.assertEqual(self.patient.external_identities.count(), 2)

    def test_unlinking_frees_the_triple_for_re_enrolment(self):
        identity = self.link(self.patient)
        identity.delete()

        self.assertEqual(self.link(self.other).patient, self.other)

    def test_an_unlinked_identity_no_longer_resolves(self):
        self.link(self.patient).delete()

        self.assertFalse(
            PatientExternalIdentity.objects.filter(
                provider_id="patient-sso", issuer=ISSUER_A, subject="sub-1"
            ).exists()
        )

    def test_deleting_the_patient_removes_its_identities(self):
        self.link(self.patient)

        Patient.objects.filter(pk=self.patient.pk).delete()

        self.assertEqual(PatientExternalIdentity.objects.count(), 0)

    def test_a_patient_identity_records_the_administrator_who_linked_it(self):
        """Rule §5.3: a patient link is administrative, so the actor matters."""
        administrator = baker.make(User, username="registrar", deleted=False)

        identity = PatientExternalIdentity.objects.create(
            patient=self.patient,
            provider_id="patient-sso",
            issuer=ISSUER_A,
            subject="sub-1",
            linked_by=administrator,
        )

        self.assertEqual(identity.linked_by, administrator)
