from rest_framework.exceptions import ValidationError

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRCreateMixin, EMRListMixin
from care.emr.resources.patient.otp_based_flow import (
    PatientOTPReadSpec,
    PatientOTPWriteSpec,
)
from config.patient_otp_authentication import (
    JWTTokenPatientAuthentication,
    OTPAuthenticatedPermission,
    patient_access_queryset,
)


class PatientOTPView(EMRCreateMixin, EMRListMixin, EMRBaseViewSet):
    authentication_classes = [JWTTokenPatientAuthentication]
    permission_classes = [OTPAuthenticatedPermission]
    pydantic_model = PatientOTPWriteSpec
    pydantic_read_model = PatientOTPReadSpec

    def perform_create(self, instance):
        if self.request.user.patient_id:
            raise ValidationError("Patient already enrolled")
        if self.request.user.email:
            instance.email = self.request.user.email.strip().lower()
        else:
            instance.phone_number = self.request.user.phone_number
        instance.save()

    def get_queryset(self):
        return patient_access_queryset(self.request.user)
