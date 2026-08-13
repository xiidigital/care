#!/usr/bin/env bash
# Build the CARE application image and push it to Artifact Registry.
#
# OpenTofu does not build images (ES-07 section 14); it selects one. This is the
# operational half of that split, and it is deliberately small — it does not
# modify the Dockerfile, does not deploy, and does not touch infrastructure.
#
# The image it produces serves every runtime role. The API, the task worker and
# every Job run this one build and differ by command and environment
# (ADR-0007 "Runtime roles").
#
# Usage:
#   infrastructure/scripts/publish-image.sh \
#       --project <gcp-project> \
#       --repository <artifact-registry-repo> \
#       [--region us-central1] \
#       [--tag <tag>]
#
#   # the dev-only fixture image, built FROM a published runtime image:
#   infrastructure/scripts/publish-image.sh \
#       --project <gcp-project> \
#       --repository <artifact-registry-repo> \
#       --fixtures-from <runtime-image@sha256:...>
#
# Prints the immutable digest reference on stdout. That is the value to put in
# the environment's `image` variable: a tag can be moved, and then nobody can
# say afterwards which build was serving.

set -euo pipefail

REGION="us-central1"
TAG=""
PROJECT=""
REPOSITORY=""
IMAGE_NAME=""
FIXTURES_FROM=""

usage() {
  sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project)       PROJECT="$2"; shift 2 ;;
    --repository)    REPOSITORY="$2"; shift 2 ;;
    --region)        REGION="$2"; shift 2 ;;
    --tag)           TAG="$2"; shift 2 ;;
    --name)          IMAGE_NAME="$2"; shift 2 ;;
    --fixtures-from) FIXTURES_FROM="$2"; shift 2 ;;
    -h|--help)       usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

# Two builds, one script, because they must stay in step: the fixture image is
# a thin layer over a specific runtime image and is worthless without it.
#
# It is the single exception to "one image serves every role" (ADR-0007 "Runtime
# roles"), and it does not weaken the rule, because no role uses it. It exists
# only so `manage.py load_fixtures` can import Faker, a development dependency
# that the production image is right to omit.
if [ -n "$FIXTURES_FROM" ]; then
  DOCKERFILE="docker/fixtures.Dockerfile"
  BUILD_ARGS=(--build-arg "BASE_IMAGE=${FIXTURES_FROM}")
  : "${IMAGE_NAME:=care-fixtures}"
else
  DOCKERFILE="docker/prod.Dockerfile"
  BUILD_ARGS=()
  : "${IMAGE_NAME:=care}"
fi

[ -n "$PROJECT" ]    || { echo "--project is required" >&2; exit 1; }
[ -n "$REPOSITORY" ] || { echo "--repository is required" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Default the tag to the commit being built, so an image is traceable to a
# source revision without consulting anything else.
if [ -z "$TAG" ]; then
  GIT_SHA="$(git rev-parse --short=12 HEAD)"
  if [ -n "$(git status --porcelain)" ]; then
    # A dirty tree produces an image that no commit describes. Marked rather
    # than refused: dev iterates, and the marker keeps it out of production,
    # where the root requires a digest anyway.
    TAG="${GIT_SHA}-dirty"
    echo "warning: working tree is not clean; tagging ${TAG}" >&2
  else
    TAG="$GIT_SHA"
  fi
fi

REGISTRY="${REGION}-docker.pkg.dev"
IMAGE="${REGISTRY}/${PROJECT}/${REPOSITORY}/${IMAGE_NAME}"

echo "==> Configuring docker credentials for ${REGISTRY}" >&2
gcloud auth configure-docker "$REGISTRY" --quiet >&2

echo "==> Building ${IMAGE}:${TAG} from ${DOCKERFILE}" >&2
# The Dockerfile, unmodified. ES-07 section 132 asks not to redesign the build;
# APP_VERSION is the one build argument, and it is informational.
docker build \
  --file "$DOCKERFILE" \
  --build-arg APP_VERSION="${TAG}" \
  "${BUILD_ARGS[@]}" \
  --tag "${IMAGE}:${TAG}" \
  . >&2

echo "==> Pushing ${IMAGE}:${TAG}" >&2
docker push "${IMAGE}:${TAG}" >&2

DIGEST="$(gcloud artifacts docker images describe "${IMAGE}:${TAG}" \
  --project "$PROJECT" --format='value(image_summary.digest)')"

if [ -z "$DIGEST" ]; then
  echo "error: could not resolve the pushed digest" >&2
  exit 1
fi

echo "" >&2
echo "==> Published. Use this immutable reference as the image variable:" >&2
echo "${IMAGE}@${DIGEST}"
