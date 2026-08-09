from django.conf import settings

from care.utils.lock import Lock


class InventoryLock(Lock):
    def __init__(self, product, location, timeout=settings.LOCK_TIMEOUT):
        super().__init__(f"location:{location.id}:product:{product.id}", timeout)
