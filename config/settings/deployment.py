import base64
import json
import logging

import sentry_sdk
from authlib.jose import JsonWebKey
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.redis import RedisIntegration

from care.utils.jwks.generate_jwk import get_jwks_from_file

from .base import *  # noqa
from .base import APP_VERSION, DATABASES, TEMPLATES, env

# DATABASES
# ------------------------------------------------------------------------------
DATABASES["default"] = env.db("DATABASE_URL")
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-proxy-ssl-header
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-ssl-redirect
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-secure
SESSION_COOKIE_SECURE = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-secure
CSRF_COOKIE_SECURE = True
# https://docs.djangoproject.com/en/dev/topics/security/#ssl-https
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-seconds
# TODO: set this to 60 seconds first and then to 518400 once you prove the former works
SECURE_HSTS_SECONDS = 60
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-include-subdomains
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-preload
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
# https://docs.djangoproject.com/en/dev/ref/middleware/#x-content-type-options-nosniff
SECURE_CONTENT_TYPE_NOSNIFF = env.bool(
    "DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", default=True
)
# https://github.com/adamchainz/django-cors-headers#cors_allowed_origins-sequencestr
CORS_ALLOWED_ORIGINS = env.json("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOWED_ORIGIN_REGEXES = env.json("CORS_ALLOWED_ORIGIN_REGEXES", default=[])

# TEMPLATES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
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
# A deployed environment that talks to a relay should do so over TLS, so True is
# the default here where base.py leaves it False. It is a default and not a
# constant: it was previously assigned unconditionally, which meant an operator
# could not select implicit TLS on port 465 — Django rejects EMAIL_USE_TLS and
# EMAIL_USE_SSL together, so forcing one made the other unreachable.
#
# Both are inert under a non-SMTP EMAIL_BACKEND. The console backend, which is a
# valid configuration in every environment, never opens a socket.
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    # False, matching base.py and test.py, and load-bearing.
    #
    # `django.setup()` applies this dictConfig, and with True `logging.config`
    # permanently sets `disabled = True` on every logger object that already
    # exists and is not named here. That is not a small set: it includes every
    # logger built while the settings modules imported, every logger Celery
    # created before Django was set up, and -- worst -- `django.request`, the
    # logger through which Django reports unhandled exceptions. A disabled
    # logger drops records at `Logger.handle()`, before any handler runs, so
    # nothing propagates to the root handler below either.
    #
    # Measured on this image before the change (unresolved-items.md L2): an
    # ERROR emitted on `django.request`, on `celery.worker` or on any
    # settings-time logger produced no output at all, while the same ERROR on a
    # logger created after setup printed normally. A 500 raised inside a view
    # therefore reached Cloud Logging with no exception type, no message and no
    # traceback, and Celery never emitted its own startup lines.
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s "
            "%(process)d %(thread)d %(message)s"
        }
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        # Declared so that re-enabling the existing loggers above does not also
        # restore Django's DEFAULT_LOGGING handlers for this one. Those are a
        # `console` handler gated on DEBUG -- which would print a second copy of
        # every record the root handler already printed, wherever DEBUG is on --
        # and `mail_admins`, which is gated on DEBUG being *off* and would make
        # every 500 in a deployed environment attempt an SMTP connection. There
        # is no mail relay to attempt it against (unresolved-items.md N1).
        # Configuring the logger here replaces both with the one console handler
        # and stops propagation, so each record is emitted exactly once.
        "django": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "django.db.backends": {
            "level": "ERROR",
            "handlers": ["console"],
            "propagate": False,
        },
        # Errors logged by the SDK itself
        "sentry_sdk": {"level": "ERROR", "handlers": ["console"], "propagate": False},
    },
}

# Sentry
# ------------------------------------------------------------------------------
# https://docs.sentry.io/platforms/python/guides/django/configuration/
if SENTRY_DSN := env("SENTRY_DSN", default=""):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=APP_VERSION,
        environment=env("SENTRY_ENVIRONMENT", default="deployment-unknown"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0),
        profiles_sample_rate=env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0),
        integrations=[
            LoggingIntegration(
                event_level=env.int("SENTRY_EVENT_LEVEL", default=logging.ERROR)
            ),
            DjangoIntegration(),
            CeleryIntegration(monitor_beat_tasks=True),
            RedisIntegration(),
        ],
    )
    ignore_logger("django.security.DisallowedHost")

# SMS API KEYS
SNS_ACCESS_KEY = env("SNS_ACCESS_KEY", default="")
SNS_SECRET_KEY = env("SNS_SECRET_KEY", default="")
SNS_REGION = env("SNS_REGION", default="ap-south-1")
SNS_ROLE_BASED_MODE = env.bool("SNS_ROLE_BASED_MODE", default=False)

# open id connect
JWKS = JsonWebKey.import_key_set(
    json.loads(
        base64.b64decode(env("JWKS_BASE64", default=get_jwks_from_file(BASE_DIR)))  # noqa F405
    )
)
