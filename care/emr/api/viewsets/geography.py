from django.db.models import Q
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet


class GeographyCatalogViewSet(ViewSet):
    """Read-only, cascading access to the imported django-cities-light data."""

    authentication_classes = []
    permission_classes = []

    def list(self, request):
        from cities_light.models import City, Country, Region, SubRegion

        level = request.query_params.get("level", "country")
        parent_id = request.query_params.get("parent")
        search = request.query_params.get("search", "").strip()
        models = {
            "country": Country,
            "region": Region,
            "subregion": SubRegion,
            "city": City,
        }
        if level not in models:
            return Response({"detail": "Invalid geography level"}, status=400)

        queryset = models[level].objects.all()
        parent_field = {
            "region": "country_id",
            "subregion": "region_id",
            "city": "subregion_id",
        }.get(level)
        if parent_field:
            if not parent_id:
                return Response(
                    {"detail": f"A parent is required for {level}"}, status=400
                )
            queryset = queryset.filter(**{parent_field: parent_id})
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(display_name__icontains=search)
            )

        try:
            limit = min(max(int(request.query_params.get("limit", 100)), 1), 200)
        except ValueError:
            return Response({"detail": "Limit must be an integer"}, status=400)
        results = []
        for node in queryset.order_by("name")[:limit]:
            country = node if level == "country" else node.country
            results.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "display_name": getattr(node, "display_name", node.name),
                    "level": level,
                    "country_code": country.code2,
                }
            )
        return Response({"count": len(results), "results": results})
