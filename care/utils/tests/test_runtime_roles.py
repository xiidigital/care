"""
Runtime roles: what each process accepts, routes, probes and refuses.

ADR-0006 makes one claim these tests exist to keep honest -- application
behaviour follows the process's *responsibility*, never the platform it runs on.
So every assertion here is about a role or a backend name, and none of them
mentions a cloud provider. The `test_no_deployment_profile_coupling` section
enforces the negative directly: no deployment-profile switch may appear in the
settings or runtime modules.

Route isolation is asserted by resolution against a rebuilt URLconf rather than
by requesting a URL, because ``config.urls`` composes itself at import time from
the role. Rebuilding it is the only way a single test process can observe what a
differently-configured process would serve; the helper always restores the
URLconf the running settings actually describe.
"""

import ast
import importlib
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve

from config.caches import (
    DUMMY_CACHE_BACKEND,
    LOCMEM_CACHE_BACKEND,
    POSTGRES_CACHE_BACKEND,
    REDIS_CACHE_BACKEND,
)
from config.health import build_health_checks
from config.runtime import (
    API_ROLE,
    DEFAULT_PROCESS_ROLE,
    INIT_ROLE,
    SCHEDULER_ROLE,
    SUPPORTED_PROCESS_ROLES,
    TASK_WORKER_ROLE,
    current_process_role,
    is_api_process,
    is_init_process,
    is_scheduler,
    is_task_worker,
    log_runtime_summary,
    runtime_summary,
    validate_process_role,
)
from config.storage import SUPPORTED_STORAGE_BACKENDS
from config.tasks import CELERY_BACKEND, CLOUD_TASKS_BACKEND

S3_STORAGE_BACKEND, GCS_STORAGE_BACKEND = SUPPORTED_STORAGE_BACKENDS

REPO_ROOT = Path(settings.BASE_DIR)
SCRIPTS = REPO_ROOT / "scripts"

WORKER_URL = "/internal/tasks/execute/"
PUBLIC_URLS = ("/api/v1/auth/login/", "/api/v1/users/", "/api/v1/files/", "/admin/")
DIAGNOSTIC_URLS = ("/", "/ping/", "/health/")


@contextmanager
def urlconf_for(role, **overrides):
    """
    The URLconf a process running ``role`` would serve.

    ``CARE_TASK_HANDLER_ENDPOINT_ENABLED`` is defaulted the way settings default
    it, so a caller naming only the role gets that role's real route surface
    rather than this process's flag carried over.
    """
    overrides.setdefault("CARE_TASK_HANDLER_ENDPOINT_ENABLED", role == TASK_WORKER_ROLE)
    import config.urls

    try:
        with override_settings(CARE_PROCESS_ROLE=role, **overrides):
            clear_url_caches()
            yield importlib.reload(config.urls)
    finally:
        clear_url_caches()
        importlib.reload(config.urls)


def resolves(urlconf, path):
    try:
        resolve(path, urlconf=urlconf)
    except Resolver404:
        return False
    return True


