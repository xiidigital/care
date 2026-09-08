"""The CARE side of the boundary: claims in, CARE credential out (ADR-0011 §4).

`test_oidc_service.py` proves the token is genuine and `test_oidc_flow.py`
drives both halves through the real HTTP routes. What is left here is the
narrow question those two do not isolate: given a set of already-validated
claims, which principal does CARE issue a credential for, and what does it
refuse to do along the way.

The refusals are the point. No principal is created, no claim other than the
triple selects one, and a subject nobody enrolled is answered exactly like a
forged signature.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from model_bakery import baker
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from care.emr.models import Patient, PatientExternalIdentity
from care.users.admin import UserAdmin
from care.users.models import User, UserExternalIdentity
from config.oidc import OidcProvider
from config.oidc_urls import build_oidc_urlpatterns
from config.oidc_views import PatientOidcExchangeView, WorkforceOidcExchangeView
from config.patient_otp_token import PatientToken

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

ENABLED_OIDC_SETTINGS = {
    "OIDC_PROVIDERS": (WORKFORCE_PROVIDER, PATIENT_PROVIDER),
    "OIDC_PUBLIC_BASE_URL": "https://care.example",
}


class OidcRouteMountingTests(SimpleTestCase):
    """A route that does not exist cannot be probed or half-guarded."""

    def test_no_providers_mount_no_routes(self):
        self.assertEqual(build_oidc_urlpatterns(()), [])
        with self.assertRaises(NoReverseMatch):
            reverse("oidc_workforce_exchange")

    def test_a_disabled_provider_mounts_no_routes(self):
        disabled = OidcProvider(
            id="clinic-sso",
            display_name="Clinic SSO",
            issuer=ISSUER,
            principal_type="workforce",
            client_id="care-workforce",
            client_secret="workforce-secret",
            enabled=False,
        )

        self.assertEqual(build_oidc_urlpatterns((disabled,)), [])

    def test_each_principal_route_appears_only_with_a_provider_serving_it(self):
        workforce_only = build_oidc_urlpatterns((WORKFORCE_PROVIDER,))

        names = {pattern.name for pattern in workforce_only}

        self.assertEqual(
            names,
            {
                "oidc_provider_list",
                "oidc_workforce_exchange",
                "oidc_link",
                "oidc_unlink",
                "oidc_linked_identities",
            },
        )

    def test_both_principal_routes_appear_when_both_are_served(self):
        patterns = build_oidc_urlpatterns((WORKFORCE_PROVIDER, PATIENT_PROVIDER))

        self.assertEqual(
            {pattern.name for pattern in patterns},
            {
                "oidc_provider_list",
                "oidc_workforce_exchange",
                "oidc_link",
                "oidc_unlink",
                "oidc_linked_identities",
                "oidc_patient_exchange",
            },
        )


class ExternalIdentityAdminTests(SimpleTestCase):
    """Rule §5.2: linking is a deliberate administrative act, so it needs a
    place to happen -- and the vendor-named column it replaced must be gone."""

    def test_the_retired_subject_column_is_not_an_admin_field(self):
        self.assertNotIn("keycloak_subject", UserAdmin.fieldsets[0][1]["fields"])

    def test_neither_principal_model_still_carries_a_subject_column(self):
        for model in (User, Patient):
            with self.subTest(model=model.__name__):
                fields = {field.name for field in model._meta.get_fields()}  # noqa: SLF001
                self.assertNotIn("keycloak_subject", fields)
                self.assertIn("external_identities", fields)


@override_settings(**ENABLED_OIDC_SETTINGS)
class OidcExchangeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _request(self, provider_id="clinic-sso"):
        return self.factory.post(
            "/exchange/",
            {
                "provider_id": provider_id,
                "code": "single-use-code",
                "code_verifier": "v" * 43,
                "nonce": "browser-nonce-value",
                "redirect_uri": "https://care.example/auth/oidc/workforce/callback",
            },
            format="json",
        )

    def enrol_user(self, subject, *, provider=WORKFORCE_PROVIDER, is_active=True):
        user = baker.make(User, is_active=is_active, deleted=False)
        UserExternalIdentity.objects.create(
            user=user,
            provider_id=provider.id,
            issuer=provider.issuer,
            subject=subject,
        )
        return user

    def enrol_patient(self, subject, *, provider=PATIENT_PROVIDER):
        patient = baker.make(Patient, deleted=False)
        PatientExternalIdentity.objects.create(
            patient=patient,
            provider_id=provider.id,
            issuer=provider.issuer,
            subject=subject,
        )
        return patient

    # -- what an enrolled identity buys --------------------------------------

    @patch("config.oidc_views.exchange_oidc_code")
    def test_workforce_exchange_issues_the_existing_staff_token_pair(self, exchange):
        user = self.enrol_user("staff-subject")
        exchange.return_value = {"sub": "staff-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RefreshToken(response.data["refresh"])["user_id"], str(user.external_id)
        )
        self.assertIn("access", response.data)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_patient_exchange_issues_a_patient_token_only(self, exchange):
        patient = self.enrol_patient("patient-subject")
        exchange.return_value = {"sub": "patient-subject"}

        response = PatientOidcExchangeView.as_view()(self._request("patient-sso"))

        self.assertEqual(response.status_code, 200)
        token = PatientToken(response.data["access"])
        self.assertEqual(token["patient_id"], str(patient.external_id))
        self.assertEqual(token["auth_provider"], "oidc")
        self.assertEqual(token["provider_id"], "patient-sso")

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_successful_login_is_recorded_on_the_identity(self, exchange):
        self.enrol_user("staff-subject")
        exchange.return_value = {"sub": "staff-subject"}

        WorkforceOidcExchangeView.as_view()(self._request())

        self.assertIsNotNone(UserExternalIdentity.objects.get().last_login_at)

    # -- the triple, and nothing but the triple -------------------------------

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_subject_enrolled_at_another_issuer_is_not_reachable(self, exchange):
        """T1: the issuer is half the key, and the half that was missing."""
        other_issuer = OidcProvider(
            id="clinic-sso",
            display_name="Clinic SSO",
            issuer="https://identity-b.example/realms/care",
            principal_type="workforce",
            client_id="care-workforce",
            client_secret="workforce-secret",
        )
        self.enrol_user("shared-subject", provider=other_issuer)
        exchange.return_value = {"sub": "shared-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_subject_enrolled_under_another_provider_is_not_reachable(self, exchange):
        """Two clients of one issuer are still two providers to CARE."""
        other_provider = OidcProvider(
            id="regional-sso",
            display_name="Regional SSO",
            issuer=ISSUER,
            principal_type="workforce",
            client_id="care-workforce",
            client_secret="workforce-secret",
        )
        self.enrol_user("shared-subject", provider=other_provider)
        exchange.return_value = {"sub": "shared-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_an_email_claim_never_selects_a_principal(self, exchange):
        """T2: the standard account-takeover path, closed by construction."""
        baker.make(User, email="victim@example.org", is_active=True, deleted=False)
        exchange.return_value = {
            "sub": "never-enrolled",
            "email": "victim@example.org",
            "email_verified": True,
        }

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_role_claims_are_never_read(self, exchange):
        """T12: an IdP cannot promote anyone, because nothing here looks."""
        exchange.return_value = {
            "sub": "never-enrolled",
            "realm_access": {"roles": ["care-admin"]},
            "groups": ["administrators"],
            "scope": "openid care:admin",
        }

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(UserExternalIdentity.objects.count(), 0)

    # -- what it refuses to do ------------------------------------------------

    @patch("config.oidc_views.exchange_oidc_code")
    def test_an_unenrolled_subject_creates_nothing(self, exchange):
        """T11. Enrolment is a separate act; this endpoint never performs one."""
        users_before = User.objects.count()
        exchange.return_value = {"sub": "never-enrolled"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(UserExternalIdentity.objects.count(), 0)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_deactivated_account_cannot_log_in(self, exchange):
        self.enrol_user("staff-subject", is_active=False)
        exchange.return_value = {"sub": "staff-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_an_unlinked_identity_no_longer_resolves(self, exchange):
        """Rule §5.6: unlink is immediate, and it is how access is withdrawn."""
        self.enrol_user("staff-subject")
        UserExternalIdentity.objects.get().delete()
        exchange.return_value = {"sub": "staff-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_workforce_exchange_never_resolves_a_patient_identity(self, exchange):
        """T3: the two tables are separate, so there is nothing to confuse."""
        self.enrol_patient("shared-subject")
        exchange.return_value = {"sub": "shared-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_patient_exchange_never_resolves_a_workforce_identity(self, exchange):
        self.enrol_user("shared-subject")
        exchange.return_value = {"sub": "shared-subject"}

        response = PatientOidcExchangeView.as_view()(self._request("patient-sso"))

        self.assertEqual(response.status_code, 401)

    @patch("config.oidc_views.exchange_oidc_code")
    def test_a_provider_serving_the_other_principal_is_refused(self, exchange):
        self.enrol_user("staff-subject")
        exchange.return_value = {"sub": "staff-subject"}

        response = WorkforceOidcExchangeView.as_view()(self._request("patient-sso"))

        self.assertEqual(response.status_code, 401)
        exchange.assert_not_called()

    @patch("config.oidc_views.exchange_oidc_code")
    def test_an_unknown_provider_id_is_refused_before_any_network_call(self, exchange):
        response = WorkforceOidcExchangeView.as_view()(self._request("no-such-thing"))

        self.assertEqual(response.status_code, 401)
        exchange.assert_not_called()

    @patch("config.oidc_views.exchange_oidc_code")
    @patch("config.oidc_views.ratelimit", return_value=True)
    def test_the_exchange_is_rate_limited_before_the_provider_is_called(
        self, limited, exchange
    ):
        response = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(response.status_code, 429)
        exchange.assert_not_called()

    @patch("config.oidc_views.exchange_oidc_code")
    def test_every_failure_looks_the_same_from_outside(self, exchange):
        """T13: an unknown subject must not be distinguishable from a bad token."""
        self.enrol_user("staff-subject", is_active=False)
        exchange.return_value = {"sub": "staff-subject"}
        deactivated = WorkforceOidcExchangeView.as_view()(self._request())

        exchange.return_value = {"sub": "never-enrolled"}
        unknown = WorkforceOidcExchangeView.as_view()(self._request())

        self.assertEqual(deactivated.status_code, unknown.status_code)
        self.assertEqual(deactivated.data, unknown.data)
