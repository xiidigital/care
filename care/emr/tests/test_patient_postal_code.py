"""
The postal-code contract on the patient API.

`pincode` moved from an integer to text in `d275c8141` so that leading zeros
survive and international formats fit, and gained validation against the country
derived from the patient's geographic organization. The rule the implementation
actually applied, though, was stricter than the one it documented: it refused
*any* patient whose jurisdiction had no country attached, whether or not a postal
code was supplied at all. Every deployment whose organization tree predates the
geographic catalogue — which is all of them, and the whole test corpus — could
not create a patient (`unresolved-items.md` P3).

The contract these tests fix is the documented one
(`docs/xii/access-control/geographic-catalog-and-postal-codes.md`):

- `pincode` is optional. Omitted, null and blank are all accepted and all store
  nothing.
- A supplied code is validated, and only when supplied.
- Which rule applies is chosen by the country derived from the jurisdiction:
  6 digits for `IN`, 5 for `MX`, ZIP or ZIP+4 for `US`.
- **When no country can be derived, the documented international fallback
  applies** — 2 to 12 alphanumerics, spaces or hyphens — rather than an error.
  CARE runs in more than one country and most of them have no rule in the table;
  refusing what the table calls "Otros" is not validation, it is an outage.

Address detail is the one thing that genuinely needs a country: a region,
subregion or city is validated against the country's catalogue, so supplying one
without a country is an error and is asserted as such.
"""

from secrets import choice

from care.emr.locks.billing import PatientCreateLock
from care.emr.resources.patient.spec import BloodGroupChoices, GenderChoices
from care.emr.tests.test_patient_api import generate_random_valid_phone_number
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase

BASE_URL = "/api/v1/patient/"

#: One code that is valid under every rule in the table, used wherever the test
#: is about something other than the format itself.
UNIVERSALLY_VALID = "560001"


class PatientPostalCodeTestBase(CareAPITestBase):
    def setUp(self):
        self.user = self.create_user()
        self.geo_organization = self.create_organization(org_type="govt")
        role = self.create_role_with_permissions(
            permissions=[
                PatientPermissions.can_create_patient.name,
                PatientPermissions.can_write_patient.name,
                PatientPermissions.can_list_patients.name,
            ]
        )
        self.attach_role_organization_user(self.geo_organization, self.user, role)
        self.client.force_authenticate(user=self.user)

    def country(self, code2, name=None):
        """Attach a catalogue country to the jurisdiction and return it."""
        from cities_light.models import Country

        country, _ = Country.objects.get_or_create(
            code2=code2, defaults={"name": name or code2}
        )
        self.geo_organization.country = country
        self.geo_organization.save(update_fields=["country"])
        return country

    def payload(self, **kwargs):
        data = {
            "name": self.fake.name(),
            "gender": choice(list(GenderChoices)),
            "address": self.fake.address(),
            "permanent_address": self.fake.address(),
            "pincode": UNIVERSALLY_VALID,
            "blood_group": choice(list(BloodGroupChoices)),
            "phone_number": generate_random_valid_phone_number(),
            "emergency_phone_number": generate_random_valid_phone_number(),
            "geo_organization": self.geo_organization.external_id,
            "age": 30,
        }
        data.update(**kwargs)
        return data

    def create(self, **kwargs):
        PatientCreateLock().release()
        return self.client.post(BASE_URL, self.payload(**kwargs), format="json")

    def created_patient(self, **kwargs):
        response = self.create(**kwargs)
        self.assertEqual(response.status_code, 200, response.json())
        from care.emr.models.patient import Patient

        return Patient.objects.get(external_id=response.json()["id"])

    def update(self, patient, **fields):
        return self.client.patch(
            f"{BASE_URL}{patient.external_id}/", fields, format="json"
        )

    def assert_rejected(self, response, message):
        self.assertEqual(response.status_code, 400, response.json())
        body = str(response.json())
        self.assertIn(message, body)


