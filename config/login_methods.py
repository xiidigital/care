"""Which ways in a deployment actually offers (ADR-0011 §6, ES-11 §10).

The three families of login method -- CARE's own phone OTP, Firebase, and OIDC
providers -- are independent switches that compose freely. Enabling one never
disables another, and every subset is a supported deployment except one: the
subset that leaves a principal type with no way in at all.

That exception is the only configuration CARE refuses, and it is checked here
because startup is the last moment at which refusing is cheap. Discovering it
from a login screen that renders no buttons is discovering it too late.

Workforce principals are not checked. CARE's own username/password login has no
switch and cannot be configured away, so a workforce principal always has at
least one method by construction.
"""

from django.core.exceptions import ImproperlyConfigured

from config.oidc import PATIENT, OidcProvider

OTP_METHOD = "otp"
FIREBASE_METHOD = "firebase"


def enabled_patient_login_methods(
    *,
    patient_otp_enabled: bool,
    firebase_enabled: bool,
    providers: tuple[OidcProvider, ...],
) -> tuple[str, ...]:
    """Every way a patient may currently authenticate, in presentation order."""
    methods: list[str] = []
    if patient_otp_enabled:
        methods.append(OTP_METHOD)
    if firebase_enabled:
        methods.append(FIREBASE_METHOD)
    methods.extend(
        provider.id
        for provider in providers
        if provider.enabled and provider.principal_type == PATIENT
    )
    return tuple(methods)


def validate_login_methods(
    *,
    enforce: bool,
    patient_otp_enabled: bool,
    firebase_enabled: bool,
    providers: tuple[OidcProvider, ...],
) -> None:
    """Refuse a deployment in which no patient can log in.

    `enforce` follows the same role split as the rest of the authentication
    configuration: a worker serves no login route and is not held to this.
    """
    if not enforce:
        return

    if enabled_patient_login_methods(
        patient_otp_enabled=patient_otp_enabled,
        firebase_enabled=firebase_enabled,
        providers=providers,
    ):
        return

    msg = (
        "No patient login method is enabled. Set CARE_PATIENT_OTP_ENABLED=true, "
        "enable Firebase, or configure an enabled OIDC provider whose "
        "principal_type is 'patient'."
    )
    raise ImproperlyConfigured(msg)
