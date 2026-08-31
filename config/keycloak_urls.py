"""Routes for the optional Keycloak exchanges."""

from django.urls import path


def build_keycloak_urlpatterns(*, enabled: bool):
    """Return no attack surface unless the integration is explicitly enabled."""
    if not enabled:
        return []

    from config.keycloak_views import (
        PatientKeycloakExchangeView,
        WorkforceKeycloakExchangeView,
    )

    return [
        path(
            "api/v1/auth/keycloak/workforce/exchange/",
            WorkforceKeycloakExchangeView.as_view(),
            name="keycloak_workforce_exchange",
        ),
        path(
            "api/v1/auth/keycloak/patient/exchange/",
            PatientKeycloakExchangeView.as_view(),
            name="keycloak_patient_exchange",
        ),
    ]