class PostalCodeIsOptionalTests(PatientPostalCodeTestBase):
    """Omitted, null and blank all mean "not recorded", and none is an error."""

    def test_a_patient_can_be_created_without_a_postal_code(self):
        payload = self.payload()
        payload.pop("pincode")
        PatientCreateLock().release()
        response = self.client.post(BASE_URL, payload, format="json")
        self.assertEqual(response.status_code, 200, response.json())

    def test_a_null_postal_code_is_accepted_and_stores_nothing(self):
        patient = self.created_patient(pincode=None)
        self.assertIsNone(patient.pincode)

    def test_a_blank_postal_code_is_accepted_and_stores_nothing(self):
        patient = self.created_patient(pincode="")
        self.assertIsNone(patient.pincode)

    def test_a_whitespace_only_postal_code_stores_nothing(self):
        patient = self.created_patient(pincode="   ")
        self.assertIsNone(patient.pincode)


class NoCountryDerivableTests(PatientPostalCodeTestBase):
    """
    The P3 regression, asserted from both sides.

    The jurisdiction created in `setUp` has no catalogue country, which is the
    state every organization tree is in until someone links it.
    """

    def test_a_patient_is_created_when_the_jurisdiction_has_no_country(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.json())

    def test_no_postal_code_and_no_country_is_still_fine(self):
        # The clearest statement of what went wrong: there was nothing to
        # validate, and it failed anyway.
        payload = self.payload()
        payload.pop("pincode")
        PatientCreateLock().release()
        self.assertEqual(
            self.client.post(BASE_URL, payload, format="json").status_code, 200
        )

    def test_an_international_format_is_accepted(self):
        # "Otros" in the documented table: 2-12 alphanumerics, spaces, hyphens.
        for code in ("SW1A 1AA", "75008", "K1A-0B1", "1010"):
            with self.subTest(pincode=code):
                patient = self.created_patient(pincode=code)
                self.assertEqual(patient.pincode, code.upper())

    def test_a_code_that_no_rule_could_accept_is_still_rejected(self):
        # The fallback is permissive, not absent.
        self.assert_rejected(self.create(pincode="!!"), "Enter a valid postal code")

    def test_a_code_that_is_too_long_is_rejected(self):
        self.assert_rejected(self.create(pincode="A" * 13), "Enter a valid postal code")


class CountrySpecificRuleTests(PatientPostalCodeTestBase):
    """When the jurisdiction resolves to a country, that country's rule applies."""

    def test_india_requires_six_digits(self):
        self.country("IN", "India")
        self.assertEqual(self.created_patient(pincode="682001").pincode, "682001")
        self.assert_rejected(self.create(pincode="6820"), "6 digits")

    def test_mexico_requires_five_digits(self):
        self.country("MX", "México")
        self.assertEqual(self.created_patient(pincode="76000").pincode, "76000")
        self.assert_rejected(self.create(pincode="760001"), "5 dígitos")

    def test_the_united_states_accepts_zip_and_zip_plus_four(self):
        self.country("US", "United States")
        self.assertEqual(self.created_patient(pincode="12345").pincode, "12345")
        self.assertEqual(
            self.created_patient(pincode="12345-6789").pincode, "12345-6789"
        )
        self.assert_rejected(self.create(pincode="1234"), "5 digits")

    def test_a_country_with_no_rule_of_its_own_uses_the_fallback(self):
        # The table has three entries; CARE is not limited to three countries.
        self.country("BR", "Brasil")
        self.assertEqual(self.created_patient(pincode="01310-100").pincode, "01310-100")

    def test_a_leading_zero_survives(self):
        # The reason the column became text in the first place.
        self.country("US", "United States")
        self.assertEqual(self.created_patient(pincode="01234").pincode, "01234")


