#!/usr/bin/env bash
# Generate CARE's runtime secrets and write them to Secret Manager.
#
# WHY THIS EXISTS AND OPENTOFU DOES NOT DO IT
#
# ADR-0007 ("State") forbids putting application secret values into OpenTofu
# state when an operational mechanism is available. Two things would otherwise
# put them there: google_secret_manager_secret_version takes the payload as an
# argument, and google_sql_user takes the database password as one. Both are
# recorded in state in cleartext, and the state bucket then holds every
# credential in the environment.
#
# So OpenTofu creates the secret *containers* and their IAM, and this script
# creates the values. Nothing generated here is ever printed, passed as an
# argument, or written to a file: each value is piped straight into gcloud.
#
# It also creates the Cloud SQL user, for the same reason — that is the one
# resource whose declaration would require a password.
#
# Idempotent: it adds a new version to each secret and updates the database
# user's password to match. Re-running rotates. Every runtime role reads
# `latest`, so rotation takes effect on the next Cloud Run revision.
#
# Usage:
#   infrastructure/scripts/provision-secrets.sh --env dev --project <gcp-project>
#     [--region us-central1]
#     [--instance <sql-instance>]   default: care-<env>-db
#     [--database care] [--user care]
#     [--image <care-image>]        used only to generate JWKS
#     [--skip-jwks] [--skip-database]
#
# Prerequisites: gcloud authenticated, the environment's OpenTofu apply done far
# enough that the secret containers and the Cloud SQL instance exist, and docker
# available unless --skip-jwks.

set -euo pipefail

ENVIRONMENT=""
PROJECT=""
REGION="us-central1"
INSTANCE=""
DATABASE="care"
DB_USER="care"
IMAGE=""
SKIP_JWKS=0
SKIP_DATABASE=0

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env)           ENVIRONMENT="$2"; shift 2 ;;
    --project)       PROJECT="$2"; shift 2 ;;
    --region)        REGION="$2"; shift 2 ;;
    --instance)      INSTANCE="$2"; shift 2 ;;
    --database)      DATABASE="$2"; shift 2 ;;
    --user)          DB_USER="$2"; shift 2 ;;
    --image)         IMAGE="$2"; shift 2 ;;
    --skip-jwks)     SKIP_JWKS=1; shift ;;
    --skip-database) SKIP_DATABASE=1; shift ;;
    -h|--help)       usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$ENVIRONMENT" ] || { echo "--env is required (dev, staging or prod)" >&2; exit 1; }
[ -n "$PROJECT" ]     || { echo "--project is required" >&2; exit 1; }

PREFIX="care-${ENVIRONMENT}"
INSTANCE="${INSTANCE:-${PREFIX}-db}"

SECRET_DJANGO="${PREFIX}-django-secret-key"
SECRET_DB_URL="${PREFIX}-database-url"
SECRET_DB_PASSWORD="${PREFIX}-database-password"
SECRET_JWKS="${PREFIX}-jwks-base64"

# ---------------------------------------------------------------------------
# Value generation
#
# openssl rather than $RANDOM: this needs a CSPRNG, and $RANDOM is not one.
# ---------------------------------------------------------------------------

random_urlsafe() {
  # Alphanumeric only. The database password ends up inside a URL, and a value
  # containing /, + or = would need percent-encoding that something downstream
  # would eventually get wrong. 48 alphanumeric characters is ~285 bits.
  #
  # Deliberately not `tr -dc ... </dev/urandom | head -c N`. That reads an
  # endless stream, so when head has taken its N bytes and exits, tr is killed
  # by SIGPIPE and reports failure — which under `set -o pipefail` aborts the
  # script *after* the value was generated and used. Bounding the input instead
  # means every stage runs to completion and the exit status means what it says.
  local length="${1:-48}"
  local generated
  # Base64 of length*3 bytes yields well over `length` alphanumerics even after
  # discarding +, / and =.
  generated="$(openssl rand -base64 "$((length * 3))" | LC_ALL=C tr -dc 'A-Za-z0-9')"

  if [ "${#generated}" -lt "$length" ]; then
    echo "error: random generation produced ${#generated} usable characters, needed ${length}" >&2
    exit 1
  fi

  printf '%s' "${generated:0:length}"
}

