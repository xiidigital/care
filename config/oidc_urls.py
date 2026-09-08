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
        OidcLinkedIdentityListView,
        OidcLinkView,
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
        routes += [
            path(
                "api/v1/auth/oidc/workforce/exchange/",
                WorkforceOidcExchangeView.as_view(),
                name="oidc_workforce_exchange",
            ),
            # Linking is workforce-only (rule §5.3): a patient link reaches a
            # clinical record and stays on the administrative path.
            path(
                "api/v1/auth/oidc/link/",
                OidcLinkView.as_view(),
                name="oidc_link",
            ),
            path(
                "api/v1/auth/oidc/link/<uuid:identity_id>/",
                OidcLinkView.as_view(),
                name="oidc_unlink",
            ),
            path(
                "api/v1/auth/oidc/identities/",
                OidcLinkedIdentityListView.as_view(),
                name="oidc_linked_identities",
            ),
        ]
    if providers_for(providers, PATIENT):
        routes.append(
            path(
                "api/v1/auth/oidc/patient/exchange/",
                PatientOidcExchangeView.as_view(),
                name="oidc_patient_exchange",
            )
        )
    return routes
