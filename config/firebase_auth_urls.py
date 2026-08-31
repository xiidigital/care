"""Routes for direct Firebase patient authentication."""

from django.urls import path


def build_firebase_auth_urlpatterns(*, enabled: bool):
    if not enabled:
        return []

    from config.firebase_auth_views import FirebasePatientExchangeView

    return [
        path(
            "api/v1/auth/firebase/patient/exchange/",
            FirebasePatientExchangeView.as_view(),
            name="firebase_patient_exchange",
        )
    ]
