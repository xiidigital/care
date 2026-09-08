"""
Base settings to build other settings files upon.
"""

import logging
import warnings
from datetime import timedelta
from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

from config.caches import (
    DEFAULT_CACHE_KEY_PREFIX,
    DEFAULT_CACHE_TABLE,
    DEFAULT_CACHE_TIMEOUT,
    DEFAULT_RATELIMIT_CACHE_TABLE,
    DEFAULT_RATELIMIT_CACHE_TIMEOUT,
    DEFAULT_RATELIMIT_KEY_PREFIX,
    DEFAULT_RATELIMIT_MAX_ENTRIES,
    POSTGRES_RATE_LIMIT_BACKEND,
    RATELIMIT_CACHE_ALIAS,
    REDIS_CACHE_BACKEND,
    REDIS_RATE_LIMIT_BACKEND,
    build_default_cache,
    build_ratelimit_caches,
    ratelimit_installed_apps,
    ratelimit_silenced_checks,
    validate_cache_backend,
    validate_cache_table_isolation,
    validate_rate_limit_backend,
)
from config.db_routers import RATELIMIT_DB_ALIAS
from config.firebase_auth import (
    DEFAULT_SMS_COUNTRY_CODES,
    validate_firebase_auth_settings,
)
from config.health import build_health_checks
from config.login_methods import validate_login_methods
from config.oidc import load_oidc_providers, validate_oidc_providers
from config.runtime import (
    API_ROLE,
    DEFAULT_PROCESS_ROLE,
    TASK_WORKER_ROLE,
    validate_process_role,
)
from config.storage import (
    AWS_ROLE_BASED_BUCKET_PROVIDER,
    build_object_storage,
    validate_storage_backend,
)
from config.tasks import (
    CLOUD_TASKS_BACKEND,
    validate_cloud_tasks_settings,
    validate_task_backend,
)
from plug_config import manager

from .config import *  # noqa F403

warnings.filterwarnings("ignore", category=UserWarning)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
APPS_DIR = BASE_DIR / "care"
env = environ.Env()


if READ_DOT_ENV_FILE := env.bool("DJANGO_READ_DOT_ENV_FILE", default=False):
    # OS environment variables take precedence over variables from .env
    env.read_env(str(BASE_DIR / ".env"))

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="eXZQzOzx8gV38rDG0Z0fFZWweUGl3LwMZ9aTKqJiXQTI0nKMh0Z7sbHfqT8KFEnd",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = env.json("DJANGO_ALLOWED_HOSTS", default=["*"])
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = env.bool("DJANGO_DEBUG", False)
# Local time zone. Choices are
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# though not all of them may be available with every OS.
# In Windows, this must be set to your system time zone.
TIME_ZONE = "Asia/Kolkata"
# https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = "en-us"
# https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1
# https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = True
# https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True
# https://docs.djangoproject.com/en/dev/ref/settings/#locale-paths
LOCALE_PATHS = [str(BASE_DIR / "locale")]

LANGUAGES = [
    ("en-us", _("English")),
    ("ml", _("Malayalam")),
    ("hi", _("Hindi")),
    ("ta", _("Tamil")),
]
# DATABASES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#databases
DATABASES = {"default": env.db("DATABASE_URL", default="postgres:///care")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=0)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Retained as compatibility metadata for lock consumers that expose a timeout
# parameter. PostgreSQL transaction-scoped advisory locks do not use a lease;
# their lifetime is the enclosing transaction (ADR-0005 / ES-05).
LOCK_TIMEOUT = env.int("LOCK_TIMEOUT", default=32)

# Read by Celery and Redis-specific features. Kept under its historical name so
# the local Docker Compose profile keeps working unchanged (ES-04 section 24).
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379")

# CACHES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#caches
#
# ADR-0004: the cache backend is a configuration choice. `redis` remains the
# default so an unconfigured checkout behaves exactly as it did upstream; the
# GCP profile selects `postgres` and needs no Redis for ordinary caching.
CARE_CACHE_BACKEND = validate_cache_backend(
    env("CARE_CACHE_BACKEND", default=REDIS_CACHE_BACKEND).strip().lower()
)

# Table name for the PostgreSQL cache. Created by an explicit
# `createcachetable` step, never on startup -- see scripts/initialize.sh.
CARE_CACHE_TABLE = env("CARE_CACHE_TABLE", default=DEFAULT_CACHE_TABLE)

# Namespace for every generic cache key. Provider names, hosts, buckets and
# project ids must not appear here (ES-04 section 11).
CARE_CACHE_KEY_PREFIX = env("CARE_CACHE_KEY_PREFIX", default=DEFAULT_CACHE_KEY_PREFIX)

# Default entry lifetime only. Call sites with their own lifetime pass an
# explicit TTL and are unaffected (ES-04 section 12).
CARE_CACHE_TIMEOUT = env.int("CARE_CACHE_TIMEOUT", default=DEFAULT_CACHE_TIMEOUT)

