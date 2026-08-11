"""
Test helper for the `ratelimit` cache alias.

Rate-limit counters moved off the `default` cache in the ES-04/L1 follow-up, so
`cache.clear()` in a test's setUp no longer resets them.

Under the suite's default Redis mode the obvious reset --
`caches["ratelimit"].clear()` -- is the wrong one: django_redis implements
`clear()` as FLUSHDB, which ignores KEY_PREFIX and would wipe the counters the
other 15 `--parallel` workers are mid-assertion on. That is the E7 defect class,
and `test_cache_isolation.py` exists to keep it dead.

`delete_pattern` is the scoped alternative. It runs the alias's KEY_FUNCTION over
the pattern, and the test profile sets that to `worker_scoped_key`, so the
pattern resolves to `care-ratelimit:w<id>:<version>:*` and reaches only the
calling worker's own keys.

RF2 made the alias's backend configurable, so this dispatches. Under PostgreSQL
`clear()` is a DELETE against the alias's own table inside the worker's own
cloned database, which reaches nothing another worker owns and is therefore the
correct reset there. Under `disabled` there is no alias and nothing to reset.
"""

from django.conf import settings
from django.core.cache import caches

from config.caches import RATELIMIT_CACHE_ALIAS


def reset_ratelimit_counters() -> None:
    """Drop this worker's rate-limit counters, leaving every other worker's alone."""
    if RATELIMIT_CACHE_ALIAS not in settings.CACHES:
        return
    alias = caches[RATELIMIT_CACHE_ALIAS]
    if hasattr(alias, "delete_pattern"):
        alias.delete_pattern("*")
    else:
        alias.clear()
