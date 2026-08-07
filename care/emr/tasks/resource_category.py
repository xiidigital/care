"""
Celery wrapper for monetary-component summarisation.

The operation itself lives with the model it recomputes
(``care.emr.models.resource_category``) because two request-path callers invoke
it inline. Only the recursive fan-out over child categories is dispatched, and
only that needs a wrapper.

The wrapper lives here rather than beside the operation so that the model module
does not import Celery. The explicit ``name`` preserves the task name the
decorator produced while it sat in the models module, so a worker draining a
queue written by an older revision still recognises it.
"""

from celery import shared_task

from care.emr.models.resource_category import summarise_monetary_components


@shared_task(name="care.emr.models.resource_category.summarise_monetary_components")
def summarise_monetary_components_task(category_id: int) -> None:
    """Celery wrapper. Accepts a primary key only; an instance is not serializable."""
    summarise_monetary_components(category_id)
