"""End-to-end checks that a patient principal reaches only its own records.

ADR-0010 gives the patient principal three shapes -- a legacy verified phone
number, a Firebase-verified email address and a Keycloak-resolved patient id.
`care/emr/tests/test_patient_identity_auth.py` covers the resolver in
isolation; these tests drive the public OTP endpoints so the authorization
boundary itself is exercised, not just the queryset helper.
"""

from django.urls import reverse

from care.emr.models import Patient
from care.utils.tests.base import CareAPITestBase
from config.patient_otp_authentication import PatientOtpObject
from config.patient_otp_token import PatientToken


class PatientIdentityApiTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.phone_patient = self.create_patient(phone_number="+5215555555555")
        self.email_patient = self.create_patient(email="patient@example.com")
        self.oidc_patient = self.create_patient()
        self.url = reverse("otp-patient-list")

        self.staff_user = self.create_user()
        self.geo_organization = self.create_organization(org_type="govt")
        self.public_facility = self.create_facility(
            user=self.staff_user,
            is_public=True,
            geo_organization=self.geo_organization,
        )

    def registration_payload(self, **overrides):
        data = {
            "name": "Synthetic Patient",
            "gender": "female",
            "address": "Synthetic address",
            "pincode": "560001",
            "age": 34,
            "registration_facility": str(self.public_facility.external_id),
        }
        data.update(overrides)
        return data

    def _authenticate(self, **identity):
        principal = PatientOtpObject()
        principal.phone_number = identity.get("phone_number")
        principal.email = identity.get("email")
        principal.patient_id = identity.get("patient_id")
        principal.auth_provider = identity.get("auth_provider")
        self.client.force_authenticate(user=principal)

    def _listed_ids(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return {result["id"] for result in response.json()["results"]}

    def test_phone_identity_lists_only_its_own_patient(self):
        self._authenticate(phone_number=self.phone_patient.phone_number)

        self.assertEqual(self._listed_ids(), {str(self.phone_patient.external_id)})

    def test_email_identity_lists_only_its_own_patient(self):
        self._authenticate(email="PATIENT@example.com", auth_provider="firebase")

        self.assertEqual(self._listed_ids(), {str(self.email_patient.external_id)})

    def test_resolved_patient_id_lists_only_that_patient(self):
        self._authenticate(
            patient_id=str(self.oidc_patient.external_id),
            auth_provider="oidc",
        )

        self.assertEqual(self._listed_ids(), {str(self.oidc_patient.external_id)})

    def test_an_identity_without_any_contact_is_refused(self):
        self._authenticate()

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_an_email_identity_registers_a_patient_without_a_phone_number(self):
        self._authenticate(email="NewPatient@Example.com", auth_provider="firebase")

        response = self.client.post(
            self.url, self.registration_payload(), format="json"
        )

        self.assertEqual(response.status_code, 200, response.json())
        created = Patient.objects.get(external_id=response.json()["id"])
        self.assertEqual(created.email, "newpatient@example.com")
        self.assertEqual(created.phone_number, "")
        self.assertEqual(self._listed_ids(), {str(created.external_id)})

    def test_a_phone_identity_still_registers_against_its_phone_number(self):
        self._authenticate(phone_number="+5215555500000")

        response = self.client.post(
            self.url, self.registration_payload(), format="json"
        )

        self.assertEqual(response.status_code, 200, response.json())
        created = Patient.objects.get(external_id=response.json()["id"])
        self.assertEqual(created.phone_number, "+5215555500000")
        self.assertIsNone(created.email)

    def test_a_resolved_patient_cannot_enrol_a_second_record(self):
        self._authenticate(
            patient_id=str(self.oidc_patient.external_id),
            auth_provider="oidc",
        )

        response = self.client.post(
            self.url, self.registration_payload(), format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._listed_ids(), {str(self.oidc_patient.external_id)})


class RawProviderTokenRejectionTests(CareAPITestBase):
    """No provider credential is ever a CARE credential."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()

    def test_a_patient_token_cannot_reach_a_staff_api(self):
        token = PatientToken()
        token["patient_id"] = str(self.patient.external_id)
        token["auth_provider"] = "oidc"

        response = self.client.get(
            reverse("patient-list"),
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
        )

        self.assertIn(response.status_code, {401, 403})

    def test_a_raw_provider_token_is_not_a_care_credential(self):
        """ADR-0011 §4: application APIs accept CARE credentials, full stop.

        The provider set is irrelevant here, which is the point -- there is no
        configuration under which an external token becomes usable at an
        application endpoint.
        """
        response = self.client.get(
            reverse("patient-list"),
            headers={
                "authorization": "Bearer raw.oidc.id-token",
                "accept": "application/json",
            },
        )

        self.assertIn(response.status_code, {401, 403})
        self.assertNotIn("raw.oidc.id-token", str(response.content))
