---
title: Plugin Impact Inventory
document: inventory/plugin-impact
version: 0.3.0
status: Draft
phase: 3
source_repository: https://github.com/ohcnetwork/care
source_branch: gcp
source_commit: 6a2976dc2512c2c532fcc70628c5690fbbbe3f3d
reviewed: 2026-08-07
---

# Plugin Impact Inventory

## ES-05 locking update

No plugin imports of `care.utils.lock`, `MultipleItemsLock`, or cache `nx`
locking were found. Plugins using `Lock` remain import-compatible but must enter
`transaction.atomic()` before acquisition; `MultipleItemsLock` is removed.

How CARE loads plugins, what is bundled at this commit, and what can and cannot
be determined about plugin compatibility from this repository alone.

Evidence labels: **verified** / **inferred** / **unknown**.

---

## 1. Headline

**verified** **No plugins are bundled at this commit.** `plug_config.py:4` is:

```python
plugs = []
```

**verified** Therefore `PLUGIN_APPS` is empty, no plugin app is installed, no
plugin URL is routed, and no plugin migration exists **in this configuration**.

**verified** However, plugins can be injected at build time or runtime **without
touching this repository**, via the `ADDITIONAL_PLUGS` environment variable. So
"no plugins bundled" is not the same as "no plugins in a given deployment".

---

## 2. Loading mechanism

**verified** Four files implement the whole system:

| File | Role |
| --- | --- |
| `plug_config.py` | Declares the plug list; instantiates `PlugManager` |
| `plugs/plug.py` | The `Plug` dataclass |
| `plugs/manager.py` | `PlugManager` — install, app list, config aggregation |
| `install_plugins.py` | Build-time entrypoint: `manager.install()` |

**verified** `plugs/plug.py:5-10` — the `Plug` dataclass:

```python
@dataclass(slots=True)
class Plug:
    name: str
    package_name: str
    version: str = field(default="@main")
    configs: dict = field(default_factory=dict)
```

**verified** `version` defaults to `"@main"` (`plug.py:8`) — a **git ref, not a
pinned release**. **inferred** the default installs a moving target; two builds
of the same commit can produce different plugin code.

### 2.1 Runtime injection

**verified** `plugs/manager.py:22-29`:

```python
if additional_plugs := os.getenv("ADDITIONAL_PLUGS"):
    try:
        for plug in json.loads(additional_plugs):
            self.add_plug(Plug(**plug))
    except json.JSONDecodeError:
        logger.error("ADDITIONAL_PLUGS is not a valid JSON")
```

**verified** `ADDITIONAL_PLUGS` is read from the environment when
`PlugManager.__init__` runs — which happens at
`config/settings/base.py:19` (`from plug_config import manager`), i.e. **at
Django settings import time, in every process**.

**verified** Malformed JSON is logged and swallowed (`manager.py:27-28`). The
process starts with **zero** plugins rather than failing. **inferred** a typo in
`ADDITIONAL_PLUGS` silently disables every plugin instead of crashing — a
deployment hazard on Cloud Run, where the symptom would be missing endpoints
rather than a failed rollout.

### 2.2 Installation

**verified** `plugs/manager.py:31-35`:

```python
def install(self) -> None:
    packages = {f"{x.package_name}{x.version}" for x in self.plugs}
    if packages:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])
```

**verified** This runs `pip install` in a subprocess. It is invoked from
`install_plugins.py` at **image build time**, `docker/prod.Dockerfile:39`:

```dockerfile
ARG ADDITIONAL_PLUGS=""
ENV ADDITIONAL_PLUGS=$ADDITIONAL_PLUGS
RUN python3 $APP_HOME/install_plugins.py
```

**verified** `ADDITIONAL_PLUGS` is plumbed through compose as a build arg:
`docker-compose.local.yaml:7-8`.

**verified critical asymmetry:** `ADDITIONAL_PLUGS` is consumed in **two
different phases**:

| Phase | Consumer | Effect |
| --- | --- | --- |
| Image build | `install_plugins.py` → `manager.install()` | `pip install`s the packages |
| Every process start | `config/settings/base.py:19` → `PlugManager.__init__` | Adds them to `INSTALLED_APPS` |

