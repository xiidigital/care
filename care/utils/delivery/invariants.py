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
    "verify-source.yml",
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

    # A human asks for a promotion; nothing else may. `workflow_call` is
    # permitted because D10 puts the operator's entry point on the default
    # branch and this implementation on the release lineage, so the dispatch
    # arrives through a shim -- a person still starts it. What stays forbidden
    # is any trigger that fires on its own: a push, a schedule, or the
    # completion of another run.
    automatic = trigger_names - {"workflow_dispatch", "workflow_call"}
    if automatic:
        findings.append(
            Finding(
                where="promote-production.yml",
                rule="production promotion is manual",
                detail=(
                    f"triggers {sorted(automatic)} fire without a person asking; "
                    "only workflow_dispatch, or a workflow_call from a dispatch "
                    "entry point, may promote"
                ),
            )
        )
    if "workflow_dispatch" not in trigger_names:
        findings.append(
            Finding(
                where="promote-production.yml",
                rule="production promotion is manual",
                detail="no workflow_dispatch trigger; promotion must be startable by a person",
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


#: Where the names live. Parsed rather than imported: this module runs in a job
#: that has not installed Django, and duplicating the list here is how the two
#: drift apart (ES-08 section 81).
TASK_SETTINGS_SOURCE = REPO_ROOT / "config" / "tasks.py"

#: Names ``config/settings/base.py`` derives from another when it is absent, so
#: a caller that sets the source has satisfied them.
CLOUD_TASKS_DERIVED_FROM = {
    "GCP_TASKS_PROJECT_ID": "GCP_PROJECT_ID",
    "GCP_TASKS_OIDC_AUDIENCE": "GCP_WORKER_URL",
}


def _cloud_tasks_required_settings() -> list[str]:
    """The names ``CARE_TASK_BACKEND=cloud_tasks`` makes mandatory."""
    if not TASK_SETTINGS_SOURCE.is_file():
        return []
    text = TASK_SETTINGS_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"CLOUD_TASKS_REQUIRED_SETTINGS\s*=\s*\((.*?)\)", text, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([A-Z0-9_]+)"', match.group(1))


def check_cloud_tasks_selection_is_complete() -> list[Finding]:
    """
    Anything that selects the Cloud Tasks backend supplies what it requires
    (ES-08 sections 10, 99).

    ``config.tasks`` validates the whole set at settings import, so a caller
    that names the backend and half the variables does not get a degraded
    run -- it gets ImproperlyConfigured before the test runner or the
    entrypoint exists. Two separate places got this wrong, which makes it an
    invariant rather than a fix.
    """
    required = _cloud_tasks_required_settings()
    if not required:
        return []

    sources = [*_workflow_paths()]
    scripts = REPO_ROOT / "infrastructure" / "scripts"
    if scripts.is_dir():
        sources.extend(sorted(scripts.rglob("*.sh")))

    findings = []
    for path in sources:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "CARE_TASK_BACKEND=cloud_tasks" not in text:
            continue
        missing = [
            name
            for name in required
            if not re.search(rf"{name}=", text)
            and not (
                CLOUD_TASKS_DERIVED_FROM.get(name)
                and re.search(rf"{CLOUD_TASKS_DERIVED_FROM[name]}=", text)
            )
        ]
        if missing:
            findings.append(
                Finding(
                    where=str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    rule="cloud tasks selection is complete",
                    detail=(
                        "selects CARE_TASK_BACKEND=cloud_tasks without "
                        + ", ".join(missing)
                    ),
                )
            )
    return findings


#: The role entrypoints that wait for PostgreSQL before they bind. Anything that
#: starts one of these has to configure it the way a deployment does.
ROLE_ENTRYPOINTS = ("./start.sh", "./start-worker.sh")

WAIT_FOR_DB_SOURCE = REPO_ROOT / "scripts" / "wait_for_db.sh"


def _wait_for_db_variables() -> list[str]:
    """The POSTGRES_* names ``scripts/wait_for_db.sh`` connects with."""
    if not WAIT_FOR_DB_SOURCE.is_file():
        return []
    text = WAIT_FOR_DB_SOURCE.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\$\{(POSTGRES_[A-Z0-9_]+)\}", text)))


def check_role_startup_supplies_database_variables() -> list[Finding]:
    """
    Anything that starts a role entrypoint supplies what its wait loop reads
    (ES-08 section 99).

    ``scripts/wait_for_db.sh`` -- which start.sh and start-worker.sh both run
    before binding -- connects with POSTGRES_HOST/PORT/USER/PASSWORD/DB and
    never reads DATABASE_URL. The managed environment sets both deliberately
    (modules/care-environment/config.tf and secrets.tf). A caller that supplies
    only DATABASE_URL is not a degraded run: psycopg falls back to a local unix
    socket, the loop exhausts its 30 attempts and the role never serves, which
    is how a green local check became a red CI job.
    """
    required = _wait_for_db_variables()
    if not required:
        return []

    scripts = REPO_ROOT / "infrastructure" / "scripts"
    if not scripts.is_dir():
        return []

    findings = []
    for path in sorted(scripts.rglob("*.sh")):
        text = path.read_text(encoding="utf-8")
        if not any(entrypoint in text for entrypoint in ROLE_ENTRYPOINTS):
            continue
        missing = [name for name in required if not re.search(rf"{name}=", text)]
        if missing:
            findings.append(
                Finding(
                    where=str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    rule="role startup supplies database variables",
                    detail=(
                        "starts a role entrypoint without "
                        + ", ".join(missing)
                        + " (scripts/wait_for_db.sh reads these, not DATABASE_URL)"
                    ),
                )
            )
    return findings


STORAGE_SETTINGS_SOURCE = REPO_ROOT / "config" / "storage.py"


def _supported_storage_backends() -> list[str]:
    """The values ``CARE_STORAGE_BACKEND`` accepts, read from the validator."""
    if not STORAGE_SETTINGS_SOURCE.is_file():
        return []
    text = STORAGE_SETTINGS_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"SUPPORTED_STORAGE_BACKENDS\s*=\s*\((.*?)\)", text, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([a-z0-9_]+)"', match.group(1))


def check_storage_backend_selection_is_supported() -> list[Finding]:
    """
    Anything that selects an object-storage provider names one that exists
    (ES-08 sections 23, 99).

    ``config/storage.py`` validates the name at settings import, so a caller
    that invents one does not degrade to a local directory -- it raises
    ImproperlyConfigured before the role binds. The startup verification
    selected a "local" provider that has never existed, and only a real
    container start revealed it.
    """
    supported = _supported_storage_backends()
    if not supported:
        return []

    sources = [*_workflow_paths()]
    scripts = REPO_ROOT / "infrastructure" / "scripts"
    if scripts.is_dir():
        sources.extend(sorted(scripts.rglob("*.sh")))

    findings = []
    for path in sources:
        if not path.is_file():
            continue
        for value in set(
            re.findall(
                r"CARE_STORAGE_BACKEND=\"?([A-Za-z0-9_]+)", path.read_text("utf-8")
            )
        ):
            if value not in supported:
                findings.append(
                    Finding(
                        where=str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                        rule="storage backend selection is supported",
                        detail=(
                            f"selects CARE_STORAGE_BACKEND={value}, which "
                            f"config/storage.py rejects (supported: "
                            + ", ".join(supported)
                            + ")"
                        ),
                    )
                )
    return findings


#: The reusable workflow that decides whether a revision belongs to the trusted
#: release lineage. Named here because the rule below is about reaching it.
SOURCE_TRUST_WORKFLOW = "verify-source.yml"


def _job_uses(job: dict) -> str:
    return job.get("uses", "") if isinstance(job, dict) else ""


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") if isinstance(job, dict) else None
    if needs is None:
        return []
    return [needs] if isinstance(needs, str) else list(needs)


def _trust_jobs(jobs: dict) -> set[str]:
    """Jobs in one workflow that are the source-trust gate, or depend on it."""
    direct = {
        name for name, job in jobs.items() if SOURCE_TRUST_WORKFLOW in _job_uses(job)
    }
    reached = set(direct)
    changed = True
    while changed:
        changed = False
        for name, job in jobs.items():
            if name in reached:
                continue
            if any(dep in reached for dep in _needs(job)):
                reached.add(name)
                changed = True
    return reached


def _gate_findings(where: str, jobs: dict) -> list[Finding]:
    """The trust gate must hold nothing it could use to authorize itself."""
    findings = []
    for name, job in jobs.items():
        perms = job.get("permissions") or {}
        if isinstance(perms, dict) and perms.get("id-token") == "write":
            findings.append(
                Finding(
                    where=where,
                    rule="source trust gate holds no credential",
                    detail=f"job '{name}' requests id-token: write",
                )
            )
        if job.get("environment"):
            findings.append(
                Finding(
                    where=where,
                    rule="source trust gate holds no credential",
                    detail=f"job '{name}' declares an environment",
                )
            )
    return findings


def check_credentialed_jobs_verify_source_trust() -> list[Finding]:
    """
    No job holding a deployment credential runs unverified source
    (ES-08 D10, ADR-0008 section 5a).

    Moving the delivery entry points onto the default branch so GitHub would
    register them (D10) made the built revision a workflow *input*. An input is
    attacker-reachable: anyone who can dispatch can type a ref. Two properties
    keep that from being a privilege escalation, and both are checked here.

    First, every job that requests ``id-token: write`` must depend, directly or
    transitively, on the job that proves the revision is reachable from the
    release lineage. Second, every checkout in such a job must name the SHA that
    job resolved -- not the ref that was typed, and not the workflow's own
    commit. Re-resolving a ref after it has been checked is a
    time-of-check/time-of-use gap: a branch can move in between.

    The gate itself must hold no credential, or it could be made to authorize
    itself.
    """
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        document = _load(path)
        jobs = _jobs(document)
        if not jobs:
            continue
        where = str(path.relative_to(REPO_ROOT)).replace("\\", "/")

        if path.name == SOURCE_TRUST_WORKFLOW:
            findings.extend(_gate_findings(where, jobs))
            continue

        trusted = _trust_jobs(jobs)
        for name, job in jobs.items():
            perms = job.get("permissions") or {}
            if not (isinstance(perms, dict) and perms.get("id-token") == "write"):
                continue

            if name not in trusted:
                findings.append(
                    Finding(
                        where=where,
                        rule="credentialed jobs verify source trust",
                        detail=(
                            f"job '{name}' requests id-token: write without "
                            f"depending on a job that uses {SOURCE_TRUST_WORKFLOW}"
                        ),
                    )
                )
                continue

            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if "actions/checkout" not in str(step.get("uses", "")):
                    continue
                ref = str((step.get("with") or {}).get("ref", ""))
                if "outputs.source_sha" not in ref:
                    findings.append(
                        Finding(
                            where=where,
                            rule="credentialed jobs verify source trust",
                            detail=(
                                f"job '{name}' checks out "
                                + (f"ref '{ref}'" if ref else "the default ref")
                                + " instead of the verified source_sha"
                            ),
                        )
                    )
    return findings


def check_reusable_workflow_calls_resolve() -> list[Finding]:
    """
    Every reusable-workflow call names a target that exists and accepts what it
    is given (ES-08 D10 Part F).

    A local ``uses: ./.github/workflows/x.yml`` resolves against the *caller's*
    commit, not against whatever branch the implementation happens to live on.
    Once the entry points live on the default branch and the implementation on
    the release lineage, that is easy to get wrong in a way no local check
    notices and GitHub reports only at dispatch time, after an operator has
    already asked for a deployment.

    Checked here: the target exists, it declares ``workflow_call``, every key
    passed in ``with:`` is an input it declares, and every input it marks
    required is supplied.
    """
    findings = []
    for path in _workflow_paths():
        if not path.is_file():
            continue
        document = _load(path)
        jobs = _jobs(document)
        if not jobs:
            continue
        where = str(path.relative_to(REPO_ROOT)).replace("\\", "/")

        for name, job in jobs.items():
            uses = _job_uses(job)
            if not uses.startswith("./.github/workflows/"):
                continue

            # removeprefix, not lstrip: lstrip strips a character *set*,
            # which eats the dot in ".github" as well.
            target = REPO_ROOT / uses.removeprefix("./")
            if not target.is_file():
                findings.append(
                    Finding(
                        where=where,
                        rule="reusable workflow calls resolve",
                        detail=f"job '{name}' calls '{uses}', which does not exist",
                    )
                )
                continue

            called = _load(target)
            # PyYAML reads a bare `on:` key as the boolean True.
            triggers = called.get("on", called.get(True)) or {}
            if not isinstance(triggers, dict) or "workflow_call" not in triggers:
                findings.append(
                    Finding(
                        where=where,
                        rule="reusable workflow calls resolve",
                        detail=f"job '{name}' calls '{uses}', which has no workflow_call trigger",
                    )
                )
                continue

            declared = (triggers.get("workflow_call") or {}).get("inputs") or {}
            supplied = job.get("with") or {}

            for key in supplied:
                if key not in declared:
                    findings.append(
                        Finding(
                            where=where,
                            rule="reusable workflow calls resolve",
                            detail=f"job '{name}' passes '{key}', which {uses} does not declare",
                        )
                    )
            for key, spec in declared.items():
                if (spec or {}).get("required") and key not in supplied:
                    findings.append(
                        Finding(
                            where=where,
                            rule="reusable workflow calls resolve",
                            detail=f"job '{name}' omits '{key}', which {uses} requires",
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
    check_cloud_tasks_selection_is_complete,
    check_role_startup_supplies_database_variables,
    check_storage_backend_selection_is_supported,
    check_credentialed_jobs_verify_source_trust,
    check_reusable_workflow_calls_resolve,
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
