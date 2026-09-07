"""Provider-agnostic OIDC configuration contract (ADR-0011 §1, §6).

These are unit tests of the loader and the validator, deliberately free of the
database and of a started process. The questions they answer are the ones that
decide whether CARE boots at all: is this provider set well-formed, is it safe,
and does this deployment still have a way for a patient to log in.

`care/users/tests/test_external_auth_startup.py` asks the same questions of a
real process; this file is where the shape of an error message is pinned down.
"""

import json
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.login_methods import validate_login_methods
from config.oidc import (
    OidcProvider,
    is_safe_oidc_url,
    load_oidc_providers,
    validate_oidc_providers,
)

WORKFORCE_PROVIDER = {
    "id": "clinic-sso",
    "display_name": "Clinic SSO",
    "issuer": "https://identity.example/realms/care",
    "principal_type": "workforce",
    "client_id": "care-workforce",
    "client_secret": "workforce-secret",
}

PATIENT_PROVIDER = {
    "id": "patient-sso",
    "display_name": "Patient SSO",
    "issuer": "https://identity.example/realms/care-patients",
    "principal_type": "patient",
    "client_id": "care-patient",
    "client_secret": "patient-secret",
}

PUBLIC_BASE_URL = "https://care.example"


def provider(**overrides) -> OidcProvider:
    return load_oidc_providers(
        raw=json.dumps([{**WORKFORCE_PROVIDER, **overrides}]), path=""
    )[0]


