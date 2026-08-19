"""
The properties ADR-0008 requires of this repository's delivery configuration.

Every rule here is one a review would otherwise have to remember. They are the
ones whose violation is silent: a production promotion that grew a build step
still passes its own workflow, a workflow that lost its ``permissions`` block
still runs, an action repinned to ``@main`` still resolves, and a project id
pasted into the common build workflow still works — for the person who pasted
it.

Checked in three places, from one implementation: the CI workflow runs
``python -m care.utils.delivery.invariants``, the application test suite runs
:mod:`care.utils.tests.test_delivery_invariants`, and a developer can run either.

Deliberately not a linter for YAML style. Each check corresponds to a numbered
requirement, and a check nobody can trace to one does not belong here.

Standalone by design: PyYAML and the standard library, no Django. It has to run
in a job that has not built the application.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Repository root. care/utils/delivery/invariants.py -> four levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: The fork's own delivery workflows. The upstream CARE workflows in the same
#: directory are inherited and are not held to these rules: they belong to
#: another deployment topology, and rewriting them is not ES-08's job.
DELIVERY_WORKFLOWS = (
    "ci.yml",
    "build-image.yml",
    "deploy-app.yml",
    "deploy-staging.yml",
    "promote-production.yml",
    "rollback.yml",
    "infra-check.yml",
    "infra-apply.yml",
)

#: The workflow that produces the artifact. ADR-0008 section 19 forbids a
#: deployment instance in it: it builds a release, not a release of one
#: environment.
COMMON_BUILD_WORKFLOWS = ("ci.yml", "build-image.yml")

#: Workflows that must never build. Production consumes an artifact
#: (ADR-0008 section 16); the deployment adapter deploys one (section 5).
NON_BUILDING_WORKFLOWS = (
    "deploy-app.yml",
    "deploy-staging.yml",
    "promote-production.yml",
    "rollback.yml",
)

#: Actions that mint credentials, publish artifacts or decide whether a
#: credential finding blocks a release. A moved tag on one of these changes what
#: does that, so they are pinned to a commit (ES-08 sections 102, 168).
SHA_PINNED_ACTION_PREFIXES = (
    "google-github-actions/",
    "opentofu/setup-opentofu",
    "gitleaks/gitleaks-action",
)

#: Deployment-instance literals. A tag, a URL or an identifier belonging to one
#: environment, appearing in a workflow that is supposed to be
#: environment-neutral.
INSTANCE_LITERAL_PATTERNS = (
    (
        r"care-(dev|staging|prod|production)-(api|worker|init|db|tasks)",
        "a named environment resource",
    ),
    (r"\bproject-[0-9a-f]{8}", "a literal GCP project id"),
    (r"https://[a-z0-9-]+\.(?:[a-z0-9-]+\.)?run\.app", "a literal Cloud Run URL"),
    (
        r"docker\.pkg\.dev/(?!\$)[a-z][a-z0-9-]{4,}/",
        "a literal Artifact Registry project",
    ),
    (r":[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]+\b", "a literal Cloud SQL connection name"),
)

#: Build invocations, in any of the forms this repository could acquire one.
BUILD_PATTERNS = (
    r"docker\s+build",
    r"buildx\s+build",
    r"docker\s+push",
    r"docker/build-push-action",
    r"docker/bake-action",
)

CREDENTIAL_LITERAL_PATTERNS = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r'"private_key"\s*:',
    r"credentials_json\s*:",
    r"service_account\.json",
)


@dataclass(frozen=True)
class Finding:
    """One violated invariant, in a form a log line can carry."""

    where: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.where}: {self.rule} — {self.detail}"


def _workflow_paths() -> list[Path]:
    return [WORKFLOW_DIR / name for name in DELIVERY_WORKFLOWS]


def _load(path: Path) -> dict:
    # yaml.safe_load turns the `on:` key into the boolean True, because YAML 1.1
    # says so. Nothing here reads it as a key, so it is left alone rather than
    # patched: a custom loader would be a second YAML dialect to maintain.
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _jobs(document: dict) -> dict:
    jobs = document.get("jobs") or {}
    return jobs if isinstance(jobs, dict) else {}


def check_workflows_exist() -> list[Finding]:
    """Every delivery workflow this module reasons about is present."""
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            findings.append(
                Finding(
                    where=str(path.relative_to(REPO_ROOT)),
                    rule="workflow present",
                    detail="missing; the delivery architecture names this workflow",
                )
            )
    return findings


def check_permissions_declared() -> list[Finding]:
    """
    Least privilege, declared (ES-08 section 61).

    A workflow with no ``permissions`` block inherits the repository default,
    which may be write. Declaring it is what makes the intent reviewable.
    """
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        document = _load(path)
        name = path.name
        if "permissions" not in document:
            findings.append(
                Finding(
                    where=name,
                    rule="permissions declared",
                    detail="no workflow-level permissions block",
                )
            )
    return findings


def check_no_pull_request_target() -> list[Finding]:
    """
    No ``pull_request_target`` (ES-08 section 62).

    It runs with repository write scope and secrets available, against a ref the
    author controls. This repository is public and forkable.
    """
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        if "pull_request_target" in path.read_text(encoding="utf-8"):
            findings.append(
                Finding(
                    where=path.name,
                    rule="no pull_request_target",
                    detail="a fork's pull request must not reach a privileged context",
                )
            )
    return findings


def _action_references(path: Path) -> list[tuple[int, str]]:
    references = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = re.search(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", line)
        if match:
            references.append((number, match.group(1)))
    return references


def check_action_pinning() -> list[Finding]:
    """
    No floating action references, and commit pins where it matters
    (ES-08 sections 102, 168).
    """
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        for number, reference in _action_references(path):
            if reference.startswith("./"):
                # A workflow in this repository, at this revision.
                continue
            if "@" not in reference:
                findings.append(
                    Finding(
                        where=f"{path.name}:{number}",
                        rule="action pinned",
                        detail=f"{reference} has no version",
                    )
                )
                continue
            action, version = reference.rsplit("@", 1)
            if version in {"main", "master", "HEAD"}:
                findings.append(
                    Finding(
                        where=f"{path.name}:{number}",
                        rule="action pinned",
                        detail=f"{reference} follows a branch",
                    )
                )
                continue
            if any(
                action.startswith(prefix) for prefix in SHA_PINNED_ACTION_PREFIXES
            ) and not re.fullmatch(r"[0-9a-f]{40}", version):
                findings.append(
                    Finding(
                        where=f"{path.name}:{number}",
                        rule="privileged action pinned to a commit",
                        detail=f"{reference} is not a 40-character commit sha",
                    )
                )
    return findings


def check_common_build_has_no_deployment_instance() -> list[Finding]:
    """
    The common build path names no deployment instance (ADR-0008 section 19,
    ES-08 section 123).

    Registry and project reach the build as configuration. A literal here would
    make the fork's own staging project part of the definition of a CARE
    release.
    """
    findings = []
    for name in COMMON_BUILD_WORKFLOWS:
        path = WORKFLOW_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for pattern, description in INSTANCE_LITERAL_PATTERNS:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            where=f"{name}:{number}",
                            rule="no hardcoded deployment instance",
                            detail=f"{description}: {line.strip()}",
                        )
                    )
    return findings


def check_deployment_workflows_do_not_build() -> list[Finding]:
    """
    Deployment and promotion consume an artifact; they never produce one
    (ADR-0008 sections 5, 16, ES-08 section 43).
    """
    findings = []
    for name in NON_BUILDING_WORKFLOWS:
        path = WORKFLOW_DIR / name
        if not path.is_file():
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            for pattern in BUILD_PATTERNS:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            where=f"{name}:{number}",
                            rule="deployment does not build",
                            detail=f"build invocation in a deployment workflow: {line.strip()}",
                        )
                    )
    return findings


def check_digest_only_deployment() -> list[Finding]:
    """
    A deployment input is a digest, and the workflow refuses anything else
    (ADR-0008 section 4, ES-08 section 43).
    """
    findings = []
    for name in ("deploy-app.yml", "promote-production.yml", "rollback.yml"):
        path = WORKFLOW_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "sha256:" not in text:
            findings.append(
                Finding(
                    where=name,
                    rule="digest-only deployment",
                    detail="no check that the requested artifact is a digest",
                )
            )
    return findings


def check_production_is_gated() -> list[Finding]:
    """
    Production promotion is manual and runs in the protected environment
    (ADR-0008 sections 17, 41).
    """
    findings = []
    path = WORKFLOW_DIR / "promote-production.yml"
    if not path.is_file():
        return findings

    document = _load(path)
    triggers = document.get(True) or document.get("on") or {}
    trigger_names = set(triggers) if isinstance(triggers, (dict, list)) else {triggers}

    if trigger_names != {"workflow_dispatch"}:
        findings.append(
            Finding(
                where="promote-production.yml",
                rule="production promotion is manual",
                detail=f"triggers are {sorted(trigger_names)}; only workflow_dispatch may promote",
            )
        )

    environments = {
        (job.get("with") or {}).get("environment")
        for job in _jobs(document).values()
        if isinstance(job, dict)
    }
    if "production" not in environments:
        findings.append(
            Finding(
                where="promote-production.yml",
                rule="production promotion is gated",
                detail="no job targets the production environment, so nothing gates it",
            )
        )

    text = path.read_text(encoding="utf-8")
    if "staging-accepted-" not in text:
        findings.append(
            Finding(
                where="promote-production.yml",
                rule="promotion requires staging acceptance",
                detail="no check for a staging acceptance record",
            )
        )
    return findings


def check_deployment_concurrency() -> list[Finding]:
    """
    Deployments to one environment do not race (ADR-0008 section 46).

    The group is keyed on the environment, and cancellation is off: cancelling
    between initialization and rollout is the case ADR-0008 section 47 is about.
    """
    findings = []
    path = WORKFLOW_DIR / "deploy-app.yml"
    if not path.is_file():
        return findings

    document = _load(path)
    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict):
        findings.append(
            Finding(
                where="deploy-app.yml",
                rule="deployment concurrency",
                detail="no concurrency group; two deployments could run against one environment",
            )
        )
        return findings

    group = str(concurrency.get("group", ""))
    if "inputs.environment" not in group:
        findings.append(
            Finding(
                where="deploy-app.yml",
                rule="deployment concurrency",
                detail=f"group '{group}' is not keyed on the environment",
            )
        )
    if concurrency.get("cancel-in-progress") is True:
        findings.append(
            Finding(
                where="deploy-app.yml",
                rule="deployment cancellation",
                detail="cancel-in-progress would interrupt a deployment mid-initialization",
            )
        )
    return findings


def check_no_secret_payload_reads() -> list[Finding]:
    """
    Deployment references secrets; it does not read them (ES-08 section 160).

    Cloud Run resolves a secret reference itself. A workflow that reads the
    plaintext needs a reason, and none of these has one.
    """
    findings = []
    candidates = list(_workflow_paths())
    candidates += sorted(
        (REPO_ROOT / "infrastructure" / "scripts" / "gcp").glob("*.sh")
    )
    for path in candidates:
        if not path.is_file():
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"gcloud\s+secrets\s+versions\s+access", line):
                findings.append(
                    Finding(
                        where=f"{path.name}:{number}",
                        rule="no secret payload reads",
                        detail=line.strip(),
                    )
                )
    return findings


def check_no_credential_literals() -> list[Finding]:
    """No credential material in delivery configuration (ADR-0008 section 34)."""
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in CREDENTIAL_LITERAL_PATTERNS:
            if re.search(pattern, text):
                findings.append(
                    Finding(
                        where=path.name,
                        rule="no credential literals",
                        detail=f"matches {pattern}",
                    )
                )
    return findings


def check_job_timeouts() -> list[Finding]:
    """Every job that runs steps has a timeout (ES-08 section 170)."""
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        for job_name, job in _jobs(_load(path)).items():
            if not isinstance(job, dict) or "steps" not in job:
                continue
            if "timeout-minutes" not in job:
                findings.append(
                    Finding(
                        where=f"{path.name}:{job_name}",
                        rule="bounded job",
                        detail="no timeout-minutes; a stuck deployment would run until the platform limit",
                    )
                )
    return findings


def check_no_continue_on_error() -> list[Finding]:
    """
    No required delivery step continues past its own failure
    (ES-08 section 84).
    """
    findings = []
    for name in (*NON_BUILDING_WORKFLOWS, "build-image.yml"):
        path = WORKFLOW_DIR / name
        if not path.is_file():
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"continue-on-error:\s*true", line):
                findings.append(
                    Finding(
                        where=f"{name}:{number}",
                        rule="failures are failures",
                        detail=line.strip(),
                    )
                )
    return findings


def _allowlist_entries() -> list[str]:
    path = REPO_ROOT / "docker" / "prod.Dockerfile.dockerignore"
    if not path.is_file():
        return []
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        rule = raw.strip()
        if not rule.startswith("!"):
            continue
        entry = rule[1:].rstrip("/")
        if "*" in entry or "?" in entry:
            continue
        entries.append(entry)
    return entries


def check_production_build_context() -> list[Finding]:
    """
    The production build context is an allowlist, and it lists things that
    exist (ADR-0008 section 36, ES-08 sections 20, 21).

    An allowlist fails closed: a path that disappears from the repository, or is
    renamed, silently stops entering the image. So each entry is checked against
    the working tree, and the first line of the file is checked to be the
    exclude-everything rule the rest depends on.
    """
    findings = []
    path = REPO_ROOT / "docker" / "prod.Dockerfile.dockerignore"
    if not path.is_file():
        return [
            Finding(
                where="docker/prod.Dockerfile.dockerignore",
                rule="controlled build context",
                detail="missing; the production build would fall back to the permissive root .dockerignore",
            )
        ]

    body = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not body or body[0] != "*":
        findings.append(
            Finding(
                where="docker/prod.Dockerfile.dockerignore",
                rule="controlled build context",
                detail="the first rule must be '*'; without it the '!' rules are not an allowlist",
            )
        )

    for entry in _allowlist_entries():
        if not (REPO_ROOT / entry).exists():
            findings.append(
                Finding(
                    where="docker/prod.Dockerfile.dockerignore",
                    rule="allowlist matches the repository",
                    detail=f"'{entry}' is allowed into the image but does not exist",
                )
            )

    # The application's own requirements, spelled out: without these the image
    # cannot serve, and an over-trimmed allowlist would fail at runtime rather
    # than at build time.
    required = {
        "manage.py",
        "care",
        "config",
        "scripts",
        "locale",
        "Pipfile",
        "Pipfile.lock",
    }
    allowed = set(_allowlist_entries())
    for entry in sorted(required - allowed):
        findings.append(
            Finding(
                where="docker/prod.Dockerfile.dockerignore",
                rule="allowlist is complete",
                detail=f"'{entry}' is required by the running application and is not allowed in",
            )
        )
    return findings


def check_root_dockerignore_categories() -> list[Finding]:
    """
    The permissive path is still hardened (ES-08 section 21).

    The development and fixture images use the root file, and a legacy
    non-BuildKit production build falls back to it.
    """
    findings = []
    path = REPO_ROOT / ".dockerignore"
    if not path.is_file():
        return [Finding(where=".dockerignore", rule="present", detail="missing")]

    text = path.read_text(encoding="utf-8")
    for needed, why in (
        (".git", "version control metadata"),
        (".claude", "agent metadata"),
        ("jwks.b64.txt", "generated private key material"),
        ("*.tfstate", "infrastructure state"),
        ("staticfiles", "generated assets that the image rebuilds"),
        ("care-backups", "local database dumps"),
    ):
        if needed not in text:
            findings.append(
                Finding(
                    where=".dockerignore",
                    rule="excluded categories",
                    detail=f"{needed} ({why}) is not excluded",
                )
            )
    return findings


def check_upstream_base_recorded() -> list[Finding]:
    """
    The upstream base is recorded, in the documented format
    (ES-08 section 28, 05-upstream-sync.md section 27).
    """
    path = REPO_ROOT / "UPSTREAM_BASE"
    if not path.is_file():
        return [
            Finding(
                where="UPSTREAM_BASE",
                rule="upstream base recorded",
                detail="missing; release metadata cannot say which upstream CARE this is",
            )
        ]

    findings = []
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        values[key.strip()] = value.strip()

    for key in ("repository", "branch", "commit", "synchronized_at"):
        if not values.get(key):
            findings.append(
                Finding(
                    where="UPSTREAM_BASE",
                    rule="upstream base recorded",
                    detail=f"no {key}",
                )
            )
    commit = values.get("commit", "")
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        findings.append(
            Finding(
                where="UPSTREAM_BASE",
                rule="upstream base recorded",
                detail=f"commit '{commit}' is not a full sha",
            )
        )
    return findings


def _invoked_repository_scripts() -> dict[str, list[str]]:
    """
    Repository scripts a delivery workflow runs directly, by workflow.

    Only direct invocations: a line whose first word -- ignoring a list marker
    and a ``run:`` key -- is a path into ``infrastructure/scripts``.
    ``bash script.sh`` is not one, and neither is a script named in a comment.
    """
    invoked: dict[str, list[str]] = {}
    for path in _workflow_paths():
        if not path.is_file():
            continue
        found: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            match = re.match(
                r"\s*(?:-\s+)?(?:run:\s*)?(infrastructure/scripts/[\w./-]+\.sh)\b", raw
            )
            if match and match.group(1) not in found:
                found.append(match.group(1))
        if found:
            invoked[path.name] = found
    return invoked


def _index_modes() -> dict[str, str] | None:
    """
    File modes as git records them, or None when git cannot be consulted.

    The index rather than the filesystem: a Windows checkout has no executable
    bit to read, and what a Linux runner will materialise is exactly what git
    stored (ES-08 section 133 found this the hard way).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "infrastructure/scripts"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    # "<mode> <object> <stage>	<path>", one line per tracked file.
    modes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        mode, _, remainder = line.partition(" ")
        _, tab, path = remainder.partition("	")
        if tab and path:
            modes[path.strip()] = mode
    return modes


