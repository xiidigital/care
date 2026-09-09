# ES-11: Standard OIDC Authentication and External Identity Linking

- **Status:** PROPOSED — not approved, not started
- **Related ADR:** ADR-0011: Standard OIDC Authentication with Keycloak as a
  Reference Implementation
- **Amends the implementation of:** ADR-0010 / ES-10
- **Depends on:** ES-10 closed baseline (2026-09-01, revalidated 2026-09-02)
- **Implementation repositories:** `care` and `care_fe`
- **Working environment:** local and dev
- **Production activation:** explicitly out of scope (ADR-0009, ES-09)

## 1. Objective

Execute ADR-0011: turn the dormant, Keycloak-shaped adapter delivered by ES-10
into a provider-agnostic OIDC integration with an explicit external-identity
model, without changing CARE's authorization, without removing any existing
login method, and without making any external service a requirement.

ES-11 is a **generalisation of working code**, not a rewrite. The OIDC flow
ES-10 shipped is correct as far as it goes; ES-11 widens its contract, fixes
the identity model underneath it, and removes the vendor from its surface.

## 2. Current state

Verified against the repository at `6c559e22a` on branch `gcp`.

### 2.1 Backend — what exists and works

| Area | File | State |
|---|---|---|
| Config contract | `config/keycloak.py` | Six required settings; URL safety check; validated only when enabled and only in the API role |
| OIDC exchange | `config/keycloak_service.py` | Discovery, issuer equality, endpoint-origin pinning, code+PKCE token call, JWKS fetch, `authlib` claim validation with 30s leeway, nonce equality |
| Exchange views | `config/keycloak_views.py` | Two views, 404 while disabled, per-IP `10/m` rate limit, generic `AuthenticationFailed`, workforce → SimpleJWT pair, patient → `PatientToken` |
| Routes | `config/keycloak_urls.py` | `build_keycloak_urlpatterns(enabled=…)` returns `[]` while disabled — no route exists at all |
| Firebase | `config/firebase_auth*.py` | Independent flag, project-id validation, Mexico-only SMS policy, phone and verified-email → `PatientToken` |
| Patient principal | `config/patient_otp_authentication.py` | `patient_access_queryset` resolves by `patient_id`, then `email`, then `phone_number`; audit label uses a keyed contact fingerprint |
| Models | `care/emr/models/patient.py`, `care/users/models.py` | `Patient.email`, `Patient.keycloak_subject`, `User.keycloak_subject` — all nullable, subjects unique |
| Migrations | `emr/0084`, `users/0028` | Applied |
| Tests | `care/users/tests/test_keycloak_{auth,service,flow}.py`, `test_external_auth_startup.py`, `care/emr/tests/test_patient_identity_{auth,api}.py` | Exchanges driven against a standards-faithful OIDC double |

### 2.2 Frontend — what exists and works

`care_fe` on branch `gcp` already carries the whole dormant surface:
`src/Utils/auth/externalAuthConfig.ts` (public build config, incomplete ⇒
disabled), `loginMethods.ts` (OTP never removed by enabling a provider),
`oidcTransaction.ts` (PKCE/state/nonce in `sessionStorage`, single-use, 10-min
TTL), `firebaseAuthClient.ts`, `emailLinkState.ts`, `patientSession.ts`,
`components/Auth/external/*`, `pages/Auth/KeycloakCallback.tsx`,
`pages/Auth/FirebaseEmailLinkCallback.tsx`, and unit tests for each helper.

### 2.3 Gaps ES-11 must close

1. **Subject-only resolution.** `PatientKeycloakExchangeView.issue_care_token`
   runs `Patient.objects.get(keycloak_subject=subject)`; the workforce view is
   the same shape. Neither consults the issuer. Safe today only because
   `config/keycloak.py` admits exactly one issuer.
2. **One issuer, two fixed clients.** `KEYCLOAK_ISSUER_URL` plus a hardcoded
   `care-workforce` / `care-patient` split. A second provider cannot be
   expressed.
3. **One identity per principal.** A unique `CharField` cannot hold a clinician
   who arrives through two providers, and cannot survive a provider migration
   without a destructive overwrite.
4. **Vendor naming throughout.** Settings, modules, routes, model fields,
   `REACT_KEYCLOAK_*`, `KeycloakCallback.tsx`, `useKeycloakRedirect`,
   `WorkforceKeycloakButton`.
5. **No linking surface.** `keycloak_subject` is set through Django admin only.
   No self-service link, no unlink, no re-link refusal, no audit record.
6. **JWKS and discovery are fetched on every exchange.** Two network round
   trips per login, no cache, no bounded refresh, and an issuer outage or
   slow response is felt on every attempt.
7. **No algorithm allowlist and no `azp` check.** `authlib` validates the
   `aud` value, but a multi-valued `aud` with an unchecked `azp` is not
   constrained, and the accepted algorithm set is whatever the JWKS offers.
8. **No logout story.** `end_session_endpoint` is not read; there is no
   RP-initiated logout and no explicit statement that CARE's session ends
   locally regardless.
