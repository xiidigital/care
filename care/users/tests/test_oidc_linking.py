"""Linking an external identity to a CARE principal (ADR-0011 §5).

This is the security-critical operation in the design. Authentication only
asks which external subject is present; linking is what decides whose account
that subject reaches, and once a link exists the exchange will honour it
forever.

So the rules here are refusals more than features. No claim creates a link. A
triple already linked is never quietly moved. A self-service link binds to the
principal that is already authenticated, never to one named in the request. A
patient link is administrative, because CARE's existing custody rules -- not
the patient's browser -- decide who may attach an identity to a clinical
record.
"""

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from model_bakery import baker
from rest_framework.test import APIRequestFactory, force_authenticate

from care.emr.models import Patient, PatientExternalIdentity
from care.users.models import User, UserExternalIdentity
from config.oidc import OidcProvider
from config.oidc_identity import (
    IdentityAlreadyLinkedError,
    link_patient_identity,
    link_workforce_identity,
    unlink_workforce_identity,
)
from config.oidc_views import OidcLinkView

ISSUER = "https://identity.example/realms/care"

WORKFORCE_PROVIDER = OidcProvider(
    id="clinic-sso",
    display_name="Clinic SSO",
    issuer=ISSUER,
    principal_type="workforce",
    client_id="care-workforce",
    client_secret="workforce-secret",
)
PATIENT_PROVIDER = OidcProvider(
    id="patient-sso",
    display_name="Patient SSO",
    issuer=ISSUER,
    principal_type="patient",
    client_id="care-patient",
    client_secret="patient-secret",
)