def check_invoked_scripts_are_executable() -> list[Finding]:
    """
    A script a workflow runs directly is executable in the index
    (ES-08 sections 132, 133).

    A repository developed on a filesystem with no permission bits records new
    scripts as 0644, and every workflow step that runs one then fails with
    "Permission denied" on the first real GitHub Actions run — after the build
    it gated. Nothing local catches it, so it is an invariant.
    """
    invoked = _invoked_repository_scripts()
    if not invoked:
        return []

    modes = _index_modes()
    if modes is None:
        # No git: a developer running this from an export. The check protects CI,
        # and CI always has the checkout it ran from.
        return []

    findings = []
    for workflow, scripts in sorted(invoked.items()):
        for script in scripts:
            mode = modes.get(script)
            if mode is None:
                findings.append(
                    Finding(
                        where=f".github/workflows/{workflow}",
                        rule="invoked scripts are executable",
                        detail=f"{script} is invoked but is not tracked",
                    )
                )
            elif not mode.endswith("755"):
                findings.append(
                    Finding(
                        where=script,
                        rule="invoked scripts are executable",
                        detail=(
                            f"mode {mode}, invoked directly by {workflow}; "
                            "fix with `git update-index --chmod=+x`"
                        ),
                    )
                )
    return findings


CHECKS = (
    check_workflows_exist,
    check_permissions_declared,
    check_no_pull_request_target,
    check_action_pinning,
    check_common_build_has_no_deployment_instance,
    check_deployment_workflows_do_not_build,
    check_digest_only_deployment,
    check_production_is_gated,
    check_deployment_concurrency,
    check_no_secret_payload_reads,
    check_no_credential_literals,
    check_job_timeouts,
    check_no_continue_on_error,
    check_production_build_context,
    check_root_dockerignore_categories,
    check_upstream_base_recorded,
    check_invoked_scripts_are_executable,
)


def run_all() -> list[Finding]:
    """Every check, in order. Returns all findings rather than the first."""
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check())
    return findings


def main() -> int:
    findings = run_all()

    if not findings:
        print(f"delivery invariants: {len(CHECKS)} checks passed")
        return 0

    print(f"delivery invariants: {len(findings)} finding(s)", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
