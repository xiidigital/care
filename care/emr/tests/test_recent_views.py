"""
Recent views on PostgreSQL (RF1).

These tests pin the behaviour that was previously produced by Redis
``LPUSH``/``LTRIM``/``LREM`` on the ``recent_views`` alias: a bounded
most-recently-used list, scoped to one user and one valueset, de-duplicated by
code, newest first. The storage changed; none of that did.

The Redis-off group is the point of the exercise. It does not merely check that
the feature happens to work when Redis is down -- it makes any Redis call at all
an error, so a regression that reintroduces one fails here rather than in a
Redis-free deployment.
"""

import ast
import threading
from pathlib import Path
from unittest.mock import patch

from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from model_bakery import baker

from care.emr.models.valueset import UserValueSetRecentView, ValueSet
from care.emr.utils.recent_views import RecentViewsManager
from care.utils.tests.base import CareAPITestBase

REPO_ROOT = Path(__file__).resolve().parents[3]


def code(value, display=None, system="http://snomed.info/sct", designation=None):
    """A MinimalCodeConcept payload, exactly as the viewset dumps it."""
    return {
        "display": display or f"Code {value}",
        "system": system,
        "code": value,
        "designation": designation,
    }


def make_valueset(slug):
    return baker.make(
        ValueSet,
        slug=slug,
        name=slug,
        status="active",
        compose={"include": [{"system": "http://snomed.info/sct"}]},
    )


class RecentViewsTestBase(TestCase):
    def setUp(self):
        super().setUp()
        from care.users.models import User

        self.user = baker.make(User)
        self.other_user = baker.make(User)
        self.valueset = make_valueset("recent-views-vs")
        self.other_valueset = make_valueset("recent-views-other-vs")

    def add(self, value, **kwargs):
        RecentViewsManager.add_recent_view(
            self.user, self.valueset, code(value, **kwargs)
        )

    def codes(self, user=None, valueset=None):
        return [
            entry["code"]
            for entry in RecentViewsManager.get_recent_views(
                user or self.user, valueset or self.valueset
            )
        ]


class RecordingAViewTests(RecentViewsTestBase):
    def test_first_view_creates_one_row(self):
        self.add("123")
        self.assertEqual(UserValueSetRecentView.objects.count(), 1)
        entry = UserValueSetRecentView.objects.get()
        self.assertEqual(entry.code, "123")
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.valueset, self.valueset)

    def test_repeated_view_does_not_create_a_duplicate(self):
        self.add("123")
        self.add("123")
        self.add("123")
        self.assertEqual(UserValueSetRecentView.objects.count(), 1)

    def test_repeated_view_becomes_the_most_recent(self):
        # The LPUSH-after-LREM behaviour: re-viewing moves an entry to the front
        # rather than leaving it where it was.
        self.add("a")
        self.add("b")
        self.add("c")
        self.assertEqual(self.codes(), ["c", "b", "a"])

        self.add("a")
        self.assertEqual(self.codes(), ["a", "c", "b"])

    def test_repeated_view_advances_the_timestamp(self):
        self.add("123")
        first = UserValueSetRecentView.objects.get().last_viewed_at
        self.add("123")
        second = UserValueSetRecentView.objects.get().last_viewed_at
        self.assertGreater(second, first)

    def test_repeated_view_refreshes_the_stored_payload(self):
        self.add("123", display="Old label")
        self.add("123", display="New label")
        entry = UserValueSetRecentView.objects.get()
        self.assertEqual(entry.display, "New label")

    def test_ordering_is_newest_first(self):
        for value in ("a", "b", "c", "d"):
            self.add(value)
        self.assertEqual(self.codes(), ["d", "c", "b", "a"])

    def test_a_payload_without_a_code_is_ignored(self):
        # Preserved from the Redis implementation, which returned early rather
        # than pushing an entry that could never be removed by code.
        RecentViewsManager.add_recent_view(self.user, self.valueset, {"code": ""})
        RecentViewsManager.add_recent_view(self.user, self.valueset, {})
        self.assertEqual(UserValueSetRecentView.objects.count(), 0)

    def test_designation_round_trips(self):
        designation = [{"language": "en", "value": "Label"}]
        self.add("123", designation=designation)
        self.assertEqual(
            RecentViewsManager.get_recent_views(self.user, self.valueset)[0][
                "designation"
            ],
            designation,
        )

    def test_deduplication_is_by_code_alone(self):
        # The Redis implementation matched on `code` and ignored `system`.
        # Same code from a different system replaces, it does not accumulate.
        self.add("123", system="http://snomed.info/sct")
        self.add("123", system="http://loinc.org")
        self.assertEqual(UserValueSetRecentView.objects.count(), 1)
        self.assertEqual(
            UserValueSetRecentView.objects.get().system, "http://loinc.org"
        )


