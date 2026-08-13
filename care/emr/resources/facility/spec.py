from typing import Literal

from django.conf import settings
from django.db.models.functions import Lower, Trim
from pydantic import UUID4, BaseModel, Field, field_validator, model_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_extra_types.coordinate import Latitude, Longitude

from care.emr.geography.postal_codes import validate_postal_code
from care.emr.models import Organization
from care.emr.models.facility_config import FacilityMonetoryConfig
from care.emr.models.patient import PatientIdentifierConfigCache
from care.emr.resources.base import EMRResource, cacheable, model_from_cache
from care.emr.resources.common.coding import Coding
from care.emr.resources.common.monetary_component import (
    DiscountConfiguration,
    MonetaryComponentDefinition,
)
from care.emr.resources.invoice.default_expression_evaluator import (
    evaluate_invoice_dummy_expression,
)
from care.emr.resources.organization.spec import OrganizationReadSpec
from care.emr.resources.permissions import FacilityPermissionsMixin
from care.facility.models import (
    REVERSE_FACILITY_TYPES,
    REVERSE_REVERSE_FACILITY_TYPES,
    Facility,
    FacilityFeature,
)


@cacheable(use_base_manager=True)
class FacilityBareMinimumSpec(EMRResource):
    __model__ = Facility
    __exclude__ = ["geo_organization"]
    id: UUID4 | None = None
    name: str


class PageMargin(BaseModel):
    top: float = Field(ge=0)
    bottom: float = Field(ge=0)
    left: float = Field(ge=0)
    right: float = Field(ge=0)


class PageConfig(BaseModel):
    size: Literal["A4", "A5", "Letter", "Legal"] | None = None
    orientation: Literal["portrait", "landscape"] | None = None
    margin: PageMargin | None = None


class PrintSetupConfig(BaseModel):
    auto_print: bool | None = None


class LogoConfig(BaseModel):
    url: str
    width: float | None = None
    height: float | None = None
    alignment: Literal["left", "center", "right"]


class HeaderImageConfig(BaseModel):
    url: str
    height: float | None = None


class FooterImageConfig(BaseModel):
    url: str | None = None
    height: float | None = None


class BrandingConfig(BaseModel):
    logo: LogoConfig | None = None
    header_image: HeaderImageConfig | None = None
    footer_image: FooterImageConfig | None = None


class WatermarkConfig(BaseModel):
    enabled: bool | None = None
    text: str | None = None
    opacity: float | None = Field(None, ge=0, le=1)
    rotation: float | None = None


class PrintTemplate(BaseModel):
    slug: str
    page: PageConfig | None = None
    print_setup: PrintSetupConfig | None = None
    branding: BrandingConfig | None = None
    watermark: WatermarkConfig | None = None


class FacilityBaseSpec(FacilityBareMinimumSpec):
    description: str
    longitude: Longitude | None = None
    latitude: Latitude | None = None
    pincode: str
    address: str
    phone_number: str
    middleware_address: str | None = None
    facility_type: str
    is_public: bool
    region_id: int | None = None
    subregion_id: int | None = None
    city_id: int | None = None


DISCOUNT_CODE_COUNT_LIMIT = 100
DISCOUNT_MONETARY_COMPONENT_COUNT_LIMIT = 100


class FacilityInvoiceExpressionSpec(BaseModel):
    invoice_number_expression: str

    @field_validator("invoice_number_expression")
    @classmethod
    def validate_invoice_number_expression(cls, v):
        if v:
            try:
                evaluate_invoice_dummy_expression(v)
            except Exception as e:
                err = "Invalid Expression"
                raise ValueError(err) from e
        return v


