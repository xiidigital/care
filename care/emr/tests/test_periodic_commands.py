"""
The periodic operations as management commands.

The point of these tests is that each command runs to completion with no
broker, no worker and no scheduler in the picture -- that is what makes the
operation available to a Cloud Run Job, and what makes Celery Beat one of
several callers rather than the only one.
"""

from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from model_bakery import baker

from care.emr.models import FileUpload, TokenSlot
from care.emr.models.scheduling.schedule import SchedulableResource
from care.emr.resources.scheduling.schedule.spec import (
    SchedulableResourceTypeOptions,
)
from care.utils.tests.base import CareAPITestBase


def run(command: str) -> str:
    out = StringIO()
    # call_command raises SystemExit only on failure, so returning normally is
    # the success exit status a Cloud Run Job observes.
    call_command(command, stdout=out)
    return out.getvalue()


class CleanupExpiredTokenSlotsCommandTests(CareAPITestBase):
    COMMAND = "cleanup_expired_token_slots"

    def setUp(self):
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.resource = baker.make(
            SchedulableResource,
            facility=self.facility,
            user=self.user,
            resource_type=SchedulableResourceTypeOptions.practitioner.value,
        )

    def test_it_succeeds_on_empty_state(self):
        self.assertIn("0", run(self.COMMAND))

    def test_it_deletes_expired_unbooked_slots(self):
        expired = self.make_slot(timezone.now() - timedelta(days=1))

        self.assertIn("1", run(self.COMMAND))

        self.assertFalse(TokenSlot.objects.filter(id=expired.id).exists())

    def test_it_leaves_future_slots_alone(self):
        upcoming = self.make_slot(timezone.now() + timedelta(days=1))

        run(self.COMMAND)

        self.assertTrue(TokenSlot.objects.filter(id=upcoming.id).exists())

    def test_running_it_twice_succeeds(self):
        self.make_slot(timezone.now() - timedelta(days=1))
        run(self.COMMAND)
        self.assertIn("0", run(self.COMMAND))

    def make_slot(self, end_datetime):
        return TokenSlot.objects.create(
            resource=self.resource,
            start_datetime=end_datetime - timedelta(hours=1),
            end_datetime=end_datetime,
        )


class CleanupIncompleteFileUploadsCommandTests(CareAPITestBase):
    COMMAND = "cleanup_incomplete_file_uploads"

    def test_it_succeeds_on_empty_state(self):
        self.assertIn("0", run(self.COMMAND))

    def test_it_leaves_completed_uploads_alone(self):
        completed = baker.make(
            FileUpload,
            upload_completed=True,
            internal_name="",
        )
        FileUpload.objects.filter(id=completed.id).update(
            created_date=timezone.now()
            - timedelta(hours=settings.FILE_UPLOAD_EXPIRY_HOURS + 1)
        )

        run(self.COMMAND)

        self.assertTrue(FileUpload.objects.filter(id=completed.id).exists())

    def test_it_leaves_recent_incomplete_uploads_alone(self):
        recent = baker.make(FileUpload, upload_completed=False, internal_name="")

        run(self.COMMAND)

        self.assertTrue(FileUpload.objects.filter(id=recent.id).exists())

    def test_running_it_twice_succeeds(self):
        run(self.COMMAND)
        self.assertIn("0", run(self.COMMAND))
