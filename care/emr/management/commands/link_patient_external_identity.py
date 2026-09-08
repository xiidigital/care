"""Enrol one external identity against a patient record (ADR-0011 §5.3).

CARE registers no `Patient` admin, so this command is the administrative path
for patient linking rather than a convenience alongside one. That is stated
here because it matters: rule §5.3 keeps patient linking off the self-service
route precisely so CARE's existing custody rules decide who may attach an
identity to a clinical record, and a command run by an operator is currently
where that judgement is exercised.

`--linked-by` is required for the same reason. A patient link with no recorded
actor is a link nobody is accountable for.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from care.emr.models import Patient
from care.users.models import User
from config.oidc import PATIENT, providers_for
from config.oidc_identity import IdentityAlreadyLinkedError, link_patient_identity


class Command(BaseCommand):
    help = "Link an external OIDC subject to an existing patient record."

    def add_arguments(self, parser):
        parser.add_argument("--provider-id", required=True, dest="provider_id")
        parser.add_argument(
            "--patient",
            required=True,
            help="The patient's external id (UUID), not a name or a contact.",
        )
        parser.add_argument("--subject", required=True)
        parser.add_argument(
            "--linked-by",
            required=True,
            dest="linked_by",
            help="Username of the administrator accountable for this enrolment.",
        )

    def handle(self, *args, **options):
        provider = next(
            (
                candidate
                for candidate in providers_for(settings.OIDC_PROVIDERS, PATIENT)
                if candidate.id == options["provider_id"]
            ),
            None,
        )
        if provider is None:
            msg = (
                f"No enabled patient provider with id '{options['provider_id']}'. "
                "Configure it in OIDC_PROVIDERS first."
            )
            raise CommandError(msg)

        try:
            patient = Patient.objects.get(external_id=options["patient"])
        except (Patient.DoesNotExist, ValueError, ValidationError) as exc:
            # Deliberately not "no patient with that id": this runs against
            # real clinical records and should not confirm which ids exist.
            msg = "No such patient."
            raise CommandError(msg) from exc

        try:
            administrator = User.objects.get(username=options["linked_by"])
        except User.DoesNotExist as exc:
            msg = f"No such administrator: {options['linked_by']}"
            raise CommandError(msg) from exc

        try:
            link_patient_identity(
                patient=patient,
                provider=provider,
                subject=options["subject"],
                linked_by=administrator,
            )
        except IdentityAlreadyLinkedError as exc:
            msg = (
                "That subject is already linked under this provider. Unlink it "
                "first if the enrolment is genuinely being moved (ADR-0011 §5.4)."
            )
            raise CommandError(msg) from exc

        # Neither the subject nor the patient's name is echoed.
        self.stdout.write(
            self.style.SUCCESS(f"Linked a patient to provider '{provider.id}'.")
        )
