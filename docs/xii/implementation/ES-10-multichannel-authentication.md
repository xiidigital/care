# ES-10: Multichannel Authentication Completion and Dormant Keycloak Readiness

- **Status:** CLOSED — implemented and verified 2026-09-01; revalidated against the ES-08/09 baseline 2026-09-02
- **Related ADR:** ADR-0010: Multichannel Authentication with Keycloak and Firebase
- **Depends on:** ES-09 technical closeout and the current local ADR-0010 backend work
- **Implementation repositories:** `care` and `care_fe`
- **Working environment:** dev
- **Production activation:** explicitly out of scope

## 1. Objective

Complete the smallest coherent implementation of ADR-0010 without redesigning
CARE authentication.

Patients SHALL be able to use the following direct choices when their
corresponding configuration is enabled:

1. Firebase SMS authentication;
2. Firebase passwordless email-link authentication; and
3. Keycloak authentication.

Consultorio users SHALL keep the existing CARE login and SHALL gain a Keycloak
option when Keycloak is enabled. Keycloak may internally use password, OTP,
passkey or federation; CARE SHALL neither know nor reproduce that choice.

No Keycloak runtime exists for this phase. The integration must be complete,
tested against controlled doubles and disabled by default, so a future operator
can activate it with configuration only and without another source change.

## 2. Non-negotiable scope

This specification is an incremental completion of the current CARE design. It
SHALL NOT introduce:

- a new identity platform or generic external-identity registry;
- a new patient or workforce principal model;
- automatic creation of a `User` or `Patient` from an external identity;
- automatic association of an external identity with a clinical record;
- authorization rules in Firebase or Keycloak;
- Twilio, Brevo SMS, Amazon Cognito or a custom OTP service;
- a deployed Keycloak server, realm database or Keycloak infrastructure;
- production authentication activation or real patient data;
- unrelated role, permission, UI or infrastructure redesign.

Existing CARE authorization, `User`, `Patient`, staff JWTs and `PatientToken`
remain authoritative.

## 3. Mandatory starting procedure

Claude SHALL begin by reading:

```text
care/docs/xii/adr/ADR-0010-multichannel-authentication-with-keycloak.md
care/docs/xii/implementation/ES-09-production-readiness.md
care/docs/xii/architecture/07-configuration-reference.md
care_fe/AGENTS.md
```

Before editing either repository, report independently in `care` and `care_fe`:

```text
git status --short
git branch --show-current
git diff --stat
git diff --name-only
```

The `care` worktree already contains uncommitted ADR-0010 implementation work.
Claude is not alone in the repository and SHALL preserve it. Do not reset,
checkout, overwrite, revert or mechanically replace existing local changes.
Inspect them first and extend or correct them only where this ES requires.

Do not push, deploy, run `tofu apply`, modify live Firebase/Google Cloud
configuration or create external accounts unless the operator separately and
explicitly authorizes that action.

## 4. Verified implementation baseline

The current local backend work already provides:

- optional normalized `Patient.email`;
- optional unique `Patient.keycloak_subject`;
- optional unique `User.keycloak_subject`;
- migrations and administration support for those fields;
- a disabled-by-default Firebase patient exchange;
- server-side Firebase ID-token verification;
- Firebase phone and verified-email evidence converted to a `PatientToken`;
- a disabled-by-default Keycloak configuration contract;
- separate workforce and patient Keycloak code exchanges;
- Authorization Code with PKCE and nonce validation;
- strict Keycloak discovery, issuer, audience and signature checks;
- existing staff JWT issuance for an enrolled workforce subject;
- `PatientToken` issuance for an enrolled patient subject;
- patient lookup/filter support for patient ID, email and phone;
- backend and dev-infrastructure configuration examples; and
- focused backend tests for the new exchanges and identity filtering.

At the last verified checkpoint, the focused backend suite passed 45 tests,
including 36 new authentication tests and 9 existing phone-OTP booking
regressions. `makemigrations --check --dry-run`, lint checks and dev OpenTofu
validation also passed. Claude SHALL rerun relevant checks after its changes
rather than assuming the worktree is unchanged.

