from enum import Enum

from pydantic import UUID4, model_validator
from pydantic_core.core_schema import ValidationInfo

from care.emr.geography.catalog import serialize_catalog_node
from care.emr.models.organization import Organization
from care.emr.resources.base import EMRResource
from care.security.authorization import AuthorizationController


class OrganizationTypeChoices(str, Enum):
    team = "team"
    govt = "govt"
    role = "role"
    product_supplier = "product_supplier"


class OrganizationBaseSpec(EMRResource):
    __model__ = Organization
    __exclude__ = ["parent"]
    id: str = None
    active: bool = True
    org_type: OrganizationTypeChoices
    name: str
    description: str = ""
    metadata: dict = {}
    country_id: int | None = None
    region_id: int | None = None
    subregion_id: int | None = None
    city_id: int | None = None


class OrganizationUpdateSpec(OrganizationBaseSpec):
    @model_validator(mode="after")
    def validate_geography_update(self, info: ValidationInfo):
        if not any(
            field in self.model_fields_set
            for field in ("country_id", "region_id", "subregion_id", "city_id")
        ):
            return self
        if self.org_type != OrganizationTypeChoices.govt:
            raise ValueError("Only government organizations can reference geography")
        obj = info.context["object"]
        if obj.has_children:
            raise ValueError(
                "A geographic organization with children cannot change node"
            )
        selected = [
            (level, node_id)
            for level, node_id in (
                ("country", self.country_id),
                ("region", self.region_id),
                ("subregion", self.subregion_id),
                ("city", self.city_id),
            )
            if node_id
        ]
        if len(selected) > 1:
            raise ValueError(
                "An organization can reference only one direct geographic node"
            )
        if selected:
            from cities_light.models import City, Country, Region, SubRegion

            models = {
                "country": Country,
                "region": Region,
                "subregion": SubRegion,
                "city": City,
            }
            level, node_id = selected[0]
            node = models[level].objects.filter(id=node_id).first()
            if node is None:
                message = f"Geographic {level} not found"
                raise ValueError(message)
            parent = obj.parent
            if parent is None:
                if level != "country":
                    raise ValueError(
                        "A root government organization must reference a country"
                    )
                return self

            parent_level, parent_node = parent.get_direct_geography()
            expected_parent = {
                "region": "country",
                "subregion": "region",
                "city": "subregion",
            }.get(level)
            if expected_parent is None:
                raise ValueError(
                    "A country organization cannot have a geographic parent"
                )
            if parent_level != expected_parent:
                message = (
                    f"A {level} organization must be created under a "
                    f"{expected_parent} organization"
                )
                raise ValueError(message)
            if parent_node.id != getattr(node, f"{expected_parent}_id"):
                raise ValueError(
                    "Geographic node does not belong to the selected parent"
                )
        return self

    def perform_extra_deserialization(self, is_update, obj):
        _assign_geography(self, obj)


class OrganizationWriteSpec(OrganizationBaseSpec):
    parent: UUID4 | None = None

    @model_validator(mode="after")
    def validate_parent_organization(self):
        if (
            self.parent
            and not Organization.objects.filter(external_id=self.parent).exists()
        ):
            err = "Parent not found"
            raise ValueError(err)
        return self

    @model_validator(mode="after")
    def validate_geography(self):
        direct_nodes = {
            "country": self.country_id,
            "region": self.region_id,
            "subregion": self.subregion_id,
            "city": self.city_id,
        }
        selected = [
            (level, node_id) for level, node_id in direct_nodes.items() if node_id
        ]
        if len(selected) > 1:
            raise ValueError(
                "An organization can reference only one direct geographic node"
            )
        if selected and self.org_type != OrganizationTypeChoices.govt:
            raise ValueError("Only government organizations can reference geography")
        if not selected:
            return self

        from cities_light.models import City, Country, Region, SubRegion

        models = {
            "country": Country,
            "region": Region,
            "subregion": SubRegion,
            "city": City,
        }
        level, node_id = selected[0]
        node = models[level].objects.filter(id=node_id).first()
        if node is None:
            message = f"Geographic {level} not found"
            raise ValueError(message)

        if not self.parent:
            if level != "country":
                raise ValueError(
                    "A root government organization must reference a country"
                )
            return self

        parent = Organization.objects.get(external_id=self.parent)
        parent_level, parent_node = parent.get_direct_geography()
        expected_parent = {
            "region": "country",
            "subregion": "region",
            "city": "subregion",
        }.get(level)
        if expected_parent is None:
            raise ValueError("A country organization cannot have a geographic parent")
        if parent_level != expected_parent:
            message = f"A {level} organization must be created under a {expected_parent} organization"
            raise ValueError(message)

        parent_id = getattr(node, f"{expected_parent}_id")
        if parent_node.id != parent_id:
            raise ValueError("Geographic node does not belong to the selected parent")
        return self

    def perform_extra_deserialization(self, is_update, obj):
        if not is_update:
            if self.parent:
                obj.parent = Organization.objects.get(external_id=self.parent)
            else:
                obj.parent = None
        _assign_geography(self, obj)


class OrganizationReadSpec(OrganizationBaseSpec):
    level_cache: int = 0
    system_generated: bool
    has_children: bool
    parent: dict
    geography: dict = {}

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        mapping["parent"] = obj.get_parent_json()
        mapping["country_id"] = obj.country_id
        mapping["region_id"] = obj.region_id
        mapping["subregion_id"] = obj.subregion_id
        mapping["city_id"] = obj.city_id
        level, node = obj.get_direct_geography()
        country = obj.get_country()
        mapping["geography"] = {
            "direct": serialize_catalog_node(node, level=level) if node else None,
            "country": serialize_catalog_node(country, level="country")
            if country
            else None,
        }


def _assign_geography(spec, obj):
    geography_fields = ("country_id", "region_id", "subregion_id", "city_id")
    if not any(field in spec.model_fields_set for field in geography_fields):
        return
    for field in geography_fields:
        setattr(
            obj, field, getattr(spec, field) if field in spec.model_fields_set else None
        )


class OrganizationRetrieveSpec(OrganizationReadSpec):
    permissions: list[str] = []
    managing_organizations: list[dict] = []

    @classmethod
    def perform_extra_user_serialization(cls, mapping, obj, user):
        mapping["permissions"] = AuthorizationController.call(
            "get_permission_on_organization", obj, user
        )
        mapping["managing_organizations"] = [
            OrganizationReadSpec.serialize(org).to_json()
            for org in Organization.objects.filter(id__in=obj.managing_organizations)
        ]
        cls.serialize_audit_users(mapping, obj)