9. **Fixed callback paths.** Redirect URIs are derived as
   `{KEYCLOAK_PUBLIC_BASE_URL}/auth/keycloak/{principal}/callback` in
   `_client_for`, so the vendor name is baked into a URL an operator must
   register with their IdP.

## 3. Scope

### 3.1 In scope

- Provider configuration record, loader, validation and startup checks.
- `UserExternalIdentity` and `PatientExternalIdentity` with a
  `(provider_id, issuer, subject)` uniqueness constraint.
- Forward and reverse data migration from the two `keycloak_subject` columns;
  removal of those columns.
- Triple-based principal resolution; elimination of every subject-only lookup.
- JWKS caching with bounded refresh, algorithm allowlist, `azp` handling.
- Provider-agnostic backend modules, settings, routes and callback paths.
- Administrative linking/unlinking, and self-service linking for an
  already-authenticated workforce principal, both audited.
- Optional RP-initiated logout, with unconditional local session termination.
- Provider-agnostic frontend configuration, transaction helper, callback route
  and components; the login screen renders providers from configuration.
- A local Keycloak container fixture and a provider-agnostic OIDC double, with
  one conformance suite run against both.
- Rewritten operator guide, configuration reference and environment examples.
- The OTP × Firebase × OIDC matrix, including the all-external-disabled state.

### 3.2 Out of scope

- Any change to CARE authorization: roles, permissions, facility or
  organization scoping, record access.
- Claim-to-role mapping in any form.
- Auto-provisioning of `User`, `Patient` or identity rows.
- Changing Firebase behaviour, or modelling Firebase as an OIDC provider.
- Removing or deprecating CARE phone OTP.
- Back-channel or front-channel logout; token revocation; refresh-token
  rotation against the provider; long-lived provider sessions.
- Dynamic client registration; client-credentials, device-code, implicit or
  hybrid flows; SAML; LDAP.
- Deploying, hosting or operating a Keycloak service in any CARE environment.
- Production activation, real patient data, GitHub environment changes, new
  GCP permissions, `tofu apply`, or live provider configuration.
- Multi-tenant provider scoping (per-facility providers). Noted as a plausible
  follow-up; a single provider set serves the whole deployment in ES-11.
- Unrelated UI, role or infrastructure redesign.

## 4. Concrete architecture

```text
                    ┌──────────────────────────────────────────┐
   browser          │  CARE backend                            │
   ───────          │                                          │
   login screen ────┼─> GET /api/v1/auth/providers/            │  public, non-secret
     renders        │      [{id, display_name, principal_type, │  provider list
     methods from   │        issuer, client_id, authorize_url}]│
     this list      │                                          │
                    │                                          │
   PKCE + state     │                                          │
   + nonce in       │                                          │
   sessionStorage   │                                          │
        │           │                                          │
        └── redirect to provider's authorization_endpoint ──────┼──> IdP
                    │                                          │    (Keycloak,
   /auth/oidc/      │                                          │     Entra,
     callback ──────┼─> POST /api/v1/auth/oidc/{principal}/    │     Authentik,
     (state check)  │          exchange/                       │     …)
                    │      {provider_id, code, code_verifier,  │
                    │       nonce, redirect_uri}               │
                    │            │                             │
                    │            ├─ resolve provider record    │
                    │            ├─ discovery (cached)  ───────┼──> IdP
                    │            ├─ token call (code+PKCE) ────┼──> IdP
                    │            ├─ JWKS (cached, bounded) ────┼──> IdP
                    │            ├─ validate iss/aud/azp/exp/  │
                    │            │  iat/nonce/alg/sig          │
                    │            ├─ resolve identity row on    │
                    │            │  (provider_id, issuer, sub) │
                    │            │      └─ no row ⇒ generic 401│
                    │            └─ issue CARE credential      │
                    │                 workforce ⇒ access/refresh
                    │                 patient   ⇒ PatientToken │
                    └──────────────────────────────────────────┘
```

Four invariants hold this together and are each covered by a test:

1. The exchange is the **only** place an external token is ever accepted.
2. Resolution reads **only** the triple.
3. A missing identity row produces the **same** response as a bad signature.
4. Nothing downstream of the exchange can tell which provider was used.

## 5. Backend changes

### 5.1 Module renames

```text
config/keycloak.py          -> config/oidc.py              provider records + validation
config/keycloak_service.py  -> config/oidc_service.py      discovery, JWKS, exchange
config/keycloak_views.py    -> config/oidc_views.py        exchange + provider list views
config/keycloak_urls.py     -> config/oidc_urls.py         conditional route builder
```

`git mv` each file so history follows. Firebase modules are untouched.

### 5.2 Provider record

```python
@dataclass(frozen=True)
class OidcProvider:
    id: str                    # slug: ^[a-z0-9][a-z0-9_-]{0,31}$
    display_name: str
    issuer: str                # exact expected `iss`, no trailing slash
    principal_type: str        # "workforce" | "patient"
    client_id: str
    client_secret: str
    enabled: bool = True
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    allow_rp_logout: bool = False
```

Validation at load, for enabled providers only:

- `id` matches the slug pattern and is unique across the set;
- `issuer` passes `is_safe_oidc_url` (HTTPS, or HTTP for
  `localhost`/`127.0.0.1`/`::1`; no credentials, query or fragment);
- `principal_type` is one of the two literals;
- `client_id` and `client_secret` are non-empty;
- `openid` is present in `scopes`;
- the whole set is rejected — CARE refuses to start — if any enabled provider
  fails, with a message that names the provider `id` and the failing field and
  never echoes a secret.

Zero providers is valid and is the default.

### 5.3 Configuration loader

One setting, `OIDC_PROVIDERS`, accepted from either:

- `OIDC_PROVIDERS` — a JSON array, for Compose, Kubernetes and `.env`;
- `OIDC_PROVIDERS_FILE` — a path to the same JSON, for secret-manager volumes
  and GCP.

Exactly one of the two may be set. Secrets stay in whichever mechanism the
deployment already uses (ADR-0006); CARE reads a value, not a vault.

`config/settings/base.py` keeps the existing shape — validate only in the API
role, as the current `KEYCLOAK_ENABLED and CARE_PROCESS_ROLE == API_ROLE`
guard already does — and drops the six `KEYCLOAK_*` settings entirely. No
compatibility shim (ADR-0011 §Relationship with ADR-0010).

### 5.4 Routes

`build_oidc_urlpatterns(providers)` returns `[]` when no provider is enabled,
preserving ES-10's "no route exists while disabled" property. Otherwise:

```text
GET  /api/v1/auth/providers/                    public list, non-secret fields only
POST /api/v1/auth/oidc/workforce/exchange/      mounted iff a workforce provider is enabled
POST /api/v1/auth/oidc/patient/exchange/        mounted iff a patient provider is enabled
POST /api/v1/auth/oidc/link/                    self-service link; workforce; CARE-authenticated
DELETE /api/v1/auth/oidc/link/{identity_id}/    self-service unlink; workforce
```

`GET /api/v1/auth/providers/` returns `id`, `display_name`, `principal_type`,
`issuer`, `client_id` and `authorization_endpoint`. It never returns a secret,
is rate-limited, and returns `[]` rather than 404 when nothing is enabled — the
frontend must distinguish "no providers" from "backend unreachable".

Callback paths carry no vendor name: `/auth/oidc/workforce/callback` and
`/auth/oidc/patient/callback`, derived from the public base URL as today.

### 5.5 Exchange service

Extending `config/oidc_service.py` from the ES-10 implementation:

- **Discovery cache** — keyed by issuer, TTL 3600s, through CARE's configured
  cache backend (locmem, PostgreSQL or Redis; never requiring Redis).
- **JWKS cache** — keyed by `jwks_uri`, TTL 3600s. On an unknown `kid`, **one**
  refresh, rate-limited to once per 60s per issuer, then fail closed.
- **Algorithm allowlist** — `RS256`, `RS384`, `RS512`, `ES256`, `ES384`,
  `ES512`. `none` and every HMAC algorithm are rejected before verification.
- **Audience** — `aud` must contain `client_id`; when `aud` is multi-valued,
  `azp` must be present and equal `client_id`.
- **Issuer** — the discovery document's `issuer` must equal the provider
  record's, and `token_endpoint`/`jwks_uri`/`end_session_endpoint` must share
  its scheme and host. This is already implemented; keep it.
- **Nonce, `exp`, `iat`** — essential; 30s leeway, unchanged.
- **Redirect URI** — byte-for-byte equality with the provider's derived
  callback, unchanged.
- **Timeouts** — 5s per request, unchanged; total exchange budget 15s.
- **Errors** — one `OidcExchangeError` with no detail, as today.

### 5.6 Principal resolution

```python
def resolve_workforce(provider, claims) -> User      # UserExternalIdentity
def resolve_patient(provider, claims) -> Patient     # PatientExternalIdentity
```

Both filter on `provider_id=provider.id, issuer=provider.issuer,
subject=claims["sub"]`, and additionally require `is_active` for `User`. No
other field participates. A CI check (`grep` in the lint step) asserts that no
identity lookup filters on `subject` without `issuer` and `provider_id`.

### 5.7 Linking

- **Administrative** — a Django admin inline on `User`, plus management
  commands for scripted enrolment. Patient linking is administrative-only
  (ADR-0011 §5.3).

  CARE registers **no `Patient` admin**, so there is no admin screen to hang a
  patient inline on. The administrative path for patients is therefore
  `link_patient_external_identity`, which requires `--linked-by`: a patient
  link with no recorded actor is a link nobody is accountable for. An earlier
  draft of this section assumed an admin surface that does not exist.
- **Self-service (workforce only)** — a CARE-authenticated user starts a normal
  OIDC round trip flagged as a link, and the callback posts to
  `/api/v1/auth/oidc/link/`. The triple is linked to *the authenticated user*,
  never to a user named in the request.
- **Re-link refused** — a triple already linked raises a conflict, is audited,
  and returns a generic failure to the caller.
- **Unlink** — removes the row only. A last-identity unlink is permitted; the
  principal falls back to its other enabled methods.
