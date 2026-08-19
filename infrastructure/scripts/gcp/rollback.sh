#!/usr/bin/env bash
# Roll a CARE environment back to a previously published immutable digest.
#
# APPLICATION ROLLBACK IS NOT DATABASE ROLLBACK.
#
# This redeploys an artifact. It does not reverse a migration and must not be
# expected to: ADR-0008 section 13 makes migration reversal an explicit
# engineering decision, and section 27 keeps it out of the automated path. If
# the release being rolled back applied a schema change that the older artifact
# cannot read, rolling the application back produces a working deployment
# against a schema it does not understand — which is worse than the failure it
# was meant to undo. Fix forward in that case (ES-08 section 93).
#
# What it does:
#
#   1  show the digest currently deployed, and the one requested
#   2  refuse a digest that is not in the registry
#   3  run the ordinary deployment path, which includes init
#   4  smoke-verify the result
#
# Init runs. The older artifact's initialization contract is the one that should
# decide whether the environment it is being deployed into is usable, and
# skipping it would hide exactly the incompatibility this warning is about.
#
# Usage:
#   CARE_ENVIRONMENT=staging GCP_PROJECT_ID=... GCP_REGION=us-central1 \
#     infrastructure/scripts/gcp/rollback.sh --digest sha256:...
#
#   --list      show recent published digests and the deployed one, then exit
#   --confirm   required to actually change anything

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

DIGEST_INPUT=""
LIST_ONLY=0
CONFIRMED=0

usage() {
  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --digest)  DIGEST_INPUT="$2"; shift 2 ;;
    --list)    LIST_ONLY=1; shift ;;
    --confirm) CONFIRMED=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

care_load_environment

CURRENT_API_IMAGE="$(care_service_image "$CARE_API_SERVICE")"
CURRENT_DIGEST="${CURRENT_API_IMAGE#*@}"

care_phase "Current deployment"
care_print_environment
echo "    deployed image       ${CURRENT_API_IMAGE:-<none>}"

if [ "$LIST_ONLY" -eq 1 ]; then
  care_phase "Published digests in ${CARE_ARTIFACT_REPOSITORY} (most recent first)"
  gcloud artifacts docker images list "$CARE_IMAGE_REPOSITORY" \
    --project "$GCP_PROJECT_ID" \
    --include-tags \
    --sort-by=~UPDATE_TIME --limit 20 \
    --format='table(DIGEST,TAGS,UPDATE_TIME)' || true
  echo ""
  echo "The rollback candidate is the digest this environment ran before the"
  echo "current one. Deployment records identify it; the registry only shows"
  echo "what was published (ES-08 section 92)."
  exit 0
fi

[ -n "$DIGEST_INPUT" ] || care_abort "--digest is required. Use --list to see published digests, and the environment's deployment records to identify which one it previously ran."

DIGEST="$(care_normalize_digest "$DIGEST_INPUT")"

care_phase "Rollback plan"
echo "    from                 ${CURRENT_DIGEST:-unknown}"
echo "    to                   ${DIGEST}"
cat <<'EOF'

    WARNING -- application rollback is not database rollback.

    Migrations applied by the release being rolled back are still applied. This
    is safe only if the older artifact can run against the current schema.
    Expand-and-contract changes usually can; a destructive migration cannot.
    Decide before continuing.
EOF

care_require_digest_exists "$DIGEST"

if [ "$DIGEST" = "$CURRENT_DIGEST" ]; then
  care_abort "the environment already runs ${DIGEST}; nothing to roll back to"
fi

if [ "$CONFIRMED" -ne 1 ]; then
  care_abort "refusing to roll back without --confirm"
fi

care_phase "Deploying the previous artifact"
"${SCRIPT_DIR}/deploy.sh" --digest "$DIGEST"

care_phase "Verifying"
"${SCRIPT_DIR}/smoke.sh" --digest "$DIGEST"

care_summary "### Rollback — ${CARE_ENVIRONMENT}"
care_summary ""
care_summary "- from: \`${CURRENT_DIGEST:-unknown}\`"
care_summary "- to: \`${DIGEST}\`"
care_summary "- database: **not** rolled back; migrations applied by the newer release remain applied"

echo ""
echo "==> Rolled back to ${DIGEST}. The database was not rolled back."
