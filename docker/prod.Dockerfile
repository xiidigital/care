FROM python:3.13-slim-bookworm AS base

ARG APP_HOME=/app

ARG BUILD_ENVIRONMENT="production"

WORKDIR $APP_HOME

ENV BUILD_ENVIRONMENT=$BUILD_ENVIRONMENT
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIPENV_VENV_IN_PROJECT=1
ENV PIPENV_CACHE_DIR=/root/.cache/pip
ENV PATH=$APP_HOME/.venv/bin:$PATH
ENV HOME=$APP_HOME


# ---
FROM base AS builder

RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential libjpeg-dev zlib1g-dev libgmp-dev libpq-dev git wget \
  libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0 libffi-dev libopenjp2-7-dev \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# use pipenv to manage virtualenv
RUN pip install pipenv==2025.1.1

RUN python -m venv $APP_HOME/.venv
COPY Pipfile Pipfile.lock $APP_HOME/
RUN pipenv install --deploy --categories "packages"

COPY plugs/ $APP_HOME/plugs/
COPY install_plugins.py plug_config.py $APP_HOME/

ARG ADDITIONAL_PLUGS=""
ENV ADDITIONAL_PLUGS=$ADDITIONAL_PLUGS
RUN python3 $APP_HOME/install_plugins.py

# ---
# Derived assets, built once per image.
#
# Until this stage existed, every runtime entrypoint ran `collectstatic` and
# `compilemessages` before binding, so each cold start recomputed the same
# content hashes, gzip, brotli and message catalogues for the same sources
# baked into the same immutable image. On Cloud Run that cost 108 of a ~130
# second cold start and exhausted the startup probe budget under scale-out
# (unresolved-items.md L8). Both inputs are files in the image and neither
# command reads configuration that varies per environment, so the result is the
# same on every instance and is computed here instead.
#
# It is a separate stage rather than steps in `runtime` so that only the built
# artefacts cross into the shipped image. Importing the settings module has
# side effects on the filesystem -- `config/settings/deployment.py` evaluates
# `get_jwks_from_file()`, which writes a freshly generated key set to
# `jwks.b64.txt` when the file is absent -- and a generated key must never be
# baked into an image every instance then shares. Here that write, and any
# .pyc, lands in a stage that is discarded.
FROM builder AS assets

# msgfmt. The builder installs no gettext; the runtime stage does, because
# `scripts/initialize.sh` still compiles messages as a deployment step.
RUN apt-get update && apt-get install --no-install-recommends -y gettext \
  && rm -rf /var/lib/apt/lists/*

COPY . $APP_HOME

# The settings module the container will run, so the manifest written here is
# the one the application looks up. `config.settings.production` and
# `config.settings.staging` derive from it and override nothing that reaches
# STATIC_ROOT, STATICFILES_DIRS, STORAGES, LOCALE_PATHS or INSTALLED_APPS, so
# all three produce the same artefacts.
#
# DATABASE_URL is the one value `deployment.py` requires without a default;
# `env.db()` parses it and opens nothing. The placeholder below is not a
# credential and is not reachable: neither command touches a database, a cache,
# a broker or the network.
ENV DJANGO_SETTINGS_MODULE=config.settings.deployment
ENV DATABASE_URL="postgres://build:build@127.0.0.1:5432/build"

RUN python manage.py collectstatic --noinput
RUN python manage.py compilemessages -v 0

# ---
FROM base AS runtime

RUN addgroup --system django \
  && adduser --system --ingroup django django

RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev libgmp-dev libpangoft2-1.0-0 gettext wget curl gnupg \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

RUN chown django:django $APP_HOME

COPY --from=builder --chown=django:django $APP_HOME/.venv $APP_HOME/.venv

ARG APP_VERSION="unknown"
ENV APP_VERSION=$APP_VERSION

COPY --chmod=0755 --chown=django:django ./scripts/*.sh $APP_HOME

COPY --chown=django:django . $APP_HOME

# After the source copy, and only the built artefacts: STATIC_ROOT with the
# WhiteNoise manifest and the pre-compressed variants, and the compiled message
# catalogues. Nothing else crosses from `assets`, and no long-running process
# rewrites either tree.
#
# `locale/` is copied whole rather than by glob because .mo files are gitignored:
# a clean checkout has none, so the compiled catalogues exist only in `assets`.
COPY --from=assets --chown=django:django $APP_HOME/staticfiles $APP_HOME/staticfiles
COPY --from=assets --chown=django:django $APP_HOME/locale $APP_HOME/locale

USER django

HEALTHCHECK \
  --interval=30s \
  --timeout=5s \
  --start-period=10s \
  --retries=12 \
  CMD ["./healthcheck.sh"]

EXPOSE 9000