- **Audit** — `link`, `unlink`, `login_success`, `login_failure`,
  `relink_refused`, each with principal type, `provider_id`, outcome and an
  opaque CARE identifier. Never a token, a raw subject or a contact value.

### 5.8 Logout

`POST /api/v1/auth/logout/` (workforce) clears CARE's session unconditionally.
When the identity used at login belongs to a provider with
`allow_rp_logout=true` and the discovery document advertises
`end_session_endpoint`, the response includes a URL the browser may then visit.
A missing, unreachable or unadvertised endpoint changes nothing about CARE's
side. `id_token` is **not** persisted for `id_token_hint`; the logout URL
carries `client_id` and `post_logout_redirect_uri` only.

### 5.9 Patient principal

`PatientToken` gains `provider_id` alongside the existing `auth_provider`.
`patient_access_queryset` is unchanged in shape — `patient_id` first — because
an OIDC-resolved patient already yields an exact `patient_id`. The audit label
gains the provider id. Phone and email resolution paths are untouched, so OTP
and Firebase regressions cannot be caused by this change.

## 6. Frontend changes

All in `care_fe`, branch `gcp`.

### 6.1 Renames and generalisation

```text
src/Utils/auth/externalAuthConfig.ts   KeycloakConfig      -> OidcConfig (provider list)
src/Utils/auth/oidcTransaction.ts      KeycloakPrincipal   -> OidcPrincipal; + providerId
src/components/Auth/external/useKeycloakRedirect.ts -> useOidcRedirect.ts
src/components/Auth/external/WorkforceKeycloakButton.tsx -> WorkforceOidcButtons.tsx
src/pages/Auth/KeycloakCallback.tsx    -> src/pages/Auth/OidcCallback.tsx
route /auth/keycloak/{principal}/callback -> /auth/oidc/{principal}/callback
```

### 6.2 Provider list from the backend

`REACT_KEYCLOAK_*` build variables are removed. The login screen fetches
`GET /api/v1/auth/providers/`, which removes a whole class of defect ES-10
guarded against manually: a frontend build can no longer advertise a provider
the backend does not have. Failure to fetch renders no external method and no
error banner — CARE's own methods are unaffected.

`REACT_FIREBASE_*` variables stay exactly as they are; Firebase configuration
is genuinely build-time public web config.

### 6.3 Login surfaces

- **Patient** — `PatientLoginMethods.tsx` renders `sms`, `email`, each enabled
  patient provider by `display_name`, and `legacy_otp`. `availablePatientLoginMethods`
  keeps its guarantee that enabling a provider never removes OTP.
- **Workforce** — username/password and CARE MFA unchanged, plus one button per
  enabled workforce provider.
- **Account settings** — a linked-identities panel listing `display_name` and a
  masked subject, with "Link another provider" and "Unlink" (workforce only).

A provider's `display_name` is operator-supplied text; render it as text, never
as markup, and never let it influence the authorization URL.

### 6.4 Transaction material

Unchanged in mechanism — `sessionStorage`, single-use, cleared on first read,
10-minute TTL — plus `providerId` in the stored transaction and a check that
the callback's provider matches the one the transaction started with.

## 7. Data model and migrations

### 7.1 Models

```python
class UserExternalIdentity(BaseModel):          # care.utils.models.base
    user          = FK(User, on_delete=CASCADE, related_name="external_identities")
    provider_id   = CharField(max_length=32, db_index=True)
    issuer        = CharField(max_length=512)
    subject       = CharField(max_length=255)
    linked_by     = FK(User, null=True, on_delete=SET_NULL, related_name="+")
    last_login_at = DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [UniqueConstraint(fields=["provider_id", "issuer", "subject"],
                                        condition=Q(deleted=False),
                                        name="unique_user_external_identity")]
```

`PatientExternalIdentity` is identical with `patient = FK("emr.Patient", …)` and
its own constraint name. `linked_by` records the administrator on the
administrative path and is null for a self-service link.

Three details settled on contact with the codebase.

**`BaseModel`, not `EMRBaseModel`.** `BaseModel` carries `external_id`,
timestamps and the soft delete this repository uses everywhere; `EMRBaseModel`
adds `history`/`meta` resource machinery these rows have no use for, and lives
in `care.emr` where a `care.users` model should not reach. `BaseModel` also
supplies `created_date`, so a separate `linked_at` would be a second name for
the same fact.

**The constraint is partial, on `deleted=False`.** That matches every other
unique constraint here (`unique_user_flag`, `unique_user_skill`) and it is
exactly what rules §5.5 and §5.6 need together: an unlinked identity stops
resolving, and its triple becomes available for a deliberate re-enrolment
rather than staying poisoned forever. A separate index on the triple would be
redundant — the partial unique index already serves the lookup, which always
filters `deleted=False` through the default manager.

**Cross-table uniqueness is a configuration invariant, not a database one.**
Two tables cannot share a constraint, so the database enforces the triple
within each table only. The same triple cannot legitimately appear in both,
because `provider_id` is part of it and a provider record declares exactly one
`principal_type` — so a provider is either workforce or patient, never both.
The guarantee is real but it is held by `config/oidc.py`, and an earlier draft
of this section credited it to the schema.

