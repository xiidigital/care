# ADR-0010: Multichannel Authentication with Keycloak and Firebase

- **Status:** Accepted
- **Date:** 2026-08-31
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None
- **Amended by:** ADR-0011 (§2, §4 and §5)

> **Read §2, §4 and §5 with ADR-0011 open.** This ADR designed around Keycloak
> rather than around OIDC, and the implementation followed. ADR-0011 replaced
> the single-issuer adapter with a provider-agnostic OIDC layer, and replaced
> the `keycloak_subject` columns with external-identity tables keyed on
> `(provider_id, issuer, subject)`.
>
> The reason is not naming. An OIDC `sub` is unique only within an issuer, and
> §4's single unique column stored no issuer — safe only for as long as exactly
> one issuer could be configured, which made that restriction load-bearing
> security rather than a limitation.
>
> §1 (two principal types), §3 (Firebase), §6 and §7 stand unchanged, and this
> ADR remains the record of what was delivered under ES-10.

## Context

CARE currently has two different principal types. Consultorio personnel use
the Django `User` model and the normal CARE JWT login. Patients use a separate
phone OTP path, a lightweight patient principal and a one-hour `PatientToken`
that scopes the public patient and booking APIs by phone number.

The required change is incremental but applies to both sides. Patients need to
enter by SMS, one-time email authentication or, once available, Keycloak.
Clinicians, administrators, reception and other consultorio users also need a
Keycloak path. No Keycloak service is available for the current phase, so CARE
must ship with the integration disabled and continue operating without it.
When a Keycloak service becomes available, an operator must be able to activate
it using configuration only, without another CARE code change. Keycloak may
authenticate through password, OTP, passkey or a federated identity provider,
but those internal choices must remain invisible to CARE.

ADR-0009 keeps production patient login disabled until authentication controls
and clinical activation are verified. This ADR selects the target login paths;
it does not activate production or authorize real patient data.

## Decision

### 1. Preserve the two CARE principal types

CARE SHALL continue to distinguish:

```text
Consultorio principal -> care.users.User -> existing staff permissions
Patient principal      -> care.emr.Patient -> existing patient/booking permissions
```

Authentication SHALL NOT turn a patient into a Django staff user or a
consultorio user into a patient principal. Existing facility, organization,
role and record authorization remains in CARE.

### 2. Ship a dormant, configuration-complete Keycloak adapter

CARE SHALL include the Keycloak OIDC adapter, exchange endpoints, model fields,
UI integration points and tests in this implementation, but SHALL keep them
disabled by default. This ADR does not require deploying or operating a
Keycloak service now.

When enabled, one externally supplied Keycloak deployment SHALL authenticate
through two distinct clients and audiences:

```text
care-workforce -> consultorio users only
care-patient   -> patients using the Keycloak option
```

The first Keycloak deployment MAY use one realm because the clients, audiences,
redirect URIs and CARE exchange endpoints provide the required separation. A
future operational need MAY split realms without changing CARE's two principal
types.

The adapter SHALL be controlled by one environment-level flag, represented as
`KEYCLOAK_ENABLED=false` by default. Enabling it SHALL require only operator
configuration for:

- the exact OIDC issuer/discovery URL;
- the workforce client ID and client secret;
- the patient client ID and client secret; and
- the public CARE base URL used to derive the two registered callback URLs.

The final setting names MAY follow the repository's configuration conventions,
but the one-flag behavior and required values are part of this decision. With
the flag off, CARE SHALL NOT contact Keycloak, expose Keycloak exchange routes
or show Keycloak login choices, and missing Keycloak configuration SHALL NOT
break startup or health checks. With the flag on, CARE SHALL validate all
required settings and URL safety at startup. OIDC discovery, issuer, audience,
signature and nonce validation SHALL fail closed during an exchange.

CARE SHALL validate only the Keycloak token contract: signature, issuer,
audience, expiry and subject. CARE SHALL NOT branch on or reproduce how
Keycloak authenticated the person. Passwords, OTP, passkeys, social login,
enterprise federation, recovery and MFA remain Keycloak concerns.

When the flag is enabled, Keycloak tokens SHALL be exchanged at the CARE
backend for the existing CARE token type appropriate to the audience:

- `care-workforce` resolves an existing `User` and issues the existing staff
  access/refresh token pair;
- `care-patient` resolves an existing `Patient` and issues a `PatientToken`.

Application APIs SHALL NOT accept raw Keycloak tokens directly.

### 3. Use direct Firebase paths for patient SMS and email

Patients SHALL have three visible login choices:

1. `Continue by SMS` — Firebase managed phone OTP;
2. `Continue by email` — Firebase managed one-time passwordless email link; and
3. `Use another login method` — Keycloak OIDC, shown only when enabled.

The third option may result in password, OTP, passkey or a federated provider,
but the UI and CARE SHALL treat all of them simply as Keycloak authentication.

