"""Run expired-token-slot cleanup without a worker or a scheduler."""

from django.core.management.base import BaseCommand

from care.emr.tasks.cleanup_expired_token_slots import cleanup_expired_token_slots


class Command(BaseCommand):
    help = "Hard-delete expired token slots that have no booking."

    def handle(self, *args, **options):
        deleted = cleanup_expired_token_slots()
        self.stdout.write(f"Deleted {deleted} expired token slots")