### 7.2 Migration sequence

| # | Migration | Operation | Reversible |
|---|---|---|---|
| 1 | `emr/0085`, `users/0029` | Create both tables and constraints | yes |
| 2 | `emr/0086`, `users/0030` | Data migration: for each non-null `keycloak_subject`, insert a row with `provider_id` from `OIDC_LEGACY_PROVIDER_ID` (default `keycloak`) and `issuer` from `OIDC_LEGACY_ISSUER` | yes, both directions written |
| 3 | `emr/0087`, `users/0031` | Remove `Patient.keycloak_subject` and `User.keycloak_subject` | yes |

Step 2 is expected to move **zero rows** in every existing environment, because
no subject has ever been enrolled. It is required to fail loudly rather than
guess if it finds rows and `OIDC_LEGACY_ISSUER` is unset — a subject with no
issuer cannot be migrated correctly, and silently inventing one would be
exactly the defect ES-11 exists to remove. Worse than inventing an issuer would
be dropping the rows and reporting success, so neither is permitted.

**Step 3 ships with phase 3, not phase 2.** The exchange views, the admin and
several test modules still resolve on `keycloak_subject`; dropping the column
before the resolution switch leaves the tree unbuildable between two phases
that are each supposed to end green. Steps 1 and 3 still reach the same
release, and the release still rolls back to the ES-10 schema by reversing
3 → 2 → 1.

**How step 2 is verified.** Its rule — refuse without an issuer, never drop a
row, reject a duplicate subject — lives in a pure function
(`care/utils/external_identity.py`) with unit tests. The schema round trip is
proved separately by running the migrations forward, backward and forward again
against a disposable database seeded with a `keycloak_subject` on both principal
types, because a `MigrationExecutor` test would have to migrate the shared
`--keepdb` database backwards across two apps and would poison it on failure.

`Patient.email` stays. It belongs to Firebase's email path, not to OIDC.

## 8. Configuration

### 8.1 Backend

```bash
# --- OIDC providers (optional; zero or more) ---------------------------
# Exactly one of OIDC_PROVIDERS or OIDC_PROVIDERS_FILE. Default: neither.
# OIDC_PROVIDERS='[{"id":"clinic-sso","display_name":"Clinic SSO",
#   "issuer":"https://sso.example.org/realms/care","principal_type":"workforce",
#   "client_id":"care-workforce","client_secret":"...","enabled":true}]'
# OIDC_PROVIDERS_FILE=/var/run/secrets/care/oidc-providers.json
# OIDC_PUBLIC_BASE_URL=            # public CARE origin; derives callback URLs
# OIDC_DISCOVERY_CACHE_SECONDS=3600
# OIDC_JWKS_CACHE_SECONDS=3600
# Only for a deployment that enrolled subjects under ADR-0010 (none known):
# OIDC_LEGACY_PROVIDER_ID=keycloak
# OIDC_LEGACY_ISSUER=
```

Removed: `KEYCLOAK_ENABLED`, `KEYCLOAK_ISSUER_URL`,
`KEYCLOAK_WORKFORCE_CLIENT_ID`, `KEYCLOAK_WORKFORCE_CLIENT_SECRET`,
`KEYCLOAK_PATIENT_CLIENT_ID`, `KEYCLOAK_PATIENT_CLIENT_SECRET`,
`KEYCLOAK_PUBLIC_BASE_URL`.

Unchanged: every `FIREBASE_AUTH_*` variable.

### 8.2 Frontend

Removed: all six `REACT_KEYCLOAK_*` variables — replaced by the backend
provider list. Unchanged: all `REACT_FIREBASE_*` variables.

### 8.3 Infrastructure

`infrastructure/` forwards `OIDC_PROVIDERS_FILE` and `OIDC_PUBLIC_BASE_URL` as
non-secret configuration and mounts the provider JSON from the existing secret
mechanism. `tofu fmt -check` and `tofu validate` run; **no plan, no apply**.

## 9. Local Keycloak for testing

A development-only fixture. It is not deployed to any environment, and deleting
it degrades the test suite and nothing else.

`docker-compose.oidc.yaml` (a separate file, not merged into
`docker-compose.local.yaml`, so `make up` is unchanged):

```yaml
services:
  oidc-test-issuer:
    image: quay.io/keycloak/keycloak:26.4
    command: ["start-dev", "--import-realm", "--http-port=8081"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin        # local only, never a secret
    volumes:
      - ./scripts/oidc/care-test-realm.json:/opt/keycloak/data/import/realm.json:ro
    ports: ["8081:8081"]
```

`scripts/oidc/care-test-realm.json` is a tracked realm export defining:

- realm `care-test`;
- confidential client `care-workforce`, PKCE S256 required, redirect
  `http://localhost:9000/auth/oidc/workforce/callback`;
- confidential client `care-patient`, same shape, patient callback;
- two synthetic users with fixed, obviously-fake credentials;
- no real data of any kind.

`make oidc-up` / `make oidc-down` / `make test-oidc` wrap it. Issuer:
`http://localhost:8081/realms/care-test` — accepted because `is_safe_oidc_url`
permits HTTP on localhost only.