## 5. Known gaps to complete

The feature is not complete merely because backend exchange endpoints exist.
The implementation owner SHALL:

1. audit and finish the backend foundation against ADR-0010;
2. add the frontend Firebase SDK and safe public configuration contract;
3. implement Firebase SMS with web reCAPTCHA;
4. implement Firebase passwordless email-link login and callback handling;
5. exchange Firebase ID tokens for CARE `PatientToken` values;
6. adapt patient-token client state so it is not phone-only;
7. implement patient Keycloak Authorization Code + PKCE initiation, callback
   and CARE exchange, hidden while disabled;
8. implement workforce Keycloak initiation, callback and CARE JWT storage,
   hidden while disabled;
9. keep existing CARE workforce login available while Keycloak is dormant;
10. cover login selection, callbacks, failures and disabled operation with
    automated tests;
11. document future Keycloak operator configuration; and
12. produce synthetic dev acceptance evidence without activating production.

## 6. Backend contract

### 6.1 Firebase patient exchange

The prepared endpoint is:

```text
POST /api/v1/auth/firebase/patient/exchange/
```

Request and successful response:

```json
{ "id_token": "firebase-id-token" }
{ "access": "care-patient-token" }
```

Accept only Firebase phone authentication with a validated normalized phone
number, or Firebase passwordless email authentication with
`email_verified=true` and a normalized email address.

The direct patient exchange SHALL not create a patient. The issued CARE token
may identify matching patient records through existing phone/email behavior,
and normal CARE authorization continues to apply.

SMS SHALL initially be limited to Mexico. Enforce this coherently through the
frontend country policy, Firebase project policy documented for the operator,
and backend validation of the verified phone identity. Automated tests SHALL
prove that an out-of-policy number cannot obtain a CARE patient token.

### 6.2 Keycloak exchanges

The prepared endpoints are:

```text
POST /api/v1/auth/keycloak/workforce/exchange/
POST /api/v1/auth/keycloak/patient/exchange/
```

Request:

```json
{
  "code": "authorization-code",
  "code_verifier": "pkce-verifier",
  "nonce": "oidc-nonce",
  "redirect_uri": "exact-registered-callback"
}
```

Workforce success returns the existing CARE access/refresh pair. Patient
success returns the existing CARE patient access token.

The frontend SHALL generate a cryptographically random PKCE verifier, S256
challenge, OAuth state and OIDC nonce. Callback processing SHALL verify state
before calling CARE and use the exact redirect URI used to start the flow.
Temporary transaction material SHALL be short-lived, single-use and removed
after success or failure.

The backend SHALL continue to reject wrong issuer/audience, expired or invalid
tokens, nonce or redirect mismatch, cross-principal tokens, disabled or missing
workforce accounts, unknown subjects and incomplete enabled configuration. No
raw Keycloak token may be accepted by normal CARE APIs.

### 6.3 Route and startup gating

When `FIREBASE_AUTH_ENABLED=false`, Firebase exchange routes SHALL be absent,
missing Firebase credentials SHALL not break startup, and Firebase patient
choices SHALL not be rendered.

When `KEYCLOAK_ENABLED=false`, Keycloak routes SHALL be absent, startup and
health checks SHALL not contact Keycloak, no Keycloak configuration SHALL be
required, no Keycloak choice SHALL be rendered, and current CARE workforce
login SHALL continue to work.

When either flag is true, all settings required for that provider SHALL be
validated early and failures SHALL be explicit without exposing secrets.

## 7. Frontend behavior

### 7.1 Patient entry

Replace the phone-only assumption with explicit, accessible choices for enabled
methods:

```text
Continue by SMS
Continue by email
Use another login method
```

`Use another login method` means Keycloak and SHALL appear only when Keycloak
is enabled. It must not list or predict mechanisms configured inside Keycloak.

Generalize the current `TokenData.phoneNumber` requirement so email- and
Keycloak-authenticated patients can use patient home and public booking without
fabricated phone values. Preserve compatible stored phone sessions where
practical and invalidate malformed or obsolete state safely.

