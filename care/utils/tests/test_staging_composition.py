"""
The staging environment's declared composition, asserted against the HCL.

Staging is meant to be production's architecture at a smaller size: what may
differ between the two is quantity — instance size, instance counts, backup
depth — and never composition (ADR-0007 *Environments*). That is a property of
tracked files, so it is checkable without a cloud project, and worth checking
without one: the failure mode is a staging environment that silently stops
resembling the thing it exists to de-risk.

What these tests do **not** do is verify the deployed environment. Whether the
init Job succeeded, whether Cloud Tasks reaches the worker, whether the console
email path arrives in Cloud Logging — those are answered against the real
environment and recorded in
``docs/xii/architecture/inventory/runtime-and-deployment.md``. Here the question
is narrower and prior to it: does the configuration we would apply declare the
composition we claim.

Read as text rather than through an HCL parser. The repository has no HCL
parsing dependency, adding one to assert six values would be a poor trade, and
`tofu validate` already covers syntax — these assertions are about content.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TERRAFORM = Path(settings.BASE_DIR) / "infrastructure/terraform"
MODULE = TERRAFORM / "modules/care-environment"
ENVIRONMENTS = TERRAFORM / "environments"

CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"


def read(path):
    return path.read_text(encoding="utf-8")


def hcl_variables(path):
    """Map every `variable "x" { ... }` in ``path`` to its body."""
    return {
        match.group(1): match.group(2)
        for match in re.finditer(
            r'variable\s+"(\w+)"\s*\{(.*?)\n\}', read(path), re.DOTALL
        )
    }


class RedisFreeCompositionTests(SimpleTestCase):
    """
    The four selections that make the managed profile Redis-free.

    They live in the module, so every environment instantiating it gets the same
    four. That is the point: a staging environment that quietly selected a
    different cache would not be testing what production runs.
    """

    def test_the_four_backend_selections_are_fixed_in_the_module(self):
        config = read(MODULE / "config.tf")
        for name, value in (
            ("CARE_STORAGE_BACKEND", "gcs"),
            ("CARE_TASK_BACKEND", "cloud_tasks"),
            ("CARE_CACHE_BACKEND", "postgres"),
            ("CARE_RATE_LIMIT_BACKEND", "postgres"),
        ):
            with self.subTest(variable=name):
                self.assertRegex(config, rf'{name}\s*=\s*"{value}"')

    def test_no_environment_overrides_a_backend_selection(self):
        # extra_env is merged last and could override any of them. Nothing does,
        # and a root that started to would be changing composition, not size.
        for root in ("dev", "staging", "prod"):
            text = read(ENVIRONMENTS / root / "main.tf")
            for name in (
                "CARE_STORAGE_BACKEND",
                "CARE_TASK_BACKEND",
                "CARE_CACHE_BACKEND",
                "CARE_RATE_LIMIT_BACKEND",
            ):
                with self.subTest(root=root, variable=name):
                    self.assertNotIn(name, text)

    def test_no_redis_resource_is_declared_anywhere(self):
        # `gcloud redis instances list` is the deployed check; this is the one
        # that fails before an apply rather than after it.
        declaration = re.compile(r'resource\s+"google_redis_\w+"')
        for path in TERRAFORM.rglob("*.tf"):
            if ".terraform" in path.parts:
                continue
            with self.subTest(path=str(path.relative_to(TERRAFORM))):
                self.assertIsNone(declaration.search(read(path)))

    def test_no_redis_url_is_placed_in_any_runtime_environment(self):
        config = read(MODULE / "config.tf")
        self.assertNotIn("REDIS_URL", config)
        self.assertNotIn("CELERY_BROKER_URL", config)


class StagingIsIsolatedFromDevTests(SimpleTestCase):
    """
    ADR-0007 requires environment separation to include stateful resources and
    secrets. Every name that matters derives from `var.environment`, so the
    isolation is structural rather than a naming convention someone maintains.
    """

    def test_staging_declares_its_own_environment(self):
        self.assertRegex(
            read(ENVIRONMENTS / "staging" / "main.tf"),
            r'environment\s*=\s*"staging"',
        )

    def test_every_stateful_name_derives_from_the_environment(self):
        main = read(MODULE / "main.tf")
        self.assertRegex(main, r'name_prefix\s*=.*"care-\$\{var\.environment\}"')
        for local_name in ("sql_instance_name", "queue_name"):
            with self.subTest(local=local_name):
                self.assertRegex(
                    main, rf"{local_name}\s*=\s*\"\$\{{local\.name_prefix\}}"
                )

    def test_secret_containers_are_namespaced_per_environment(self):
        secrets = read(MODULE / "secrets.tf")
        # Every secret_id in the required set is prefixed; none is a bare name
        # that two environments could both resolve to.
        required = secrets.partition("required_secrets = {")[2].partition(
            "\n  optional_secrets"
        )[0]
        for match in re.finditer(r"secret_id\s*=\s*\"([^\"]+)\"", required):
            with self.subTest(secret=match.group(1)):
                self.assertTrue(match.group(1).startswith("${local.name_prefix}"))

    def test_staging_state_lives_under_its_own_prefix(self):
        # A cross-environment apply requires editing a tracked file, which is
        # the property that keeps staging state off dev's.
        self.assertRegex(
            read(ENVIRONMENTS / "staging" / "main.tf"),
            r'prefix\s*=\s*"environments/staging"',
        )

    def test_the_fixture_loader_cannot_reach_staging(self):
        # load_fixtures is destructive and seeds accounts whose passwords are
        # published. The module refuses it outside dev; staging does not even
        # expose the variable.
        self.assertNotIn(
            "enable_fixture_loader", read(ENVIRONMENTS / "staging" / "main.tf")
        )


class StagingMirrorsProductionTests(SimpleTestCase):
    """
    Sizing may differ. Protection and composition may not.
    """

    def test_both_protect_stateful_resources(self):
        for root in ("staging", "prod"):
            text = read(ENVIRONMENTS / root / "main.tf")
            with self.subTest(root=root):
                self.assertRegex(text, r"sql_deletion_protection\s*=\s*true")
                self.assertRegex(text, r"sql_terraform_deletion_protection\s*=\s*true")
                self.assertRegex(text, r"bucket_force_destroy\s*=\s*false")
                self.assertRegex(text, r"bucket_versioning\s*=\s*true")

    def test_both_keep_the_worker_private_by_omission(self):
        # There is no knob to make it public: the module's cloud-run-service
        # refuses an allUsers binding for the worker role. Asserted so that a
        # future root cannot introduce one by copying the api flag.
        for root in ("staging", "prod"):
            with self.subTest(root=root):
                self.assertNotIn(
                    "worker_allow_unauthenticated",
                    read(ENVIRONMENTS / root / "main.tf"),
                )

    def test_both_run_the_scheduler(self):
        for root in ("staging", "prod"):
            with self.subTest(root=root):
                self.assertRegex(
                    read(ENVIRONMENTS / root / "main.tf"),
                    r"scheduler_jobs_enabled\s*=\s*true",
                )


class ConsoleEmailIsTheDeclaredDefaultTests(SimpleTestCase):
    """
    Console email is a valid configuration in every environment, and neither
    staging nor production may require SMTP credentials to be applied
    (``unresolved-items.md`` N1).
    """

    def test_staging_and_prod_default_to_the_console_backend(self):
        for root in ("staging", "prod"):
            variables = hcl_variables(ENVIRONMENTS / root / "variables.tf")
            with self.subTest(root=root):
                self.assertIn("django_email_backend", variables)
                self.assertIn(CONSOLE_BACKEND, variables["django_email_backend"])

    def test_no_root_requires_an_email_variable(self):
        # A variable with no default is a required decision: `tofu plan` fails
        # until it is made. No email variable may be one, in any environment.
        for root in ("dev", "staging", "prod"):
            for name, body in hcl_variables(
                ENVIRONMENTS / root / "variables.tf"
            ).items():
                if "email" not in name.lower():
                    continue
                with self.subTest(root=root, variable=name):
                    self.assertIn(
                        "default",
                        body,
                        f"{root} makes {name} a required decision, which turns "
                        f"external email delivery back into a deployment "
                        f"precondition.",
                    )

    def test_every_environment_can_supply_transport_settings_generically(self):
        # The path a future operator takes, present in every root: non-secret
        # settings through extra_env, the password through optional_secrets.
        for root in ("staging", "prod"):
            variables = hcl_variables(ENVIRONMENTS / root / "variables.tf")
            with self.subTest(root=root):
                self.assertIn("extra_env", variables)
                self.assertIn("optional_secrets", variables)

    def test_no_validation_rule_mentions_email(self):
        # An environment guard that rejected console mode, or demanded a relay,
        # would make N1 a blocker again by a different route.
        for path in TERRAFORM.rglob("*.tf"):
            if ".terraform" in path.parts:
                continue
            text = read(path)
            for match in re.finditer(r"validation\s*\{(.*?)\n\s*\}", text, re.DOTALL):
                with self.subTest(path=str(path.relative_to(TERRAFORM))):
                    self.assertNotIn("EMAIL", match.group(1).upper())