class WorkforceLinkingRuleTests(TestCase):
    def setUp(self):
        self.alice = baker.make(User, username="alice", deleted=False)
        self.mallory = baker.make(User, username="mallory", deleted=False)
        self.registrar = baker.make(User, username="registrar", deleted=False)

    def test_a_link_records_the_triple_and_the_actor(self):
        identity = link_workforce_identity(
            user=self.alice,
            provider=WORKFORCE_PROVIDER,
            subject="staff-subject",
            linked_by=self.registrar,
        )

        self.assertEqual(identity.user, self.alice)
        self.assertEqual(identity.provider_id, "clinic-sso")
        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.subject, "staff-subject")
        self.assertEqual(identity.linked_by, self.registrar)

    def test_a_self_service_link_records_no_administrator(self):
        """Null `linked_by` is the record that the principal acted for itself."""
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        self.assertIsNone(identity.linked_by)

    def test_an_already_linked_triple_is_never_moved(self):
        """Rule §5.4, first writer wins. The attack this closes is simple:
        link your own IdP subject to someone else's CARE account."""
        link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        with self.assertRaises(IdentityAlreadyLinkedError):
            link_workforce_identity(
                user=self.mallory,
                provider=WORKFORCE_PROVIDER,
                subject="staff-subject",
            )

        self.assertEqual(UserExternalIdentity.objects.get().user, self.alice)

    def test_relinking_the_same_triple_to_the_same_user_is_also_refused(self):
        """Otherwise a repeat is a silent no-op that looks like it worked."""
        link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        with self.assertRaises(IdentityAlreadyLinkedError):
            link_workforce_identity(
                user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
            )

    def test_the_same_subject_at_another_provider_is_a_separate_link(self):
        other = OidcProvider(
            id="regional-sso",
            display_name="Regional SSO",
            issuer="https://identity-b.example/realms/care",
            principal_type="workforce",
            client_id="care-workforce",
            client_secret="regional-secret",
        )
        link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="shared"
        )
        link_workforce_identity(user=self.alice, provider=other, subject="shared")

        self.assertEqual(self.alice.external_identities.count(), 2)

    def test_unlinking_leaves_the_principal_and_its_other_methods_intact(self):
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        unlinked = unlink_workforce_identity(
            user=self.alice, external_id=identity.external_id
        )

        self.assertTrue(unlinked)
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())
        self.assertEqual(self.alice.external_identities.count(), 0)

    def test_unlinking_frees_the_triple_for_a_deliberate_re_enrolment(self):
        """Rule §5.5: the subject is immutable, so correcting one is
        unlink-then-link, and both halves are recorded."""
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )
        unlink_workforce_identity(user=self.alice, external_id=identity.external_id)

        replacement = link_workforce_identity(
            user=self.mallory, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        self.assertEqual(replacement.user, self.mallory)

    def test_a_principal_cannot_unlink_an_identity_it_does_not_hold(self):
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        unlinked = unlink_workforce_identity(
            user=self.mallory, external_id=identity.external_id
        )

        self.assertFalse(unlinked)
        self.assertEqual(UserExternalIdentity.objects.get().user, self.alice)


class PatientLinkingRuleTests(TestCase):
    def setUp(self):
        self.patient = baker.make(Patient, name="Patient One", deleted=False)
        self.registrar = baker.make(User, username="registrar", deleted=False)

    def test_a_patient_link_requires_an_administrator(self):
        """Rule §5.3. There is no self-service path to a clinical record."""
        identity = link_patient_identity(
            patient=self.patient,
            provider=PATIENT_PROVIDER,
            subject="patient-subject",
            linked_by=self.registrar,
        )

        self.assertEqual(identity.patient, self.patient)
        self.assertEqual(identity.linked_by, self.registrar)

    def test_a_patient_link_without_an_actor_is_refused(self):
        with self.assertRaises(ValueError):
            link_patient_identity(
                patient=self.patient,
                provider=PATIENT_PROVIDER,
                subject="patient-subject",
                linked_by=None,
            )

        self.assertEqual(PatientExternalIdentity.objects.count(), 0)

    def test_an_already_linked_patient_triple_is_never_moved(self):
        other = baker.make(Patient, name="Patient Two", deleted=False)
        link_patient_identity(
            patient=self.patient,
            provider=PATIENT_PROVIDER,
            subject="patient-subject",
            linked_by=self.registrar,
        )

        with self.assertRaises(IdentityAlreadyLinkedError):
            link_patient_identity(
                patient=other,
                provider=PATIENT_PROVIDER,
                subject="patient-subject",
                linked_by=self.registrar,
            )

    def test_a_workforce_provider_cannot_link_a_patient(self):
        """A provider serves one principal type, and linking honours that."""
        with self.assertRaises(ValueError):
            link_patient_identity(
                patient=self.patient,
                provider=WORKFORCE_PROVIDER,
                subject="patient-subject",
                linked_by=self.registrar,
            )


class IdentityAuditTests(TestCase):
    """ADR-0011 §5.8: recorded through the existing trail, opaquely.

    The thing that must never appear is the subject. It is the credential half
    of the triple: anyone who can read it and control a provider record could
    enrol it elsewhere.
    """

    def setUp(self):
        self.alice = baker.make(User, username="alice", deleted=False)
        self.mallory = baker.make(User, username="mallory", deleted=False)

    def test_a_link_is_recorded_without_the_subject(self):
        with self.assertLogs("audit_log", level="INFO") as captured:
            link_workforce_identity(
                user=self.alice,
                provider=WORKFORCE_PROVIDER,
                subject="staff-subject",
            )

        record = "\n".join(captured.output)
        self.assertIn("link", record)
        self.assertIn("clinic-sso", record)
        self.assertIn(str(self.alice.external_id), record)
        self.assertNotIn("staff-subject", record)

    def test_a_refused_relink_is_recorded(self):
        link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        with (
            self.assertLogs("audit_log", level="INFO") as captured,
            self.assertRaises(IdentityAlreadyLinkedError),
        ):
            link_workforce_identity(
                user=self.mallory,
                provider=WORKFORCE_PROVIDER,
                subject="staff-subject",
            )

        record = "\n".join(captured.output)
        self.assertIn("relink_refused", record)
        self.assertNotIn("staff-subject", record)

    def test_an_unlink_is_recorded(self):
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )

        with self.assertLogs("audit_log", level="INFO") as captured:
            unlink_workforce_identity(user=self.alice, external_id=identity.external_id)

        record = "\n".join(captured.output)
        self.assertIn("unlink", record)
        self.assertNotIn("staff-subject", record)