**inferred** If the runtime value differs from the build-time value, Django lists
an app in `INSTALLED_APPS` that was never pip-installed, and the process dies at
startup with `ModuleNotFoundError`. On Cloud Run — where the image and the env
vars are configured independently — this is an easy misconfiguration. The
variable must be identical at build and deploy.

### 2.3 Integration points

**verified** Exactly three:

| Integration | Location | Mechanism |
| --- | --- | --- |
| Apps | `config/settings/base.py:142, 149` | `PLUGIN_APPS = manager.get_apps()`; appended to `INSTALLED_APPS` |
| Settings | `config/settings/base.py:146` | `PLUGIN_CONFIGS = manager.get_config()` |
| URLs | `config/urls.py:111-112` | `path(f"api/{plug}/", include(f"{plug}.urls"))` |

**verified** `config/urls.py:111-112` requires every plugin to expose a
`urls` module. There is no `try`/`except` — a plugin without `urls.py` raises at
import and the process fails to start.

**verified** `manager.get_config()` (`manager.py:41-49`) returns a
`defaultdict[str, dict]` keyed by plugin name. **unknown** how plugins read it;
no consumer of `PLUGIN_CONFIGS` exists in this repository beyond its definition.

**verified** `PlugConfig` is also a **database model** with its own viewset
(`care/users/api/viewsets/plug_config.py`), distinct from `PLUGIN_CONFIGS`.
Its `list` action is **unauthenticated** — `get_authenticators` returns `[]` for
`GET` (`plug_config.py:36-39`) — and the response is cached under
`care_plug_viewset_list` (`plug_config.py:14, 17-22`).

---

## 3. Impact assessment per risk category

Because `plugs = []`, every row below is about what a plugin **could** introduce,
not what one does today.

| Risk | Determinable here? | Assessment |
| --- | --- | --- |
| Celery tasks | **no** | `app.autodiscover_tasks()` (`config/celery_app.py:18`) scans every app in `INSTALLED_APPS`. Any plugin `tasks.py` is registered automatically and would need a Cloud Tasks route. |
| Redis dependencies | **no** | A plugin can import `django_redis` or call `cache.set(..., nx=True)` freely. Nothing constrains it. |
| Direct S3 / boto3 | **no** | `boto3` is a core dependency, importable by any plugin. |
| Signed URLs | **partly, since ES-01** | CARE no longer generates any. `S3FilesManager` is still importable from `care.emr.utils.file_manager` but exposes no signed-URL method — see §9. A plugin can still construct its own `boto3` client, so the guarantee is CARE's, not the platform's. |
| Custom health checks | **partly** | `HEALTHY_DJANGO` (`config/settings/base.py:453-467`) is a plain list. A plugin cannot append to it through the plug system — no hook exists. **inferred** plugins cannot register health checks. |
| Custom startup behavior | **no** | Standard Django `AppConfig.ready()` is available to any plugin app. |
| Additional migrations | **no** | Plugin apps are ordinary Django apps; their migrations run with `migrate`. Given §2 of `runtime-and-deployment.md`, they would run only in the Celery Beat container. |

**verified** The health-check row is the only category the plug system
structurally prevents. All others are wide open because plugins are just Django
apps with unrestricted imports.

---

## 4. What cannot be determined

**unknown**, and not determinable from this repository:

1. **Which plugins any given deployment runs.** Governed by `ADDITIONAL_PLUGS`,
   set outside version control.
2. **Whether known CARE plugins are GCP-compatible.** No plugin source is vendored.
3. **Plugin Celery task shapes** — payloads, retries, idempotency.
4. **Plugin storage usage** — buckets, signed URLs, direct object access.
5. **Plugin Redis usage** — locks, raw clients, pattern deletes.
6. **Plugin migration dependencies** on core CARE tables.
7. **What `PLUGIN_CONFIGS` keys mean**, since no consumer exists here.

**verified** The repository offers no manifest, lockfile or compatibility matrix
for plugins. `plug_config.py` is the only declaration point and it is empty.

**inferred** Any statement that "CARE plugins work on GCP" is unsupportable from
this repository. Each deployment's plugin set has to be inventoried separately,
using the same method applied here to core CARE.

---

## 5. Consequences for the GCP migration

**inferred**, flowing from verified facts above:

1. **The plugin system is a hole in every other inventory in this directory.**
   The storage, task and cache inventories are complete for *core CARE at this
   commit*. They are not complete for any deployment with plugins.

