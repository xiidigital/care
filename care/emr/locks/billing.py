from django.conf import settings

from care.utils.lock import Lock


class AccountLock(Lock):
    def __init__(self, account, timeout=settings.LOCK_TIMEOUT):
        super().__init__(f"account:{account.id}", timeout)


class InvoiceLock(Lock):
    def __init__(self, invoice, timeout=settings.LOCK_TIMEOUT):
        super().__init__(f"invoice:{invoice.id}", timeout)


class InvoiceCreateLock(Lock):
    def __init__(self, timeout=settings.LOCK_TIMEOUT):
        super().__init__("create_invoice", timeout)


class PatientCreateLock(Lock):
    def __init__(self, timeout=settings.LOCK_TIMEOUT):
        super().__init__("create_patient", timeout)


class ChargeItemLock(Lock):
    def __init__(self, charge_item, timeout=settings.LOCK_TIMEOUT):
        super().__init__(f"charge_item:{charge_item.id}", timeout)


class InventoryItemLock(Lock):
    def __init__(self, inventory_item, timeout=settings.LOCK_TIMEOUT):
        super().__init__(f"inventory_item:{inventory_item.id}", timeout)
