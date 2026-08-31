import hashlib

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from care.emr.models import Patient
from config.patient_otp_token import PatientToken

#: Enough of a hash to separate two patients in an audit trail, short enough
#: that it is obviously not a contact value.
CONTACT_FINGERPRINT_LENGTH = 12


class PatientOtpObject:
    service = "patient_otp"
    is_alternative_login = True
    is_authenticated = True
    is_anonymous = True
    phone_number = None
    email = None
    patient_id = None
    auth_provider = None


class CustomJWTAuthentication(JWTAuthentication):
    """
    An authentication plugin that authenticates requests through a JSON web
    token provided in a request header.
    """

    www_authenticate_realm = "api"

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None
        try:
            validated_token = self.get_validated_token(raw_token)
        except Exception:
            return None
        return self.get_user(validated_token), validated_token

    def get_validated_token(self, raw_token):
        """
        Validates an encoded JSON web token and returns a validated token
        wrapper object.
        """
        messages = []
        try:
            return PatientToken(raw_token)
        except TokenError as e:
            messages.append(
                {
                    "token_class": PatientToken.__name__,
                    "token_type": PatientToken.token_type,
                    "message": e.args[0],
                }
            )

        raise InvalidToken(
            {
                "detail": _("Given token not valid for any token type"),
                "messages": messages,
            }
        )


class JWTTokenPatientAuthentication(CustomJWTAuthentication):
    def get_user(self, validated_token):
        """
        Returns a stateless user object which is backed by the given validated
        token.
        """
        obj = PatientOtpObject()
        obj.phone_number = validated_token.get("phone_number")
        obj.email = validated_token.get("email")
        obj.patient_id = validated_token.get("patient_id")
        obj.auth_provider = validated_token.get("auth_provider")
        return obj


def patient_access_queryset(principal):
    """Resolve a patient principal without broadening an exact enrolled identity."""
    if patient_id := getattr(principal, "patient_id", None):
        return Patient.objects.filter(external_id=patient_id)
    if email := getattr(principal, "email", None):
        return Patient.objects.filter(email__iexact=email.strip())
    if phone_number := getattr(principal, "phone_number", None):
        return Patient.objects.filter(phone_number=phone_number)
    return Patient.objects.none()


def patient_principal_label(principal) -> str:
    """Describe a patient principal for the audit trail without exposing it.

    ADR-0010 §6 requires principal type, provider and outcome to be recorded
    through opaque CARE identifiers. Two of the three identity shapes carry no
    phone number at all, so the legacy `phone_number[-4:]` slice both crashed
    and was the wrong thing to record.
    """
    provider = getattr(principal, "auth_provider", None) or "otp"

    if patient_id := getattr(principal, "patient_id", None):
        return f"patient|{provider}|id:{patient_id}"
    if email := getattr(principal, "email", None):
        return f"patient|{provider}|email:{contact_fingerprint(email)}"
    if phone_number := getattr(principal, "phone_number", None):
        return f"patient|{provider}|phone:{phone_number[-4:]}"
    return f"patient|{provider}|unresolved"


def contact_fingerprint(contact: str) -> str:
    """Keyed, truncated digest -- stable across a deployment, not reversible."""
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}|{contact.strip().lower()}".encode()
    ).hexdigest()
    return digest[:CONTACT_FINGERPRINT_LENGTH]


class OTPAuthenticatedPermission(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and getattr(request.user, "is_alternative_login", False)
            and (
                getattr(request.user, "patient_id", None)
                or getattr(request.user, "email", None)
                or getattr(request.user, "phone_number", None)
            )
        )