class IsolationTests(RecentViewsTestBase):
    def test_recency_is_scoped_to_the_user(self):
        self.add("mine")
        RecentViewsManager.add_recent_view(
            self.other_user, self.valueset, code("theirs")
        )
        self.assertEqual(self.codes(), ["mine"])
        self.assertEqual(self.codes(user=self.other_user), ["theirs"])

    def test_recency_is_scoped_to_the_valueset(self):
        self.add("here")
        RecentViewsManager.add_recent_view(
            self.user, self.other_valueset, code("there")
        )
        self.assertEqual(self.codes(), ["here"])
        self.assertEqual(self.codes(valueset=self.other_valueset), ["there"])

    def test_the_same_code_can_exist_in_two_scopes(self):
        self.add("123")
        RecentViewsManager.add_recent_view(self.user, self.other_valueset, code("123"))
        RecentViewsManager.add_recent_view(self.other_user, self.valueset, code("123"))
        self.assertEqual(UserValueSetRecentView.objects.count(), 3)


class MaxRecentViewTests(RecentViewsTestBase):
    def test_the_default_bound_is_twenty(self):
        # MAX_RECENT_VIEW_FOR_VALUESET is undefined repo-wide; the getattr
        # default is the bound that actually applies.
        self.assertEqual(RecentViewsManager.MAX_RECENT_VIEW, 20)

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_the_list_is_bounded(self):
        for value in ("a", "b", "c", "d", "e"):
            self.add(value)
        self.assertEqual(UserValueSetRecentView.objects.count(), 3)

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_the_oldest_entries_are_the_ones_removed(self):
        for value in ("a", "b", "c", "d", "e"):
            self.add(value)
        self.assertEqual(self.codes(), ["e", "d", "c"])

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_re_viewing_rescues_an_entry_from_being_trimmed(self):
        for value in ("a", "b", "c"):
            self.add(value)
        self.add("a")  # a is now newest, b is now oldest
        self.add("d")
        self.assertEqual(self.codes(), ["d", "a", "c"])

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_trimming_does_not_reach_into_another_scope(self):
        for value in ("a", "b", "c", "d", "e"):
            RecentViewsManager.add_recent_view(
                self.other_user, self.valueset, code(value)
            )
        self.add("mine")
        self.assertEqual(self.codes(), ["mine"])
        self.assertEqual(UserValueSetRecentView.objects.count(), 4)

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_a_read_never_returns_more_than_the_bound(self):
        # Rows above the bound can exist transiently between a concurrent
        # insert and its trim; a read must not expose them.
        for value in ("a", "b", "c"):
            self.add(value)
        with patch.object(RecentViewsManager, "_trim"):
            self.add("d")
        self.assertEqual(UserValueSetRecentView.objects.count(), 4)
        self.assertEqual(len(self.codes()), 3)


class DeletionTests(RecentViewsTestBase):
    def test_remove_one_removes_only_that_code(self):
        self.add("a")
        self.add("b")
        RecentViewsManager.remove_recent_view(self.user, self.valueset, code("a"))
        self.assertEqual(self.codes(), ["b"])

    def test_remove_one_is_scoped(self):
        self.add("a")
        RecentViewsManager.add_recent_view(self.other_user, self.valueset, code("a"))
        RecentViewsManager.remove_recent_view(self.user, self.valueset, code("a"))
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.codes(user=self.other_user), ["a"])

    def test_removing_an_absent_code_is_not_an_error(self):
        self.add("a")
        RecentViewsManager.remove_recent_view(self.user, self.valueset, code("absent"))
        self.assertEqual(self.codes(), ["a"])

    def test_removing_a_payload_without_a_code_is_ignored(self):
        self.add("a")
        RecentViewsManager.remove_recent_view(self.user, self.valueset, {"code": ""})
        self.assertEqual(self.codes(), ["a"])

    def test_clear_empties_the_scope(self):
        self.add("a")
        self.add("b")
        RecentViewsManager.clear_recent_views(self.user, self.valueset)
        self.assertEqual(self.codes(), [])

    def test_clear_is_scoped(self):
        self.add("a")
        RecentViewsManager.add_recent_view(self.other_user, self.valueset, code("a"))
        RecentViewsManager.add_recent_view(self.user, self.other_valueset, code("a"))
        RecentViewsManager.clear_recent_views(self.user, self.valueset)
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.codes(user=self.other_user), ["a"])
        self.assertEqual(self.codes(valueset=self.other_valueset), ["a"])

    def test_clearing_an_empty_scope_is_not_an_error(self):
        RecentViewsManager.clear_recent_views(self.user, self.valueset)
        self.assertEqual(self.codes(), [])


