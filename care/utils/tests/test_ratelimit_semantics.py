"""
What the rate-limit cache keys actually separate.

ES-04 sections 17 and 34 ask whether production uses a globally shared key where
caller-specific dimensions are required, and say to fix it *only* if the intended
semantics are unambiguous.

`unresolved-items.md` E7 recorded that `config/ratelimit.py:9` returns the
constant `"ratelimit"` and concluded the counter is therefore global. Re-verified
during ES-04, that conclusion is wrong, and these tests are the evidence.

`django_ratelimit._make_cache_key` builds the key from
``[group, rate, key_value, window]``. CARE's `ratelimit()` puts the caller
dimension into the **group** -- ``_group = group + f"-{key}"`` -- and only then
uses the constant key function. So `reset-request-alice` and `reset-request-bob`
are different groups and get different buckets. The constant key function is
redundant, not incorrect.

No production change was made. Changing the key shape would silently reset every
live limiter for no correctness gain.
"""

from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django_ratelimit.core import _get_window, _make_cache_key

from care.utils.tests.ratelimit import reset_ratelimit_counters
from config.ratelimit import get_ratelimit_key, ratelimit

RATE = "10/h"


def cache_key_for(group, value):
    window = _get_window(value, 3600)
    return _make_cache_key(group, window, RATE, value, "ALL")


class RateLimitKeyDimensionTests(SimpleTestCase):
    def test_the_key_function_is_constant(self):
        # Restating the observation E7 started from, so the correction below is
        # not mistaken for a disagreement about the facts.
        self.assertEqual(get_ratelimit_key("any-group", None), "ratelimit")

    def test_the_caller_dimension_travels_in_the_group(self):
        # Different usernames -> different groups -> different cache keys, even
        # though the key function returned the same constant for both.
        alice = cache_key_for("reset-request-alice", "ratelimit")
        bob = cache_key_for("reset-request-bob", "ratelimit")
        self.assertNotEqual(alice, bob)

    def test_different_endpoints_do_not_share_a_bucket(self):
        self.assertNotEqual(
            cache_key_for("reset-check-alice", "ratelimit"),
            cache_key_for("reset-confirm-alice", "ratelimit"),
        )

    def test_the_same_caller_reuses_one_bucket(self):
        self.assertEqual(
            cache_key_for("reset-request-alice", "ratelimit"),
            cache_key_for("reset-request-alice", "ratelimit"),
        )


@override_settings(DISABLE_RATELIMIT=False)
class RateLimitCallerIsolationTests(SimpleTestCase):
    """
    End-to-end through CARE's own wrapper: one caller exhausting a limit must
    not throttle another.
    """

    def setUp(self):
        super().setUp()
        # Counters live in the `ratelimit` alias since the L1 follow-up, so
        # clearing `default` would reset nothing. This drops only this worker's
        # keys -- see care/utils/tests/ratelimit.py for why not `clear()`.
        reset_ratelimit_counters()
        self.factory = RequestFactory()

    def request(self):
        request = self.factory.post("/api/v1/auth/reset-password/")
        request.META["REMOTE_ADDR"] = "203.0.113.9"
        return request

    def exhaust(self, username, limit=10):
        for _ in range(limit):
            ratelimit(self.request(), "reset-request", [username], RATE)

    def test_one_username_hitting_the_limit_does_not_throttle_another(self):
        self.exhaust("alice")
        # alice is now over the limit; bob has made no requests at all.
        self.assertFalse(ratelimit(self.request(), "reset-request", ["bob"], RATE))

    def test_a_caller_that_exceeds_the_rate_is_limited(self):
        # Confirms the limiter is actually counting, so the test above is not
        # passing merely because nothing is enforced.
        #
        # Once the limit trips, `ratelimit()` falls through to captcha
        # validation and returns `not validatecaptcha(request)`. The captcha is
        # stubbed to a failure so the assertion is about the counter rather
        # than about Google's response; a plain WSGIRequest has no `.data`
        # either, which is a DRF attribute.
        self.exhaust("carol")
        with patch("config.ratelimit.validatecaptcha", return_value=False):
            result = ratelimit(self.request(), "reset-request", ["carol"], RATE)
        self.assertTrue(result)

    def test_a_caller_under_the_rate_is_not_limited(self):
        self.assertFalse(ratelimit(self.request(), "reset-request", ["dave"], RATE))