def script(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def shell_commands(name):
    """A shell script with its comments removed -- what it actually executes.

    These scripts explain in prose what they deliberately do *not* do, and name
    the platforms they must not be coupled to in order to say so. Assertions
    about behaviour therefore read the commands, not the commentary.
    """
    return "\n".join(
        line for line in script(name).splitlines() if not line.lstrip().startswith("#")
    )


def python_code(relative):
    """A Python module with comments and docstrings removed.

    Same reason as :func:`shell_commands`: ``config/runtime.py`` documents the
    settings ADR-0006 forbids, and documenting them must not be mistaken for
    consulting them.
    """
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class ProcessRoleValidationTests(SimpleTestCase):
    """Section 40. Every supported role, and clear failure for anything else."""

    def test_every_documented_role_is_accepted(self):
        self.assertEqual(
            SUPPORTED_PROCESS_ROLES,
            (API_ROLE, TASK_WORKER_ROLE, SCHEDULER_ROLE, INIT_ROLE),
        )
        for role in SUPPORTED_PROCESS_ROLES:
            self.assertEqual(validate_process_role(role), role)

    def test_an_unknown_role_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_process_role("worker")

    def test_an_unknown_role_is_not_quietly_treated_as_the_api(self):
        # The accident this exists to prevent: a misspelled worker role serving
        # the public application instead of failing the deployment.
        with self.assertRaises(ImproperlyConfigured):
            validate_process_role("task_wroker")

    def test_the_failure_names_every_valid_role(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            validate_process_role("cloud_run")
        message = str(caught.exception)
        for role in SUPPORTED_PROCESS_ROLES:
            self.assertIn(role, message)

    def test_a_provider_named_role_is_rejected(self):
        for role in ("gcp_api", "cloud_api", "serverless"):
            with self.assertRaises(ImproperlyConfigured):
                validate_process_role(role)

    def test_the_roles_removed_in_es_06_are_no_longer_accepted(self):
        # ES-03 shipped `job` and `celery_worker`. ADR-0006 renamed the first to
        # `init` and folded the second into `task_worker`, which is a role about
        # responsibility rather than about transport. Accepting the old names
        # would leave two vocabularies describing the same four processes.
        for role in ("job", "celery_worker"):
            with self.assertRaises(ImproperlyConfigured):
                validate_process_role(role)

    def test_the_default_is_the_api(self):
        self.assertEqual(DEFAULT_PROCESS_ROLE, API_ROLE)
        self.assertEqual(settings.CARE_PROCESS_ROLE, API_ROLE)


class RuntimeRoleApiTests(SimpleTestCase):
    """Section 11. One place to ask the question, four predicates."""

    def test_the_predicates_agree_with_the_active_role(self):
        for role, predicate in (
            (API_ROLE, is_api_process),
            (TASK_WORKER_ROLE, is_task_worker),
            (SCHEDULER_ROLE, is_scheduler),
            (INIT_ROLE, is_init_process),
        ):
            with override_settings(CARE_PROCESS_ROLE=role):
                self.assertEqual(current_process_role(), role)
                self.assertTrue(predicate())

    def test_exactly_one_predicate_is_true_for_any_role(self):
        predicates = (is_api_process, is_task_worker, is_scheduler, is_init_process)
        for role in SUPPORTED_PROCESS_ROLES:
            with override_settings(CARE_PROCESS_ROLE=role):
                self.assertEqual(sum(p() for p in predicates), 1)


class ApiRouteTests(SimpleTestCase):
    """Section 41. The API serves the application and not the worker."""

    def test_the_public_api_resolves(self):
        with urlconf_for(API_ROLE) as urlconf:
            for path in PUBLIC_URLS:
                self.assertTrue(resolves(urlconf, path), path)

    def test_the_file_routes_resolve(self):
        with urlconf_for(API_ROLE) as urlconf:
            self.assertTrue(resolves(urlconf, "/api/v1/files/"))

    def test_the_diagnostic_routes_resolve(self):
        with urlconf_for(API_ROLE) as urlconf:
            for path in DIAGNOSTIC_URLS:
                self.assertTrue(resolves(urlconf, path), path)

    def test_the_internal_task_route_does_not_resolve(self):
        # Section 24, mandatory. Isolation is by absence: the route is not
        # registered, so no authentication mistake can make it reachable.
        with urlconf_for(API_ROLE) as urlconf:
            self.assertFalse(resolves(urlconf, WORKER_URL))


class WorkerRouteTests(SimpleTestCase):
    """Section 42. The worker serves the task endpoint and nothing public."""

    def test_the_internal_task_route_resolves(self):
        with urlconf_for(TASK_WORKER_ROLE) as urlconf:
            self.assertTrue(resolves(urlconf, WORKER_URL))

    def test_no_public_api_route_resolves(self):
        with urlconf_for(TASK_WORKER_ROLE) as urlconf:
            for path in PUBLIC_URLS:
                self.assertFalse(resolves(urlconf, path), path)

    def test_the_shared_diagnostics_resolve(self):
        # Intentionally shared, and this is the exact shared scope: liveness,
        # dependency diagnostics, build version, and the `home` route every
        # error template reverses.
        with urlconf_for(TASK_WORKER_ROLE) as urlconf:
            for path in (*DIAGNOSTIC_URLS, "/app_version/"):
                self.assertTrue(resolves(urlconf, path), path)

    def test_the_schema_routes_do_not_resolve(self):
        with urlconf_for(TASK_WORKER_ROLE) as urlconf:
            for path in ("/api/schema/", "/swagger/", "/redoc/"):
                self.assertFalse(resolves(urlconf, path), path)

    def test_the_endpoint_can_still_be_switched_off_on_a_worker(self):
        with urlconf_for(
            TASK_WORKER_ROLE, CARE_TASK_HANDLER_ENDPOINT_ENABLED=False
        ) as urlconf:
            self.assertFalse(resolves(urlconf, WORKER_URL))


class NonServingRoleRouteTests(SimpleTestCase):
    """The scheduler and init roles serve no HTTP, and route accordingly."""

    def test_they_expose_no_public_api(self):
        for role in (SCHEDULER_ROLE, INIT_ROLE):
            with urlconf_for(role) as urlconf:
                for path in PUBLIC_URLS:
                    self.assertFalse(resolves(urlconf, path), f"{role} {path}")

    def test_they_expose_no_worker_route(self):
        for role in (SCHEDULER_ROLE, INIT_ROLE):
            with urlconf_for(role) as urlconf:
                self.assertFalse(resolves(urlconf, WORKER_URL), role)


class HealthIsolationTests(SimpleTestCase):
    """
    Section 49. A process is never judged on another role's dependency.
    """

    def slugs(self, **kwargs):
        kwargs.setdefault("cache_backend", REDIS_CACHE_BACKEND)
        kwargs.setdefault("task_backend", CELERY_BACKEND)
        kwargs.setdefault("broker_url", "redis://localhost:6379")
        return [check.slug for check in build_health_checks(**kwargs)]

    def test_the_api_probes_its_database_and_cache(self):
        slugs = self.slugs(role=API_ROLE)
        self.assertIn("main_database", slugs)
        self.assertIn("main_cache", slugs)

    def test_a_cloud_tasks_api_does_not_probe_a_broker_it_never_uses(self):
        # The ES-03 finding, resolved: under Cloud Tasks there is no Celery
        # queue, and a probe of one would report the API permanently unhealthy.
        slugs = self.slugs(
            role=API_ROLE,
            task_backend=CLOUD_TASKS_BACKEND,
            cache_backend=POSTGRES_CACHE_BACKEND,
        )
        self.assertNotIn("celery_queue_length", slugs)

    def test_an_api_with_no_redis_capability_selected_probes_no_redis(self):
        # Section 49: with a PostgreSQL cache and an HTTP task transport,
        # nothing the API health endpoint checks touches Redis, so Redis being
        # down cannot make the API report unhealthy.
        checks = build_health_checks(
            role=API_ROLE,
            cache_backend=POSTGRES_CACHE_BACKEND,
            task_backend=CLOUD_TASKS_BACKEND,
            broker_url="redis://localhost:6379",
        )
        self.assertEqual(
            [check.slug for check in checks], ["main_database", "main_cache"]
        )

    def test_a_celery_api_still_probes_the_queue(self):
        self.assertIn("celery_queue_length", self.slugs(role=API_ROLE))

    def test_the_worker_probes_its_registry_and_not_the_public_api(self):
        slugs = self.slugs(role=TASK_WORKER_ROLE, task_backend=CLOUD_TASKS_BACKEND)
        self.assertIn("main_database", slugs)
        self.assertIn("task_registry", slugs)
        self.assertNotIn("celery_queue_length", slugs)

    def test_the_scheduler_reports_no_cache_dependency(self):
        # Deciding when work runs needs neither cache nor storage.
        slugs = self.slugs(role=SCHEDULER_ROLE)
        self.assertNotIn("main_cache", slugs)
        self.assertIn("celery_queue_length", slugs)

    def test_init_has_no_http_health(self):
        # Section 32. Its health is its exit status.
        for task_backend in (CELERY_BACKEND, CLOUD_TASKS_BACKEND):
            self.assertEqual(self.slugs(role=INIT_ROLE, task_backend=task_backend), [])

    def test_the_registry_probe_reports_the_registered_tasks(self):
        checks = build_health_checks(
            role=TASK_WORKER_ROLE,
            cache_backend=LOCMEM_CACHE_BACKEND,
            task_backend=CLOUD_TASKS_BACKEND,
            broker_url="",
        )
        registry_check = next(c for c in checks if c.slug == "task_registry")
        status, meta = registry_check.check()
        self.assertEqual(status, 200)
        self.assertGreater(meta["registered_tasks"], 0)


class BackendOrthogonalityTests(SimpleTestCase):
    """
    Section 47. A role never constrains a backend, and no combination is
    rejected for failing to match a deployment platform.
    """

    COMBINATIONS = (
        (API_ROLE, CELERY_BACKEND, S3_STORAGE_BACKEND, REDIS_CACHE_BACKEND),
        (API_ROLE, CLOUD_TASKS_BACKEND, GCS_STORAGE_BACKEND, POSTGRES_CACHE_BACKEND),
        (
            TASK_WORKER_ROLE,
            CLOUD_TASKS_BACKEND,
            GCS_STORAGE_BACKEND,
            POSTGRES_CACHE_BACKEND,
        ),
        # Deliberately unusual, and deliberately valid: a Celery worker storing
        # to Google Cloud Storage is a composition, not a contradiction.
        (TASK_WORKER_ROLE, CELERY_BACKEND, GCS_STORAGE_BACKEND, LOCMEM_CACHE_BACKEND),
        (SCHEDULER_ROLE, CELERY_BACKEND, S3_STORAGE_BACKEND, DUMMY_CACHE_BACKEND),
        (INIT_ROLE, CLOUD_TASKS_BACKEND, S3_STORAGE_BACKEND, POSTGRES_CACHE_BACKEND),
    )

    def test_no_combination_is_rejected(self):
        for role, task_backend, storage, cache in self.COMBINATIONS:
            with self.subTest(role=role, task=task_backend, storage=storage):
                self.assertEqual(validate_process_role(role), role)
                build_health_checks(
                    role=role,
                    cache_backend=cache,
                    task_backend=task_backend,
                    broker_url="redis://localhost:6379",
                )

    def test_the_role_does_not_appear_in_backend_selection(self):
        # Section 26: no `if role == ... then force backend`. Reading the
        # settings module is the check, because that is where such a rule would
        # have to live for it to take effect.
        source = (REPO_ROOT / "config" / "settings" / "base.py").read_text(
            encoding="utf-8"
        )
        for backend_setting in (
            "CARE_TASK_BACKEND",
            "CARE_STORAGE_BACKEND",
            "CARE_CACHE_BACKEND",
        ):
            assignment = f"{backend_setting} = validate"
            self.assertIn(assignment, source)
            index = source.index(assignment)
            statement = source[index : source.index("\n\n", index)]
            self.assertNotIn("CARE_PROCESS_ROLE", statement)


class NoDeploymentProfileTests(SimpleTestCase):
    """
    Section 48. There is no application-level deployment-profile concept, and
    nothing consults one.
    """

    FORBIDDEN = (
        "CARE_RUNTIME_PROFILE",
        "RUNTIME_PROFILE",
        "IS_GCP",
        "USE_GCP",
        "USE_CLOUD_RUN",
        "CLOUD_MODE",
    )

    #: Where such a switch could actually take effect. Documentation is not
    #: scanned: ADR-0006 names these variables in order to reject them.
    RUNTIME_MODULES = (
        "config/settings/base.py",
        "config/settings/local.py",
        "config/settings/production.py",
        "config/settings/staging.py",
        "config/settings/deployment.py",
        "config/settings/test.py",
        "config/runtime.py",
        "config/urls.py",
        "config/health.py",
        "config/tasks.py",
        "config/caches.py",
        "config/storage.py",
    )

    def test_no_runtime_module_consults_a_deployment_profile(self):
        for relative in self.RUNTIME_MODULES:
            code = python_code(relative)
            for name in self.FORBIDDEN:
                self.assertNotIn(name, code, f"{relative} references {name}")

    def test_settings_expose_no_deployment_profile(self):
        for name in self.FORBIDDEN:
            self.assertFalse(hasattr(settings, name), name)

    def test_the_process_role_is_the_only_runtime_selector(self):
        # A role is a responsibility. If a platform name were ever accepted as
        # one, this is where it would show.
        for name in ("local", "traditional", "cloud", "gcp", "docker", "kubernetes"):
            with self.assertRaises(ImproperlyConfigured):
                validate_process_role(name)


class InitializationSeparationTests(SimpleTestCase):
    """
    Section 45. No long-running process performs deployment initialization.
    Asserted against the scripts themselves, not against documentation.
    """

    INITIALIZATION_COMMANDS = (
        "migrate",
        "createcachetable",
        "sync_permissions_roles",
        "sync_valueset",
        "initialize.sh",
    )

    LONG_RUNNING_SCRIPTS = (
        "start.sh",
        "start-dev.sh",
        "start-worker.sh",
        "celery_worker.sh",
        "celery-dev.sh",
        "celery_beat.sh",
        "celery_beat-dev.sh",
    )

    def test_no_long_running_entrypoint_initializes(self):
        for name in self.LONG_RUNNING_SCRIPTS:
            commands = shell_commands(name)
            for command in self.INITIALIZATION_COMMANDS:
                self.assertNotIn(command, commands, f"{name} runs {command}")

    def test_the_scheduler_no_longer_owns_initialization(self):
        # The coupling ADR-0006 names explicitly: migrations were a side effect
        # of starting Celery Beat, so a deployment without beat never migrated.
        for name in ("celery_beat.sh", "celery_beat-dev.sh"):
            self.assertNotIn("initialize.sh", shell_commands(name))

    def test_initialization_runs_the_documented_sequence(self):
        source = script("initialize.sh")
        for command in (
            "migrate --noinput",
            "createcachetable",
            "compilemessages",
            "sync_permissions_roles",
            "sync_valueset",
        ):
            self.assertIn(command, source)

    def test_initialization_stops_at_the_first_failure(self):
        # Section 17: fail on first failed step, non-zero exit. `set -e` is what
        # makes a failed migration abort instead of proceeding to seed data.
        self.assertIn("set -eo pipefail", script("initialize.sh"))

    def test_initialization_starts_no_server_worker_or_scheduler(self):
        source = script("initialize.sh")
        for forbidden in ("gunicorn", "runserver", "celery"):
            self.assertNotIn(forbidden, source)


class RoleEntrypointTests(SimpleTestCase):
    """
    Sections 18, 20 and 21. Every role has one entrypoint, and the difference
    between roles is command and environment -- never a different image.
    """

    ENTRYPOINTS = {
        "start.sh": API_ROLE,
        "start-dev.sh": API_ROLE,
        "start-worker.sh": TASK_WORKER_ROLE,
        "celery_worker.sh": TASK_WORKER_ROLE,
        "celery-dev.sh": TASK_WORKER_ROLE,
        "celery_beat.sh": SCHEDULER_ROLE,
        "celery_beat-dev.sh": SCHEDULER_ROLE,
        "initialize.sh": INIT_ROLE,
    }

    def test_every_entrypoint_declares_its_role(self):
        for name, role in self.ENTRYPOINTS.items():
            self.assertIn(
                f'export CARE_PROCESS_ROLE="${{CARE_PROCESS_ROLE:-{role}}}"',
                script(name),
                name,
            )

    def test_every_role_has_an_entrypoint(self):
        self.assertEqual(set(self.ENTRYPOINTS.values()), set(SUPPORTED_PROCESS_ROLES))

    def test_no_entrypoint_runs_a_provider_specific_command(self):
        for name in self.ENTRYPOINTS:
            commands = shell_commands(name).lower()
            for provider in ("gcloud", "kubectl", "aws ", "az "):
                self.assertNotIn(provider, commands, f"{name} runs {provider}")

    def test_the_local_composition_assigns_one_role_per_service(self):
        compose = (REPO_ROOT / "docker-compose.local.yaml").read_text(encoding="utf-8")
        for role in SUPPORTED_PROCESS_ROLES:
            self.assertIn(f"CARE_PROCESS_ROLE: {role}", compose)

    def test_every_local_service_runs_the_same_image(self):
        # Section 20: one built image, four roles.
        compose = (REPO_ROOT / "docker-compose.local.yaml").read_text(encoding="utf-8")
        images = {
            line.split(":", 1)[1].strip()
            for line in compose.splitlines()
            if line.strip().startswith("image:")
        }
        self.assertEqual(images, {"care_local"})


class RedisIsNotUniversalTests(SimpleTestCase):
    """
    ADR-0006: Redis is capability-specific, not a CARE runtime requirement --
    and the startup wait must say so, or a Cloud Run instance blocks forever on
    a Redis that a PostgreSQL-cache, Cloud Tasks deployment does not have.
    """

    def test_the_startup_wait_is_conditional_on_the_selected_backends(self):
        source = script("wait_for_redis.sh")
        self.assertIn("CARE_CACHE_BACKEND", source)
        self.assertIn("CARE_TASK_BACKEND", source)

    def test_rate_limiting_remains_a_declared_redis_capability(self):
        # Not hidden: the alias still requires Redis, and the login and
        # password-reset paths depend on it. It does not gate process startup.
        from config.caches import RATELIMIT_CACHE_ALIAS

        self.assertIn(RATELIMIT_CACHE_ALIAS, settings.CACHES)
        self.assertIn("redis", settings.CACHES[RATELIMIT_CACHE_ALIAS]["BACKEND"])

    def test_recent_views_is_no_longer_a_redis_capability(self):
        # RF1: recent views is a PostgreSQL model. No alias, no Redis.
        self.assertNotIn("recent_views", settings.CACHES)


class StartupLoggingTests(SimpleTestCase):
    """Section 33. The role is visible, the credentials are not."""

    def test_the_summary_names_the_role_and_the_selected_backends(self):
        summary = runtime_summary()
        self.assertEqual(
            set(summary),
            {
                "process_role",
                "storage_backend",
                "task_backend",
                "cache_backend",
                # RF2. The backend name alone would not tell a reader whether
                # the limits are actually enforced under load, so the guarantee
                # is stated next to it.
                "rate_limit_backend",
                "rate_limit_semantics",
            },
        )
        self.assertEqual(summary["process_role"], settings.CARE_PROCESS_ROLE)
        self.assertEqual(
            summary["rate_limit_backend"], settings.CARE_RATE_LIMIT_BACKEND
        )

    def test_the_summary_carries_no_credential_or_connection_string(self):
        values = " ".join(runtime_summary().values())
        for secret in (settings.REDIS_URL, str(settings.DATABASES["default"])):
            self.assertNotIn(secret, values)
        for scheme in ("://", "password", "secret", "token"):
            self.assertNotIn(scheme, values.lower())

    def test_it_is_logged_once_per_process(self):
        import config.runtime

        config.runtime._summary_logged = False  # noqa: SLF001
        try:
            with self.assertLogs("care.runtime", level="INFO") as logs:
                log_runtime_summary()
                log_runtime_summary()
            self.assertEqual(len(logs.output), 1)
            self.assertIn(f"process_role={settings.CARE_PROCESS_ROLE}", logs.output[0])
        finally:
            config.runtime._summary_logged = True  # noqa: SLF001