**The same conformance suite runs twice**: once against this container
(`@pytest.mark.oidc_live`, skipped when the container is absent, so the default
`make test` and CI stay hermetic and fast) and once against the
provider-agnostic double from ES-10. A test that passes against Keycloak and
fails against the double has found vendor coupling, which is the whole reason
both exist.

## 10. OTP × Firebase × OIDC matrix

Every row is a supported configuration and a test.

| # | OTP | Firebase | OIDC | Patient methods | Workforce methods | Expected system state |
|---|---|---|---|---|---|---|
| 1 | on | off | none | OTP | password + MFA | Today's default. No external route mounted, no external config required, startup and health checks make no outbound call. |
| 2 | on | on | none | OTP, SMS, email | password + MFA | ES-10's dev state. Unchanged by ES-11. |
| 3 | on | off | workforce | OTP | password + MFA + provider(s) | Clinic with staff SSO; patients unaffected. |
| 4 | on | off | patient | OTP + provider(s) | password + MFA | Patients through an IdP; staff unchanged. |
| 5 | on | on | both | OTP, SMS, email, provider(s) | password + MFA + provider(s) | Maximal. Every method independently reachable. |
| 6 | off | on | both | SMS, email, provider(s) | password + MFA + provider(s) | Operator has retired OTP after migration. Explicit decision only. |
| 7 | off | off | both | provider(s) | provider(s) | IdP-only deployment. |
| 8 | on | off | 2 workforce providers | OTP | password + MFA + both | Two issuers; identical `sub` in each resolves to different principals or to none. |
| 9 | off | off | none | — | password + MFA | Startup **refuses**: no patient method enabled. |
| 10 | on | on | provider disabled mid-flight | OTP, SMS, email | password + MFA | Provider's routes and choices disappear on restart; identity rows retained, not deleted. |

Row 8 is the regression test for the ADR-0011 §3 defect. Row 9 is the only
refused configuration. Row 1 must stay green throughout ES-11 — it is the
proof that no external service became a requirement.

## 11. Threat model

| # | Threat | Vector | Control | Test |
|---|---|---|---|---|
| T1 | Cross-issuer subject collision | Provider B mints `sub` matching an enrolled provider-A identity | Resolution on the full triple; no subject-only lookup; CI grep | Matrix row 8 |
| T2 | Claim-based takeover | Attacker sets `email` to a victim's on an IdP they control | No claim but the triple resolves a principal; email non-authoritative | `test_email_claim_never_resolves` |
| T3 | Confused deputy across principal types | Patient-audience token posted to the workforce exchange | Provider declares one `principal_type`; audience mismatch rejected before lookup | `test_cross_principal_rejected` |
| T4 | Token substitution / audience confusion | Token minted for another RP at the same issuer | `aud` must equal `client_id`; `azp` checked when `aud` is multi-valued | `test_wrong_audience`, `test_multi_aud_requires_azp` |
| T5 | Algorithm confusion | `alg: none`, or HMAC signed with a public JWKS value | Allowlist RS/ES only, applied before verification | `test_alg_none_rejected`, `test_hmac_rejected` |
| T6 | Key confusion | Unknown `kid`, or a key fetched from an attacker origin | JWKS only from the discovered `jwks_uri`; endpoint origin pinned to issuer; one bounded refresh | `test_unknown_kid_bounded_refresh` |
| T7 | Replay | Captured code or ID token reused | PKCE S256; single-use `state`/`nonce` cleared on first read; `exp`/`iat` with 30s leeway | `test_nonce_replay_rejected` |
| T8 | Authorization-code injection | Code from another session delivered to this callback | PKCE verifier bound to the transaction; exact redirect URI; provider id must match the transaction | `test_code_injection_rejected` |
| T9 | Open redirect | Crafted `destination` after login | Same-origin `safeDestination` (already implemented); exact registered redirect URI | `test_destination_same_origin` |
| T10 | Silent re-binding | Provider record re-pointed at a hostile issuer | `issuer` stored on the identity row; a re-pointed provider stops resolving | `test_issuer_change_breaks_resolution` |
| T11 | Auto-provisioning escalation | Valid token, no enrolled identity | No auto-provisioning anywhere; generic 401 | `test_unknown_subject_creates_nothing` |
| T12 | Privilege escalation via claims | `realm_access`/`groups` in the token | No authorization claim is read; CI grep for claim names | `test_role_claims_ignored` |
| T13 | Account enumeration | Response differs for unknown vs invalid | One `AuthenticationFailed` message and status for every failure | `test_uniform_failure_responses` |
| T14 | Link hijack | Attacker links their identity to a victim principal | Self-service links only to the *authenticated* principal; patient linking administrative-only; re-link refused | `test_relink_refused`, `test_link_binds_to_request_user` |
| T15 | Secret disclosure | Client secret in a bundle, a log or the provider list | Secrets backend-only; provider list returns non-secret fields; log-scrubbing test | `test_provider_list_has_no_secrets` |
| T16 | SSRF via configuration | Issuer pointed at an internal address | `is_safe_oidc_url`: HTTPS, or HTTP for loopback only; no credentials/query/fragment; endpoint origin pinned | `test_issuer_url_safety` |
| T17 | DoS via issuer | Slow or flapping IdP amplified per login | Discovery and JWKS cached; 5s timeouts; 15s budget; per-IP rate limit | `test_jwks_cached_single_fetch` |
| T18 | Token leakage in logs | Raw token, verifier or nonce written out | Never logged; audit uses opaque identifiers and keyed fingerprints | `test_no_token_in_logs` |
| T19 | Stale access after deprovisioning | User disabled at the IdP keeps a CARE session | CARE credential lifetime bounds exposure; unlink is immediate; documented as a known limitation | documented, not tested |
| T20 | Provider outage locks everyone out | Sole IdP unavailable | Other methods stay enabled and independent; row 1 and row 5 prove coexistence | Matrix rows 1, 5, 10 |

