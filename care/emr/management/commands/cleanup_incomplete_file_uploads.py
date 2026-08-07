"""Run incomplete-upload cleanup without a worker or a scheduler."""

from django.core.management.base import BaseCommand

from care.emr.tasks.cleanup_incomplete_file_uploads import (
    cleanup_incomplete_file_uploads,
)


class Command(BaseCommand):
    help = "Hard-delete file uploads that were never completed."

    def handle(self, *args, **options):
        deleted = cleanup_incomplete_file_uploads()
        self.stdout.write(f"Deleted {deleted} incomplete file uploads")