class MissingStateTests(RecentViewsTestBase):
    def test_reading_a_scope_that_was_never_written_returns_an_empty_list(self):
        # LRANGE on a missing key returned []; so does this.
        self.assertEqual(
            RecentViewsManager.get_recent_views(self.user, self.valueset), []
        )

    def test_the_returned_shape_is_the_minimal_code_concept_dict(self):
        self.add("123")
        self.assertEqual(
            RecentViewsManager.get_recent_views(self.user, self.valueset),
            [
                {
                    "display": "Code 123",
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "designation": None,
                }
            ],
        )

    def test_the_key_order_matches_the_pydantic_dump(self):
        # The response is rendered from this dict, so key order is part of the
        # contract that must not drift.
        self.add("123")
        entry = RecentViewsManager.get_recent_views(self.user, self.valueset)[0]
        self.assertEqual(list(entry), ["display", "system", "code", "designation"])


class UnexpectedRedisCallError(AssertionError):
    """Raised if the recent-views path attempts to talk to Redis."""


def _forbid(*args, **kwargs):
    msg = "recent views made a Redis call"
    raise UnexpectedRedisCallError(msg)


class RedisUnavailableTests(RecentViewsTestBase):
    """
    Mandatory RF1 verification: recent views works with Redis unavailable.

    Every route to Redis is made to raise -- the direct client the old
    implementation used, and `execute_command`, through which any django_redis
    cache operation would have to pass. Create, read, update, delete and trim
    then have to run without touching it.
    """

    def setUp(self):
        super().setUp()
        patches = [
            patch("django_redis.get_redis_connection", _forbid),
            patch("redis.Redis.execute_command", _forbid),
            patch("redis.Redis.pipeline", _forbid),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_guard_actually_fires(self):
        # Otherwise every assertion below would pass vacuously.
        from django_redis import get_redis_connection

        with self.assertRaises(UnexpectedRedisCallError):
            get_redis_connection("ratelimit")

    @patch.object(RecentViewsManager, "MAX_RECENT_VIEW", 3)
    def test_create_read_update_delete_and_trim_need_no_redis(self):
        self.add("a")
        self.assertEqual(self.codes(), ["a"])

        self.add("b")
        self.add("a")
        self.assertEqual(self.codes(), ["a", "b"])

        for value in ("c", "d", "e"):
            self.add(value)
        self.assertEqual(self.codes(), ["e", "d", "c"])

        RecentViewsManager.remove_recent_view(self.user, self.valueset, code("e"))
        self.assertEqual(self.codes(), ["d", "c"])

        RecentViewsManager.clear_recent_views(self.user, self.valueset)
        self.assertEqual(self.codes(), [])


class NoRedisImportTests(TestCase):
    """
    Static proof, independent of whether any test happens to exercise the path.
    """

    MODULES = (
        "care/emr/utils/recent_views.py",
        "care/emr/models/valueset.py",
        "care/emr/api/viewsets/valueset.py",
    )

    def test_the_recent_views_implementation_does_not_import_redis(self):
        for relative in self.MODULES:
            with self.subTest(module=relative):
                source = (REPO_ROOT / relative).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative)
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                for forbidden in ("django_redis", "redis", "get_redis_connection"):
                    self.assertNotIn(forbidden, imported)

    def test_the_recent_views_implementation_names_no_redis_command(self):
        source = (REPO_ROOT / "care/emr/utils/recent_views.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        called = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for command in ("lpush", "ltrim", "lrem", "lrange", "get_redis_connection"):
            self.assertNotIn(command, called)


class RecentViewsApiTests(CareAPITestBase):
    """The four endpoints, unchanged in contract, now reading PostgreSQL."""

    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.client.force_authenticate(user=self.user)
        self.valueset = make_valueset("api-recent-views")

    def url(self, action):
        return reverse(f"value-set-{action}", kwargs={"slug": self.valueset.slug})

    @patch.object(ValueSet, "lookup", return_value=True)
    def add(self, value, mock_lookup):
        return self.client.post(self.url("add-recent-view"), code(value), format="json")

    def test_the_full_endpoint_cycle(self):
        self.assertEqual(self.client.get(self.url("recent-views")).json(), [])

        response = self.add("123")
        self.assertEqual(response.status_code, 200)
        self.assertIn("added to recent views", response.json()["message"])

        listed = self.client.get(self.url("recent-views")).json()
        self.assertEqual(
            listed,
            [
                {
                    "display": "Code 123",
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "designation": None,
                }
            ],
        )

        self.add("456")
        self.assertEqual(
            [
                entry["code"]
                for entry in self.client.get(self.url("recent-views")).json()
            ],
            ["456", "123"],
        )

        removed = self.client.post(
            self.url("remove-recent-view"), code("456"), format="json"
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(
            [
                entry["code"]
                for entry in self.client.get(self.url("recent-views")).json()
            ],
            ["123"],
        )

        cleared = self.client.post(self.url("clear-recent-views"), format="json")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.get(self.url("recent-views")).json(), [])

    def test_the_response_exposes_no_persistence_detail(self):
        self.add("123")
        entry = self.client.get(self.url("recent-views")).json()[0]
        for leaked in ("id", "pk", "user", "valueset", "last_viewed_at"):
            self.assertNotIn(leaked, entry)

    @patch.object(ValueSet, "lookup", return_value=False)
    def test_an_invalid_code_is_still_rejected_before_anything_is_stored(
        self, mock_lookup
    ):
        response = self.client.post(
            self.url("add-recent-view"), code("nope"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserValueSetRecentView.objects.count(), 0)

    def test_another_user_does_not_see_this_users_list(self):
        self.add("123")
        self.client.force_authenticate(user=self.create_user())
        self.assertEqual(self.client.get(self.url("recent-views")).json(), [])

    def test_the_endpoints_require_authentication(self):
        # Anonymous users cannot use the feature: there is no scope to read.
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.url("recent-views")).status_code, (401, 403))