2. **`autodiscover_tasks` means plugin tasks appear without registration**
   (`config/celery_app.py:18`). A Cloud Tasks design that enumerates known tasks
   by hand will silently drop them.

3. **`ADDITIONAL_PLUGS` must match between build and deploy** (§2.2), and a JSON
   typo disables plugins silently (§2.1). Both deserve a startup assertion.

4. **`version` defaults to `@main`** (`plugs/plug.py:8`). Reproducible GCP builds
   require explicit pins.

5. **Plugins cannot contribute health checks** (§3), so the Cloud Run health
   endpoint stays under core control.

6. **Plugin migrations inherit the beat-only migration problem**
   (`runtime-and-deployment.md` §2). Whatever replaces beat must run them too.

**Recommendation (inferred):** treat the plugin set as an explicit input to the
GCP design. Before deploying, run this same inventory against each plugin the
target deployment actually installs. Document that set in
`07-configuration-reference.md` rather than assuming the empty default.

---

## 9. Storage API change in ES-01 (deprecation notice)

Recorded 2026-08-07.

**verified** `care.emr.utils.file_manager.S3FilesManager` — the one storage
symbol this inventory identified as plugin-reachable — still imports and still
works. It is now a **deprecated** subclass of `FilesManager`.

What changed:

| Aspect | Before | After |
| --- | --- | --- |
| Base | own class over `boto3` | `FilesManager`, delegating to Django Storage |
| Constructor argument | `BucketType.PATIENT` | `"PATIENT"` or `"patient"`; the old enum member still works via its `.value` |
| `put_object` / `get_object` / `delete_object` | boto3 calls, returned provider response dicts | Django Storage; return a name, a file object, or `None` |
| `put_object(**kwargs)` | passed provider kwargs through | replaced by an optional `content_type` |
| `file_contents` | `(content_type, bytes)` tuple | `bytes` |
| `delete_object(quiet=...)` | argument accepted | removed; deletion is idempotent |
| `signed_url` / `read_signed_url` | present | **removed** |
| Unknown bucket argument | n/a | raises `ValueError` |

**Importing it emits a `DeprecationWarning`.** Migrate to
`FilesManager("patient")` or, better,
`django.core.files.storage.storages["patient"]`.

**The signed-URL removal is deliberate and will not be restored.** ADR-0001
requires that no storage-provider URL reach a client. A plugin that needs to
hand a file to a browser should link to the CARE download route rather than mint
a bucket URL:

```http
GET /api/v1/files/{external_id}/download/
```

**verified** Nothing prevents a plugin from importing `boto3` itself and
generating its own presigned URL — `boto3` remains a core dependency for SNS.
The guarantee ES-01 establishes is that *CARE* generates none; it is not
enforced against plugin code. **Recommend** adding this to plugin review
criteria rather than attempting to block the import.

**unknown** Which plugins, if any, import `S3FilesManager`. No plugin source is
vendored here, so the shim is retained on the assumption that some do.

---

## 10. Task registration after ES-03

Recorded 2026-08-07. Section 5 item 2 predicted that "a Cloud Tasks design that
enumerates known tasks by hand will silently drop" plugin tasks. That is exactly
what ES-03 implements, and the prediction holds. This section states what a
plugin can and cannot do.

### 10.1 Celery is unchanged for plugins

**verified** `app.autodiscover_tasks()` (`config/celery_app.py:18`) still scans
every app in `INSTALLED_APPS`. A plugin's `tasks.py` is still registered
automatically, and a plugin still dispatches it however it did before -- ES-03
removed no Celery capability.

**verified** A plugin using `.delay()` on its own task continues to work under
`CARE_TASK_BACKEND=celery`, which is the default.

### 10.2 The core registry is closed

**verified** `care/utils/tasks/registry.py` resolves a task name through an
explicit dictionary populated by the modules named in `HANDLER_MODULES`, a
constant of that module. There is no `eval`, no `import_string` from a payload
and no scan of installed apps. `HANDLER_MODULES` currently lists exactly one
module: `care.emr.tasks.handlers`.