class LoadOidcProvidersTests(SimpleTestCase):
    """Reading the provider set. Nothing here contacts an issuer."""

    def test_no_configuration_at_all_is_a_supported_state(self):
        """ADR-0011 §7: zero providers is CARE's default, not an error."""
        self.assertEqual(load_oidc_providers(raw="", path=""), ())

    def test_blank_configuration_is_treated_as_absent(self):
        self.assertEqual(load_oidc_providers(raw="   ", path="  "), ())

    def test_an_empty_list_is_a_supported_state(self):
        self.assertEqual(load_oidc_providers(raw="[]", path=""), ())

    def test_both_sources_at_once_is_refused(self):
        """Two sources of truth would silently pick a winner. Refuse instead."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_oidc_providers(raw="[]", path="/nonexistent/providers.json")

        self.assertIn("OIDC_PROVIDERS", str(caught.exception))
        self.assertIn("OIDC_PROVIDERS_FILE", str(caught.exception))

    def test_a_provider_set_is_read_from_the_inline_value(self):
        providers = load_oidc_providers(
            raw=json.dumps([WORKFORCE_PROVIDER, PATIENT_PROVIDER]), path=""
        )

        self.assertEqual([p.id for p in providers], ["clinic-sso", "patient-sso"])
        self.assertEqual(providers[0].principal_type, "workforce")
        self.assertEqual(providers[1].principal_type, "patient")

    def test_a_provider_set_is_read_from_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.json"
            path.write_text(json.dumps([WORKFORCE_PROVIDER]))

            providers = load_oidc_providers(raw="", path=str(path))

        self.assertEqual([p.id for p in providers], ["clinic-sso"])

    def test_an_unreadable_file_refuses_rather_than_starting_empty(self):
        """Starting with no providers because a mount failed is a silent outage."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_oidc_providers(raw="", path="/nonexistent/providers.json")

        self.assertIn("OIDC_PROVIDERS_FILE", str(caught.exception))

    def test_malformed_json_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_oidc_providers(raw="{not json", path="")

        self.assertIn("OIDC_PROVIDERS", str(caught.exception))

    def test_a_json_object_instead_of_a_list_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            load_oidc_providers(raw=json.dumps(WORKFORCE_PROVIDER), path="")

    def test_a_missing_required_field_names_the_field(self):
        incomplete = {k: v for k, v in WORKFORCE_PROVIDER.items() if k != "client_id"}

        with self.assertRaises(ImproperlyConfigured) as caught:
            load_oidc_providers(raw=json.dumps([incomplete]), path="")

        self.assertIn("client_id", str(caught.exception))

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        """A typo that is silently dropped is a security control silently dropped."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_oidc_providers(
                raw=json.dumps([{**WORKFORCE_PROVIDER, "clientsecret": "oops"}]),
                path="",
            )

        self.assertIn("clientsecret", str(caught.exception))

    def test_defaults_are_conservative(self):
        loaded = provider()

        self.assertTrue(loaded.enabled)
        self.assertIn("openid", loaded.scopes)
        self.assertFalse(loaded.allow_rp_logout)

    def test_a_trailing_slash_on_the_issuer_is_normalised_away(self):
        """`iss` is compared for equality later; normalise once, here."""
        loaded = provider(issuer="https://identity.example/realms/care/")

        self.assertEqual(loaded.issuer, "https://identity.example/realms/care")


class ValidateOidcProvidersTests(SimpleTestCase):
    """Refusing to start on a provider set that could not work, or is unsafe."""

    def validate(self, providers, *, public_base_url=PUBLIC_BASE_URL, enforce=True):
        validate_oidc_providers(
            providers, public_base_url=public_base_url, enforce=enforce
        )

    def test_no_providers_needs_no_public_base_url(self):
        self.validate((), public_base_url="")

    def test_a_complete_set_validates(self):
        self.validate(
            load_oidc_providers(
                raw=json.dumps([WORKFORCE_PROVIDER, PATIENT_PROVIDER]), path=""
            )
        )

    def test_a_non_api_role_validates_nothing(self):
        """A worker assembles the same environment but serves no login route."""
        self.validate((provider(issuer="http://identity.example"),), enforce=False)

    def test_duplicate_ids_are_refused_even_when_one_is_disabled(self):
        """The id keys stored identities; a collision is not a future problem."""
        providers = load_oidc_providers(
            raw=json.dumps(
                [WORKFORCE_PROVIDER, {**PATIENT_PROVIDER, "id": "clinic-sso"}]
            ),
            path="",
        )

        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate(providers)

        self.assertIn("clinic-sso", str(caught.exception))

    def test_a_malformed_id_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(id="Clinic SSO"),))

        self.assertIn("id", str(caught.exception))

    def test_an_unknown_principal_type_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(principal_type="admin"),))

        self.assertIn("principal_type", str(caught.exception))

    def test_an_insecure_issuer_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(issuer="http://identity.example/realms/care"),))

        self.assertIn("issuer", str(caught.exception))
        self.assertIn("clinic-sso", str(caught.exception))

    def test_a_loopback_issuer_is_allowed_over_http(self):
        """The local test issuer must be usable without weakening the rule."""
        self.validate((provider(issuer="http://localhost:8081/realms/care-test"),))

    def test_an_issuer_carrying_credentials_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(
                (provider(issuer="https://u:p@identity.example/realms/care"),)
            )

    def test_a_missing_secret_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(client_secret=""),))

        self.assertIn("client_secret", str(caught.exception))

    def test_scopes_without_openid_are_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(scopes=["profile", "email"]),))

        self.assertIn("scopes", str(caught.exception))

    def test_a_disabled_provider_is_not_held_to_the_enabled_contract(self):
        """A provider switched off is inert, not a startup failure."""
        self.validate((provider(enabled=False, issuer="http://identity.example"),))

    def test_enabled_providers_require_a_public_base_url(self):
        """Callback URLs are derived from it; without it no exchange can match."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(),), public_base_url="")

        self.assertIn("OIDC_PUBLIC_BASE_URL", str(caught.exception))

    def test_a_public_base_url_with_a_path_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(),), public_base_url="https://care.example/app")

        self.assertIn("OIDC_PUBLIC_BASE_URL", str(caught.exception))

    def test_a_validation_failure_never_echoes_a_secret(self):
        """Startup output reaches logs and CI. A secret must not ride along."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate((provider(issuer="http://identity.example"),))

        self.assertNotIn("workforce-secret", str(caught.exception))


class SafeOidcUrlTests(SimpleTestCase):
    """T16: the issuer URL is operator input, and it is fetched server-side."""

    def test_https_is_accepted(self):
        self.assertTrue(is_safe_oidc_url("https://identity.example/realms/care"))

    def test_loopback_http_is_accepted(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            with self.subTest(host=host):
                self.assertTrue(is_safe_oidc_url(f"http://{host}:8081/realms/care"))

    def test_non_loopback_http_is_rejected(self):
        self.assertFalse(is_safe_oidc_url("http://identity.example/realms/care"))

    def test_credentials_query_and_fragment_are_rejected(self):
        for url in (
            "https://user:pass@identity.example/realms/care",
            "https://identity.example/realms/care?next=1",
            "https://identity.example/realms/care#fragment",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_safe_oidc_url(url))

    def test_a_hostless_or_unparseable_url_is_rejected(self):
        for url in ("", "not-a-url", "https://", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assertFalse(is_safe_oidc_url(url))


class LoginMethodAvailabilityTests(SimpleTestCase):
    """ES-11 §10: the matrix, at the only point where it can be enforced.

    Every row here is a supported deployment except the one that leaves a
    principal type with no way in at all.
    """

    def validate(self, *, otp=True, firebase=False, providers=()):
        validate_login_methods(
            enforce=True,
            patient_otp_enabled=otp,
            firebase_enabled=firebase,
            providers=providers,
        )

    def test_row_1_otp_alone_is_valid(self):
        """CARE's default: no external dependency of any kind."""
        self.validate()

    def test_row_2_otp_and_firebase(self):
        self.validate(firebase=True)

    def test_row_3_otp_and_a_workforce_provider(self):
        self.validate(providers=(provider(),))

    def test_row_4_otp_and_a_patient_provider(self):
        self.validate(providers=(provider(principal_type="patient"),))

    def test_row_6_firebase_replaces_a_retired_otp(self):
        self.validate(otp=False, firebase=True)

    def test_row_7_a_patient_provider_replaces_a_retired_otp(self):
        self.validate(otp=False, providers=(provider(principal_type="patient"),))

    def test_row_9_no_patient_method_at_all_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.validate(otp=False)

        self.assertIn("patient", str(caught.exception).lower())

    def test_a_workforce_provider_does_not_give_patients_a_way_in(self):
        """The near miss of row 9: something is enabled, but not for patients."""
        with self.assertRaises(ImproperlyConfigured):
            self.validate(otp=False, providers=(provider(),))

    def test_a_disabled_patient_provider_does_not_count(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(
                otp=False,
                providers=(provider(principal_type="patient", enabled=False),),
            )

    def test_a_non_api_role_is_not_held_to_the_matrix(self):
        validate_login_methods(
            enforce=False,
            patient_otp_enabled=False,
            firebase_enabled=False,
            providers=(),
        )
