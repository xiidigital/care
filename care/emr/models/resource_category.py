from datetime import datetime, timedelta

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from care.emr.models.base import SlugBaseModel
from care.utils.tasks import enqueue_task_on_commit


class ResourceCategory(SlugBaseModel):
    facility = models.ForeignKey(
        "facility.Facility",
        on_delete=models.PROTECT,
    )
    resource_type = models.CharField(max_length=255)
    resource_sub_type = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    parent = models.ForeignKey(
        "emr.ResourceCategory",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    is_child = models.BooleanField(default=False)
    cached_parent_json = models.JSONField(default=dict)
    parent_cache = ArrayField(models.IntegerField(), default=list)
    level_cache = models.IntegerField(default=0)
    root_org = models.ForeignKey(
        "emr.ResourceCategory",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="root",
    )
    has_children = models.BooleanField(default=False)

    # Charge Item Fields

    configured_monetary_components = models.JSONField(default=list)
    calculated_monetary_components = models.JSONField(default=list)

    cache_expiry_days = 15

    class Meta:
        indexes = [
            models.Index(fields=["slug", "facility"]),
        ]

    def set_organization_cache(self):
        if self.parent:
            self.parent_cache = [*self.parent.parent_cache, self.parent.id]
            self.level_cache = self.parent.level_cache + 1
            if self.parent.root_org is None:
                self.root_org = self.parent
            else:
                self.root_org = self.parent.root_org
            if not self.parent.has_children:
                self.parent.has_children = True
                self.parent.save(update_fields=["has_children"])
        super().save()

    def get_parent_json(self):
        if self.parent_id:
            if self.cached_parent_json and timezone.now() < datetime.fromisoformat(
                self.cached_parent_json["cache_expiry"]
            ):
                return self.cached_parent_json
            self.parent.get_parent_json()
            self.cached_parent_json = {
                "id": str(self.parent.external_id),
                "slug": self.parent.slug,
                "title": self.parent.title,
                "description": self.parent.description,
                "parent": self.parent.cached_parent_json,
                "cache_expiry": str(
                    timezone.now() + timedelta(days=self.cache_expiry_days)
                ),
            }
            self.save(update_fields=["cached_parent_json"])
            return self.cached_parent_json
        return {}

    def save(self, *args, **kwargs):
        if not self.id:
            super().save(*args, **kwargs)
            self.set_organization_cache()
            summarise_monetary_components(self)
        else:
            super().save(*args, **kwargs)


def merge_monetary_components(parent_components, child_components):
    no_code_components = []
    components = {}
    for component in parent_components:
        if component.get("code"):
            key = component.get("code").get("system") + component.get("code").get(
                "code"
            )
            components[key] = component
        else:
            no_code_components.append(component)
    # Override with child components
    for component in child_components:
        if component.get("code"):
            key = component.get("code").get("system") + component.get("code").get(
                "code"
            )
            components[key] = component
        else:
            no_code_components.append(component)
    # Final components
    final_components = no_code_components
    for _, component in components.items():
        final_components.append(component)
    return final_components


def summarise_monetary_components(category: ResourceCategory | int):
    """
    Recompute a category's calculated monetary components from its parent's, then
    schedule the same work for each child.

    The two callers of this function -- ``ResourceCategory.save`` above and the
    ``set_monetary_components`` viewset action -- call it inline and always did,
    despite it carrying a Celery decorator before ADR-0003. That is preserved:
    the caller's own category is recomputed within the request, transactionally,
    so a read immediately afterwards sees the new value.

    Only the fan-out over children is asynchronous, which is also what it was
    before. It is dispatched after commit because each child reads
    ``category.parent.calculated_monetary_components``, the row this function
    has just written: dispatching inside the open transaction would let a worker
    read the old value, or a value that was subsequently rolled back.

    ``category`` may be a model instance or a primary key because the inline
    callers pass both. The task payload is always an integer id -- a model
    instance is not JSON-serializable and could never have been queued.
    """
    if isinstance(category, int):
        category = ResourceCategory.objects.get(id=category)
    if not category.parent:
        category.calculated_monetary_components = (
            category.configured_monetary_components
        )
    else:
        # Merge parent and child monetary components
        category.calculated_monetary_components = merge_monetary_components(
            category.parent.calculated_monetary_components,
            category.configured_monetary_components,
        )
    category.save(update_fields=["calculated_monetary_components"])

    for component in ResourceCategory.objects.filter(parent=category):
        enqueue_task_on_commit(
            "summarise_monetary_components", {"category_id": component.id}
        )
