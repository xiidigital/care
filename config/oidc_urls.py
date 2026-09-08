"""Routes for the optional OIDC exchanges (ADR-0011 §6).

A route that does not exist cannot be probed, rate-limited around, or left
half-guarded by a permission class. With no provider configured -- CARE's
default -- none of these is mounted at all.
"""

from django.urls import path

from config.oidc import PATIENT, WORKFORCE, providers_for


def build_oidc_urlpatterns(providers):
    """Mount only what some enabled provider can actually serve."""
    if not any(provider.enabled for provider in providers):
        return []

    from config.oidc_views import (
        OidcProviderListView,
        PatientOidcExchangeView,
        WorkforceOidcExchangeView,
    )

    routes = [
        path(
            "api/v1/auth/providers/",
            OidcProviderListView.as_view(),
            name="oidc_provider_list",
        )
    ]
    if providers_for(providers, WORKFORCE):
        routes.append(
            path(
                "api/v1/auth/oidc/workforce/exchange/",
                WorkforceOidcExchangeView.as_view(),
                name="oidc_workforce_exchange",
            )
        )
    if providers_for(providers, PATIENT):
        routes.append(
            path(
                "api/v1/auth/oidc/patient/exchange/",
                PatientOidcExchangeView.as_view(),
                name="oidc_patient_exchange",
            )
        )
    return routes