T19 is stated rather than solved: without back-channel logout or token
introspection, a CARE session outlives an IdP deprovisioning by at most the
CARE credential's lifetime. Closing it means back-channel logout, which is
explicitly out of scope. The operator guide must say so plainly.

## 12. Testing

### 12.1 Backend

```text
care/users/tests/test_oidc_provider_config.py     loader, validation, startup refusal
care/users/tests/test_oidc_service.py             discovery, JWKS cache, alg, aud/azp, nonce
care/users/tests/test_oidc_exchange.py            workforce exchange, cross-principal, generic failures
care/users/tests/test_oidc_linking.py             admin, self-service, re-link refusal, unlink, audit
care/users/tests/test_oidc_conformance.py         runs against the double and (marked) live Keycloak
care/users/tests/test_external_auth_startup.py    extended: matrix rows 1, 9, 10
care/emr/tests/test_patient_external_identity.py  patient exchange, triple resolution, row 8
care/emr/tests/test_patient_identity_{auth,api}.py  existing; must not regress
care/users/tests/test_keycloak_*.py               renamed, content preserved where still valid
```

Migration tests: forward on an empty database, forward on a seeded
`keycloak_subject` fixture, reverse, and the loud failure when
`OIDC_LEGACY_ISSUER` is unset with rows present.

### 12.2 Frontend

```text
src/Utils/auth/__tests__/oidcTransaction.test.ts      extended for providerId binding
src/Utils/auth/__tests__/externalAuthConfig.test.ts   rewritten for the fetched provider list
src/Utils/auth/__tests__/loginMethods.test.ts         extended for N providers
src/components/Auth/external/__tests__/               provider rendering, display_name escaping
src/pages/Auth/__tests__/OidcCallback.test.tsx        state check, provider match, failure paths
```

Playwright: workforce OIDC login against the local Keycloak container; patient
OIDC login; matrix row 1 (no external method rendered anywhere).

### 12.3 Commands

```bash
# backend
pipenv run python manage.py test care.users care.emr --keepdb --parallel
pipenv run python manage.py makemigrations --check --dry-run
pipenv run ruff check . && pipenv run ruff format --check .
DJANGO_SETTINGS_MODULE=config.settings.local pipenv run python manage.py check

# live-issuer suite (requires the container)
make oidc-up && make test-oidc && make oidc-down

# frontend
npm run lint && npm run build && npm run test
npx playwright test auth

# infrastructure
tofu fmt -check && tofu validate      # no plan, no apply
```

CI runs everything except `test-oidc`, which is opt-in and skipped when the
container is absent.

## 13. Acceptance criteria

ES-11 is complete only when every item has evidence.

**Architecture**
- [ ] No file, setting, route, model field, exported symbol or frontend
      variable in either repository contains `keycloak` outside test fixtures,
      the realm export and historical migrations.
- [ ] `grep -ri keycloak care/ config/ src/` returns only those allowed hits.
- [ ] Zero configured providers is a supported, tested state (matrix row 1).

**Identity**
- [ ] Both identity tables exist with the triple constraint.
- [ ] Migrations run forward and reverse; the seeded-fixture case is proven.
- [ ] `keycloak_subject` columns are gone from both models.
- [ ] No lookup anywhere filters on `subject` without `issuer` and
      `provider_id`; the CI check enforces it.
- [ ] Two providers sharing a `sub` resolve to different principals or to none.

**Security**
- [ ] Every threat T1–T18 has a passing test; T19 and T20 are documented.
- [ ] A valid token with no enrolled identity creates nothing and returns the
      same response as a bad signature.
- [ ] No role, group or scope claim is read anywhere.
- [ ] No secret appears in the provider list, a bundle, a log or a tracked file.

**Coexistence**
- [ ] All ten matrix rows behave as specified.
- [ ] Enabling or disabling any provider changes no other method.
- [ ] Existing OTP and Firebase tests pass unchanged.

**Portability**
- [ ] CARE starts, serves and passes health checks with no OIDC configuration
      and no outbound call.
- [ ] Neither Keycloak, Redis, Firebase nor any GCP service is required by any
      code path added here.
- [ ] The conformance suite passes identically against the generic double and
      against live Keycloak.