Firebase SHALL replace only the direct proof-of-contact step. After Firebase
verifies the phone or email, CARE validates the Firebase ID token and issues a
`PatientToken`. CARE SHALL NOT generate, persist or compare the Firebase SMS
code or email action code.

In this ADR, `email OTP` means the one-time passwordless email credential
provided by Firebase, implemented as a single-use link rather than a numeric
code. Requiring a numeric email code would need a different email OTP provider
or an extension of CARE's existing OTP storage and is not selected here.

### 4. Make only the required model additions

`Patient` SHALL gain:

- one optional normalized email field; and
- one optional unique Keycloak subject field for the patient OIDC client.

`User` SHALL gain one optional unique Keycloak subject field for the workforce
OIDC client.

No generic contact table, external-identity registry or account-linking
framework is introduced by this ADR.

The existing `PatientToken` and patient principal SHALL be extended only as
needed to support:

```text
firebase phone -> verified normalized phone
firebase email -> verified normalized email
keycloak       -> resolved patient identity
```

Existing phone-based patient and booking filters SHALL be extended for email
and Keycloak-authenticated patients. The current contact-based household and
enrollment behavior is otherwise unchanged.

### 5. Keep provider exchange paths explicit

The implementation SHALL add bounded server-side exchanges rather than a new
general authentication platform:

```text
Firebase patient evidence -> PatientToken
Keycloak patient evidence  -> PatientToken
Keycloak workforce evidence -> existing staff JWT pair
```

Each exchange SHALL allowlist its exact issuer, audience and redirect/client
configuration. A token intended for `care-patient` SHALL never enter the
workforce exchange, and a `care-workforce` token SHALL never enter the patient
exchange.

Keycloak subject assignment to an existing CARE `User` or `Patient` SHALL be
an explicit administrative enrollment step. CARE SHALL not create staff users,
roles or patient records merely because Keycloak produced a valid token.

### 6. Apply proportional clinical and abuse controls

The implementation SHALL:

- use OIDC Authorization Code with PKCE for browser Keycloak login;
- use Firebase web reCAPTCHA and abuse controls for phone login;
- initially allow Firebase SMS delivery only to Mexico;
- return generic failures that do not reveal whether a user or patient exists;
- avoid clinical context in authentication SMS and email;
- avoid logging raw provider tokens or full phone/email values;
- mask contacts in UI responses;
- record principal type, provider and outcome using opaque CARE identifiers;
- keep provider secrets in Secret Manager and out of frontend bundles; and
- use synthetic users, Firebase test numbers, emulation or fakes in automated
  and non-production acceptance tests.

Because Google states that phone numbers used for Firebase phone
authentication are sent to and stored by Google for fraud and abuse
prevention, the patient-facing notice SHALL disclose that processing before
production use.

### 7. Keep Keycloak dormant until an operator enables it

Firebase patient login SHALL be controlled independently from the single
Keycloak integration flag. While `KEYCLOAK_ENABLED` is false, existing staff
login remains active and patients use the enabled direct CARE/Firebase paths.
No Keycloak runtime, database, realm or client is required in this state.

Before enabling the flag, an operator SHALL supply a reachable Keycloak issuer,
create the `care-workforce` and `care-patient` clients, register the documented
callbacks, store client secrets in Secret Manager and enroll the corresponding
CARE subjects. Activating the prepared integration then requires configuration
and deployment only, with no source edit, migration or new application build.

The legacy paths SHALL be disabled and later removed only after their
replacements are proven and supported clients have migrated. Production
activation remains subject to ADR-0009 and ES-09.

## Implementation handoff

The implementation owner should deliver the smallest coherent change in this
order:

1. add optional patient email and optional Keycloak subject fields to `Patient`
   and `User`, including migrations, validation and administration support;
2. implement the disabled-by-default Keycloak configuration contract, startup
   validation and route/UI gating;
3. implement strict Keycloak discovery/token validation and the two explicit
   CARE exchanges against test doubles or a disposable test instance;
4. document the realm, separate `care-workforce` and `care-patient` OIDC
   clients, audiences, secrets and redirect URIs an operator must create;
5. configure Firebase Authentication and implement server-side Firebase ID
   token validation for patient login;
6. extend the current patient principal, `PatientToken` and public endpoint
   filters for phone, email and a Keycloak-resolved patient;
7. implement the three patient UI choices, hiding the Keycloak option while
   disabled, and redirect consultorio login to Keycloak only when enabled;
8. test issuer/audience confusion, expiry, disabled subjects, wrong principal
   type, unverified email, SMS abuse, generic failures and current CARE
   authorization boundaries;
9. verify both disabled operation and enabled operation in automated tests,
   then migrate synthetic dev and staging users when a Keycloak test service is
   available; and
10. leave production login changes disabled until the clinical activation and
    operator approval gates are complete.

The future Keycloak operator SHALL document backup, restore, upgrades, admin
access and secret rotation before production activation. Provisioning and
operating that Keycloak service is not part of the current implementation.
Detailed role redesign is not part of this ADR: existing CARE roles remain
authoritative.