class PostalCodeOnUpdateTests(PatientPostalCodeTestBase):
    """Partial update carries the same contract, and only when the field is sent."""

    def test_a_postal_code_can_be_set_on_update(self):
        patient = self.created_patient(pincode=None)
        response = self.update(patient, pincode="411001")
        self.assertEqual(response.status_code, 200, response.json())
        patient.refresh_from_db()
        self.assertEqual(patient.pincode, "411001")

    def test_a_postal_code_can_be_cleared_on_update(self):
        patient = self.created_patient(pincode="411001")
        response = self.update(patient, pincode="")
        self.assertEqual(response.status_code, 200, response.json())
        patient.refresh_from_db()
        self.assertIsNone(patient.pincode)

    def test_an_invalid_postal_code_is_rejected_on_update(self):
        self.country("IN", "India")
        patient = self.created_patient(pincode="682001")
        self.assert_rejected(self.update(patient, pincode="12"), "6 digits")

    def test_an_unrelated_update_does_not_revalidate_the_postal_code(self):
        # A patient stored before a country was attached must stay editable
        # afterwards; otherwise attaching a country freezes existing records.
        patient = self.created_patient(pincode="SW1A 1AA")
        self.country("IN", "India")
        response = self.update(patient, name="Renamed Patient")
        self.assertEqual(response.status_code, 200, response.json())
        patient.refresh_from_db()
        self.assertEqual(patient.name, "Renamed Patient")
        self.assertEqual(patient.pincode, "SW1A 1AA")

    def test_updating_a_patient_whose_jurisdiction_has_no_country_works(self):
        patient = self.created_patient()
        response = self.update(patient, pincode="ABC 123")
        self.assertEqual(response.status_code, 200, response.json())
        patient.refresh_from_db()
        self.assertEqual(patient.pincode, "ABC 123")


class ObjectActionsDoNotCarryARequestBodyTests(PatientPostalCodeTestBase):
    """
    The second defect the country precondition was hiding.

    Seven actions on this viewset call `authorize_update({}, instance)` with an
    empty dict, meaning "no request body to authorize". The registration-facility
    check added alongside the postal-code work read `.registration_facility` off
    that dict, so each of them raised AttributeError and answered 500 -- which
    nobody saw, because every patient these tests would have acted on failed to
    be created in the first place.
    """

    def test_a_tag_action_does_not_raise(self):
        from care.emr.models.tag_config import TagConfig

        patient = self.created_patient()
        tag = TagConfig.objects.create(
            status="active",
            display="Postal probe",
            category="clinical",
            resource="patient",
        )
        response = self.client.post(
            f"{BASE_URL}{patient.external_id}/set_instance_tags/",
            {"tags": [str(tag.external_id)]},
            format="json",
        )
        self.assertNotEqual(response.status_code, 500)

    def test_an_add_user_action_does_not_raise(self):
        patient = self.created_patient()
        other_user = self.create_user()
        role = self.create_role_with_permissions(permissions=[])
        response = self.client.post(
            f"{BASE_URL}{patient.external_id}/add_user/",
            {"user": str(other_user.external_id), "role": str(role.external_id)},
            format="json",
        )
        self.assertNotEqual(response.status_code, 500)


class AddressDetailNeedsACountryTests(PatientPostalCodeTestBase):
    """
    The one thing a missing country really does prevent.

    A region, subregion or city is validated against the country's catalogue, so
    without a country there is nothing to check it against. This is the part of
    the original rule that was right, kept.
    """

    def test_a_region_without_a_country_is_rejected(self):
        self.assert_rejected(
            self.create(region_id=1),
            "can only be recorded when the geographic organization resolves to "
            "a country",
        )

    def test_a_city_without_a_country_is_rejected(self):
        self.assert_rejected(
            self.create(city_id=1),
            "can only be recorded when the geographic organization resolves to "
            "a country",
        )

    def test_omitting_address_detail_is_not_an_error(self):
        self.assertEqual(self.create().status_code, 200)
