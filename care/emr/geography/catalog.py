"""Utilities for resolving geography from django-cities-light records."""

from typing import Any


def serialize_catalog_node(node: Any, *, level: str) -> dict | None:
    if node is None:
        return None
    return {
        "id": node.id,
        "name": node.name,
        "level": level,
        "country_code": getattr(getattr(node, "country", None), "code2", None)
        if level != "country"
        else node.code2,
    }