class LinkExternalIdentityCommandTests(TestCase):
    """Scripted enrolment, for an operator populating a new provider."""

    def setUp(self):
        self.user = baker.make(User, username="clinician", deleted=False)

    def call(self, **kwargs):
        call_command(
            "link_external_identity",
            provider_id=kwargs.get("provider_id", "clinic-sso"),
            username=kwargs.get("username", "clinician"),
            subject=kwargs.get("subject", "staff-subject"),
        )

    def settings_with_provider(self):
        return self.settings(OIDC_PROVIDERS=(WORKFORCE_PROVIDER, PATIENT_PROVIDER))

    def test_the_command_links_a_configured_provider(self):
        with self.settings_with_provider():
            self.call()

        identity = UserExternalIdentity.objects.get()
        self.assertEqual(identity.user, self.user)
        self.assertEqual(identity.issuer, ISSUER)

    def test_the_command_refuses_an_unconfigured_provider(self):
        with self.settings_with_provider(), self.assertRaises(CommandError):
            self.call(provider_id="no-such-provider")

    def test_the_command_refuses_a_patient_provider_for_a_user(self):
        with self.settings_with_provider(), self.assertRaises(CommandError):
            self.call(provider_id="patient-sso")

    def test_the_command_refuses_a_duplicate_link(self):
        with self.settings_with_provider():
            self.call()
            with self.assertRaises(CommandError):
                self.call()

        self.assertEqual(UserExternalIdentity.objects.count(), 1)


@override_settings(
    OIDC_PROVIDERS=(WORKFORCE_PROVIDER, PATIENT_PROVIDER),
    OIDC_PUBLIC_BASE_URL="https://care.example",
)
class SelfServiceLinkViewTests(TestCase):
    """Rule §5.2(b), at the HTTP boundary.

    The invariant worth stating: this endpoint reads `request.user` and never
    a principal named in the body. A link endpoint that accepted a target is
    an account-takeover endpoint with extra steps.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.alice = baker.make(User, username="alice", is_active=True, deleted=False)
        self.mallory = baker.make(
            User, username="mallory", is_active=True, deleted=False
        )

    def request(self, principal, **overrides):
        request = self.factory.post(
            "/link/",
            {
                "provider_id": "clinic-sso",
                "code": "single-use-code",
                "code_verifier": "v" * 43,
                "nonce": "browser-nonce-value",
                "redirect_uri": "https://care.example/auth/oidc/workforce/callback",
                **overrides,
            },
            format="json",
        )
        force_authenticate(request, user=principal)
        return request

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_link_binds_to_the_authenticated_principal(self, exchange):
        exchange.return_value = {"sub": "staff-subject"}

        response = OidcLinkView.as_view()(self.request(self.alice))

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(UserExternalIdentity.objects.get().user, self.alice)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_named_target_in_the_body_is_ignored(self, exchange):
        """The takeover attempt: ask to link the subject onto someone else."""
        exchange.return_value = {"sub": "staff-subject"}

        response = OidcLinkView.as_view()(
            self.request(self.mallory, user="alice", username="alice")
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(UserExternalIdentity.objects.get().user, self.mallory)

    def test_an_unauthenticated_caller_cannot_link(self):
        request = self.factory.post("/link/", {}, format="json")

        response = OidcLinkView.as_view()(request)

        self.assertIn(response.status_code, {401, 403})
        self.assertEqual(UserExternalIdentity.objects.count(), 0)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_subject_already_linked_elsewhere_is_refused_opaquely(self, exchange):
        """T13 again: a distinct 'already linked' would be an enrolment oracle."""
        link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )
        exchange.return_value = {"sub": "staff-subject"}

        response = OidcLinkView.as_view()(self.request(self.mallory))

        # 403 rather than 401: the CARE session is valid, so DRF does not ask
        # the caller to authenticate again. What is refused is the link.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UserExternalIdentity.objects.get().user, self.alice)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_patient_provider_cannot_be_linked_here(self, exchange):
        """Rule §5.3: there is no self-service path to a clinical record."""
        exchange.return_value = {"sub": "patient-subject"}

        response = OidcLinkView.as_view()(
            self.request(self.alice, provider_id="patient-sso")
        )

        self.assertEqual(response.status_code, 403)
        exchange.assert_not_called()
        self.assertEqual(PatientExternalIdentity.objects.count(), 0)

    def test_a_principal_can_unlink_its_own_identity(self):
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )
        request = self.factory.delete("/link/")
        force_authenticate(request, user=self.alice)

        response = OidcLinkView.as_view()(request, identity_id=identity.external_id)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(UserExternalIdentity.objects.count(), 0)

    def test_a_principal_cannot_unlink_another_principals_identity(self):
        identity = link_workforce_identity(
            user=self.alice, provider=WORKFORCE_PROVIDER, subject="staff-subject"
        )
        request = self.factory.delete("/link/")
        force_authenticate(request, user=self.mallory)

        response = OidcLinkView.as_view()(request, identity_id=identity.external_id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(UserExternalIdentity.objects.get().user, self.alice)
