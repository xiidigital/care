"""
Per-user "recently viewed codes" lists.

**This is not ADR-0004 cache and must not be treated as such.** It is a
Redis-specific component, isolated here so that ordinary cache consumers stay
provider-neutral (ES-04 section 15).

It keeps a bounded most-recently-used list per user and valueset, built from
``LPUSH`` + ``LTRIM`` with ``LREM`` for de-duplication. Django's cache API has no
list primitives, and emulating one by reading a JSON blob, editing it and writing
it back would lose the atomicity ``LPUSH``/``LTRIM`` provide -- concurrent views
from two requests would silently drop entries. So this was not migrated to the
configurable cache; it was moved out of ``care/emr/models/valueset.py``, where a
module-level ``django_redis`` import made the whole models package depend on
Redis, and given its own alias.

Consequence, recorded deliberately: selecting ``CARE_CACHE_BACKEND=postgres``
does **not** make this feature work without Redis. It still needs one, and says
so. Replacing it with an explicit PostgreSQL model is a schema change and is
tracked in ``unresolved-items.md``, not attempted here.
"""

import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django_redis import get_redis_connection

from config.caches import RECENT_VIEWS_CACHE_ALIAS


class RecentViewsManager:
    _client = None
    MAX_RECENT_VIEW = getattr(settings, "MAX_RECENT_VIEW_FOR_VALUESET", 20)

    @classmethod
    def get_client(cls):
        if cls._client is None:
            try:
                cls._client = get_redis_connection(RECENT_VIEWS_CACHE_ALIAS)
            except (NotImplementedError, AttributeError) as e:
                # Raised when the alias is not backed by django_redis. Say what
                # is missing rather than surfacing a bare NotImplementedError
                # from deep inside the cache layer.
                msg = (
                    "Recent views require a Redis-backed "
                    f"CACHES[{RECENT_VIEWS_CACHE_ALIAS!r}] alias. This feature "
                    "is not part of CARE_CACHE_BACKEND and has no portable "
                    "backend yet."
                )
                raise ImproperlyConfigured(msg) from e
        return cls._client

    @classmethod
    def _remove_by_code(cls, cache_key, code):
        client = cls.get_client()
        current_items = client.lrange(cache_key, 0, -1)

        for item in current_items:
            try:
                item_dict = json.loads(item)
                if item_dict.get("code") == code:
                    client.lrem(cache_key, 0, item)
            except Exception:  # noqa: S112
                continue

    @classmethod
    def get_recent_views(cls, cache_key):
        client = cls.get_client()
        items = client.lrange(cache_key, 0, -1)
        return [json.loads(item.decode()) for item in items]

    @classmethod
    def add_recent_view(cls, cache_key, code_obj):
        code = code_obj.get("code")
        if not code:
            return

        cls._remove_by_code(cache_key, code)

        client = cls.get_client()
        code_json = json.dumps(code_obj)
        client.lpush(cache_key, code_json)
        client.ltrim(cache_key, 0, cls.MAX_RECENT_VIEW - 1)

    @classmethod
    def remove_recent_view(cls, cache_key, code_obj):
        code = code_obj.get("code")
        if not code:
            return
        cls._remove_by_code(cache_key, code)

    @classmethod
    def clear_recent_views(cls, cache_key):
        client = cls.get_client()
        client.delete(cache_key)
