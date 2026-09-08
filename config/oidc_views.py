"""Turning a validated external identity into a CARE credential (ADR-0011 §4).

The boundary is the point of this module. An external token is accepted here
and nowhere else, and what leaves is an ordinary CARE credential for a
principal that was already enrolled and already authorized. Nothing downstream
can tell which provider was used, or that one was used at all.

Resolution reads `(provider_id, issuer, subject)` and nothing else. No claim --
not `email`, not `preferred_username`, not any role or group -- selects a
principal or grants anything. A valid token whose triple matches no enrolled
identity is answered exactly like a bad signature: enrolment is a separate,
deliberate act, and this endpoint never performs one.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.timezone import localtime, now
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from care.emr.models import PatientExternalIdentity
from care.users.models import UserExternalIdentity
from config.oidc import PATIENT, WORKFORCE, callback_url, providers_for
from config.oidc_service import OidcExchangeError, exchange_oidc_code
from config.patient_otp_token import PatientToken
from config.ratelimit import ratelimit

User = get_user_model()

#: One message for every failure. An unknown subject, a bad signature, a wrong
#: audience and a disabled account are indistinguishable from outside, so the
#: endpoint cannot be used to discover who is enrolled (T13).
GENERIC_FAILURE = "External authentication could not be completed"


class OidcExchangeSerializer(serializers.Serializer):
    provider_id = serializers.CharField(max_length=32)
    code = serializers.CharField(max_length=4096, trim_whitespace=False)
    code_verifier = serializers.CharField(
        min_length=43, max_length=128, trim_whitespace=False
    )
    nonce = serializers.CharField(min_length=16, max_length=255, trim_whitespace=False)
    redirect_uri = serializers.URLField(max_length=2048)


class OidcExchangeAuthentication(BaseAuthentication):
    """Advertise the OIDC boundary so rejected exchanges remain HTTP 401."""

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class BaseOidcExchangeView(APIView):
    authentication_classes = [OidcExchangeAuthentication]
    permission_classes = []
    principal_type: str

    def post(self, request, *args, **kwargs):
        serializer = OidcExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)

        provider = self._provider(payload.pop("provider_id"))
        if provider is None:
            # A provider that is not configured for this principal type is not
            # a hint worth giving; the route only exists at all because some
            # provider is enabled for it.
            raise AuthenticationFailed(GENERIC_FAILURE)

        if ratelimit(request, f"oidc-{self.principal_type}-exchange", ["ip"], "10/m"):
            raise Throttled

        try:
            claims = exchange_oidc_code(provider=provider, **payload)
            return self.issue_care_token(provider, claims["sub"])
        except (OidcExchangeError, KeyError):
            raise AuthenticationFailed(GENERIC_FAILURE) from None

    def _provider(self, provider_id: str):
        for provider in providers_for(settings.OIDC_PROVIDERS, self.principal_type):
            if provider.id == provider_id:
                return provider
        return None

    def issue_care_token(self, provider, subject: str) -> Response:
        raise NotImplementedError


class WorkforceOidcExchangeView(BaseOidcExchangeView):
    principal_type = WORKFORCE

    def issue_care_token(self, provider, subject: str) -> Response:
        identity = (
            UserExternalIdentity.objects.filter(
                provider_id=provider.id,
                issuer=provider.issuer,
                subject=subject,
                user__is_active=True,
                user__deleted=False,
            )
            .select_related("user")
            .first()
        )
        if identity is None:
            raise AuthenticationFailed(GENERIC_FAILURE)

        user = identity.user
        refresh = RefreshToken.for_user(user)
        signed_in_at = localtime(now())
        User.objects.filter(pk=user.pk).update(last_login=signed_in_at)
        UserExternalIdentity.objects.filter(pk=identity.pk).update(
            last_login_at=signed_in_at
        )
        return Response({"refresh": str(refresh), "access": str(refresh.access_token)})


class PatientOidcExchangeView(BaseOidcExchangeView):
    principal_type = PATIENT

    def issue_care_token(self, provider, subject: str) -> Response:
        identity = (
            PatientExternalIdentity.objects.filter(
                provider_id=provider.id,
                issuer=provider.issuer,
                subject=subject,
                patient__deleted=False,
            )
            .select_related("patient")
            .first()
        )
        if identity is None:
            raise AuthenticationFailed(GENERIC_FAILURE)

        PatientExternalIdentity.objects.filter(pk=identity.pk).update(
            last_login_at=localtime(now())
        )
        token = PatientToken()
        token["patient_id"] = str(identity.patient.external_id)
        token["auth_provider"] = "oidc"
        token["provider_id"] = provider.id
        return Response({"access": str(token)})


class OidcProviderListView(APIView):
    """What the login screen may offer, answered by the backend that serves it.

    Before this, a frontend build declared its own provider list and could
    advertise a method the backend did not have. Reading it from here removes
    that whole class of defect.

    Every field is a public identifier -- a client id and an issuer are meant
    to ship in a bundle. A client secret is not, and cannot reach this response
    because the serialisation is explicit rather than a dump of the record.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        if ratelimit(request, "oidc-provider-list", ["ip"], "60/m"):
            raise Throttled

        return Response(
            [
                {
                    "id": provider.id,
                    "display_name": provider.display_name,
                    "principal_type": provider.principal_type,
                    "issuer": provider.issuer,
                    "client_id": provider.client_id,
                    "scopes": list(provider.scopes),
                    "redirect_uri": callback_url(
                        settings.OIDC_PUBLIC_BASE_URL, provider.principal_type
                    ),
                }
                for provider in settings.OIDC_PROVIDERS
                if provider.enabled
            ]
        )
