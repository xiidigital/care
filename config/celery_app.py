import os

from celery import Celery
from celery.signals import beat_init, celeryd_init

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("care")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

app.conf.update(enable_utc=False, timezone="Asia/Kolkata")
# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


# ADR-0006: the two Celery processes are runtime roles like any other -- a
# worker carries `task_worker` and beat carries `scheduler` -- and each should
# say so once at startup. Connected as signals rather than called at import,
# because this module is also imported by the API to dispatch work, and the API
# announces itself from config/wsgi.py instead.
#
# `celeryd_init` rather than `worker_ready`: it fires as the worker process
# starts, before the consumer blueprint, so the line appears even where the
# blueprint does not finish announcing itself -- which is the case in this
# repository's local Docker environment, where Celery's own "ready" line is
# also absent (recorded in inventory/runtime-and-deployment.md).
@celeryd_init.connect
@beat_init.connect
def _log_runtime_summary(**kwargs):
    import django

    # These signals fire before Celery's Django fixup has necessarily run, and
    # `LOGGING` is applied by `django.setup()`. Without it the record reaches a
    # logger with no handler and is dropped -- the line would be silently
    # missing exactly where it is most useful. `setup()` returns immediately
    # once the app registry is populated, so calling it here is not a second
    # initialization.
    django.setup()

    from config.runtime import log_runtime_summary

    log_runtime_summary()