# Redis cache URL, provider-neutral. Falls back to the legacy REDIS_URL.
REDIS_CACHE_URL = env("REDIS_CACHE_URL", default="")

# RATE LIMIT STORE
# ------------------------------------------------------------------------------
# RF2: where rate-limit counters live, chosen independently of CARE_CACHE_BACKEND
# and never inferred from it. `redis` remains the default so an existing
# deployment keeps the strict semantics it already had; the Redis-free
# managed-cloud profile selects `postgres` and accepts best-effort counting.
# See config/caches.build_ratelimit_cache for what each mode guarantees.
CARE_RATE_LIMIT_BACKEND = validate_rate_limit_backend(
    env("CARE_RATE_LIMIT_BACKEND", default=REDIS_RATE_LIMIT_BACKEND).strip().lower()
)

# Redis URL for rate-limit counters, configurable independently of the cache
# (07-configuration-reference.md §28.1). Falls back to the legacy REDIS_URL.
# Read only when CARE_RATE_LIMIT_BACKEND=redis.
REDIS_RATE_LIMIT_URL = env("REDIS_RATE_LIMIT_URL", default="")
REDIS_RATE_LIMIT_PREFIX = env(
    "REDIS_RATE_LIMIT_PREFIX", default=DEFAULT_RATELIMIT_KEY_PREFIX
)

# Read only when CARE_RATE_LIMIT_BACKEND=postgres. Its own table -- never
# CARE_CACHE_TABLE -- created by the same `createcachetable` step.
CARE_RATE_LIMIT_TABLE = env(
    "CARE_RATE_LIMIT_TABLE", default=DEFAULT_RATELIMIT_CACHE_TABLE
)
CARE_RATE_LIMIT_CACHE_TIMEOUT = env.int(
    "CARE_RATE_LIMIT_CACHE_TIMEOUT", default=DEFAULT_RATELIMIT_CACHE_TIMEOUT
)
CARE_RATE_LIMIT_MAX_ENTRIES = env.int(
    "CARE_RATE_LIMIT_MAX_ENTRIES", default=DEFAULT_RATELIMIT_MAX_ENTRIES
)

# Both tables are DatabaseCache LOCATIONs when both selections name PostgreSQL,
# and a shared name would silently merge two responsibilities: the router below
# tells the caches apart by table name alone, so `default` cache traffic would
# start riding the rate-limit connection. Refused at import rather than
# discovered later. See config/caches.validate_cache_table_isolation.
validate_cache_table_isolation(
    CARE_CACHE_BACKEND,
    CARE_RATE_LIMIT_BACKEND,
    cache_table=CARE_CACHE_TABLE,
    rate_limit_table=CARE_RATE_LIMIT_TABLE,
)

# The PostgreSQL counter needs a connection that is not the request's.
#
# ATOMIC_REQUESTS is on, and DRF's exception handler calls set_rollback() for
# every APIException it converts into a response. A failed login raises
# AuthenticationFailed, so the request transaction -- and the counter increment
# inside it -- is rolled back. The limiter would then count successful requests
# and forget failed ones, which is the opposite of what it is for.
#
# Same database, separate connection, ATOMIC_REQUESTS off, so counter writes
# commit on their own terms. Only the rate-limit table is routed here; see
# config/db_routers.py for why the ordinary cache is left alone.
if CARE_RATE_LIMIT_BACKEND == POSTGRES_RATE_LIMIT_BACKEND:
    DATABASES[RATELIMIT_DB_ALIAS] = {
        **DATABASES["default"],
        "ATOMIC_REQUESTS": False,
    }
    DATABASE_ROUTERS = ["config.db_routers.RateLimitCacheRouter"]

CACHES = {
    "default": build_default_cache(
        CARE_CACHE_BACKEND,
        redis_url=REDIS_CACHE_URL,
        legacy_redis_url=REDIS_URL,
        table=CARE_CACHE_TABLE,
        key_prefix=CARE_CACHE_KEY_PREFIX,
        timeout=CARE_CACHE_TIMEOUT,
    ),
    # Not cache, and not selected by CARE_CACHE_BACKEND. Contributes nothing at
    # all under CARE_RATE_LIMIT_BACKEND=disabled; see config/caches.py.
    **build_ratelimit_caches(
        CARE_RATE_LIMIT_BACKEND,
        redis_url=REDIS_RATE_LIMIT_URL,
        legacy_redis_url=REDIS_URL,
        key_prefix=REDIS_RATE_LIMIT_PREFIX,
        table=CARE_RATE_LIMIT_TABLE,
        timeout=CARE_RATE_LIMIT_CACHE_TIMEOUT,
        max_entries=CARE_RATE_LIMIT_MAX_ENTRIES,
    ),
    "swagger_cache": {  # In-memory cache (only for Swagger)
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "swagger-schema-cache",
    },
}

