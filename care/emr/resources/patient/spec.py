import datetime
import re
import uuid
from enum import Enum

from django.conf import settings
from django.utils import timezone
from pydantic import UUID4, BaseModel, Field, field_validator, model_validator
from pydantic_core.core_schema import ValidationInfo

from care.emr.extensions.base import ExtensionResource
from care.emr.extensions.validator import (
    ExtensionListRenderer,
    ExtensionRetrieveRenderer,
    ExtensionValidator,
)
from care.emr.geography.postal_codes import validate_postal_code
from care.emr.models import Organization
from care.emr.models.patient import (
    Patient,
    PatientIdentifier,
    PatientIdentifierConfigCache,
)
from care.emr.resources.base import EMRResource, PhoneNumber
from care.emr.resources.patient_identifier.spec import PatientIdentifierListSpec
from care.emr.resources.permissions import PatientPermissionsMixin
from care.emr.tagging.base import PatientFacilityTagManager, PatientInstanceTagManager
from care.emr.utils.datetime_type import StrictTZAwareDateTime
from care.utils.time_util import care_now


def _get_registration_facility(facility_id):
    from care.facility.models import Facility

    facility = Facility.objects.filter(external_id=facility_id).select_related(
        "geo_organization"
    ).first()
    if facility is None:
        raise ValueError("Registration facility does not exist")
    if facility.geo_organization is None:
        raise ValueError("Registration facility has no geographic organization")
    if facility.geo_organization.get_country() is None:
        raise ValueError("Registration facility geographic organization has no country")
    return facility


def _validate_location(country, region_id, subregion_id, city_id):
    from cities_light.models import City, Region, SubRegion

    region = Region.objects.filter(id=region_id).first() if region_id else None
    subregion = (
        SubRegion.objects.filter(id=subregion_id).first() if subregion_id else None
    )
    city = City.objects.filter(id=city_id).first() if city_id else None

    if subregion and not region:
        raise ValueError("A subregion requires a selected region")
    if city and not subregion:
        raise ValueError("A city requires a selected subregion")
    if region and region.country_id != country.id:
        raise ValueError("Region does not belong to the registration facility country")
    if subregion:
        if subregion.country_id != country.id:
            raise ValueError("Subregion does not belong to the registration facility country")
        if region and subregion.region_id != region.id:
            raise ValueError("Subregion does not belong to the selected region")
    if city:
        if city.country_id != country.id:
            raise ValueError("City does not belong to the registration facility country")
        if subregion and city.subregion_id != subregion.id:
            raise ValueError("City does not belong to the selected subregion")
    return region, subregion, city


class BloodGroupChoices(str, Enum):
    A_negative = "A_negative"
    A_positive = "A_positive"
    B_negative = "B_negative"
    B_positive = "B_positive"
    AB_negative = "AB_negative"
    AB_positive = "AB_positive"
    O_negative = "O_negative"
    O_positive = "O_positive"
    unknown = "unknown"


class GenderChoices(str, Enum):
    male = "male"
    female = "female"
    non_binary = "non_binary"
    transgender = "transgender"


class PatientBaseSpec(EMRResource):
    __model__ = Patient
    ___extension_resource_type__ = ExtensionResource.patient
    __exclude__ = [
        "geo_organization",
        "instance_identifiers",
        "facility_identifiers",
        "instance_tags",
        "facility_tags",
    ]
    __store_metadata__ = True

    id: UUID4 | None = None
    name: str
    gender: GenderChoices
    phone_number: PhoneNumber = Field(max_length=14)
    emergency_phone_number: PhoneNumber | None = Field(None, max_length=14)
    address: str | None = None
    permanent_address: str | None = None
    pincode: str | None = None
    deceased_datetime: StrictTZAwareDateTime | None = None
    blood_group: BloodGroupChoices | None = None

    @field_validator("deceased_datetime")
    @classmethod
    def validate_deceased_datetime(cls, deceased_datetime):
        if deceased_datetime is None:
            return None
        if deceased_datetime > care_now():
            raise ValueError("Deceased datetime cannot be in the future")
        return deceased_datetime


def validate_identifier_config(config, value, obj=None):
    queryset = PatientIdentifier.objects.filter(value=value)
    if "config_obj" in config:
        queryset = queryset.filter(config=config["config_obj"])
    else:
        queryset = queryset.filter(config__external_id=config["id"])
    if obj:
        queryset = queryset.exclude(patient=obj)
    if config["config"]["unique"] and queryset.exists():
        err = f"Identifier config {config['config']['system']} is not unique"
        raise ValueError(err)
    if (
        value
        and config["config"]["regex"]
        and not re.match(config["config"]["regex"], value)
    ):
        err = f"Identifier config {config['config']['system']} is not valid"
        raise ValueError(err)


class PatientIdentifierConfigRequest(BaseModel):
    config: UUID4
    value: str