class FacilityCreateSpec(FacilityBaseSpec):
    geo_organization: UUID4
    features: list[int]
    print_templates: list[PrintTemplate] = []

    @field_validator("facility_type")
    @classmethod
    def validate_facility_type(cls, v):
        if v not in REVERSE_REVERSE_FACILITY_TYPES:
            valid = ", ".join(sorted(REVERSE_REVERSE_FACILITY_TYPES.keys()))
            err = f"Invalid facility type '{v}'. Valid options are: {valid}"
            raise ValueError(err)
        return v

    @field_validator("features")
    @classmethod
    def validate_features(cls, features):
        valid_feature_ids = {feature.value for feature in FacilityFeature}
        invalid_feature_ids = sorted(set(features) - valid_feature_ids)
        if invalid_feature_ids:
            valid = ", ".join(str(feature_id) for feature_id in sorted(valid_feature_ids))
            invalid = ", ".join(str(feature_id) for feature_id in invalid_feature_ids)
            message = f"Invalid facility feature ID(s): {invalid}. Valid IDs are: {valid}"
            raise ValueError(message)
        return features

    @model_validator(mode="after")
    def validate_geography_and_postal_code(self):
        from cities_light.models import City, Region, SubRegion

        organization = Organization.objects.filter(
            external_id=self.geo_organization, org_type="govt"
        ).first()
        if organization is None:
            raise ValueError("Geo Organization does not exist")

        country = organization.get_country()
        country_code = country.code2 if country else None
        self.pincode = validate_postal_code(self.pincode, country_code)

        if country and (not self.region_id or not self.subregion_id):
            raise ValueError(
                "Region and subregion are required when the jurisdiction has a country"
            )

        region = Region.objects.filter(id=self.region_id).first() if self.region_id else None
        subregion = (
            SubRegion.objects.filter(id=self.subregion_id).first()
            if self.subregion_id
            else None
        )
        city = City.objects.filter(id=self.city_id).first() if self.city_id else None

        if self.region_id and region is None:
            raise ValueError("Geographic region not found")
        if self.subregion_id and subregion is None:
            raise ValueError("Geographic subregion not found")
        if self.city_id and city is None:
            raise ValueError("Geographic city not found")
        if region and country and region.country_id != country.id:
            raise ValueError("Region does not belong to the organization's country")
        if subregion and (not region or subregion.region_id != region.id):
            raise ValueError("Subregion does not belong to the selected region")
        if city and (not subregion or city.subregion_id != subregion.id):
            raise ValueError("City does not belong to the selected subregion")
        return self

    @field_validator("name")
    @classmethod
    def validate_name_uniqueness(cls, v, info: ValidationInfo):
        if not v:
            return v

        normalized_name = v.strip().lower()
        context = info.context or {}
        is_update = context.get("is_update", False)
        obj = context.get("object")

        qs = Facility.objects.annotate(normalized_name=Lower(Trim("name"))).filter(
            normalized_name=normalized_name
        )

        if is_update and obj:
            qs = qs.exclude(id=obj.id)

        if qs.exists():
            err = "A facility with this name already exists"
            raise ValueError(err)

        return v

    def perform_extra_deserialization(self, is_update, obj):
        obj.geo_organization = Organization.objects.get(
            external_id=self.geo_organization, org_type="govt"
        )
        obj.facility_type = REVERSE_REVERSE_FACILITY_TYPES[self.facility_type]
        obj.region_id = self.region_id
        obj.subregion_id = self.subregion_id
        obj.city_id = self.city_id


class FacilityReadSpec(FacilityBaseSpec):
    features: list[int]
    cover_image_url: str
    read_cover_image_url: str
    geo_organization: dict = {}
    location: dict = {}
    created_by: dict | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        from care.emr.resources.user.spec import UserSpec

        mapping["id"] = obj.external_id
        mapping["read_cover_image_url"] = obj.read_cover_image_url()
        mapping["facility_type"] = REVERSE_FACILITY_TYPES[obj.facility_type]
        mapping["region_id"] = obj.region_id
        mapping["subregion_id"] = obj.subregion_id
        mapping["city_id"] = obj.city_id
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
            "city": {"id": obj.city_id, "name": obj.city.name} if obj.city_id else None,
        }
        if obj.created_by_id:
            mapping["created_by"] = model_from_cache(UserSpec, id=obj.created_by_id)