class ConcurrentWriteTests(TransactionTestCase):
    """
    Real PostgreSQL, real threads, real connections.

    `TransactionTestCase` rather than `TestCase` because each thread opens its
    own connection and would otherwise be unable to see -- or collide with --
    rows written inside the test's own transaction.
    """

    def setUp(self):
        super().setUp()
        from care.users.models import User

        self.user = baker.make(User)
        self.valueset = make_valueset("concurrent-recent-views")

    def run_concurrently(self, targets):
        errors = []

        def wrapped(fn):
            def run():
                try:
                    fn()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    connections.close_all()

            return run

        threads = [threading.Thread(target=wrapped(fn)) for fn in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return errors

    def test_concurrent_views_of_the_same_code_produce_one_row(self):
        # The unique constraint is what makes this safe: the loser of the race
        # gets an IntegrityError, which update_or_create retries as a fetch.
        def view():
            RecentViewsManager.add_recent_view(self.user, self.valueset, code("123"))

        errors = self.run_concurrently([view] * 8)
        self.assertEqual(errors, [])
        self.assertEqual(UserValueSetRecentView.objects.count(), 1)

    def test_concurrent_writes_do_not_leave_permanent_unbounded_growth(self):
        max_entries = 3
        with patch.object(RecentViewsManager, "MAX_RECENT_VIEW", max_entries):

            def view(value):
                def run():
                    RecentViewsManager.add_recent_view(
                        self.user, self.valueset, code(value)
                    )

                return run

            errors = self.run_concurrently([view(str(i)) for i in range(12)])
            self.assertEqual(errors, [])

            # A concurrent burst can leave rows above the bound transiently:
            # each writer trims against the window it saw. The next write must
            # bring it back down, and reads are bounded throughout.
            RecentViewsManager.add_recent_view(self.user, self.valueset, code("final"))
            self.assertEqual(UserValueSetRecentView.objects.count(), max_entries)
            self.assertEqual(
                len(RecentViewsManager.get_recent_views(self.user, self.valueset)),
                max_entries,
            )

    def tearDown(self):
        UserValueSetRecentView.objects.all().delete()
        super().tearDown()