**Frontend**
- [ ] The login screen renders providers from the backend list.
- [ ] A build cannot advertise a provider the backend lacks.
- [ ] `display_name` is rendered as text and cannot inject markup.
- [ ] Linked-identities panel supports link and unlink for workforce.

**Documentation**
- [ ] Section 16's documents all exist and are accurate.

**Boundaries**
- [ ] No Keycloak service deployed to any CARE environment.
- [ ] Production authentication unchanged and not enabled.
- [ ] No real patient data used.
- [ ] No `tofu apply`, no GitHub environment change, no new GCP permission.

## 14. Rollout

Local and dev only. Each phase ends green before the next starts.

| Phase | Content | Gate |
|---|---|---|
| 0 | Approve ADR-0011 and ES-11 | Operator approval, recorded |
| 1 | Provider record, loader, validation, startup checks | Matrix rows 1 and 9 pass; no behaviour change yet |
| 2 | Identity tables + backfill (steps 1–2) | Backfill rule tested; forward/backward proved on a disposable database |
| 3 | Triple resolution, admin, then drop the columns (step 3) | T1, T4, T5, T6, T17 pass; full backend suite green |
| 4 | Module/route/settings renames | Full backend suite green |
| 5 | Linking, unlinking, audit | T14 passes; audit records verified |
| 6 | Frontend renames, provider list, panels | Frontend suite and build green |
| 7 | Local Keycloak fixture; conformance suite against both | Both runs identical |
| 8 | Documentation rewrite | Section 16 complete |
| 9 | Dev acceptance against the local container, synthetic identities only | Matrix proven; evidence recorded |

Staging follows only if dev is stable **and** separately authorised. Production
remains governed by ADR-0009 and ES-09 and is not touched by ES-11.

## 15. Rollback

The release is reversible at every phase, and the reversal is cheap because
nothing external is enrolled.

- **Configuration rollback (seconds).** Remove `OIDC_PROVIDERS` /
  `OIDC_PROVIDERS_FILE` and restart. Every OIDC route disappears, no choice is
  rendered, identity rows are retained, OTP and Firebase are untouched. This is
  the first response to any incident.
- **Single-provider rollback.** Set that provider's `enabled` to false. Its
  identities stop resolving; other providers are unaffected.
- **Application rollback.** Redeploy the previous image. The ES-11 schema is
  additive except for the dropped columns, so the previous image runs against
  the new schema for everything but external login — which the previous image
  can only perform with `KEYCLOAK_ENABLED=true`, a state no environment uses.
- **Schema rollback.** Reverse migrations 3 → 2 → 1. Step 3's reverse restores
  the columns; step 2's reverse repopulates them from the identity rows; step
  1's reverse drops the tables. Tested in CI.
- **Frontend rollback.** Redeploy the previous bundle. It reads
  `REACT_KEYCLOAK_*`, which will be unset, so it renders no external method —
  a safe degradation, not a break.

Rollback triggers: any authentication failure affecting a method other than
OIDC; any principal resolving to the wrong record; any secret appearing in a
response or log; startup failure in an environment that had no OIDC
configuration.

## 16. Required documentation

| Document | Action |
|---|---|
| `docs/xii/adr/ADR-0011-standard-oidc-authentication.md` | New (this change's ADR) |
| `docs/xii/implementation/ES-11-standard-oidc-authentication.md` | This document |
| `docs/xii/operations/oidc-provider-guide.md` | New. Replaces the Keycloak guide. Provider-agnostic requirements, the provider JSON, callback registration, enrolment, rotation, rollback, the T19 limitation, and a full Keycloak worked example plus short notes for Entra ID, Authentik and Zitadel |
| `docs/xii/operations/keycloak-activation-guide.md` | Removed; replaced by the above, with pointers left in ADR-0010 and ES-10 |
| `docs/xii/architecture/07-configuration-reference.md` | `OIDC_*` added, `KEYCLOAK_*` removed |
| `docs/xii/adr/ADR-0010-multichannel-authentication-with-keycloak.md` | Header note: §2, §4 and §5 amended by ADR-0011 |
| `docs/xii/implementation/ES-10-multichannel-authentication.md` | Header note: superseded in part by ES-11; remains the record of what was delivered |
| `.env.example` | `OIDC_*` block replaces the `KEYCLOAK_*` block |
| `care_fe` `.env.example` / build docs | `REACT_KEYCLOAK_*` removed; provider list documented |
| `CLAUDE.md` (both repos) | Note that identity resolution uses the triple and that no claim grants authorization |

## 17. Required handoff report

Report separately for `care` and `care_fe`:

```text
files changed
behavior implemented
migrations added or changed, and their reverse status
tests and exact results
build and lint results
configuration variables added and removed
secrets or external configuration still required
matrix rows proven, with evidence per row
threats tested vs documented-only
dev acceptance performed
known limitations
production state: unchanged
```

Do not describe an unperformed check as passed. Do not mark ES-11 implemented
until every applicable acceptance item has evidence. Passing backend tests
alone is not completion: the matrix, the conformance suite against both
issuers, and the documentation are part of the deliverable.
