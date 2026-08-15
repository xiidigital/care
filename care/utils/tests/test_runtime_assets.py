"""
Where derived assets are built, and by whom.

``collectstatic`` and ``compilemessages`` turn files that are already in the
image into other files in the image. Neither reads anything that varies between
instances, so both belong to the build. Running them at container start instead
cost ES-07 108 of a ~130 second Cloud Run cold start and killed instances that
exhausted the startup probe budget while scaling out
(``unresolved-items.md`` L8).

Nothing here can inspect a built image, so the split is asserted where it is
declared -- the Dockerfile and the entrypoints -- and the machinery those
artefacts depend on is exercised for real: the configured staticfiles backend
writes a manifest and pre-compressed variants, and WhiteNoise serves what it
wrote.
"""

import re
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from whitenoise import WhiteNoise

REPO_ROOT = Path(settings.BASE_DIR)

#: Entrypoints for long-running processes started from the production image.
#: Each serves one runtime role (ADR-0006) and each used to build assets first.
PRODUCTION_ENTRYPOINTS = (
    "scripts/start.sh",
    "scripts/start-worker.sh",
    "scripts/celery_worker.sh",
    "scripts/celery_beat.sh",
)

#: Commands that must not appear in those.
BUILD_COMMANDS = ("collectstatic", "compilemessages")


def read(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def command_lines(script, command):
    """
    Lines that actually *run* ``command``, ignoring prose about it.

    The entrypoints carry long comments explaining why they no longer collect
    static files, and a test that cannot tell a comment from an invocation would
    fail on its own documentation.
    """
    pattern = re.compile(rf"^\s*[^#\n]*manage\.py\s+{command}\b", re.MULTILINE)
    return pattern.findall(script)


class BuildOwnsDerivedAssetsTests(SimpleTestCase):
    def test_the_production_image_collects_static_files(self):
        dockerfile = read("docker/prod.Dockerfile")
        self.assertTrue(
            command_lines(dockerfile, "collectstatic"),
            "docker/prod.Dockerfile must run collectstatic: nothing else does, "
            "and the runtime entrypoints must not.",
        )

    def test_the_production_image_compiles_messages(self):
        # .mo files are gitignored, so a clean checkout has none and the image
        # would ship untranslated if the build did not compile them.
        dockerfile = read("docker/prod.Dockerfile")
        self.assertTrue(command_lines(dockerfile, "compilemessages"))

    def test_the_built_assets_are_copied_into_the_shipped_stage(self):
        # Building them in a discarded stage would be worse than not building
        # them at all, so the copy is part of the contract.
        dockerfile = read("docker/prod.Dockerfile")
        for artefact in ("staticfiles", "locale"):
            with self.subTest(artefact=artefact):
                self.assertRegex(
                    dockerfile,
                    rf"COPY --from=assets[^\n]*/{artefact}\s",
                )

    def test_no_production_entrypoint_builds_assets_at_start(self):
        for entrypoint in PRODUCTION_ENTRYPOINTS:
            script = read(entrypoint)
            for command in BUILD_COMMANDS:
                with self.subTest(entrypoint=entrypoint, command=command):
                    self.assertEqual(
                        command_lines(script, command),
                        [],
                        f"{entrypoint} runs {command} at start. It is built into "
                        f"the image; running it again per instance is the L8 "
                        f"cold-start regression.",
                    )

    def test_initialization_still_compiles_messages(self):
        # The init role is a deployment step, not an instance start: it runs
        # once per release and costs no cold start. Left alone deliberately, so
        # a deployment that overlays its own catalogues keeps working.
        self.assertTrue(command_lines(read("scripts/initialize.sh"), "compilemessages"))

    def test_local_development_still_builds_its_own_assets(self):
        # The opposite arrangement, on purpose: docker/dev.Dockerfile builds no
        # assets and docker-compose.local.yaml mounts the working tree over
        # /app, so anything the image built would be shadowed, and the sources
        # change while the container runs.
        script = read("scripts/start-dev.sh")
        for command in BUILD_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(command_lines(script, command))


class StaticManifestTests(SimpleTestCase):
    """
    The backend the image builds with, exercised on files this test owns.

    ``CompressedManifestStaticFilesStorage`` is what makes the collected tree
    immutable and cacheable: content-hashed names, a manifest mapping the source
    name onto them, and gzip and brotli siblings. If any part of that stops
    working the image ships assets the application cannot resolve.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def storage(self):
        from django.utils.module_loading import import_string

        backend = settings.STORAGES["staticfiles"]["BACKEND"]
        return import_string(backend)(location=str(self.root))

    def collect(self, files):
        """Write ``files`` into the root and post-process them as the build does."""
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        storage = self.storage()
        found = {name: (storage, name) for name in files}
        return {
            original: hashed
            for original, hashed, done in storage.post_process(found)
            if done
        }

    def test_the_configured_backend_is_the_manifest_backend(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_post_processing_writes_a_loadable_manifest(self):
        processed = self.collect({"probe/site.css": "body { color: red; }\n" * 40})
        self.assertIn("probe/site.css", processed)

        # Loaded the way the application loads it: through the storage, not by
        # reading the file, so a manifest the storage cannot parse fails here.
        reloaded = self.storage()
        hashed = reloaded.stored_name("probe/site.css")
        self.assertNotEqual(hashed, "probe/site.css")
        self.assertTrue((self.root / hashed).exists())
        self.assertTrue((self.root / "staticfiles.json").exists())

    def test_a_hashed_asset_has_gzip_and_brotli_siblings(self):
        self.collect({"probe/app.js": "console.log('hello');\n" * 60})
        hashed = self.storage().stored_name("probe/app.js")
        self.assertTrue((self.root / f"{hashed}.gz").exists())
        self.assertTrue((self.root / f"{hashed}.br").exists())

    def test_whitenoise_serves_the_built_tree(self):
        css = "body { color: red; }\n" * 40
        self.collect({"probe/site.css": css})
        hashed = self.storage().stored_name("probe/site.css")

        served = self.serve(f"/staticfiles/{hashed}")
        self.assertEqual(served["status"], "200 OK")
        self.assertTrue(served["headers"]["Content-Type"].startswith("text/css"))

        compressed = self.serve(
            f"/staticfiles/{hashed}", accept_encoding="gzip, deflate, br"
        )
        self.assertEqual(compressed["status"], "200 OK")
        self.assertIn(compressed["headers"].get("Content-Encoding"), {"br", "gzip"})

    def serve(self, path, accept_encoding=None):
        """GET ``path`` through WhiteNoise itself, over the built tree."""

        def fallback(environ, start_response):  # pragma: no cover - never reached
            start_response("404 Not Found", [])
            return [b""]

        app = WhiteNoise(fallback, root=str(self.root), prefix="/staticfiles/")

        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "wsgi.input": None,
        }
        if accept_encoding:
            environ["HTTP_ACCEPT_ENCODING"] = accept_encoding

        body = app(environ, start_response)
        if hasattr(body, "close"):
            body.close()
        return captured