# URLS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "config.urls"
# https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "config.wsgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "django.forms",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "django_filters",
    "cities_light",
    # Present unless CARE_RATE_LIMIT_BACKEND=disabled, where there is no
    # rate-limit cache alias for its system check to validate. See
    # config/caches.ratelimit_installed_apps.
    *ratelimit_installed_apps(CARE_RATE_LIMIT_BACKEND),
    "corsheaders",
    "djangoql",
    "maintenance_mode",
    "django.contrib.postgres",
    "healthy_django",
    "import_export",
]

# Import only the countries where the deployment operates by default. Expand
# this comma-separated setting before importing another country's localities.
CITIES_LIGHT_INCLUDE_COUNTRIES = env.list(
    "CITIES_LIGHT_INCLUDE_COUNTRIES", default=["MX"]
)

# Keep the model in step with the schema the package's own migrations created.
#
# django-cities-light declares `search_names` with `db_index=INDEX_SEARCH_NAMES`,
# and that setting auto-detects to False on PostgreSQL — while the package ships
# migration 0013 with `db_index=True`, which is what actually built
# `cities_light_city_search_names_fb77fed2` in every database that ran it. The
# result on a PostgreSQL project that leaves this unset is a permanent pending
# migration inside site-packages, which no repository can author.
#
# True is the accurate description of the deployed schema, and it is also the
# behaviour CARE wants: the search-name lookup is indexed.
CITIES_LIGHT_INDEX_SEARCH_NAMES = True

LOCAL_APPS = [
    "care.security",
    "care.facility",
    "care.users",
    "care.audit_log",
    "care.emr",
]

PLUGIN_APPS = manager.get_apps()

# Plugin Section

PLUGIN_CONFIGS = manager.get_config()

# https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS + PLUGIN_APPS

# MIGRATIONS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#migration-modules
MIGRATION_MODULES = {"sites": "care.contrib.sites.migrations"}

# AUTHENTICATION
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#authentication-backends
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

# https://docs.djangoproject.com/en/dev/ref/settings/#auth-user-model
AUTH_USER_MODEL = "users.User"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-redirect-url
LOGIN_REDIRECT_URL = "/"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-url
LOGIN_URL = "/"
# https://docs.djangoproject.com/en/dev/ref/settings/#logout-redirect-url
LOGOUT_REDIRECT_URL = "/"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = [
    # https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.common.BrokenLinkEmailsMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "maintenance_mode.middleware.MaintenanceModeMiddleware",
    "care.audit_log.middleware.AuditLogMiddleware",
]

# add RequestTimeLoggingMiddleware based on the environment variable
if env.bool("ENABLE_REQUEST_TIME_LOGGING", default=False):
    MIDDLEWARE.insert(0, "config.middlewares.RequestTimeLoggingMiddleware")

# STATIC
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#static-files
# https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(BASE_DIR / "staticfiles")
# https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/staticfiles/"
# https://docs.djangoproject.com/en/dev/ref/settings/#staticfiles-dirs
STATICFILES_DIRS = [str(APPS_DIR / "static")]

# https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = str(APPS_DIR / "media")
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/mediafiles/"

# https://docs.djangoproject.com/en/dev/ref/settings/#std-setting-STORAGES
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }
}

# https://whitenoise.readthedocs.io/en/latest/django.html#WHITENOISE_MANIFEST_STRICT
WHITENOISE_MANIFEST_STRICT = False

# TEMPLATES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # https://docs.djangoproject.com/en/dev/ref/settings/#template-dirs
        "DIRS": [str(APPS_DIR / "templates")],
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-loaders
            # https://docs.djangoproject.com/en/dev/ref/templates/api/#loader-types
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# https://docs.djangoproject.com/en/dev/ref/settings/#form-renderer
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# FIXTURES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#fixture-dirs
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-httponly
SESSION_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-httponly
CSRF_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#x-frame-options
X_FRAME_OPTIONS = "DENY"
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-trusted-origins
CSRF_TRUSTED_ORIGINS = env.json("CSRF_TRUSTED_ORIGINS", default=[])