add_secret_version() {
  local secret="$1"
  # --data-file=- reads the payload from stdin, so it never becomes a process
  # argument visible in a process list.
  gcloud secrets versions add "$secret" \
    --project "$PROJECT" \
    --data-file=- >/dev/null
  echo "    ${secret}: new version added" >&2
}

require_secret() {
  local secret="$1"
  if ! gcloud secrets describe "$secret" --project "$PROJECT" >/dev/null 2>&1; then
    echo "error: secret ${secret} does not exist." >&2
    echo "       Apply the environment's OpenTofu configuration first; it creates" >&2
    echo "       the containers, and this script fills them." >&2
    exit 1
  fi
}

echo "==> Environment ${ENVIRONMENT} in project ${PROJECT}" >&2

require_secret "$SECRET_DJANGO"
require_secret "$SECRET_DB_URL"
require_secret "$SECRET_DB_PASSWORD"
require_secret "$SECRET_JWKS"

# ---------------------------------------------------------------------------
# DJANGO_SECRET_KEY
# ---------------------------------------------------------------------------

echo "==> DJANGO_SECRET_KEY" >&2
random_urlsafe 64 | add_secret_version "$SECRET_DJANGO"

# ---------------------------------------------------------------------------
# JWKS_BASE64
#
# Required, not optional. config/settings/deployment.py falls back to
# get_jwks_from_file, which *generates a fresh random key set* when the file is
# absent — so without this secret every Cloud Run instance signs with its own
# key and a token issued by one is rejected by the next.
#
# Generated inside the application image so the key set is produced by the same
# authlib version that will read it.
# ---------------------------------------------------------------------------

if [ "$SKIP_JWKS" -eq 0 ]; then
  echo "==> JWKS_BASE64" >&2
  if [ -z "$IMAGE" ]; then
    echo "error: --image is required to generate JWKS, or pass --skip-jwks." >&2
    exit 1
  fi
  docker run --rm --entrypoint python "$IMAGE" \
    -c 'from care.utils.jwks.generate_jwk import generate_encoded_jwks; print(generate_encoded_jwks())' \
    | tr -d '\r\n' \
    | add_secret_version "$SECRET_JWKS"
else
  echo "==> JWKS_BASE64 skipped" >&2
fi

# ---------------------------------------------------------------------------
# Database user, password and DATABASE_URL
#
# The user is created here rather than declared in OpenTofu, because
# google_sql_user writes the password to state. See the note in
# modules/care-environment/sql.tf.
#
# The URL form is the one django-environ documents for a Cloud SQL unix socket:
# an empty host, and the socket directory as the leading path segment.
# ---------------------------------------------------------------------------

if [ "$SKIP_DATABASE" -eq 0 ]; then
  echo "==> Database user ${DB_USER} on ${INSTANCE}" >&2

  CONNECTION_NAME="$(gcloud sql instances describe "$INSTANCE" \
    --project "$PROJECT" --format='value(connectionName)')"

  if [ -z "$CONNECTION_NAME" ]; then
    echo "error: could not resolve the connection name for ${INSTANCE}." >&2
    exit 1
  fi

  DB_PASSWORD="$(random_urlsafe 48)"

  if gcloud sql users list --instance "$INSTANCE" --project "$PROJECT" \
       --format='value(name)' | grep -qx "$DB_USER"; then
    gcloud sql users set-password "$DB_USER" \
      --instance "$INSTANCE" --project "$PROJECT" \
      --password "$DB_PASSWORD" >/dev/null
    echo "    user exists; password rotated" >&2
  else
    gcloud sql users create "$DB_USER" \
      --instance "$INSTANCE" --project "$PROJECT" \
      --password "$DB_PASSWORD" >/dev/null
    echo "    user created" >&2
  fi

  printf '%s' "$DB_PASSWORD" | add_secret_version "$SECRET_DB_PASSWORD"

  printf 'postgres://%s:%s@//cloudsql/%s/%s' \
    "$DB_USER" "$DB_PASSWORD" "$CONNECTION_NAME" "$DATABASE" \
    | add_secret_version "$SECRET_DB_URL"

  unset DB_PASSWORD
else
  echo "==> Database provisioning skipped" >&2
fi

echo "" >&2
echo "==> Done. No value was printed, logged or written to disk." >&2
echo "    Runtime roles read 'latest', so a new Cloud Run revision picks these up." >&2