class FacilityRetrieveSpec(FacilityReadSpec, FacilityPermissionsMixin):
    flags: list[str] = []
    discount_codes: list[dict] = []
    discount_monetary_components: list[dict] = []
    discount_configuration: dict | None = None

    instance_discount_codes: list[dict] = []
    instance_discount_monetary_components: list[dict] = []
    instance_tax_codes: list[dict] = []
    instance_tax_monetary_components: list[dict] = []
    instance_informational_codes: list[dict] = []
    # Identifiers
    patient_instance_identifier_configs: list[dict] = []
    patient_facility_identifier_configs: list[dict] = []
    invoice_number_expression: str | None = None

    print_templates: list[dict] = []

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        from care.emr.models.facility_config import FacilityMonetoryConfig

        super().perform_extra_serialization(mapping, obj)
        facility_monetory_config = FacilityMonetoryConfig.get_monetory_config(obj.id)

        mapping["invoice_number_expression"] = (
            facility_monetory_config.invoice_number_expression
        )
        mapping["discount_codes"] = facility_monetory_config.discount_codes
        mapping["discount_monetary_components"] = (
            facility_monetory_config.discount_monetary_components
        )
        mapping["discount_configuration"] = (
            facility_monetory_config.discount_configuration
        )

        mapping["flags"] = obj.get_facility_flags()
        mapping["instance_discount_codes"] = settings.DISCOUNT_CODES
        mapping["instance_discount_monetary_components"] = (
            settings.DISCOUNT_MONETARY_COMPONENT_DEFINITIONS
        )
        mapping["instance_tax_codes"] = settings.TAX_CODES
        mapping["instance_tax_monetary_components"] = (
            settings.TAX_MONETARY_COMPONENT_DEFINITIONS
        )
        mapping["patient_instance_identifier_configs"] = (
            PatientIdentifierConfigCache.get_instance_config()
        )
        mapping["patient_facility_identifier_configs"] = (
            PatientIdentifierConfigCache.get_facility_config(obj.id)
        )
        mapping["instance_informational_codes"] = settings.INFORMATIONAL_MONETARY_CODES


class FacilityMonetaryCodeSpec(EMRResource):
    __model__ = FacilityMonetoryConfig
    __exclude__ = []

    discount_codes: list[Coding]
    discount_monetary_components: list[MonetaryComponentDefinition]
    discount_configuration: DiscountConfiguration | None

    @model_validator(mode="after")
    def validate_count(self):
        if len(self.discount_codes) >= DISCOUNT_CODE_COUNT_LIMIT:
            raise ValueError("Discount codes cannot be more than 100.")
        if (
            len(self.discount_monetary_components)
            >= DISCOUNT_MONETARY_COMPONENT_COUNT_LIMIT
        ):
            raise ValueError("Discount monetary components cannot be more than 100.")
        return self

    @model_validator(mode="after")
    def validate_codes(self):
        # Duplicate codes are not allowed
        codes = [code.code for code in self.discount_codes]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate codes are not allowed.")
        # Redefining system codes are not allowed
        system_codes = [[code.code, code.system] for code in settings.DISCOUNT_CODES]
        for code in self.discount_codes:
            if [code.code, code.system] in system_codes:
                raise ValueError("Redefining system codes are not allowed.")
        # All monetary components code must be defined
        facility_codes = [[code.code, code.system] for code in self.discount_codes]
        all_allowed_codes = system_codes + facility_codes
        for definition in self.discount_monetary_components:
            if (
                definition.code
                and [
                    definition.code.code,
                    definition.code.system,
                ]
                not in all_allowed_codes
            ):
                raise ValueError("All monetary components code must be defined.")
        return self


class FacilityMinimalReadSpec(FacilityBaseSpec):
    features: list[int]
    cover_image_url: str
    read_cover_image_url: str
    geo_organization: dict = {}
    location: dict = {}

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        mapping["read_cover_image_url"] = obj.read_cover_image_url()
        mapping["facility_type"] = REVERSE_FACILITY_TYPES[obj.facility_type]
        mapping["region_id"] = obj.region_id
        mapping["subregion_id"] = obj.subregion_id
        mapping["city_id"] = obj.city_id
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
            "city": {"id": obj.city_id, "name": obj.city.name} if obj.city_id else None,
        }
