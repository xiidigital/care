"""
The delivery invariants, asserted by the application's own test suite.

Two kinds of test, and both are needed.

The first asserts that this repository satisfies every invariant. That is the
regression gate: a workflow edited to publish from a pull request, to build
during promotion, or to name the fork's staging project in the common build path
fails here rather than in review.

The second asserts that each check would actually fail if the property were
violated, against a temporary tree built for the purpose. Without it a check
whose pattern silently stopped matching would keep reporting success, which is
the failure mode that makes a green suite worthless.

Skipped, with a reason, where the repository sources are not present. The
production image carries the application and not the delivery configuration
(docker/prod.Dockerfile.dockerignore), so the suite running inside a production
image legitimately cannot check this.
"""

import shutil
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from care.utils.delivery import invariants

WORKFLOWS_PRESENT = invariants.WORKFLOW_DIR.is_dir()

SKIP_REASON = (
    "delivery configuration is not present in this tree "
    f"({invariants.WORKFLOW_DIR}); the production image excludes it by design"
)


@unittest.skipUnless(WORKFLOWS_PRESENT, SKIP_REASON)
class DeliveryInvariantsTests(SimpleTestCase):
    """This repository satisfies every ES-08 delivery invariant."""

    def test_all_invariants_hold(self):
        findings = invariants.run_all()
        self.assertEqual(
            findings,
            [],
            "delivery invariants violated:\n"
            + "\n".join(f"  {finding}" for finding in findings),
        )

    def test_every_check_is_reported_individually(self):
        # A failing suite should say which invariant broke, so each check is
        # asserted on its own as well as in aggregate.
        for check in invariants.CHECKS:
            with self.subTest(check=check.__name__):
                self.assertEqual(check(), [], check.__doc__)