**inferred** Therefore a plugin task is **Celery-only**. Under
`CARE_TASK_BACKEND=cloud_tasks`, a plugin calling `enqueue_task("its_task", ...)`
receives `UnknownTaskError` at the call site -- which is at least a clear
failure at dispatch rather than a silent drop or a stuck queue.

### 10.3 How a plugin could register a handler

**verified** The mechanism a future phase would use already exists and is one
line. `register_task(name, handler=..., payload_model=..., celery_task=...)` is
importable, and `HANDLER_MODULES` is an ordinary tuple. Making it settings-driven
would let a plugin contribute handlers.

**Not done in ES-03, deliberately.** ADR-0003 says not to invent a plugin SDK,
and no plugin is bundled at this commit to validate a design against. Two
questions have to be answered first, and neither is answerable from this
repository:

1. **Name collisions.** Task names are a flat namespace. Two plugins registering
   `cleanup` would currently raise `ValueError` at import, failing startup. A
   plugin-facing registry probably needs namespacing.
2. **Trust.** Making `HANDLER_MODULES` configurable means an environment
   variable naming importable modules. That is a much weaker property than the
   current one -- today no configuration value can influence what is imported --
   and it interacts with the `ADDITIONAL_PLUGS` build/deploy asymmetry in §2.2.

### 10.4 The failure mode plugins should be warned about

**verified** ES-03 hit it in core code. `autodiscover_tasks` imports an app's
`tasks` module and **goes no deeper**, so for a `tasks/` *package* only
`__init__.py` runs. Two CARE wrappers were registered purely as a side effect of
viewsets importing them; when those imports went away, a live worker registered
three of six task names and dispatching either missing one would have failed
with `NotRegistered`.

**Recommended plugin review criterion:** a plugin whose `tasks` is a package must
import every wrapper in its `__init__.py`. Relying on an unrelated import is not
registration, and the symptom appears only at dispatch time.

### 10.5 Summary for a deployment

| Scenario | Supported |
| --- | --- |
| Plugin Celery task, `CARE_TASK_BACKEND=celery` | yes, unchanged |
| Plugin Celery task, `CARE_TASK_BACKEND=cloud_tasks` | **no** -- `UnknownTaskError` at dispatch |
| Plugin calling core `enqueue_task` with a core task name | yes, both backends |
| Plugin registering its own handler | not yet; see §10.3 |
| Plugin contributing a Celery Beat schedule | yes, unchanged |
| Plugin reaching the internal worker endpoint | no -- the route serves registered core names only |

**unknown**, unchanged from §4: which plugins any deployment runs, and whether
any of them define tasks at all. That has to be inventoried per deployment.

---

## 11. Cache impact after ES-04

Recorded 2026-08-09. ES-04 §26 asked for plugin cache assumptions to be
reviewed and classified.

**verified** `plug_config.py` declares `plugs = []`. No plugin is enabled in this
fork, and `plugs/manager.py` and `plugs/plug.py` contain no cache or Redis
reference of any kind — a grep for `cache` and `redis` across `plugs/` and
`plug_config.py` returns nothing. The loader is provider-neutral.

So the classification ES-04 asked for is, for this repository:

| Class | Plugins |
| --- | --- |
| provider-neutral | the plugin loader itself |
| Redis-required | none enabled |
| unknown | any plugin a downstream deployment adds |

**inferred** For a plugin added later, the rule follows from what it imports,
and no new SDK was introduced to mediate it:

- a plugin using `django.core.cache` inherits `CARE_CACHE_BACKEND` automatically
  and stays portable, provided it restricts itself to the portable API;
- a plugin importing `django_redis`, calling `get_redis_connection`, or using
  `delete_pattern` is **Redis-required**, and will fail under
  `CARE_CACHE_BACKEND=postgres`, `locmem` or `dummy`;
- a plugin calling `cache.set(..., nx=True)` on the `default` cache now raises
  `TypeError` under every non-Redis backend rather than silently not locking.
  That change is deliberate and is the safer failure: before ES-04 the shim
  returned `True` and the plugin would have believed it held a lock.

**verified** The import boundary is enforced by a test rather than by convention.
`care/utils/tests/test_cache_config.py::DirectRedisBoundaryTests` walks `care/`
and `config/` and fails on any `django_redis` import outside a named allowlist.
It deliberately does **not** scan `plugs/`, because plugins are third-party code
and ES-04 §32 says not to prohibit those imports globally while unresolved
responsibilities still legitimately use them.

