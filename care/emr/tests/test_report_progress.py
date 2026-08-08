"""
Report-generation progress as shared cache.

ES-04 section 33. ADR-0004 allowed progress to live either in the configured
shared cache or in an explicit PostgreSQL model. The decision recorded in
`care/emr/reports/report_utils.py` is shared cache, because the value is a
percentage that costs at most a duplicate render if lost, while the durable
artefacts -- the `ReportUpload` row and the stored object -- are written
independently of it.

The functions were called `set_lock`/`clear_lock` before ES-04 despite never
passing `nx`. The tests below assert the progress semantics they actually have,
including the fact that they do *not* exclude concurrent callers.
"""

from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings

from care.emr.reports import report_utils
from config.caches import build_default_cache

POSTGRES_CACHES = {
    "default": build_default_cache(
        "postgres", table="care_cache_test", key_prefix="care-progress-test"
    )
}


class ReportProgressTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        caches["default"].clear()
        self.key = report_utils.get_progress_key("encounter", "abc-123")

    def test_missing_progress_reads_as_none(self):
        # The API turns this into "no generation running", so None must mean
        # absent rather than zero -- 0% would be indistinguishable from done.
        self.assertIsNone(report_utils.get_progress(self.key))

    def test_initial_progress_is_published(self):
        report_utils.set_progress(self.key, 10)
        self.assertEqual(report_utils.get_progress(self.key), 10)

    def test_progress_can_be_updated(self):
        report_utils.set_progress(self.key, 10)
        report_utils.set_progress(self.key, 30)
        self.assertEqual(report_utils.get_progress(self.key), 30)

    def test_progress_can_be_cleared(self):
        report_utils.set_progress(self.key, 30)
        report_utils.clear_progress(self.key)
        self.assertIsNone(report_utils.get_progress(self.key))

    def test_progress_expires(self):
        report_utils.set_progress(self.key, 10, timeout=0)
        self.assertIsNone(report_utils.get_progress(self.key))

    def test_default_timeout_is_the_documented_two_minutes(self):
        self.assertEqual(report_utils.PROGRESS_TIMEOUT, 120)

    def test_keys_for_different_reports_do_not_collide(self):
        other = report_utils.get_progress_key("encounter", "def-456")
        report_utils.set_progress(self.key, 10)
        self.assertIsNone(report_utils.get_progress(other))

    def test_keys_for_different_report_types_do_not_collide(self):
        other = report_utils.get_progress_key("discharge", "abc-123")
        report_utils.set_progress(self.key, 10)
        self.assertIsNone(report_utils.get_progress(other))

    def test_progress_is_not_a_lock(self):
        # Deliberate assertion of a known limitation. Two callers can both
        # publish progress; nothing is excluded. ADR-0004 requires progress not
        # to be implemented as a lock, and ES-05 owns real mutual exclusion.
        report_utils.set_progress(self.key, 10)
        report_utils.set_progress(self.key, 10)
        self.assertEqual(report_utils.get_progress(self.key), 10)

    def test_cache_failure_reads_as_no_progress(self):
        # ES-04 section 20: the failure policy for this value is fail-open. A
        # cache outage costs a duplicate render, never a wrong report.
        with override_settings(CACHES={"default": build_default_cache("dummy")}):
            report_utils.set_progress(self.key, 30)
            self.assertIsNone(report_utils.get_progress(self.key))


@override_settings(CACHES=POSTGRES_CACHES)
class ReportProgressOnPostgresTests(TestCase):
    """
    The same semantics on the backend the GCP profile selects.

    Progress crosses a process boundary -- written by the worker, read by the
    API -- so the backend carrying it has to be shared. This is the check that
    the PostgreSQL profile can carry it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        with override_settings(CACHES=POSTGRES_CACHES):
            call_command("createcachetable", verbosity=0)

    def setUp(self):
        super().setUp()
        caches["default"].clear()
        self.key = report_utils.get_progress_key("encounter", "abc-123")

    def test_progress_round_trips_through_the_database_cache(self):
        report_utils.set_progress(self.key, 30)
        self.assertEqual(report_utils.get_progress(self.key), 30)

    def test_progress_is_visible_to_a_separate_cache_client(self):
        # Stands in for the worker/API split: a different client object reading
        # the same table sees the value, which LocMem could not provide.
        report_utils.set_progress(self.key, 30)
        with override_settings(
            CACHES={
                "second": build_default_cache(
                    "postgres",
                    table="care_cache_test",
                    key_prefix="care-progress-test",
                )
            }
        ):
            self.assertEqual(
                caches["second"].get(f"report_generation_progress:{self.key}"), 30
            )

    def test_progress_can_be_cleared(self):
        report_utils.set_progress(self.key, 30)
        report_utils.clear_progress(self.key)
        self.assertIsNone(report_utils.get_progress(self.key))