The UI SHALL avoid account enumeration, mask contacts, prevent duplicate
submissions, provide accessible labels/focus/status messages, retain only a
same-origin destination, and never log provider tokens, codes, nonce or PKCE
verifier.

### 7.2 Firebase SMS

Use Firebase web phone authentication and its supported reCAPTCHA flow. CARE
SHALL not request, store or compare the SMS code on its backend.

After Firebase verifies the code, obtain its ID token, send it once to the CARE
Firebase exchange, store only the returned CARE patient token through the
existing lifecycle, and discard transient Firebase authentication state.

Before sending the SMS, disclose concisely that Google receives and stores the
phone number for spam and abuse prevention. Do not include clinical context in
messages.

### 7.3 Firebase email link

Use Firebase passwordless email-link authentication, not a custom numeric email
code. Configure an allowed callback URL, remember only the minimum email state
needed for the same-device flow, and allow safe email re-entry when the link
opens on another device.

After email-link sign-in, exchange the verified Firebase ID token for a CARE
patient token. Remove email-link query parameters and temporary state after
processing.

### 7.4 Consultorio login

Keep existing username/password and CARE MFA unchanged. When Keycloak is
enabled, add a separate Keycloak action. Its callback SHALL exchange through
the workforce endpoint, store the CARE access/refresh pair through the existing
session lifecycle and load normal current-user authorization state.

Do not map Keycloak roles to CARE roles.

## 8. Configuration contract

Use repository conventions for exact names and provide tracked examples. The
backend contract includes at least:

```text
FIREBASE_AUTH_ENABLED=false
FIREBASE_AUTH_PROJECT_ID=

KEYCLOAK_ENABLED=false
KEYCLOAK_ISSUER_URL=
KEYCLOAK_WORKFORCE_CLIENT_ID=
KEYCLOAK_WORKFORCE_CLIENT_SECRET=
KEYCLOAK_PATIENT_CLIENT_ID=
KEYCLOAK_PATIENT_CLIENT_SECRET=
KEYCLOAK_PUBLIC_BASE_URL=
```

Secret values SHALL use the existing secret mechanism and SHALL never enter a
frontend bundle or committed environment file. The prepared Firebase backend
verifier uses Google's public signing certificates and the configured project
ID; do not add an administrative Firebase credential unless a separately
reviewed implementation proves it is necessary.

Frontend public build configuration SHALL explicitly cover Firebase enabled,
Firebase public web configuration, Keycloak enabled, public issuer information,
the two public client IDs, and exact workforce, patient and Firebase email
callback URLs. Firebase web configuration and client IDs are public identifiers;
Keycloak client secrets are backend-only.

Frontend/backend inconsistency SHALL fail safely. A visible method must never
lead to an intentionally absent backend route in a correctly built environment.

## 9. Future Keycloak operator guide

Create a tracked guide sufficient to enable Keycloak without changing CARE
code. Document:

- supported issuer/discovery requirements;
- one realm with separate `care-workforce` and `care-patient` clients;
- confidential-client and PKCE expectations matching the implementation;
- exact audiences and callback URLs;
- origin/logout URL restrictions if used;
- secret storage;
- explicit enrollment of both Keycloak subject fields;
- synthetic pre-activation testing;
- flag-based enablement and rollback;
- secret rotation; and
- backup, restore, upgrade and administrator responsibilities.

Warn that production enablement remains governed by ES-09. Do not invent realm
credentials or claim live verification without a real runtime.

## 10. Security and regression tests

Tests SHALL prove at least:

- provider routes and choices are unavailable while disabled;
- disabled startup needs no provider credentials or network;
- Firebase accepts verified phone and verified email-link identities;
- Firebase rejects unverified email, wrong project/audience, expired tokens,
  unsupported providers and non-Mexico SMS identities;
- unknown identities receive generic failures;
- patient identity cannot obtain workforce tokens and vice versa;
- PKCE, nonce, state and redirect checks fail closed;
- callback state is single-use and cleared;
- secrets/raw provider tokens are absent from logs and UI errors;
- current staff password/MFA login remains functional;
- current patient phone OTP and booking tests do not regress while legacy
  support remains; and