class InvariantDetectionTests(SimpleTestCase):
    """
    Each check fails when its property is violated.

    The tree is built rather than copied so a test states exactly the condition
    it is about.
    """

    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "docker").mkdir()
        self.addCleanup(self._temporary.cleanup)

        self._patches = [
            mock.patch.object(invariants, "REPO_ROOT", self.root),
            mock.patch.object(
                invariants, "WORKFLOW_DIR", self.root / ".github" / "workflows"
            ),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def write_workflow(self, name, content):
        path = self.root / ".github" / "workflows" / name
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_missing_permissions_is_detected(self):
        self.write_workflow(
            "ci.yml",
            """
            name: CI
            on:
              pull_request:
            jobs:
              build:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - uses: actions/checkout@v5
            """,
        )
        findings = invariants.check_permissions_declared()
        self.assertTrue(any(finding.where == "ci.yml" for finding in findings))

    def test_pull_request_target_is_detected(self):
        self.write_workflow(
            "ci.yml",
            """
            name: CI
            on:
              pull_request_target:
            permissions:
              contents: read
            jobs: {}
            """,
        )
        self.assertTrue(invariants.check_no_pull_request_target())

    def test_floating_action_reference_is_detected(self):
        self.write_workflow(
            "ci.yml",
            """
            name: CI
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - uses: some/action@main
            """,
        )
        findings = invariants.check_action_pinning()
        self.assertTrue(
            any("follows a branch" in finding.detail for finding in findings)
        )

    def test_unpinned_privileged_action_is_detected(self):
        self.write_workflow(
            "build-image.yml",
            """
            name: Build
            permissions:
              contents: read
            jobs:
              publish:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - uses: google-github-actions/auth@v3
            """,
        )
        findings = invariants.check_action_pinning()
        self.assertTrue(
            any("commit sha" in finding.detail for finding in findings),
            findings,
        )

    def test_hardcoded_deployment_instance_is_detected(self):
        self.write_workflow(
            "build-image.yml",
            """
            name: Build
            permissions:
              contents: read
            jobs:
              publish:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - run: gcloud run services update care-staging-api
            """,
        )
        findings = invariants.check_common_build_has_no_deployment_instance()
        self.assertTrue(findings, "a named staging service should be rejected")

    def test_build_step_in_promotion_is_detected(self):
        self.write_workflow(
            "promote-production.yml",
            """
            name: Promote
            on:
              workflow_dispatch:
            permissions:
              contents: read
            jobs:
              production:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - run: docker build -f docker/prod.Dockerfile .
            """,
        )
        findings = invariants.check_deployment_workflows_do_not_build()
        self.assertTrue(
            findings, "a build in the promotion workflow should be rejected"
        )

    def test_promotion_triggered_by_push_is_detected(self):
        self.write_workflow(
            "promote-production.yml",
            """
            name: Promote
            on:
              push:
                branches: [gcp]
            permissions:
              contents: read
            jobs:
              production:
                uses: ./.github/workflows/deploy-app.yml
                with:
                  environment: production
            """,
        )
        findings = invariants.check_production_is_gated()
        self.assertTrue(
            any("manual" in finding.rule for finding in findings),
            findings,
        )

    def test_cancellable_deployment_is_detected(self):
        self.write_workflow(
            "deploy-app.yml",
            """
            name: Deploy
            on:
              workflow_call:
            permissions:
              contents: read
            concurrency:
              group: care-deploy-${{ inputs.environment }}
              cancel-in-progress: true
            jobs: {}
            """,
        )
        findings = invariants.check_deployment_concurrency()
        self.assertTrue(
            any("cancellation" in finding.rule for finding in findings),
            findings,
        )

    def test_secret_payload_read_is_detected(self):
        self.write_workflow(
            "deploy-app.yml",
            """
            name: Deploy
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-24.04
                timeout-minutes: 10
                steps:
                  - run: gcloud secrets versions access latest --secret=care-database-url
            """,
        )
        self.assertTrue(invariants.check_no_secret_payload_reads())

    def test_missing_job_timeout_is_detected(self):
        self.write_workflow(
            "ci.yml",
            """
            name: CI
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-24.04
                steps:
                  - uses: actions/checkout@v5
            """,
        )
        findings = invariants.check_job_timeouts()
        self.assertTrue(any(finding.rule == "bounded job" for finding in findings))

    def test_denylist_build_context_is_detected(self):
        # Not an allowlist: the first rule is not `*`, so every `!` below it is
        # meaningless and the builder's working tree enters the image.
        (self.root / "docker" / "prod.Dockerfile.dockerignore").write_text(
            ".git\n!care\n", encoding="utf-8"
        )
        findings = invariants.check_production_build_context()
        self.assertTrue(
            any("first rule must be '*'" in finding.detail for finding in findings),
            findings,
        )

    def test_allowlisted_path_that_does_not_exist_is_detected(self):
        (self.root / "docker" / "prod.Dockerfile.dockerignore").write_text(
            "*\n!manage.py\n!care\n!config\n!scripts\n!locale\n!Pipfile\n!Pipfile.lock\n",
            encoding="utf-8",
        )
        findings = invariants.check_production_build_context()
        self.assertTrue(
            any("does not exist" in finding.detail for finding in findings),
            findings,
        )

    def test_missing_upstream_base_is_detected(self):
        findings = invariants.check_upstream_base_recorded()
        self.assertTrue(any("missing" in finding.detail for finding in findings))

    def test_upstream_base_with_short_commit_is_detected(self):
        (self.root / "UPSTREAM_BASE").write_text(
            "repository=https://example.invalid/care\n"
            "branch=develop\n"
            "commit=deadbeef\n"
            "synchronized_at=2026-01-01T00:00:00Z\n",
            encoding="utf-8",
        )
        findings = invariants.check_upstream_base_recorded()
        self.assertTrue(
            any("not a full sha" in finding.detail for finding in findings),
            findings,
        )

    def test_real_allowlist_passes_against_a_complete_tree(self):
        # The positive control for the two negative tests above: the
        # repository's own allowlist, against a tree that has what it names.
        source = (
            Path(__file__).resolve().parents[3]
            / "docker"
            / "prod.Dockerfile.dockerignore"
        )
        if not source.is_file():
            self.skipTest(SKIP_REASON)
        shutil.copy(source, self.root / "docker" / "prod.Dockerfile.dockerignore")
        for entry in (
            "manage.py",
            "Pipfile",
            "Pipfile.lock",
            "install_plugins.py",
            "plug_config.py",
        ):
            (self.root / entry).write_text("", encoding="utf-8")
        for entry in ("care", "config", "scripts", "locale", "data", "plugs"):
            (self.root / entry).mkdir(exist_ok=True)
        # The allowlist re-includes this one path inside an otherwise excluded
        # directory, so the tree has to have it for the check to be satisfied.
        (self.root / "care" / "media").mkdir(exist_ok=True)
        (self.root / "care" / "media" / ".gitkeep").write_text("", encoding="utf-8")
        self.assertEqual(invariants.check_production_build_context(), [])

    def test_non_executable_invoked_script_is_detected(self):
        # The defect the first real GitHub Actions run found: a script the
        # workflow runs directly, recorded 0644 because it was written on a
        # filesystem with no permission bits. Nothing local notices; the runner
        # answers "Permission denied".
        self.write_workflow(
            "deploy-app.yml",
            """
            name: Deploy application (reusable)
            on:
              workflow_call:
            jobs:
              deploy:
                runs-on: ubuntu-24.04
                timeout-minutes: 60
                steps:
                  - run: infrastructure/scripts/gcp/deploy.sh --digest sha256:abc
            """,
        )
        with mock.patch.object(
            invariants,
            "_index_modes",
            return_value={"infrastructure/scripts/gcp/deploy.sh": "100644"},
        ):
            findings = invariants.check_invoked_scripts_are_executable()
        self.assertTrue(
            any("mode 100644" in finding.detail for finding in findings),
            findings,
        )

        with mock.patch.object(
            invariants,
            "_index_modes",
            return_value={"infrastructure/scripts/gcp/deploy.sh": "100755"},
        ):
            self.assertEqual(invariants.check_invoked_scripts_are_executable(), [])

    def test_untracked_invoked_script_is_detected(self):
        self.write_workflow(
            "ci.yml",
            """
            name: CARE CI
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-24.04
                timeout-minutes: 15
                steps:
                  - run: |
                      infrastructure/scripts/verify-image.sh --image care:ci
            """,
        )
        with mock.patch.object(invariants, "_index_modes", return_value={}):
            findings = invariants.check_invoked_scripts_are_executable()
        self.assertTrue(
            any("is not tracked" in finding.detail for finding in findings),
            findings,
        )
