"""
CARE's rate-limit seam.

Every caller in the application goes through :func:`ratelimit` and none of them
knows which store is counting. RF2 made that store configurable --
``CARE_RATE_LIMIT_BACKEND`` selects ``redis``, ``postgres`` or ``disabled`` --
and this module is where the choice is absorbed. A call site that branched on
the backend name would be a defect: the guarantee differs between modes, but the
question ("is this caller over its limit?") does not, and neither does the
answer's shape.

``django_ratelimit`` still does the counting in both counting modes. RF2 changed
where the counters live and what happens when that store fails, not how a rate
is interpreted or how a window is computed.
"""

import logging

import requests
from django.conf import settings
from django.db import DatabaseError, transaction
from django_ratelimit.core import is_ratelimited

from config.caches import (
    DISABLED_RATE_LIMIT_BACKEND,
    POSTGRES_RATE_LIMIT_BACKEND,
)

logger = logging.getLogger(__name__)

VALIDATE_CAPTCHA_REQUEST_TIMEOUT = 5


def get_ratelimit_key(group, request):
    return "ratelimit"


def rate_limiting_enabled() -> bool:
    """Whether this deployment counts requests at all.

    Two independent ways to say no, kept separate on purpose.
    ``CARE_RATE_LIMIT_BACKEND=disabled`` is a deployment declaring that CARE
    performs no application rate limiting; ``DISABLE_RATELIMIT`` is the older
    per-environment override that local and test settings use. Neither is
    inferred from anything else -- in particular, an unreachable Redis does not
    disable rate limiting, it trips the failure policy below.
    """
    if settings.DISABLE_RATELIMIT:
        return False
    return settings.CARE_RATE_LIMIT_BACKEND != DISABLED_RATE_LIMIT_BACKEND


def _is_ratelimited(request, group, key, rate, increment):
    """Ask ``django_ratelimit`` for one counter, applying the mode's failure policy.

    **Redis.** Straight through. The alias sets ``IGNORE_EXCEPTIONS``, so an
    outage becomes an unknown count inside the library, and ``RATELIMIT_FAIL_OPEN
    = False`` turns an unknown count into ``should_limit``. The caller then meets
    a captcha.

    **PostgreSQL.** ``DatabaseCache`` has no ``IGNORE_EXCEPTIONS``; a missing
    cache table or a broken connection raises ``DatabaseError`` out of
    ``cache.add()``. Letting that propagate would turn a store failure into a 500
    on login and password reset, and swallowing it as "not limited" would turn
    the same failure into unrestricted traffic on exactly the endpoints that must
    not have it. Neither is acceptable, so the failure is caught and reported as
    limited -- which lands the caller on the captcha path, the same place a Redis
    outage lands them. Fail-closed with an escape, identical semantics, different
    mechanism.

    The savepoint is what makes that escape real. ``ATOMIC_REQUESTS`` is on, so a
    failed statement would otherwise poison the surrounding transaction and every
    later query in the request -- including the ones the captcha path needs.
    Rolling back to a savepoint confines the damage to the counter.
    """
    if settings.CARE_RATE_LIMIT_BACKEND != POSTGRES_RATE_LIMIT_BACKEND:
        return is_ratelimited(
            request, group=group, key=key, rate=rate, increment=increment
        )

    try:
        with transaction.atomic():
            return is_ratelimited(
                request, group=group, key=key, rate=rate, increment=increment
            )
    except DatabaseError:
        # No connection details, no key, no caller identity -- this line can end
        # up in an aggregator.
        logger.warning(
            "Rate-limit counter unavailable for group %r; treating as limited.",
            group,
        )
        return True


def validatecaptcha(request):
    recaptcha_response = request.data.get(settings.GOOGLE_CAPTCHA_POST_KEY)
    if not recaptcha_response:
        return False
    values = {
        "secret": settings.GOOGLE_RECAPTCHA_SECRET_KEY,
        "response": recaptcha_response,
    }
    captcha_response = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data=values,
        timeout=VALIDATE_CAPTCHA_REQUEST_TIMEOUT,
    )
    result = captcha_response.json()

    return bool(result["success"])


# refer https://django-ratelimit.readthedocs.io/en/stable/rates.html for rate
def ratelimit(
    request, group="", keys=None, rate=settings.DJANGO_RATE_LIMIT, increment=True
):
    if keys is None:
        keys = [None]
    # `disabled` returns here: no counter operation, no cache lookup, no
    # connection of any kind. False is "not rate limited", which is what every
    # call site already treats as the unremarkable case.
    if not rate_limiting_enabled():
        return False

    checkcaptcha = False
    for key in keys:
        if key == "ip":
            _group = group
            _key = "ip"
        else:
            _group = group + f"-{key}"
            _key = get_ratelimit_key
        if _is_ratelimited(request, _group, _key, rate, increment):
            checkcaptcha = True

    if checkcaptcha:
        return not validatecaptcha(request)

    return False


def get_user_readable_rate_limit_time(rate_limit):
    if not rate_limit:
        return "1 second"

    _requests, time = rate_limit.split("/")

    time_unit_map = {
        "s": "second(s)",
        "m": "minute(s)",
        "h": "hour(s)",
        "d": "day(s)",
    }

    time_value = time[:-1]
    time_unit = time[-1]

    return f"{time_value or 1} {time_unit_map.get(time_unit, 'second(s)')}"


USER_READABLE_RATE_LIMIT_TIME = get_user_readable_rate_limit_time(
    settings.DJANGO_RATE_LIMIT
)