- patient lookup works with phone, email and resolved patient ID tokens.

Avoid live SMS/email in automation. Use Firebase emulator support, documented
test numbers or narrow doubles, plus a standards-faithful OIDC double.

## 11. Validation procedure

Use the real commands discovered in each repository. At minimum report:

### Backend

```text
focused Firebase, Keycloak, patient identity and legacy OTP tests
makemigrations --check --dry-run
configured lint/format checks for changed files
Django startup/system checks with both providers disabled
startup validation tests for enabled but incomplete configuration
```

### Frontend

```text
npm run lint
npm run build
focused authentication state and callback tests
relevant Playwright patient and workforce login journeys
```

### Infrastructure/configuration

```text
tofu fmt -check for changed OpenTofu files
tofu validate for any changed environment root
secret and tracked-environment-file scan
```

Do not run a live plan or apply merely to satisfy this ES.

## 12. Dev acceptance

Use synthetic dev identities to demonstrate:

1. existing consultorio login succeeds with Keycloak disabled;
2. startup and health checks succeed with both providers disabled;
3. enabled Firebase configuration exposes SMS and email choices;
4. a Firebase test phone enters the patient flow;
5. a verified Firebase test email enters the patient flow;
6. invalid, expired and unknown identities receive generic failures;
7. Keycloak choices remain hidden because no runtime is activated now; and
8. both Keycloak audience contracts pass using controlled doubles.

Staging may follow only after dev is stable and explicitly authorized.
Production remains unchanged.

## 13. Completion criteria

ES-10 is complete only when:

- [x] existing backend work has been reviewed and preserved;
- [x] backend migrations and identity exchanges are complete;
- [x] provider configuration is documented and disabled by default;
- [x] Firebase SMS patient login works end to end — verified against the real
      dev Firebase project with a documented test number: a Google-signed ID
      token was exchanged for a `PatientToken` that reached the matching
      patient record;
- [x] Firebase email-link patient login works end to end — verified the same
      way with a verified synthetic email identity, and the issued
      `PatientToken` carries no fabricated phone number;
- [x] patient client state no longer assumes every identity has a phone;
- [x] patient Keycloak UI/callback code exists and is gated off;
- [x] workforce Keycloak UI/callback code exists and is gated off;
- [x] existing workforce login remains functional;
- [x] the operator guide permits configuration-only Keycloak activation;
- [x] required negative, regression and frontend tests pass;
- [x] synthetic dev Firebase acceptance passes against the real dev project,
      using a documented test number and a synthetic verified address;
- [x] no real Keycloak service was required;
- [x] production authentication was not enabled;
- [x] no real patient data was used; and
- [x] final evidence lists tests, builds, limitations and operator actions.

All criteria are demonstrated.

Two limits are worth stating precisely rather than hiding behind a tick:

- **The browser reCAPTCHA step is not automated.** Firebase escalates its
  invisible reCAPTCHA to a visual challenge under headless automation, and
  solving CAPTCHAs is out of bounds. The SMS journey is therefore verified in
  two halves that meet at the ID token: the UI half by Playwright against an
  enabled build, and the token-to-CARE half with a real Google-signed token
  obtained through Firebase's documented test-number REST flow.
- **Keycloak still has no runtime**, by design. Its exchanges are proven
  against a standards-faithful OIDC double and remain dormant.

Passing backend tests alone is not completion. A document-only Keycloak design
is not completion either: dormant frontend and backend paths must exist and
remain covered by tests.

## 14. Required handoff report

At the end, Claude SHALL report separately for `care` and `care_fe`:

```text
files changed
behavior implemented
migrations added or changed
tests and exact results
build and lint results
configuration variables added
secrets or external configuration still required
dev acceptance performed
known limitations
production state: unchanged
```

Do not describe an unperformed check as passed. Do not mark ES-10 implemented
until every applicable completion item has evidence.
