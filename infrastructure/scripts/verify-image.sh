#!/usr/bin/env bash
# Inspect a built CARE production image without deploying it.
#
# ADR-0008 section 36 requires production artifacts to be derived from known
# repository content, and ES-08 sections 95-98 require proof that the image
# carries its built assets and none of the builder's local files. Both are
# properties of the artifact, not of the build log, so they are checked here
# against the image itself.
#
# Runs offline: no registry, no GCP, no running environment. It is the same
# check in CI and on a workstation.
#
# Usage:
#   infrastructure/scripts/verify-image.sh --image care:local
#   infrastructure/scripts/verify-image.sh --image care:local --inventory-out inv.txt
#
# --inventory-out writes the sorted list of application files in the image.
# Two builds of the same source produce the same inventory whatever local
# scratch the builder had; that is how the controlled-build-context claim is
# proven rather than asserted (ES-08 section 20).

set -euo pipefail

IMAGE=""
INVENTORY_OUT=""
APP_HOME="/app"

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)         IMAGE="$2"; shift 2 ;;
    --inventory-out) INVENTORY_OUT="$2"; shift 2 ;;
    -h|--help)       usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$IMAGE" ] || { echo "error: --image is required" >&2; exit 1; }

FAILURES=0

fail() {
  echo "FAIL  $*" >&2
  FAILURES=$((FAILURES + 1))
}

pass() {
  echo "ok    $*"
}

# Every check reads the image the same way: a throwaway container running one
# shell command. Nothing is written, and nothing persists between checks.
run_in_image() {
  docker run --rm --entrypoint /bin/sh "$IMAGE" -c "$1"
}

echo "==> Verifying ${IMAGE}"

# ---------------------------------------------------------------------------
# 1. Static assets (ES-08 section 97)
#
# collectstatic runs in the image's assets stage and must never run again at
# startup. The manifest is the artefact that proves it did: WhiteNoise resolves
# every hashed asset name through it, and a missing manifest is a 500 on the
# first templated page rather than a startup failure.
# ---------------------------------------------------------------------------

MANIFEST="${APP_HOME}/staticfiles/staticfiles.json"
if run_in_image "test -f ${MANIFEST}"; then
  ENTRIES="$(run_in_image "python -c \"import json;print(len(json.load(open('${MANIFEST}'))['paths']))\"" 2>/dev/null || echo 0)"
  if [ "${ENTRIES:-0}" -gt 0 ]; then
    pass "static manifest present (${ENTRIES} entries)"
  else
    fail "static manifest ${MANIFEST} is empty"
  fi
else
  fail "static manifest ${MANIFEST} is missing; the image would run collectstatic at startup"
fi

STATIC_FILES="$(run_in_image "find ${APP_HOME}/staticfiles -type f | wc -l" 2>/dev/null || echo 0)"
if [ "${STATIC_FILES:-0}" -gt 1 ]; then
  pass "collected static files present (${STATIC_FILES})"
else
  fail "no collected static files under ${APP_HOME}/staticfiles"
fi

# ---------------------------------------------------------------------------
# 2. Compiled message catalogues (ES-08 section 98)
#
# .mo files are gitignored, so a clean checkout has none and they exist only
# because compilemessages ran in the assets stage. Their presence is therefore
# also evidence that the stage ran.
# ---------------------------------------------------------------------------

MO_COUNT="$(run_in_image "find ${APP_HOME}/locale -name '*.mo' | wc -l" 2>/dev/null || echo 0)"
if [ "${MO_COUNT:-0}" -gt 0 ]; then
  pass "compiled message catalogues present (${MO_COUNT})"
else
  fail "no compiled catalogues under ${APP_HOME}/locale; compilemessages would run at startup"
fi

# ---------------------------------------------------------------------------
# 3. Runtime role entrypoints (ES-06)
# ---------------------------------------------------------------------------

for entrypoint in start.sh start-worker.sh initialize.sh healthcheck.sh; do
  if run_in_image "test -x ${APP_HOME}/${entrypoint}"; then
    pass "entrypoint ${entrypoint} present and executable"
  else
    fail "entrypoint ${entrypoint} missing or not executable"
  fi
done

# ---------------------------------------------------------------------------
# 4. Local-only artefacts (ES-08 section 95, unresolved-items.md P2)
#
# Each of these reached an image at least once, or would have under the
# previous denylist. Named individually so a failure says which one.
# ---------------------------------------------------------------------------

FORBIDDEN=(
  ".git"
  ".github"
  ".claude"
  ".env"
  "jwks.b64.txt"
  "htmlcov"
  "coverage.xml"
  ".coverage"
  "test-results"
  "care_db.dump"
  "care-backups"
  "output"
  "infrastructure"
  "docs"
  "node_modules"
  ".idea"
  ".vscode"
  ".pytest_cache"
  ".mypy_cache"
)

for path in "${FORBIDDEN[@]}"; do
  if run_in_image "test -e ${APP_HOME}/${path}"; then
    fail "local-only path present in image: ${APP_HOME}/${path}"
  fi
done
pass "no local-only artefacts found (${#FORBIDDEN[@]} paths checked)"

# Anything named like infrastructure state, anywhere in the application tree.
STATE_HITS="$(run_in_image "find ${APP_HOME} -path ${APP_HOME}/.venv -prune -o \( -name '*.tfstate' -o -name '*.tfstate.*' -o -name '*.tfplan' -o -name 'terraform.tfvars' -o -name 'backend.hcl' -o -name '*.pem' -o -name '*_rsa' \) -print" 2>/dev/null || true)"
if [ -n "$STATE_HITS" ]; then
  fail "infrastructure state or key material in image:"
  echo "$STATE_HITS" >&2
else
  pass "no infrastructure state or key material in the application tree"
fi

# ---------------------------------------------------------------------------
# 5. Application inventory
#
# The application tree only. .venv is dependency content installed from
# Pipfile.lock, not build context, and listing it would bury the signal.
# ---------------------------------------------------------------------------

INVENTORY="$(run_in_image "find ${APP_HOME} -path ${APP_HOME}/.venv -prune -o -type f -print | sed 's|^${APP_HOME}/||' | LC_ALL=C sort")"
INVENTORY_COUNT="$(printf '%s\n' "$INVENTORY" | wc -l | tr -d ' ')"
INVENTORY_DIGEST="$(printf '%s\n' "$INVENTORY" | sha256sum | cut -d' ' -f1)"

pass "application inventory: ${INVENTORY_COUNT} files, sha256 ${INVENTORY_DIGEST}"

if [ -n "$INVENTORY_OUT" ]; then
  printf '%s\n' "$INVENTORY" > "$INVENTORY_OUT"
  echo "      inventory written to ${INVENTORY_OUT}"
fi

echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "==> ${FAILURES} check(s) failed" >&2
  exit 1
fi
echo "==> Image verification passed"
