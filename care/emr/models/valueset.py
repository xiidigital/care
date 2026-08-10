from django.conf import settings
from django.db import models

from care.emr.fhir.resources.valueset import ValueSetResource
from care.emr.models import EMRBaseModel
from care.emr.resources.common.valueset import ValueSetCompose


class ValueSet(EMRBaseModel):
    slug = models.SlugField(max_length=255, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(default="")
    compose = models.JSONField(default=dict)
    status = models.CharField(max_length=255)
    is_system_defined = models.BooleanField(default=False)

    def create_composition(self):
        systems = {}
        compose = self.compose
        if type(self.compose) is dict:
            compose = ValueSetCompose(**self.compose)
        for include in compose.include:
            system = include.system
            if system not in systems:
                systems[system] = {"include": []}
            systems[system]["include"].append(include.model_dump(exclude_defaults=True))
        for exclude in compose.exclude:
            system = exclude.system
            if system not in systems:
                systems[system] = {"exclude": []}
            elif "exclude" not in systems[system]:
                systems[system]["exclude"] = []
            systems[system]["exclude"].append(exclude.model_dump(exclude_defaults=True))
        return systems

    def search(self, search="", count=10, display_language=None):
        systems = self.create_composition()
        results = []
        for system in systems:
            temp = ValueSetResource().filter(
                search=search, count=count, **systems[system]
            )
            if display_language:
                temp = temp.filter(display_language=display_language)
            results.extend(temp.search())
        return results

    def lookup(self, code):
        systems = self.create_composition()
        results = []
        for system in systems:
            results.append(ValueSetResource().filter(**systems[system]).lookup(code))
        return any(results)


class UserValueSetPreference(EMRBaseModel):
    user = models.ForeignKey("users.User", on_delete=models.CASCADE)
    valueset = models.ForeignKey("emr.ValueSet", on_delete=models.CASCADE)
    favorite_codes = models.JSONField(default=list)

    class Meta:
        unique_together = ("user", "valueset")

    MAX_FAVORITES = getattr(settings, "MAX_FAVORITES_FOR_VALUESET", 50)


class UserValueSetRecentView(models.Model):
    """
    One entry of a user's most-recently-viewed codes for a single valueset.

    Replaces the Redis list that backed recent views (RF1). The recency scope is
    the same composite the Redis key encoded -- user plus valueset -- and the
    uniqueness constraint is on ``code`` alone because the Redis implementation
    de-duplicated on ``code`` alone, ignoring ``system``.

    Deliberately a plain ``models.Model`` rather than ``EMRBaseModel``. This is
    ephemeral UI convenience state: it needs no audit trail, no ``external_id``
    and no history. Soft deletion would actively break it -- a soft-deleted row
    still occupies the unique constraint, so re-viewing a removed code would
    raise ``IntegrityError``, and trimming would never reclaim anything.

    The code payload is stored as columns rather than a JSON blob because its
    shape is fixed by
    :class:`~care.emr.fhir.resources.code_concept.MinimalCodeConcept`; the
    reader reassembles that exact dict, so the API response is unchanged.
    """

    user = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="valueset_recent_views"
    )
    valueset = models.ForeignKey("emr.ValueSet", on_delete=models.CASCADE)
    code = models.CharField(max_length=255)
    system = models.CharField(max_length=255)
    display = models.TextField()
    designation = models.JSONField(null=True, blank=True, default=None)
    last_viewed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "valueset", "code"],
                name="unique_user_valueset_recent_view_code",
            )
        ]
        indexes = [
            # Covers both reads: list a scope newest-first, and select the
            # retained window when trimming to MAX_RECENT_VIEW.
            models.Index(
                fields=["user", "valueset", "-last_viewed_at"],
                name="user_valueset_recent_view_idx",
            )
        ]

    def __str__(self):
        return f"{self.code} ({self.valueset_id}) for user {self.user_id}"
