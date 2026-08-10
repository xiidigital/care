"""
Per-user "recently viewed codes" lists, persisted in PostgreSQL.

**This is not ADR-0004 cache and must not be treated as such.** It is durable
per-user state with its own model, isolated behind this service boundary so
callers stay unaware of how it is stored (RF1).

It keeps a bounded most-recently-used list per user and valueset. It used to do
that with Redis ``LPUSH``/``LTRIM``/``LREM`` on a dedicated ``recent_views``
cache alias; RF1 replaced that with :class:`UserValueSetRecentView`. Recency is
an ordinary ``ORDER BY last_viewed_at DESC``, de-duplication is a unique
constraint rather than a scan-and-remove, and the bound is enforced by deleting
everything outside the retained window.

Consequence, recorded deliberately: recent views no longer require Redis in any
configuration, and a deployment with ``CARE_CACHE_BACKEND=postgres`` keeps this
feature. Existing Redis recent-view state was **not** migrated -- it is
ephemeral, non-critical user convenience state, and the new table starts empty.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from care.emr.models.valueset import UserValueSetRecentView


class RecentViewsManager:
    """
    Service boundary for recent views.

    Callers pass domain objects -- a user and a valueset -- and receive plain
    dicts in the ``MinimalCodeConcept`` shape. They never see the model.
    """

    MAX_RECENT_VIEW = getattr(settings, "MAX_RECENT_VIEW_FOR_VALUESET", 20)

    @classmethod
    def _scoped(cls, user, valueset):
        return UserValueSetRecentView.objects.filter(user=user, valueset=valueset)

    @staticmethod
    def _serialize(entry):
        # Field order matches MinimalCodeConcept.model_dump(), so the rendered
        # JSON is byte-for-byte what the Redis implementation returned.
        return {
            "display": entry.display,
            "system": entry.system,
            "code": entry.code,
            "designation": entry.designation,
        }

    @classmethod
    def _trim(cls, user, valueset):
        """
        Drop everything outside the newest ``MAX_RECENT_VIEW`` entries.

        The retained window is a sliced subquery, so at most one bounded id set
        is ever materialised -- by PostgreSQL, inside the ``DELETE``, never in
        Python. The ordering matches :meth:`get_recent_views` exactly, so the
        rows that survive are precisely the ones a read would have returned.
        """
        retained = (
            cls._scoped(user, valueset)
            .order_by("-last_viewed_at", "-id")
            .values_list("id", flat=True)[: cls.MAX_RECENT_VIEW]
        )
        cls._scoped(user, valueset).exclude(id__in=retained).delete()

    @classmethod
    def get_recent_views(cls, user, valueset):
        """Return the scope's entries, most recently viewed first."""
        return [
            cls._serialize(entry)
            for entry in cls._scoped(user, valueset).order_by("-last_viewed_at", "-id")[
                : cls.MAX_RECENT_VIEW
            ]
        ]

    @classmethod
    def add_recent_view(cls, user, valueset, code_obj):
        """
        Record a view, moving the code to the most-recent position.

        ``update_or_create`` under the unique constraint is what makes a repeat
        view an update rather than a second row: two concurrent requests for the
        same code cannot both insert, because the loser's ``IntegrityError`` is
        caught and retried as a fetch. The trim runs in the same transaction so
        a write is never observable as an over-long list.
        """
        code = code_obj.get("code")
        if not code:
            return

        with transaction.atomic():
            UserValueSetRecentView.objects.update_or_create(
                user=user,
                valueset=valueset,
                code=code,
                defaults={
                    "system": code_obj.get("system", ""),
                    "display": code_obj.get("display", ""),
                    "designation": code_obj.get("designation"),
                    "last_viewed_at": timezone.now(),
                },
            )
            cls._trim(user, valueset)

    @classmethod
    def remove_recent_view(cls, user, valueset, code_obj):
        """Remove one code from the scope. A code that is not there is not an error."""
        code = code_obj.get("code")
        if not code:
            return
        cls._scoped(user, valueset).filter(code=code).delete()

    @classmethod
    def clear_recent_views(cls, user, valueset):
        """Remove every entry in the scope."""
        cls._scoped(user, valueset).delete()
