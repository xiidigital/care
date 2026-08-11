"""
Database routing for the PostgreSQL rate-limit counter.

This exists because of one interaction that is invisible until it is looked for.

``DATABASES["default"]["ATOMIC_REQUESTS"]`` is True, so every request runs in a
transaction. Django REST Framework's exception handler calls ``set_rollback()``
whenever it converts an ``APIException`` into a response, which marks that
transaction for rollback. A failed login raises ``AuthenticationFailed``, so the
whole request is rolled back -- including, when the rate-limit counter lives in
a ``DatabaseCache``, the increment that was supposed to record the attempt.

The result is a limiter that counts successful requests and forgets failed ones.
For a limiter whose entire purpose is throttling repeated failed logins, MFA
challenges and password-reset attempts, that is not a weaker guarantee; it is no
guarantee. Redis never had the problem, because its counters were never in the
database in the first place.

The fix is the extension point Django's own ``DatabaseCache`` implementation
points at. From ``django/core/cache/backends/db.py``::

    class Options:
        \"\"\"A class that will quack like a Django model _meta class.

        This allows cache operations to be controlled by the router
        \"\"\"

So the counter is given a connection of its own -- same database, separate
connection, ``ATOMIC_REQUESTS`` off -- and its writes commit on their own terms
rather than riding on the fate of the request that made them. No custom cache
backend, no raw SQL, no patched Django: one router and one extra alias.

**Scope is deliberately narrow.** Only the rate-limit table is routed, matched
by table name. The ordinary ``default`` cache is left on the request's own
connection even when ``CARE_CACHE_BACKEND=postgres``, because that is a
pre-existing ADR-0004 behaviour with a different risk profile -- a cache entry
lost to a rolled-back request is a cache miss, and changing it is not RF2's
call to make. It is recorded in inventory/unresolved-items.md instead.

Under ``CARE_RATE_LIMIT_BACKEND=redis`` or ``disabled`` this router is not
installed and no second alias exists.
"""

from django.conf import settings

#: The ``DATABASES`` alias the rate-limit counter is routed to. Same database as
#: ``default``; what differs is that it is a separate connection with
#: ``ATOMIC_REQUESTS`` disabled.
RATELIMIT_DB_ALIAS = "ratelimit"

#: ``django.core.cache.backends.db.Options`` sets this on every ``DatabaseCache``
#: model stub, whichever alias it belongs to. It identifies cache traffic, but
#: not *which* cache, which is why the table name is checked too.
CACHE_APP_LABEL = "django_cache"


class RateLimitCacheRouter:
    """Send rate-limit cache reads and writes to :data:`RATELIMIT_DB_ALIAS`."""

    def _is_rate_limit_cache(self, model) -> bool:
        meta = getattr(model, "_meta", None)
        if getattr(meta, "app_label", None) != CACHE_APP_LABEL:
            return False
        # Every DatabaseCache alias shares one stub class and one model name, so
        # the table is the only thing that distinguishes the rate-limit cache
        # from the ordinary one. Matching on app_label alone would silently
        # capture the default cache as well.
        return meta.db_table == settings.CARE_RATE_LIMIT_TABLE

    def db_for_read(self, model, **hints):
        if self._is_rate_limit_cache(model):
            return RATELIMIT_DB_ALIAS
        return None

    def db_for_write(self, model, **hints):
        if self._is_rate_limit_cache(model):
            return RATELIMIT_DB_ALIAS
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # Cache entries have no relations, and both aliases are the same
        # database, so this router has no opinion.
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # No opinion, on purpose. `createcachetable` asks
        # `router.allow_migrate_model(db, ...)` with db="default", and the two
        # aliases address the same physical database, so the existing
        # initialization step creates the table exactly as it did before.
        # Returning False for "default" here would break `scripts/initialize.sh`
        # for no benefit; returning True would claim an opinion about every
        # other app's migrations.
        return None
