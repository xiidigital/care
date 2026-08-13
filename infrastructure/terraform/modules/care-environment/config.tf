# The application environment, assembled once and split by role.
#
# Every name here is one the application actually reads. Where the
# configuration reference documents a variable that no code reads —
# CARE_ENVIRONMENT is the notable one — it is omitted rather than set, so that
# nothing in this file implies an effect it does not have (ES-07 section 5).

locals {
  # Cloud Run hostnames end in .run.app. Django reads a leading dot as a
  # subdomain wildcard, which is what lets a service be reachable at its
  # generated URL without that URL being knowable before the service exists.
  # Custom domains are added through the variable.
  #
  # 127.0.0.1 is required, not incidental. Cloud Run's startup and liveness
  # probes connect to the container directly and send `Host: 127.0.0.1`, so
  # without it Django's CommonMiddleware raises DisallowedHost and answers the
  # probe with 400 — the container serves correctly and is killed anyway.
  #
  # It grants nothing externally. The only requests carrying that Host header
  # are the platform's own loopback probes; an internet request arrives through
  # the Cloud Run front end, which sets the real hostname.
  allowed_hosts = concat(var.django_allowed_hosts, [".run.app", "127.0.0.1"])

  csrf_trusted_origins = concat(var.csrf_trusted_origins, ["https://*.run.app"])

  # Shared by api, worker and init. Backend selections and infrastructure
  # facts; no credentials.
  common_env = merge(
    {
      DJANGO_SETTINGS_MODULE = "config.settings.deployment"

      DJANGO_DEBUG               = var.django_debug ? "true" : "false"
      DJANGO_ALLOWED_HOSTS       = jsonencode(local.allowed_hosts)
      CSRF_TRUSTED_ORIGINS       = jsonencode(local.csrf_trusted_origins)
      CORS_ALLOWED_ORIGINS       = jsonencode(var.cors_allowed_origins)
      DJANGO_SECURE_SSL_REDIRECT = var.django_secure_ssl_redirect ? "true" : "false"

      # ---------------------------------------------------------------------
      # The Redis-free composition (ADR-0007, ES-07 section 72).
      #
      # Four independent selections, none of which names Redis, and no Redis
      # variable is set anywhere in this configuration. scripts/wait_for_redis.sh
      # reads the two backend values below and exits without waiting when
      # neither selects Redis, so startup does not block on a service that
      # does not exist.
      # ---------------------------------------------------------------------
      CARE_STORAGE_BACKEND    = "gcs"
      CARE_TASK_BACKEND       = "cloud_tasks"
      CARE_CACHE_BACKEND      = "postgres"
      CARE_RATE_LIMIT_BACKEND = "postgres"

      # Distinct tables, enforced by the application as well: config/caches.py
      # refuses a configuration where both DatabaseCache aliases name the same
      # one. Set explicitly so the separation is visible here too.
      CARE_CACHE_TABLE      = "care_cache"
      CARE_RATE_LIMIT_TABLE = "care_ratelimit_cache"

      GCP_PROJECT_ID = var.project_id
      GCS_PROJECT_ID = var.project_id

      CARE_PATIENT_STORAGE_BUCKET  = local.patient_bucket
      CARE_FACILITY_STORAGE_BUCKET = local.facility_bucket
      CARE_REPORT_STORAGE_BUCKET   = local.report_bucket

      CONN_MAX_AGE       = tostring(var.conn_max_age)
      SENTRY_ENVIRONMENT = var.environment

      # Payloads reference clinical data. Never true in a deployed environment.
      CARE_TASK_LOG_PAYLOAD = "false"

      # Identifies the running build at /app_version/, which is how the
      # same-image requirement is checked from outside (ES-07 section 101).
      APP_VERSION = var.image
    },
    var.current_domain != "" ? { CURRENT_DOMAIN = var.current_domain } : {},

    # Set only when chosen. Empty leaves the application default rather than
    # asserting one here — but see the variable: the default is SMTP to
    # localhost, which no Cloud Run container answers.
    var.django_email_backend != "" ? { DJANGO_EMAIL_BACKEND = var.django_email_backend } : {},

    var.extra_env,
  )

  # Cloud Tasks configuration. Required by every role that selects the
  # cloud_tasks backend, because settings validate the backend rather than the
  # role — so the worker carries these too even though it enqueues nothing.
  cloud_tasks_env_common = {
    GCP_TASKS_LOCATION        = var.region
    GCP_TASKS_QUEUE           = google_cloud_tasks_queue.default.name
    GCP_TASKS_SERVICE_ACCOUNT = google_service_account.tasks_invoker.email
  }

  # For the roles that actually dispatch, read straight off the created service.
  # These are the values Cloud Tasks uses, so they are the ones that must be
  # exactly right, and reading them back is what guarantees it.
  #
  # Cloud Run validates an OIDC audience against the service URL, so the
  # audience is the worker's origin rather than the task path.
  cloud_tasks_env_dispatcher = merge(local.cloud_tasks_env_common, {
    GCP_WORKER_URL          = "${module.worker.uri}/internal/tasks/execute/"
    GCP_TASKS_OIDC_AUDIENCE = module.worker.uri
  })

  # For the worker's own environment, which cannot reference the worker. See the
  # long note in main.tf: this satisfies validation and nothing dispatches with
  # it.
  cloud_tasks_env_self = merge(local.cloud_tasks_env_common, {
    GCP_WORKER_URL          = "${local.worker_self_url}/internal/tasks/execute/"
    GCP_TASKS_OIDC_AUDIENCE = local.worker_self_url
  })

  # scripts/wait_for_db.sh runs before gunicorn in start.sh and start-worker.sh
  # and connects with these rather than with DATABASE_URL. The host is the
  # Cloud SQL socket directory; libpq treats a leading slash as a unix socket.
  #
  # The init role does not run wait_for_db.sh, so it receives none of these and
  # no database password.
  postgres_env = {
    POSTGRES_HOST = local.cloudsql_socket_dir
    POSTGRES_PORT = "5432"
    POSTGRES_USER = var.database_user
    POSTGRES_DB   = var.database_name
  }

  api_env = merge(
    local.common_env,
    local.cloud_tasks_env_dispatcher,
    local.postgres_env,
    {
      GUNICORN_WORKERS = tostring(var.api_gunicorn_workers)
    },
  )

  worker_env = merge(
    local.common_env,
    local.cloud_tasks_env_self,
    local.postgres_env,
    {
      GUNICORN_WORKERS = tostring(var.worker_gunicorn_workers)

      # Redundant with the role default in config/settings/base.py, and set
      # anyway: this is the one flag that decides whether the private task route
      # is registered, and it should not be inferred when reading the worker's
      # own configuration.
      CARE_TASK_HANDLER_ENDPOINT_ENABLED = "true"
    },
  )

  init_env = merge(
    local.common_env,
    local.cloud_tasks_env_dispatcher,
  )

  # The fixture Job (dev only). Three deliberate departures from init_env, each
  # forced by how `load_fixtures` works rather than chosen: it builds its data
  # by calling CARE's own viewsets through DRF's APIClient, in-process, so that
  # every side effect a real request has also happens here.
  #
  # That in-process client is what the three overrides are for:
  #
  #   DJANGO_DEBUG               care/fixtures/context.py refuses to run at all
  #                              unless settings.DEBUG, a guard against seeding
  #                              synthetic patients into a real deployment.
  #   DJANGO_ALLOWED_HOSTS       APIClient sends Host: testserver; without it
  #                              CommonMiddleware raises DisallowedHost.
  #   DJANGO_SECURE_SSL_REDIRECT APIClient requests are HTTP, so a redirect to
  #                              HTTPS would turn every fixture call into a 301.
  #
  # None of them reaches a serving role. This map is consumed by one Job that
  # is created only when var.enable_fixture_loader is true, which the module
  # permits only in dev.
  fixture_env = merge(
    local.init_env,
    {
      DJANGO_DEBUG               = "true"
      DJANGO_SECURE_SSL_REDIRECT = "false"
      DJANGO_ALLOWED_HOSTS       = jsonencode(concat(local.allowed_hosts, ["testserver"]))
    },
  )
}