class PatientCreateSpec(ExtensionValidator, PatientBaseSpec):
    __exclude__ = [*PatientBaseSpec.__exclude__,
        "registration_facility",
        "region_id",
        "subregion_id",
        "city_id",
    ]

    name: str = Field(max_length=settings.PATIENT_NAME_MAX_LENGTH)
    geo_organization: UUID4 | None = None
    registration_facility: UUID4 | None = None
    region_id: int | None = None
    subregion_id: int | None = None
    city_id: int | None = None
    date_of_birth: datetime.date | None = None

    age: int | None = None

    identifiers: list[PatientIdentifierConfigRequest] = []

    tags: list[UUID4] = []

    @field_validator("geo_organization")
    @classmethod
    def validate_geo_organization(cls, geo_organization):
        if geo_organization is None:
            return None
        if not Organization.objects.filter(
            org_type="govt", external_id=geo_organization
        ).exists():
            raise ValueError("Geo Organization does not exist")
        return geo_organization

    @model_validator(mode="after")
    def resolve_registration_facility_geography(self):
        if self.registration_facility:
            facility = _get_registration_facility(self.registration_facility)
            self.geo_organization = facility.geo_organization.external_id
        if self.geo_organization is None:
            raise ValueError("Geo Organization is required")
        return self

    @model_validator(mode="after")
    def validate_identifiers(self):
        instance_identifier_configs = PatientIdentifierConfigCache.get_instance_config()
        configs = {str(x.config): x for x in self.identifiers}
        for identifier_config in instance_identifier_configs:
            if str(identifier_config["id"]) in configs:
                value = configs[str(identifier_config["id"])].value
                if identifier_config["config"]["required"] and not value:
                    err = f"Identifier config {identifier_config['config']['system']} is required"
                    raise ValueError(err)
                validate_identifier_config(identifier_config, value)
        return self

    @model_validator(mode="after")
    def validate_postal_code_for_geography(self):
        organization = Organization.objects.get(external_id=self.geo_organization)
        country = organization.get_country()
        if country is None:
            raise ValueError("Geo Organization has no country")
        _validate_location(country, self.region_id, self.subregion_id, self.city_id)
        self.pincode = validate_postal_code(
            self.pincode, country.code2
        )
        return self

    def perform_extra_deserialization(self, is_update, obj):
        obj.geo_organization = Organization.objects.get(
            external_id=self.geo_organization
        )
        obj.region_id = self.region_id
        obj.subregion_id = self.subregion_id
        obj.city_id = self.city_id
        if self.age:
            # override dob if user chooses to update age
            obj.date_of_birth = None
            obj.year_of_birth = timezone.now().date().year - self.age
        else:
            obj.year_of_birth = self.date_of_birth.year
        obj._identifiers = self.identifiers  # noqa: SLF001
        obj._tags = self.tags  # noqa: SLF001
        if not self.pincode:
            obj.pincode = None


