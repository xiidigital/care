"""Separate Keycloak-to-CARE exchanges for workforce and patient principals."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.timezone import localtime, now
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound, Throttled
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from care.emr.models import Patient
from config.keycloak_service import KeycloakExchangeError, exchange_keycloak_code
from config.patient_otp_token import PatientToken
from config.ratelimit import ratelimit

User = get_user_model()


class KeycloakExchangeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=4096, trim_whitespace=False)
    code_verifier = serializers.CharField(
        min_length=43, max_length=128, trim_whitespace=False
    )
    nonce = serializers.CharField(min_length=16, max_length=255, trim_whitespace=False)
    redirect_uri = serializers.URLField(max_length=2048)


class KeycloakExchangeAuthentication(BaseAuthentication):
    """Advertise the OIDC boundary so rejected exchanges remain HTTP 401."""

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class BaseKeycloakExchangeView(APIView):
    authentication_classes = [KeycloakExchangeAuthentication]
    permission_classes = []
    principal_type: str

    def post(self, request, *args, **kwargs):
        if not settings.KEYCLOAK_ENABLED:
            raise NotFound

        serializer = KeycloakExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if ratelimit(
            request, f"keycloak-{self.principal_type}-exchange", ["ip"], "10/m"
        ):
            raise Throttled
        try:
            claims = exchange_keycloak_code(
                principal_type=self.principal_type,
                **serializer.validated_data,
            )
            return self.issue_care_token(claims["sub"])
        except (KeycloakExchangeError, KeyError):
            raise AuthenticationFailed(
                "External authentication could not be completed"
            ) from None

    def issue_care_token(self, subject: str) -> Response:
        raise NotImplementedError


class WorkforceKeycloakExchangeView(BaseKeycloakExchangeView):
    principal_type = "workforce"

    def issue_care_token(self, subject: str) -> Response:
        try:
            user = User.objects.get(keycloak_subject=subject, is_active=True)
        except User.DoesNotExist:
            raise AuthenticationFailed(
                "External authentication could not be completed"
            ) from None

        refresh = RefreshToken.for_user(user)
        User.objects.filter(pk=user.pk).update(last_login=localtime(now()))
        return Response({"refresh": str(refresh), "access": str(refresh.access_token)})


class PatientKeycloakExchangeView(BaseKeycloakExchangeView):
    principal_type = "patient"

    def issue_care_token(self, subject: str) -> Response:
        try:
            patient = Patient.objects.get(keycloak_subject=subject)
        except Patient.DoesNotExist:
            raise AuthenticationFailed(
                "External authentication could not be completed"
            ) from None

        token = PatientToken()
        token["patient_id"] = str(patient.external_id)
        token["auth_provider"] = "keycloak"
        return Response({"access": str(token)})