**Not changed:** plugin Celery behaviour, and no generic plugin cache SDK was
introduced. Both were explicitly out of scope.

**unknown**, unchanged from §4: which plugins any given deployment runs. A
deployment adding one has to check it against the three rules above itself.

---

## 12. Runtime roles after ES-06

Recorded 2026-08-09. ES-06 §39 asks for plugin assumptions about URL
registration, startup, Celery, process role, `AppConfig.ready()` and
autodiscovery to be reviewed and classified.

**verified** `plug_config.py` still declares `plugs = []`, so as in every
previous section this is a statement about what a plugin *would* encounter, not
about an enabled one. A grep across `plugs/` and `plug_config.py` for
`CARE_PROCESS_ROLE`, `urls`, `ready` and `celery` returns nothing: the loader is
role-neutral and was not changed.

### 12.1 The change that affects plugins

**verified** `config/urls.py` registers plugin URLs for the `api` role only:

```python
if settings.CARE_PROCESS_ROLE == API_ROLE:
    for plug in settings.PLUGIN_APPS:
        urlpatterns += [path(f"api/{plug}/", include(f"{plug}.urls"))]
```

Before ES-06 the loop ran in every process. Plugin routes are public API
surface, so they follow the public API rather than being mounted in a task
worker that must not serve it.

**verified** Everything else a plugin touches is unchanged. Plugin apps are
still in `INSTALLED_APPS` in every role, `AppConfig.ready()` still runs in every
process, `PLUGIN_CONFIGS` is still built at settings import, and
`app.autodiscover_tasks()` still scans every installed app.

### 12.2 Classification

Using the categories ES-06 §39 asks for:

| Plugin behaviour | Class | Why |
| --- | --- | --- |
| Django app with models, viewsets and `urls.py` | **compatible** | routes serve under `api`; models and migrations are role-independent |
| `AppConfig.ready()` doing registration, signal wiring, checks | **compatible** | `ready()` runs in all four roles, unchanged |
| Celery task defined in the plugin's `tasks` module | **compatible** | `autodiscover_tasks` is unchanged; a `task_worker` running Celery executes it |
| Reading `PLUGIN_CONFIGS` or `settings` | **compatible** | settings are identical across roles apart from role-derived values |
| Calling `reverse()` on a **core** CARE URL from a task handler | **API-only assumption** | core public URLs do not resolve under `task_worker`; see §12.3 |
| Calling `reverse()` on its **own** URL from a task handler | **API-only assumption** | plugin URLs now register under `api` only |
| Rendering a template that contains `{% url %}` for a public route | **API-only assumption** | same mechanism |
| Expecting its endpoints to answer on the worker service | **worker-incompatible** | by design: the worker serves the task endpoint and diagnostics |
| Contributing a Celery Beat schedule entry | **compatible** | beat is the `scheduler` role and is otherwise unchanged |
| Assuming beat startup migrates the database | **scheduler-incompatible** | beat no longer initializes; see `runtime-and-deployment.md` §15.2 |
| Registering a health check | **unknown, still impossible** | `HEALTHY_DJANGO` is now built by `config.health.build_health_checks` and still has no plugin hook |
| Anything a downstream deployment installs | **unknown** | unchanged from §4 |

### 12.3 The one real hazard, and how core avoids it

**verified** Core CARE has the same exposure and it was checked rather than
assumed: no task handler in `care/emr/tasks/` calls `reverse()` or
`reverse_lazy()`. The one reverse that could reach a worker is `{% url 'home' %}`
in `care/templates/base.html`, which every error template extends — so `home` is
deliberately part of the diagnostic route set served by every role. Without it a
worker would answer an ordinary 404 with `NoReverseMatch`.

**Recommended plugin review criterion**, alongside the ones in §10.4 and §11: a
plugin task handler must not build URLs. If it needs an absolute link for an
email or a document, it should take the site URL from configuration rather than
from the URLconf of the process that happens to be executing it.

### 12.4 Not built

No plugin runtime SDK, no role hook, no way for a plugin to opt into worker
routing. ES-06 §39 says not to build one, and with no plugin bundled there is
nothing to validate a design against. A plugin that genuinely needs a
worker-side route is the case that should motivate the design, and none exists.