class PatientUpdateSpec(ExtensionValidator, PatientBaseSpec):
    __exclude__ = [*PatientBaseSpec.__exclude__,
        "registration_facility",
        "region_id",
        "subregion_id",
        "city_id",
    ]

    name: str | None = Field(default=None, max_length=settings.PATIENT_NAME_MAX_LENGTH)
    gender: GenderChoices | None = None
    phone_number: PhoneNumber | None = Field(default=None, max_length=14)
    emergency_phone_number: PhoneNumber | None = Field(default=None, max_length=14)
    address: str | None = None
    permanent_address: str | None = None
    pincode: str | None = None
    blood_group: BloodGroupChoices | None = None
    date_of_birth: datetime.date | None = None
    age: int | None = None
    geo_organization: UUID4 | None = None
    registration_facility: UUID4 | None = None
    region_id: int | None = None
    subregion_id: int | None = None
    city_id: int | None = None

    identifiers: list[PatientIdentifierConfigRequest] = []

    @field_validator("geo_organization")
    @classmethod
    def validate_geo_organization(cls, geo_organization):
        if geo_organization is None:
            return None
        if not Organization.objects.filter(
            org_type="govt", external_id=geo_organization
        ).exists():
            raise ValueError("Geo Organization does not exist")
        return geo_organization

    @model_validator(mode="after")
    def resolve_registration_facility_geography(self, info: ValidationInfo):
        if self.registration_facility:
            facility = _get_registration_facility(self.registration_facility)
            self.geo_organization = facility.geo_organization.external_id
        elif self.geo_organization is None:
            existing_geography = info.context["object"].geo_organization
            if existing_geography is None:
                raise ValueError("Patient has no geographic organization")
            self.geo_organization = existing_geography.external_id
        return self

    def perform_extra_deserialization(self, is_update, obj):
        if is_update:
            obj._identifiers = self.identifiers  # noqa: SLF001
            if self.geo_organization:
                obj.geo_organization = Organization.objects.get(
                    external_id=self.geo_organization
                )
            if "region_id" in self.model_fields_set:
                obj.region_id = self.region_id
            if "subregion_id" in self.model_fields_set:
                obj.subregion_id = self.subregion_id
            if "city_id" in self.model_fields_set:
                obj.city_id = self.city_id
            if self.age is not None:
                obj.date_of_birth = None
                obj.year_of_birth = timezone.now().year - self.age
            elif self.date_of_birth:
                obj.year_of_birth = self.date_of_birth.year
        if not self.pincode:
            obj.pincode = None

    @model_validator(mode="after")
    def validate_postal_code_for_geography(self, info: ValidationInfo):
        location_fields = {"geo_organization", "registration_facility", "region_id", "subregion_id", "city_id"}
        if "pincode" not in self.model_fields_set and not (
            location_fields & self.model_fields_set
        ):
            return self
        organization = (
            Organization.objects.get(external_id=self.geo_organization)
            if self.geo_organization
            else info.context["object"].geo_organization
        )
        country = organization.get_country() if organization else None
        if country is None:
            raise ValueError("Geo Organization has no country")
        obj = info.context["object"]
        _validate_location(
            country,
            self.region_id if "region_id" in self.model_fields_set else obj.region_id,
            self.subregion_id
            if "subregion_id" in self.model_fields_set
            else obj.subregion_id,
            self.city_id if "city_id" in self.model_fields_set else obj.city_id,
        )
        if "pincode" in self.model_fields_set:
            self.pincode = validate_postal_code(self.pincode, country.code2)
        return self

    @field_validator("identifiers")
    @classmethod
    def validate_identifiers(cls, identifiers, info):
        instance_identifier_configs = PatientIdentifierConfigCache.get_instance_config()
        configs = {str(x.config): x for x in identifiers}
        for identifier_config in instance_identifier_configs:
            if str(identifier_config["id"]) in configs:
                value = configs[str(identifier_config["id"])].value
                if identifier_config["config"]["required"] and not value:
                    err = f"Identifier config {identifier_config['config']['system']} is required"
                    raise ValueError(err)
                validate_identifier_config(
                    identifier_config, value, info.context.get("object")
                )
        return identifiers


class PatientListSpec(ExtensionListRenderer, PatientBaseSpec):
    date_of_birth: datetime.date | None = None
    year_of_birth: datetime.date | None = None

    created_date: datetime.datetime
    modified_date: datetime.datetime

    instance_tags: list[dict] = []
    facility_tags: list[dict] = []

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        mapping["id"] = obj.external_id
        mapping["instance_tags"] = PatientInstanceTagManager().render_tags(obj)
        if kwargs.get("facility"):
            mapping["facility_tags"] = PatientFacilityTagManager(
                kwargs["facility"]
            ).render_tags(obj)
        super().perform_extra_serialization(mapping, obj, *args, **kwargs)


class PatientPartialSpec(EMRResource):
    __model__ = Patient

    id: UUID4 | None = None
    name: str
    gender: GenderChoices
    phone_number: str
    partial_id: str

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["partial_id"] = str(obj.external_id)[:5]
        mapping["id"] = str(uuid.uuid4())


class PatientIdentifierResponse(BaseModel):
    config: PatientIdentifierListSpec
    value: str


class PatientRetrieveSpec(
    ExtensionRetrieveRenderer, PatientListSpec, PatientPermissionsMixin
):
    geo_organization: dict = {}
    location: dict = {}

    created_by: dict | None = None
    updated_by: dict | None = None

    instance_identifiers: list[PatientIdentifierResponse] = []
    facility_identifiers: list[PatientIdentifierResponse] = []

    extensions: dict

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        from care.emr.resources.organization.spec import OrganizationReadSpec

        super().perform_extra_serialization(mapping, obj, *args, **kwargs)
        if obj.geo_organization:
            mapping["geo_organization"] = OrganizationReadSpec.serialize(
                obj.geo_organization
            ).to_json()
        mapping["location"] = {
            "region": {"id": obj.region_id, "name": obj.region.name}
            if obj.region_id
            else None,
            "subregion": {"id": obj.subregion_id, "name": obj.subregion.name}
            if obj.subregion_id
            else None,
            "city": {"id": obj.city_id, "name": obj.city.name}
            if obj.city_id
            else None,
        }
        cls.serialize_audit_users(mapping, obj)
        if obj.instance_identifiers:
            mapping["instance_identifiers"] = [
                {
                    "config": PatientIdentifierConfigCache.get_config(x["config"]),
                    "value": x["value"],
                }
                for x in obj.instance_identifiers
            ]
        if kwargs.get("facility"):
            facility = kwargs.get("facility")
            if facility and obj.facility_identifiers:
                mapping["facility_identifiers"] = [
                    {
                        "config": PatientIdentifierConfigCache.get_config(x["config"]),
                        "value": x["value"],
                    }
                    for x in obj.facility_identifiers.get(str(facility.id), [])
                ]
