import datetime

from django.utils import timezone
from pydantic import UUID4, model_validator

from care.emr.extensions.base import ExtensionResource
from care.emr.extensions.validator import ExtensionValidator
from care.emr.geography.postal_codes import validate_postal_code
from care.emr.models import Organization
from care.emr.models.patient import Patient
from care.emr.resources.base import EMRResource
from care.emr.resources.patient.spec import (
    BloodGroupChoices,
    GenderChoices,
    _country_code,
    _get_registration_facility,
    _validate_location_for_country,
)


class PatientOTPBaseSpec(EMRResource):
    __model__ = Patient
    __exclude__ = ["geo_organization"]
    ___extension_resource_type__ = ExtensionResource.patient
    id: UUID4 = None


class PatientOTPReadSpec(PatientOTPBaseSpec):
    name: str
    gender: str
    phone_number: str
    emergency_phone_number: str
    address: str
    pincode: str
    date_of_birth: datetime.date
    year_of_birth: int
    geo_organization: dict | None = None
    blood_group: BloodGroupChoices | None = None
    extensions: dict

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        if obj.geo_organization:
            mapping["geo_organization"] = obj.geo_organization.get_parent_json()


class PatientOTPWriteSpec(ExtensionValidator, PatientOTPBaseSpec):
    __exclude__ = [*PatientOTPBaseSpec.__exclude__,
        "registration_facility",
        "region_id",
        "subregion_id",
        "city_id",
    ]

    name: str
    gender: GenderChoices
    date_of_birth: datetime.date | None = None
    age: int | None = None
    address: str
    pincode: str
    geo_organization: UUID4 | None = None
    registration_facility: UUID4
    region_id: int | None = None
    subregion_id: int | None = None
    city_id: int | None = None
    blood_group: BloodGroupChoices | None = None

    @model_validator(mode="after")
    def validate_age(self):
        if not (self.age or self.date_of_birth):
            raise ValueError("Either age or date of birth is required")
        return self

    @model_validator(mode="after")
    def validate_postal_code_for_geography(self):
        facility = _get_registration_facility(self.registration_facility)
        if not facility.is_public:
            raise ValueError("Registration facility is not public")
        self.geo_organization = facility.geo_organization.external_id
        organization = Organization.objects.get(external_id=self.geo_organization)
        country = organization.get_country()
        # Same contract as the authenticated create path: the country selects
        # which postal-code rule applies, and its absence selects the
        # international fallback rather than refusing the registration. The
        # public flow still requires `pincode` itself -- that is a separate
        # decision, and it is unchanged.
        _validate_location_for_country(
            country, self.region_id, self.subregion_id, self.city_id
        )
        self.pincode = validate_postal_code(self.pincode, _country_code(country))
        return self

    def perform_extra_deserialization(self, is_update, obj):
        if not is_update:
            obj.geo_organization = Organization.objects.get(
                external_id=self.geo_organization
            )
            obj.region_id = self.region_id
            obj.subregion_id = self.subregion_id
            obj.city_id = self.city_id
            if self.age:
                obj.year_of_birth = timezone.now().date().year - self.age
            else:
                obj.year_of_birth = self.date_of_birth.year
