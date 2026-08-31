"""Exchange verified Firebase patient identity for CARE's PatientToken."""

from django.conf import settings
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound, Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from config.firebase_auth_service import (
    FirebaseTokenError,
    normalize_verified_phone_number,
    verify_firebase_id_token,
)
from config.patient_otp_token import PatientToken
from config.ratelimit import ratelimit


class FirebaseTokenSerializer(serializers.Serializer):
    id_token = serializers.CharField(max_length=16384, trim_whitespace=False)


class FirebaseExchangeAuthentication(BaseAuthentication):
    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class FirebasePatientExchangeView(APIView):
    authentication_classes = [FirebaseExchangeAuthentication]
    permission_classes = []

    def post(self, request, *args, **kwargs):
        if not settings.FIREBASE_AUTH_ENABLED:
            raise NotFound

        serializer = FirebaseTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if ratelimit(request, "firebase-patient-exchange", ["ip"], "10/m"):
            raise Throttled
        try:
            claims = verify_firebase_id_token(serializer.validated_data["id_token"])
            token = self._patient_token(claims)
        except (FirebaseTokenError, KeyError, TypeError, ValueError):
            raise AuthenticationFailed(
                "External authentication could not be completed"
            ) from None
        return Response({"access": str(token)})

    @staticmethod
    def _patient_token(claims: dict) -> PatientToken:
        provider = claims.get("firebase", {}).get("sign_in_provider")
        token = PatientToken()
        token["auth_provider"] = "firebase"

        if provider == "phone" and (phone_number := claims.get("phone_number")):
            token["phone_number"] = normalize_verified_phone_number(phone_number)
            return token

        if (
            provider == "password"
            and claims.get("email_verified") is True
            and (email := claims.get("email"))
        ):
            token["email"] = email.strip().lower()
            return token

        raise FirebaseTokenError
