# A development-only companion image for the manually invoked fixture Job.
# Runtime roles keep using docker/prod.Dockerfile's immutable image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN /app/.venv/bin/pip install --no-cache-dir Faker==38.2.0

# The fixture job is built from an immutable runtime image but must carry the
# current fixture helper implementation from this source revision.
COPY --chown=django:django care/fixtures/context.py /app/care/fixtures/context.py
USER django