# https://github.com/adamchainz/django-cors-headers#cors_allowed_origin_regexes-sequencestr--patternstr
# CORS_URLS_REGEX = r"^/api/.*$"

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-timeout
EMAIL_TIMEOUT = 5
# https://docs.djangoproject.com/en/dev/ref/settings/#default-from-email
DEFAULT_FROM_EMAIL = env(
    "EMAIL_FROM", default="Open Healthcare Network <ops@care.ohc.network>"
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_PASSWORD", default="")
# Both are read as booleans rather than as raw strings. `env(...)` returns the
# string when the variable is present, and every non-empty string is truthy —
# so `EMAIL_USE_TLS=false` used to enable TLS.
#
# Django rejects a configuration that sets both; nothing here needs to restate
# that. Which one an operator selects is a property of the relay they chose, so
# neither is given a value here beyond the off default that keeps a
# non-delivering environment from asserting a transport it does not use.
# https://docs.djangoproject.com/en/dev/ref/settings/#email-use-tls
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-use-ssl
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-subject-prefix
EMAIL_SUBJECT_PREFIX = env("DJANGO_EMAIL_SUBJECT_PREFIX", default="[Care]")

# ADMIN
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#server-email
# SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
# https://docs.djangoproject.com/en/dev/ref/settings/#admins
# ADMINS = [("""👪""", "admin@ohc.network")]
# https://docs.djangoproject.com/en/dev/ref/settings/#managers
# MANAGERS = ADMINS

# Django Admin URL.
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin")

# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s"
        },
        "request_time": {
            "format": "INFO %(asctime)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "time_logging": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "request_time",
        },
    },
    "loggers": {
        "time_logging_middleware": {
            "handlers": ["time_logging"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

# Django Rest Framework
# ------------------------------------------------------------------------------
# https://www.django-rest-framework.org/api-guide/settings/
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # "rest_framework.authentication.BasicAuthentication",
        # Primary api authentication
        # "rest_framework_simplejwt.authentication.JWTAuthentication",
        "config.authentication.CustomJWTAuthentication",
        "config.authentication.CustomBasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "care.security.utils.permission_class.CareAuthentication",
    ],
    "DEFAULT_PAGINATION_CLASS": "care.utils.pagination.care_pagination.CareLimitOffsetPagination",
    "PAGE_SIZE": 14,
    "SEARCH_PARAM": "search_text",
    "DEFAULT_SCHEMA_CLASS": "care.utils.swagger.schema.AutoSchema",
    "EXCEPTION_HANDLER": "config.exception_handler.exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# drf-spectacular (schema generation)
# ------------------------------------------------------------------------------
# https://drf-spectacular.readthedocs.io/en/latest/settings.html
SPECTACULAR_SETTINGS = {
    "TITLE": "Care API",
    "DESCRIPTION": "Documentation of API endpoints of Care ",
    "VERSION": "1.0.0",
    "DISABLE_ERRORS_AND_WARNINGS": True,
}

# Simple JWT (JWT Authentication)
# ------------------------------------------------------------------------------
# https://django-rest-framework-simplejwt.readthedocs.io/en/latest/settings.html
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env("JWT_ACCESS_TOKEN_LIFETIME", default=10)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        minutes=env("JWT_REFRESH_TOKEN_LIFETIME", default=30)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "USER_ID_FIELD": "external_id",
}

# Celery (background tasks)
# ------------------------------------------------------------------------------
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-timezone
if USE_TZ:
    # https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-timezone
    CELERY_TIMEZONE = TIME_ZONE
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-broker_url
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-result_backend
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-accept_content
CELERY_ACCEPT_CONTENT = ["json"]
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-task_serializer
CELERY_TASK_SERIALIZER = "json"
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#std:setting-result_serializer
CELERY_RESULT_SERIALIZER = "json"
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#task-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_TIME_LIMIT = 1800 * 5
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#task-soft-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_SOFT_TIME_LIMIT = 1800

# Portable asynchronous execution (ADR-0003)
# ------------------------------------------------------------------------------
# The transport carrying asynchronous work is a configuration choice. Celery
# remains the default so a local checkout, Docker Compose and every traditional
# deployment keep behaving exactly as before.
CARE_TASK_BACKEND = validate_task_backend(
    env("CARE_TASK_BACKEND", default="celery").strip().lower()
)

# Runtime role (ADR-0006)
# ------------------------------------------------------------------------------
# What this process is responsible for -- not where it runs. Selects routing,
# startup, health and configuration validation; never clinical behaviour. It is
# orthogonal to every backend variable above and below: any role may be combined
# with any supported storage, task and cache backend.
CARE_PROCESS_ROLE = validate_process_role(
    env("CARE_PROCESS_ROLE", default=DEFAULT_PROCESS_ROLE).strip().lower()
)

# Optional external authentication adapters (ADR-0010, ADR-0011)
# ------------------------------------------------------------------------------
# Flags are available to every role because the deployment environment is
# assembled once, but only the API serves authentication routes or needs the
# provider configuration and secrets.
FIREBASE_AUTH_ENABLED = env.bool("FIREBASE_AUTH_ENABLED", default=False)
FIREBASE_AUTH_PROJECT_ID = env("FIREBASE_AUTH_PROJECT_ID", default="")
# ADR-0010 §6 restricts the first SMS rollout to Mexico. Widening it is an
# operator decision expressed here, not a code change.
FIREBASE_AUTH_SMS_COUNTRY_CODES = env.list(
    "FIREBASE_AUTH_SMS_COUNTRY_CODES", default=list(DEFAULT_SMS_COUNTRY_CODES)
)
validate_firebase_auth_settings(
    enabled=FIREBASE_AUTH_ENABLED and CARE_PROCESS_ROLE == API_ROLE,
    project_id=FIREBASE_AUTH_PROJECT_ID,
    sms_country_codes=FIREBASE_AUTH_SMS_COUNTRY_CODES,
)

# Standard OIDC providers (ADR-0011)
# ------------------------------------------------------------------------------
# CARE speaks OpenID Connect, not a vendor. A provider is a configuration
# record; zero of them is the default and a fully supported production state,
# in which no OIDC route is mounted and no issuer is ever contacted.
#
# The set arrives as JSON, inline for Compose and Kubernetes or as a file for a
# secret-manager volume -- one source or the other, never both. Client secrets
# live in whichever secret mechanism the deployment already uses; CARE reads a
# value and never a vault.
OIDC_PROVIDERS_FILE = env("OIDC_PROVIDERS_FILE", default="")
OIDC_PROVIDERS = load_oidc_providers(
    raw=env("OIDC_PROVIDERS", default=""),
    path=OIDC_PROVIDERS_FILE,
)

# The public CARE origin. Callback URLs are derived from it and matched
# byte-for-byte at the exchange, so it is required once any provider is enabled.
OIDC_PUBLIC_BASE_URL = env("OIDC_PUBLIC_BASE_URL", default="")

# Discovery and JWKS are cached through CARE's configured cache backend, which
# may be locmem, PostgreSQL or Redis. None of them becomes a requirement here.
OIDC_DISCOVERY_CACHE_SECONDS = env.int("OIDC_DISCOVERY_CACHE_SECONDS", default=3600)
OIDC_JWKS_CACHE_SECONDS = env.int("OIDC_JWKS_CACHE_SECONDS", default=3600)

validate_oidc_providers(
    OIDC_PROVIDERS,
    public_base_url=OIDC_PUBLIC_BASE_URL,
    enforce=CARE_PROCESS_ROLE == API_ROLE,
)

# CARE's own phone OTP (ADR-0011 §6)
# ------------------------------------------------------------------------------
# On by default, and removed only by an explicit operator decision once its
# replacement is proven -- never as a side effect of enabling another provider.
# For a clinic with no identity provider it is the only method that works.
CARE_PATIENT_OTP_ENABLED = env.bool("CARE_PATIENT_OTP_ENABLED", default=True)

# Only an installation that actually enrolled subjects under ADR-0010 needs
# these: the old `keycloak_subject` column stored a subject and no issuer, and
# the migration into ADR-0011's triple refuses to invent one. No known
# environment enrolled any, so the migration is expected to move zero rows.
OIDC_LEGACY_PROVIDER_ID = env("OIDC_LEGACY_PROVIDER_ID", default="keycloak")
OIDC_LEGACY_ISSUER = env("OIDC_LEGACY_ISSUER", default="")

# The one login configuration CARE refuses: a deployment where no patient can
# get in. Cheap to catch here, expensive to discover from an empty login screen.
validate_login_methods(
    enforce=CARE_PROCESS_ROLE == API_ROLE,
    patient_otp_enabled=CARE_PATIENT_OTP_ENABLED,
    firebase_enabled=FIREBASE_AUTH_ENABLED,
    providers=OIDC_PROVIDERS,
)

# The private task-execution route is served only by the worker role, so the
# public API never exposes it. Set explicitly to override.
CARE_TASK_HANDLER_ENDPOINT_ENABLED = env.bool(
    "CARE_TASK_HANDLER_ENDPOINT_ENABLED", default=CARE_PROCESS_ROLE == TASK_WORKER_ROLE
)

# Task payloads carry identifiers, not records. The ceiling is a guard against
# a caller quietly enqueueing a whole clinical object.
CARE_TASK_MAX_PAYLOAD_BYTES = env.int("CARE_TASK_MAX_PAYLOAD_BYTES", default=10 * 1024)

# Payload logging is prohibited in production: payloads reference clinical data.
CARE_TASK_LOG_PAYLOAD = env.bool("CARE_TASK_LOG_PAYLOAD", default=False)

# Cloud Tasks. Required only when that backend is selected; a Celery deployment
# needs none of these and must not be made to supply them.
GCP_PROJECT_ID = env("GCP_PROJECT_ID", default="")
GCP_TASKS_PROJECT_ID = env("GCP_TASKS_PROJECT_ID", default=GCP_PROJECT_ID)
GCP_TASKS_LOCATION = env("GCP_TASKS_LOCATION", default="")
GCP_TASKS_QUEUE = env("GCP_TASKS_QUEUE", default="")
GCP_WORKER_URL = env("GCP_WORKER_URL", default="")
GCP_TASKS_SERVICE_ACCOUNT = env("GCP_TASKS_SERVICE_ACCOUNT", default="")
# Usually the worker service origin; it must match what the worker's IAM policy
# expects. Defaults to the worker URL so a correct deployment needs one value.
GCP_TASKS_OIDC_AUDIENCE = env("GCP_TASKS_OIDC_AUDIENCE", default=GCP_WORKER_URL)

if CARE_TASK_BACKEND == CLOUD_TASKS_BACKEND:
    validate_cloud_tasks_settings(
        {
            "GCP_TASKS_PROJECT_ID": GCP_TASKS_PROJECT_ID,
            "GCP_TASKS_LOCATION": GCP_TASKS_LOCATION,
            "GCP_TASKS_QUEUE": GCP_TASKS_QUEUE,
            "GCP_WORKER_URL": GCP_WORKER_URL,
            "GCP_TASKS_SERVICE_ACCOUNT": GCP_TASKS_SERVICE_ACCOUNT,
            "GCP_TASKS_OIDC_AUDIENCE": GCP_TASKS_OIDC_AUDIENCE,
        }
    )

# Maintenance Mode
# ------------------------------------------------------------------------------
# https://github.com/fabiocaccamo/django-maintenance-mode/tree/main#configuration-optional
MAINTENANCE_MODE = int(env("MAINTENANCE_MODE", default="0"))

#  Password Reset
# ------------------------------------------------------------------------------
# https://github.com/anexia-it/django-rest-passwordreset#configuration--settings
DJANGO_REST_PASSWORDRESET_NO_INFORMATION_LEAKAGE = True
DJANGO_REST_MULTITOKENAUTH_RESET_TOKEN_EXPIRY_TIME = 24
# https://github.com/anexia-it/django-rest-passwordreset#custom-email-lookup
DJANGO_REST_LOOKUP_FIELD = "username"

# Health Django (Health Check Config)
# ------------------------------------------------------------------------------
# https://github.com/vigneshhari/healthy_django
#
# Composed from the runtime role and the selected backends rather than fixed:
# a process must not be reported unhealthy for a dependency belonging to a role
# it does not carry, or to a backend it did not select. See config/health.py.
HEALTHY_DJANGO = build_health_checks(
    role=CARE_PROCESS_ROLE,
    cache_backend=CARE_CACHE_BACKEND,
    task_backend=CARE_TASK_BACKEND,
    broker_url=REDIS_URL,
)

# Audit logs
# ------------------------------------------------------------------------------
AUDIT_LOG_ENABLED = env.bool("AUDIT_LOG_ENABLED", default=False)
AUDIT_LOG = {
    "globals": {
        "exclude": {
            "applications": [
                "plain:contenttypes",
                "plain:admin",
                "plain:basehttp",
                "glob:session*",
                "glob:auth*",
                "plain:migrations",
                "plain:audit_log",
            ]
        }
    },
    "models": {
        "exclude": {
            "applications": [],
            "models": ["plain:facility.HistoricalPatientRegistration"],
            "fields": {
                "facility.PatientRegistration": [
                    "name",
                    "phone_number",
                    "emergency_phone_number",
                    "address",
                ],
                "facility.PatientExternalTest": ["name", "address", "mobile_number"],
            },
        }
    },
}

# OTP
# ------------------------------------------------------------------------------
OTP_REPEAT_WINDOW = 6  # OTPs will only be valid for 6 hours to login
OTP_MAX_REPEATS_WINDOW = 10  # times OTPs can be sent within OTP_REPEAT_WINDOW
OTP_LENGTH = 5

# Rate Limiting
# ------------------------------------------------------------------------------
# django_ratelimit reads its counters from this alias instead of `default`, so
# the ADR-0004 cache choice no longer decides whether management commands run
# (unresolved-items.md L1). Which technology backs the alias is RF2's
# CARE_RATE_LIMIT_BACKEND; under `disabled` the alias is absent and so is the
# app, which is why nothing here consults it.
RATELIMIT_USE_CACHE = RATELIMIT_CACHE_ALIAS

# Left explicit rather than relying on the library default. When the rate-limit
# store cannot be reached the count is unknown, and an unknown count must not
# read as "under the limit" on the login and password-reset paths. False makes
# django_ratelimit report should_limit, which sends CARE's wrapper to captcha
# validation (07-configuration-reference.md §26.4). It is the Redis half of the
# policy; the PostgreSQL half is in config/ratelimit.py, because a DatabaseCache
# failure raises rather than returning None.
RATELIMIT_FAIL_OPEN = False

# Exactly one check, in exactly one mode: django_ratelimit.E003 under
# CARE_RATE_LIMIT_BACKEND=postgres, where CARE knowingly accepts a non-atomic
# increment in exchange for a Redis-free deployment. Empty in every other mode.
# W001 is deliberately left to fire so the weaker guarantee stays visible to
# whoever runs `manage.py check`. See config/caches.ratelimit_silenced_checks.
SILENCED_SYSTEM_CHECKS = ratelimit_silenced_checks(CARE_RATE_LIMIT_BACKEND)

# The pre-existing kill switch, kept unchanged for backward compatibility. It
# short-circuits the wrapper the same way CARE_RATE_LIMIT_BACKEND=disabled does,
# but it is a per-environment override rather than a deployment's declared
# backend -- local and test settings set it, and continue to.
DISABLE_RATELIMIT = env.bool("DISABLE_RATELIMIT", default=False)
DJANGO_RATE_LIMIT = env("RATE_LIMIT", default="5/10m")
GOOGLE_RECAPTCHA_SECRET_KEY = env("GOOGLE_RECAPTCHA_SECRET_KEY", default="")
GOOGLE_RECAPTCHA_SITE_KEY = env("GOOGLE_RECAPTCHA_SITE_KEY", default="")
GOOGLE_CAPTCHA_POST_KEY = "g-recaptcha-response"

# SMS
# ------------------------------------------------------------------------------
USE_SMS = False

# Cloud and Buckets
# ------------------------------------------------------------------------------


# Credential source. AWS_ROLE_BASED omits the key/secret so that boto3 resolves
# instance-role credentials itself. Every other value supplies them explicitly.
BUCKET_PROVIDER = env("BUCKET_PROVIDER", default="aws").upper()
BUCKET_REGION = env("BUCKET_REGION", default="ap-south-1")
BUCKET_KEY = env("BUCKET_KEY", default="")
BUCKET_SECRET = env("BUCKET_SECRET", default="")
BUCKET_ENDPOINT = env("BUCKET_ENDPOINT", default="")

FILE_UPLOAD_BUCKET = env("FILE_UPLOAD_BUCKET", default="")
FILE_UPLOAD_REGION = env("FILE_UPLOAD_REGION", default=BUCKET_REGION)
FILE_UPLOAD_KEY = env("FILE_UPLOAD_KEY", default=BUCKET_KEY)
FILE_UPLOAD_SECRET = env("FILE_UPLOAD_SECRET", default=BUCKET_SECRET)
FILE_UPLOAD_BUCKET_ENDPOINT = env(
    "FILE_UPLOAD_BUCKET_ENDPOINT", default=BUCKET_ENDPOINT
)

ALLOWED_MIME_TYPES = set(
    env.list(
        "ALLOWED_MIME_TYPES",
        default=[
            # Images
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/bmp",
            "image/webp",
            "image/svg+xml",
            # Videos
            "video/mp4",
            "video/mpeg",
            "video/x-msvideo",
            "video/quicktime",
            "video/x-ms-wmv",
            "video/x-flv",
            "video/webm",
            # Audio
            "audio/mpeg",
            "audio/wav",
            "audio/aac",
            "audio/ogg",
            "audio/midi",
            "audio/x-midi",
            "audio/webm",
            "audio/mp4",
            # Documents
            "text/plain",
            "text/csv",
            "application/rtf",
            "application/msword",
            "application/vnd.oasis.opendocument.text",
            "application/pdf",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.oasis.opendocument.spreadsheet",
        ],
    )
)

ALLOWED_FILE_EXTENSIONS = set(
    env.list(
        "ALLOWED_FILE_EXTENSIONS",
        default=[
            # Images
            "jpg",
            "jpeg",
            "png",
            "gif",
            "bmp",
            "webp",
            "svg",
            # Videos
            "mp4",
            "mpeg",
            "avi",
            "mov",
            "wmv",
            "flv",
            "webm",
            # Audio
            "mp3",
            "wav",
            "aac",
            "ogg",
            "midi",
            "mid",
            "m4a",
            # Documents
            "txt",
            "csv",
            "rtf",
            "doc",
            "odt",
            "pdf",
            "xls",
            "xlsx",
            "ods",
        ],
    )
)

BLOCKED_FILE_EXTENSIONS = set(
    env.list(
        "BLOCKED_FILE_EXTENSIONS",
        default=[
            # Executable Files
            "exe",
            "dll",
            "msi",
            "msp",
            "mst",
            "com",
            "scr",
            "sys",
            "pif",
            # Registry Files
            "reg",
            # Script Files
            "bat",
            "cmd",
            "wsf",
            "sh",
        ],
    )
)


FACILITY_S3_BUCKET = env("FACILITY_S3_BUCKET", default="")
FACILITY_S3_REGION = env("FACILITY_S3_REGION_CODE", default=BUCKET_REGION)
FACILITY_S3_KEY = env("FACILITY_S3_KEY", default=BUCKET_KEY)
FACILITY_S3_SECRET = env("FACILITY_S3_SECRET", default=BUCKET_SECRET)
FACILITY_S3_BUCKET_ENDPOINT = env(
    "FACILITY_S3_BUCKET_ENDPOINT", default=BUCKET_ENDPOINT
)


# Portable object storage (ADR-0001)
# ------------------------------------------------------------------------------
# Provider selection is configuration-only. Application code addresses the
# logical aliases below and never learns which provider implements them.
CARE_STORAGE_BACKEND = validate_storage_backend(
    env("CARE_STORAGE_BACKEND", default="s3").strip().lower()
)

# Provider-neutral bucket names. Each falls back to the pre-existing setting so
# that no local or deployed configuration has to be rewritten. `report` shares
# the patient bucket, matching the behaviour it replaces, but stays a separate
# alias so it can be pointed elsewhere without touching application code.
CARE_PATIENT_STORAGE_BUCKET = env(
    "CARE_PATIENT_STORAGE_BUCKET", default=FILE_UPLOAD_BUCKET
)
CARE_FACILITY_STORAGE_BUCKET = env(
    "CARE_FACILITY_STORAGE_BUCKET", default=FACILITY_S3_BUCKET
)
CARE_REPORT_STORAGE_BUCKET = env(
    "CARE_REPORT_STORAGE_BUCKET", default=FILE_UPLOAD_BUCKET
)

# Optional; Application Default Credentials are used when unset. Named per
# 07-configuration-reference.md section 12.4.
GCS_PROJECT_ID = env("GCS_PROJECT_ID", default="")

# Role-based AWS credentials: omit key/secret so the SDK resolves the instance
# role itself. The endpoint is suppressed along with them, preserving the
# behaviour of the boto3 client this replaces -- a role-based deployment always
# talked to the default AWS endpoint. A deployment that needs an instance role
# *and* a custom endpoint (a VPC endpoint, or an S3-compatible gateway) is
# therefore not expressible today; splitting the two would be a config change,
# not a refactor, so it is left for whoever first needs it.
_ROLE_BASED_BUCKET = AWS_ROLE_BASED_BUCKET_PROVIDER == BUCKET_PROVIDER

STORAGES = {
    # staticfiles is configured near the top of this file and stays on
    # WhiteNoise; only the object-storage aliases are added here because they
    # depend on the bucket settings defined above.
    **STORAGES,
    "patient": build_object_storage(
        CARE_STORAGE_BACKEND,
        CARE_PATIENT_STORAGE_BUCKET,
        region_name=FILE_UPLOAD_REGION,
        access_key=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_KEY,
        secret_key=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_SECRET,
        endpoint_url=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_BUCKET_ENDPOINT,
        project_id=GCS_PROJECT_ID,
    ),
    "facility": build_object_storage(
        CARE_STORAGE_BACKEND,
        CARE_FACILITY_STORAGE_BUCKET,
        region_name=FACILITY_S3_REGION,
        access_key=None if _ROLE_BASED_BUCKET else FACILITY_S3_KEY,
        secret_key=None if _ROLE_BASED_BUCKET else FACILITY_S3_SECRET,
        endpoint_url=None if _ROLE_BASED_BUCKET else FACILITY_S3_BUCKET_ENDPOINT,
        project_id=GCS_PROJECT_ID,
    ),
    "report": build_object_storage(
        CARE_STORAGE_BACKEND,
        CARE_REPORT_STORAGE_BUCKET,
        region_name=FILE_UPLOAD_REGION,
        access_key=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_KEY,
        secret_key=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_SECRET,
        endpoint_url=None if _ROLE_BASED_BUCKET else FILE_UPLOAD_BUCKET_ENDPOINT,
        project_id=GCS_PROJECT_ID,
    ),
}

# current hosted domain
CURRENT_DOMAIN = env("CURRENT_DOMAIN", default="localhost:4000")
BACKEND_DOMAIN = env("BACKEND_DOMAIN", default="localhost:9000")

APP_VERSION = env("APP_VERSION", default="unknown")

IS_PRODUCTION = False
# Timeout for middleware request (in seconds)
MIDDLEWARE_REQUEST_TIMEOUT = env.int("MIDDLEWARE_REQUEST_TIMEOUT", 20)

SNOWSTORM_DEPLOYMENT_URL = env(
    "SNOWSTORM_DEPLOYMENT_URL", default="http://165.22.211.144/fhir"
)

DJANGO_REST_MULTITOKENAUTH_REQUIRE_USABLE_PASSWORD = False

SMS_BACKEND = "care.utils.sms.backend.sns.SnsBackend"

OTP_SMS_LOGIN_CONTENT = env(
    "OTP_SMS_LOGIN_CONTENT",
    default="Care OTP for login is {otp}. Please do not share this with anyone.",
)

OTP_SMS_RESET_PASSWORD_CONTENT = env(
    "OTP_SMS_RESET_PASSWORD_CONTENT",
    default="Care OTP for password reset is {otp}. Please do not share this with anyone.",
)

USER_CREATE_PASSWORD_EMAIL_TEMPLATE_PATH = env(
    "USER_CREATE_PASSWORD_TEMPLATE_PATH", default="email/user_create_password.html"
)

USER_RESET_PASSWORD_EMAIL_TEMPLATE_PATH = env(
    "USER_RESET_PASSWORD_TEMPLATE_PATH", default="email/user_reset_password.html"
)

TOTP_ENABLED_EMAIL_TEMPLATE_PATH = env(
    "TOTP_ENABLED_EMAIL_TEMPLATE_PATH", default="email/totp_enabled.html"
)

TOTP_DISABLED_EMAIL_TEMPLATE_PATH = env(
    "TOTP_DISABLED_EMAIL_TEMPLATE_PATH", default="email/totp_disabled.html"
)

# Cleanup incomplete file uploads, set to 0 to disable
FILE_UPLOAD_EXPIRY_HOURS = env.int("FILE_UPLOAD_EXPIRY_HOURS", default=24)