## Consequences

### Positive

- CARE can adopt Keycloak later without another application code change.
- Until then, CARE runs normally without a Keycloak service or its operational
  cost.
- Once enabled, consultorio authentication is centralized in Keycloak without
  moving CARE authorization into Keycloak.
- Patients can choose SMS, email or any method configured behind Keycloak.
- CARE does not know whether Keycloak used password, OTP, passkey or federation.
- Existing `User`, `Patient`, staff JWT and `PatientToken` concepts remain in
  place.
- Workforce and patient tokens are separated by audience, exchange endpoint and
  CARE model.

### Negative

- Enabling Keycloak later adds a service, database, backup and upgrade
  responsibility.
- Patient login has two external providers and three paths to test.
- A Keycloak subject field must be administered on both CARE principal models.
- Existing login paths coexist temporarily during migration.
- The dormant adapter must remain covered by tests so it does not rot before
  its first real deployment.

### Risks and mitigations

- **Patient/workforce confusion:** separate OIDC clients, audiences, exchanges
  and target models.
- **Forged provider token:** strict signature, issuer, audience and expiry
  validation on the CARE backend.
- **Invalid or unavailable Keycloak:** disabled mode never contacts it; enabled
  mode validates configuration and fails closed; rollback uses the same flag.
- **Dormant integration drift:** contract tests cover OIDC discovery, both
  audiences and both exchanges before a real Keycloak deployment exists.
- **Firebase SMS abuse:** reCAPTCHA, Mexico-only policy and quotas.
- **Account enumeration:** uniform responses and observable behavior.
- **Provider-specific authorization:** exchange to existing CARE tokens and
  keep roles and clinical permissions in CARE.

## Alternatives rejected

### Defer all Keycloak code until a server exists

Rejected because it would require another CARE code change later. The adapter
and its configuration contract are in scope now; only the external Keycloak
runtime and its activation are deferred.

### Let CARE understand Keycloak authentication methods

Rejected because Keycloak must be free to change between password, OTP,
passkey and federation without CARE code changes.

### Use one undifferentiated Keycloak client for everyone

Rejected because patient and workforce tokens could be confused at the CARE
boundary.

### Replace CARE authorization with Keycloak roles

Rejected because existing facility, organization, staff and patient access
rules remain authoritative and are outside this bounded login change.

### Introduce a generic identity registry now

Rejected as unnecessary. Optional Keycloak subject fields on the two existing
principal models are sufficient for the selected providers.

### Exchange Firebase tokens inside Keycloak

Rejected because it couples the direct patient paths to Keycloak and adds a
custom or non-standard token-exchange dependency. CARE can validate Firebase
evidence and issue the existing `PatientToken` directly.

## Implementation status

- [x] Architectural decision accepted.
- [x] Patient email and Keycloak subject fields implemented.
- [x] User Keycloak subject field implemented.
- [x] Disabled-by-default Keycloak configuration and gating implemented.
- [x] Keycloak workforce and patient exchanges implemented and contract-tested.
- [x] Realm and two-client operator configuration documented
      (replaced by `docs/xii/operations/oidc-provider-guide.md` under ADR-0011).
- [ ] External Keycloak runtime available (deferred; not required now).
- [ ] Workforce and patient OIDC clients configured (deferred until runtime).
- [x] Firebase patient token exchange implemented.
- [x] Mexico-only SMS policy enforced at the CARE boundary.
- [x] SMS, email and Keycloak patient UI implemented, each gated by its flag.
- [x] Consultorio login ready behind the flag; password and CARE MFA unchanged.
- [x] Patient session generalized past the phone-only assumption.
- [x] Firebase Authentication configured on the dev project, SMS restricted to
      Mexico, phone and passwordless email sign-in enabled.
- [x] Dev acceptance completed for the dormant state and for both enabled
      Firebase paths against the real dev project.
- [ ] Staging acceptance. Deferred; dev must be stable and explicitly
      authorized first.
- [ ] Production authentication approved and enabled.

## Related documents and sources

- `docs/xii/adr/ADR-0009-controlled-production-activation.md`
- `docs/xii/implementation/ES-09-production-readiness.md`
- `care/users/models.py`
- `care/emr/models/patient.py`
- `care/emr/api/otp_viewsets/login.py`
- `care/emr/api/otp_viewsets/patient.py`
- `care/emr/api/otp_viewsets/slot.py`
- `config/auth_views.py`
- `config/patient_otp_authentication.py`
- `config/patient_otp_token.py`
- Firebase phone authentication:
  `https://firebase.google.com/docs/auth/web/phone-auth`
- Firebase passwordless email-link authentication:
  `https://firebase.google.com/docs/auth/web/email-link-auth`
- Keycloak server administration and identity brokering:
  `https://www.keycloak.org/docs/latest/server_admin/`
- Keycloak securing applications with OIDC:
  `https://www.keycloak.org/securing-apps/oidc-layers`
