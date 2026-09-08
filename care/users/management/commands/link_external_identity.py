"""Enrol one external identity from the command line (ADR-0011 §5.2).

The scripted half of administrative linking, for an operator populating a new
provider from a roster rather than clicking through the admin one user at a
time.

It refuses more than it accepts, and deliberately takes a subject rather than
discovering one: there is no lookup by email here, because a link chosen from a
claim is the takeover this whole design exists to prevent.
"""

from django.core.management.base import BaseCommand, CommandError

from care.users.models import User
from config.oidc import WORKFORCE, providers_for
from config.oidc_identity import IdentityAlreadyLinkedError, link_workforce_identity


class Command(BaseCommand):
    help = "Link an external OIDC subject to an existing CARE user."

    def add_arguments(self, parser):
        parser.add_argument("--provider-id", required=True, dest="provider_id")
        parser.add_argument("--username", required=True)
        parser.add_argument("--subject", required=True)
        parser.add_argument(
            "--linked-by",
            dest="linked_by",
            help="Username of the administrator performing the enrolment.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        provider = next(
            (
                candidate
                for candidate in providers_for(settings.OIDC_PROVIDERS, WORKFORCE)
                if candidate.id == options["provider_id"]
            ),
            None,
        )
        if provider is None:
            msg = (
                f"No enabled workforce provider with id "
                f"'{options['provider_id']}'. Configure it in OIDC_PROVIDERS first."
            )
            raise CommandError(msg)

        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            msg = f"No such user: {options['username']}"
            raise CommandError(msg) from exc

        linked_by = None
        if options.get("linked_by"):
            try:
                linked_by = User.objects.get(username=options["linked_by"])
            except User.DoesNotExist as exc:
                msg = f"No such administrator: {options['linked_by']}"
                raise CommandError(msg) from exc

        try:
            link_workforce_identity(
                user=user,
                provider=provider,
                subject=options["subject"],
                linked_by=linked_by,
            )
        except IdentityAlreadyLinkedError as exc:
            msg = (
                "That subject is already linked under this provider. Unlink it "
                "first if the enrolment is genuinely being moved (ADR-0011 §5.4)."
            )
            raise CommandError(msg) from exc

        # The subject is not echoed: this output reaches shell history and CI.
        self.stdout.write(
            self.style.SUCCESS(f"Linked {user.username} to provider '{provider.id}'.")
        )
