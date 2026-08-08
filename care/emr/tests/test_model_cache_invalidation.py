"""
Model cache invalidation after `delete_pattern` was removed.

ES-04 section 31. The instruction there is explicit: do not merely assert that
`delete_pattern` disappeared, assert that stale cached values are actually
invalidated. So these tests exercise the read-through cache and then check what
a subsequent read returns, rather than inspecting keys.

Background: `delete_model_cache` used to call `cache.delete_pattern(...)`, a
django_redis extension that expanded `serializers_cache:<model>:<pk>:*` with
SCAN. It is not part of Django's cache API and raises AttributeError on
DatabaseCache, LocMemCache and DummyCache alike. It now deletes an explicit key
per registered resource, using the registry the `@cacheable` decorator fills.
"""

from django.core.cache import cache

from care.emr.resources.base import (
    _CACHEABLE_RESOURCES,
    model_cache_key,
    model_from_cache,
    model_string,
)
from care.emr.resources.facility.spec import FacilityBareMinimumSpec
from care.facility.models.facility import Facility
from care.utils.tests.base import CareAPITestBase


class ModelCacheInvalidationTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.create_super_user()
        self.facility = self.create_facility(user=self.user, name="Original Name")

    def cache_key_for(self, identifier):
        return model_cache_key(
            model_string(Facility), FacilityBareMinimumSpec.__name__, identifier
        )

    def test_first_read_populates_the_cache(self):
        cache.delete(self.cache_key_for(self.facility.id))
        data = model_from_cache(FacilityBareMinimumSpec, id=self.facility.id)
        self.assertEqual(data["name"], "Original Name")
        self.assertIsNotNone(cache.get(self.cache_key_for(self.facility.id)))

    def test_saving_the_model_invalidates_the_cached_value(self):
        model_from_cache(FacilityBareMinimumSpec, id=self.facility.id)
        self.assertIsNotNone(cache.get(self.cache_key_for(self.facility.id)))

        self.facility.name = "Renamed"
        self.facility.save()

        self.assertIsNone(cache.get(self.cache_key_for(self.facility.id)))

    def test_a_read_after_a_write_returns_the_new_value(self):
        # The behaviour that actually matters: no stale data is served. This is
        # what would regress if invalidation missed a key.
        self.assertEqual(
            model_from_cache(FacilityBareMinimumSpec, id=self.facility.id)["name"],
            "Original Name",
        )

        self.facility.name = "Renamed"
        self.facility.save()

        self.assertEqual(
            model_from_cache(FacilityBareMinimumSpec, id=self.facility.id)["name"],
            "Renamed",
        )

    def test_the_external_id_keyed_entry_is_also_invalidated(self):
        # model_from_cache accepts either identifier, so both are cached and
        # both must be dropped. The old wildcard covered this incidentally;
        # explicit deletion has to do it on purpose.
        model_from_cache(FacilityBareMinimumSpec, external_id=self.facility.external_id)
        self.assertIsNotNone(cache.get(self.cache_key_for(self.facility.external_id)))

        self.facility.name = "Renamed"
        self.facility.save()

        self.assertIsNone(cache.get(self.cache_key_for(self.facility.external_id)))
        self.assertEqual(
            model_from_cache(
                FacilityBareMinimumSpec, external_id=self.facility.external_id
            )["name"],
            "Renamed",
        )

    def test_invalidation_does_not_disturb_other_instances(self):
        # delete_pattern matched on pk, so this held before; explicit deletion
        # must not have widened the blast radius.
        other = self.create_facility(user=self.user, name="Other")
        model_from_cache(FacilityBareMinimumSpec, id=self.facility.id)
        model_from_cache(FacilityBareMinimumSpec, id=other.id)

        self.facility.name = "Renamed"
        self.facility.save()

        self.assertIsNone(cache.get(self.cache_key_for(self.facility.id)))
        self.assertIsNotNone(cache.get(self.cache_key_for(other.id)))


class CacheableRegistryTests(CareAPITestBase):
    """
    The registry that replaced the wildcard.

    A pattern deleted every resource cached under a pk without knowing their
    names. Explicit deletion only works if every `@cacheable` resource is
    registered, so that is asserted directly.
    """

    def test_every_cacheable_resource_is_registered_against_its_model(self):
        self.assertIn(
            FacilityBareMinimumSpec.__name__,
            _CACHEABLE_RESOURCES[model_string(Facility)],
        )

    def test_registry_covers_all_four_cacheable_specs(self):
        # If a fifth is added without registration its cache would go stale
        # forever, so this fails loudly when the set changes.
        registered = {name for names in _CACHEABLE_RESOURCES.values() for name in names}
        self.assertEqual(
            registered,
            {
                "FacilityBareMinimumSpec",
                "PatientIdentifierListSpec",
                "TagConfigReadSpec",
                "UserSpec",
            },
        )

    def test_every_registered_model_invalidates_all_of_its_resources(self):
        # Guards the fan-out: one database model may back several specs, and a
        # save must drop every one of them, not just the first.
        model_key = model_string(Facility)
        _CACHEABLE_RESOURCES[model_key].add("PretendSecondSpec")
        try:
            first = self.cache_key(model_key, "FacilityBareMinimumSpec")
            second = self.cache_key(model_key, "PretendSecondSpec")
            cache.set(first, {"stale": True})
            cache.set(second, {"stale": True})

            self.facility.save()

            self.assertIsNone(cache.get(first))
            self.assertIsNone(cache.get(second))
        finally:
            _CACHEABLE_RESOURCES[model_key].discard("PretendSecondSpec")

    def cache_key(self, model_key, resource_name):
        return model_cache_key(model_key, resource_name, self.facility.id)

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.create_super_user()
        self.facility = self.create_facility(user=self.user, name="Original Name")
