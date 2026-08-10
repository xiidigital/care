"""
Test helper for the `ratelimit` cache alias.

Rate-limit counters moved off the `default` cache in the ES-04/L1 follow-up, so
`cache.clear()` in a test's setUp no longer resets them. They live in Redis
because django_ratelimit requires an atomic INCR (see
`config/caches.build_ratelimit_cache`), and Redis is shared by every
`--parallel` worker.

That makes the obvious reset -- `caches["ratelimit"].clear()` -- the wrong one:
django_redis implements `clear()` as FLUSHDB, which ignores KEY_PREFIX and would
wipe the counters and recent-views entries the other 15 workers are mid-assertion
on. That is the E7 defect class, and `test_cache_isolation.py` exists to keep it
dead.

`delete_pattern` is the scoped alternative. It runs the alias's KEY_FUNCTION over
the pattern, and the test profile sets that to `worker_scoped_key`, so the
pattern resolves to `care-ratelimit:w<id>:<version>:*` and reaches only the
calling worker's own keys.
"""

from django.core.cache import caches

from config.caches import RATELIMIT_CACHE_ALIAS


def reset_ratelimit_counters() -> None:
    """Drop this worker's rate-limit counters, leaving every other worker's alone."""
    caches[RATELIMIT_CACHE_ALIAS].delete_pattern("*")
