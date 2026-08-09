from django.db import transaction
from django.db.models import Sum

from care.emr.locks.billing import AccountLock
from care.emr.models.account import Account
from care.emr.models.charge_item import ChargeItem
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationOutcomeOptions,
    PaymentReconciliationStatusOptions,
)
from care.utils.rounding.rounding import care_round
from care.utils.time_util import care_now


def calculate_payment_reconciliation_summary(payment_reconciliations):
    return care_round(payment_reconciliations.aggregate(total=Sum("amount"))["total"])


def calculate_charge_item_amount_sum(charge_items):
    return care_round(charge_items.aggregate(total=Sum("total_price"))["total"])


def sync_account_items(account: Account):
    with transaction.atomic(), AccountLock(account):
        charge_items_base = ChargeItem.objects.filter(
            account=account,
        )
        charge_items = charge_items_base.filter(
            status__in=[
                ChargeItemStatusOptions.paid.value,
                ChargeItemStatusOptions.billed.value,
            ],
        )
        billable_charge_items = charge_items_base.filter(
            status=ChargeItemStatusOptions.billable.value,
        )
        payment_reconciliations = PaymentReconciliation.objects.filter(
            account=account,
            status=PaymentReconciliationStatusOptions.active.value,
            outcome=PaymentReconciliationOutcomeOptions.complete.value,
            is_credit_note=False,
        )
        credit_note_payment_reconciliations = PaymentReconciliation.objects.filter(
            account=account,
            status=PaymentReconciliationStatusOptions.active.value,
            outcome=PaymentReconciliationOutcomeOptions.complete.value,
            is_credit_note=True,
        )
        # charge_items_summary = calculate_charge_items_summary(charge_items)
        account.total_billable_charge_items = calculate_charge_item_amount_sum(
            billable_charge_items
        )
        account.total_gross = calculate_charge_item_amount_sum(charge_items)

        payment_reconciliation_total = calculate_payment_reconciliation_summary(
            payment_reconciliations
        )
        credit_note_payment_reconciliation_total = (
            calculate_payment_reconciliation_summary(
                credit_note_payment_reconciliations
            )
        )
        # account.cached_items = charge_items_summary["charge_items_copy"]
        # account.total_net = charge_items_summary["net"]
        # account.total_gross = charge_items_summary["gross"]
        account.total_paid = care_round(
            payment_reconciliation_total - credit_note_payment_reconciliation_total
        )
        account.total_balance = care_round(account.total_gross - account.total_paid)
        # account.total_price_components = json.loads(
        #     json.dumps(
        #         charge_items_summary["total_price_components"],
        #         cls=DjangoJSONEncoder,
        #     )
        # ) # Serialize Decimal properly
        account.calculated_at = care_now()


def rebalance_account(account_id):
    """
    Recalculate an account's balances from its charge items and payments.

    Every one of the twelve call sites invokes this inline, inside the request
    transaction, and none ever dispatched it -- the ``@app.task()`` decorator it
    carried before ADR-0003 was aspirational. It stays synchronous deliberately:
    these are financial balances, and making them eventually consistent would
    expose intermediate totals to reads that happen between the write and the
    recalculation.
    """
    account = Account.objects.get(id=account_id)
    sync_account_items(account)
    account.save()
