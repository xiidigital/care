import base64
import json

from authlib.jose import JsonWebKey

from care.utils.jwks.generate_jwk import get_jwks_from_file
from config.caches import RATELIMIT_CACHE_ALIAS, build_ratelimit_cache

from .base import *  # noqa
from .base import BASE_DIR, REDIS_URL, TEMPLATES, env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[-1]["OPTIONS"]["loaders"] = [  # type: ignore[index]
    (
        "django.template.loaders.cached.Loader",
        [
            "django.template.loaders.filesystem.Loader",
            "django.template.loaders.app_directories.Loader",
        ],
    )
]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# Your stuff...
# ------------------------------------------------------------------------------

DATABASES = {"default": env.db("DATABASE_URL", default="postgres:///care-test")}

# Cache isolation for the test suite -- this is the E7 fix.
#
# E7 was a defect *class*, not a flaky test: every one of the 16 `--parallel`
# workers pointed at the same Redis database, and `cache.clear()` in the setUp
# of test_reset_password_api and test_valueset_api calls `flushdb()` in
# django_redis, which wipes the entire database and ignores KEY_PREFIX. One
# worker's clear destroyed cache state the other 15 were mid-assertion on,
# producing `200 != 429` in the rate-limit tests and missing favorites entries.
# (The old KEY_PREFIX above would not have helped even against ordinary key
# collisions: it sat inside OPTIONS, and BaseCache reads KEY_PREFIX from the
# top level, so it was never applied.)
#
# LocMem removes the class rather than the six symptoms: each worker is a
# separate process with its own cache, so `clear()` cannot reach across workers
# and no key can collide. It is also what ES-04 section 9 nominates for tests.
# Redis and PostgreSQL cache semantics are not left untested -- they have
# dedicated coverage in care/utils/tests/test_cache_backends.py, which selects
# each backend explicitly.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "care-test",
        "KEY_PREFIX": "care-test",
    },
    # This one must stay on Redis even in tests: it needs a guarantee with no
    # portable equivalent, and a LocMem stand-in is not even configurable,
    # because django_ratelimit's E003 rejects it as "not a shared cache". It is
    # namespaced per worker instead, which is what stops parallel workers
    # sharing the rate-limit bucket keyed on the test client's fixed 127.0.0.1.
    #
    # Never call `clear()` on it: django_redis implements it as FLUSHDB, which
    # ignores KEY_PREFIX and is the mechanism behind E7. Reset rate-limit
    # counters with `care.utils.tests.ratelimit.reset_ratelimit_counters()`,
    # which deletes only the current worker's keys.
    RATELIMIT_CACHE_ALIAS: {
        **build_ratelimit_cache(REDIS_URL),
        "KEY_FUNCTION": "config.caches.worker_scoped_key",
    },
    "swagger_cache": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "swagger-schema-cache",
    },
}

# No SILENCED_SYSTEM_CHECKS. `django_ratelimit.E003` and `W001` were silenced
# here while rate limiting read the `default` cache, which the profile above
# sets to LocMem. Rate limiting now names its own Redis alias, so both checks
# pass on their own and the suite sees the same check results as production.

# https://whitenoise.evans.io/en/stable/django.html#whitenoise-makes-my-tests-run-slow
WHITENOISE_AUTOREFRESH = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(message)s"}
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
        },
        "audit_log": {
            "handlers": ["console"],
            "level": "ERROR",
        },
        "celery": {
            "handlers": ["console"],
            "level": "ERROR",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

CELERY_TASK_ALWAYS_EAGER = True


# open id connect
JWKS = JsonWebKey.import_key_set(
    json.loads(
        base64.b64decode(
            env(
                "JWKS_BASE64",
                default=get_jwks_from_file(BASE_DIR),
            )
        )
    )
)

DISABLE_RATELIMIT = True

SMS_BACKEND = "care.utils.sms.backend.console.ConsoleBackend"

# https://github.com/anexia-it/django-rest-passwordreset#configuration--settings
DJANGO_REST_PASSWORDRESET_NO_INFORMATION_LEAKAGE = True
