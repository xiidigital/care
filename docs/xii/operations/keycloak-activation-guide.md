# Keycloak activation guide

- **Status:** Prepared, not activated. No Keycloak service exists for CARE.
- **Applies to:** ADR-0010 and ES-10.
- **Audience:** the operator who will later run a Keycloak deployment for CARE.

CARE ships the Keycloak adapter compiled in and dormant. Activating it is a
**configuration and deployment change only**: no source edit, no migration and
no new application build are required. This guide is the complete list of what
an operator must create outside CARE and set inside it.

> Nothing in this guide has been verified against a live Keycloak server,
> because none exists. Every value below is derived from the implemented
> contract in `config/keycloak.py`, `config/keycloak_service.py` and
> `config/keycloak_views.py`, and from the contract tests in
> `care/users/tests/test_keycloak_service.py` and
> `care/users/tests/test_keycloak_flow.py`, which drive the mounted HTTP
> exchanges against a standards-faithful OIDC double. Treat the first real
> activation as a dev exercise to be proven, not as a step already taken.
>
> **Production enablement remains governed by ADR-0009 and ES-09.** This guide
> does not authorize it.

---

## 1. What CARE requires from the issuer

CARE speaks standard OpenID Connect Authorization Code with PKCE. It requires:

| Requirement | Detail |
|---|---|
| Discovery | `<issuer>/.well-known/openid-configuration` must be reachable |
| `issuer` claim | Must equal `KEYCLOAK_ISSUER_URL` exactly, ignoring a trailing `/` |
| Endpoint origin | `token_endpoint` and `jwks_uri` must share the issuer's scheme and host |
| Transport | `https` — plain `http` is accepted only for `localhost`/`127.0.0.1`/`::1` |
| Signing | RS-family JWKS published at `jwks_uri` |
| ID token claims | `iss`, `aud`, `exp`, `iat`, `nonce`, `sub`, all required |
| Clock skew | 30 seconds of leeway |

CARE validates **only** the token contract. It does not read roles, groups or
any other Keycloak claim, and it never branches on *how* Keycloak authenticated
the person. Password, OTP, passkey, social login, enterprise federation,
recovery and MFA are Keycloak's concerns and may change at any time without a
CARE change.

## 2. Realm and clients

Create **one realm**. Inside it create **two clients**. The separation that
matters to CARE is the audience, not the realm, so one realm is sufficient; a
future operational need may split realms without any CARE change.

| Client ID | Principal | CARE token issued on success |
|---|---|---|
| `care-workforce` | Consultorio users (`care.users.User`) | Existing CARE access + refresh pair |
| `care-patient` | Patients (`care.emr.Patient`) | Existing one-hour `PatientToken` |

Both clients must be:

- **Confidential** — CARE authenticates to the token endpoint with HTTP Basic
  using the client ID and secret, so a public client will not work.
- **PKCE-enabled**, method **S256**. The browser generates the verifier and
  challenge; CARE forwards the verifier during the code exchange.
- Configured so the ID token's `aud` claim equals the client ID. CARE requires
  `aud == client_id` exactly. A token minted for `care-patient` is refused by
  the workforce exchange and vice versa; this is asserted by
  `test_a_patient_audience_token_is_refused_by_the_workforce_exchange` and its
  mirror.

Client IDs are configurable (`KEYCLOAK_WORKFORCE_CLIENT_ID`,
`KEYCLOAK_PATIENT_CLIENT_ID`); the names above are the documented defaults.

## 3. Redirect URIs

CARE derives both callbacks from `KEYCLOAK_PUBLIC_BASE_URL` and will not accept
any other value. `KEYCLOAK_PUBLIC_BASE_URL` must be an origin only — scheme and
host, no path, query or fragment.

```text
<KEYCLOAK_PUBLIC_BASE_URL>/auth/keycloak/workforce/callback
<KEYCLOAK_PUBLIC_BASE_URL>/auth/keycloak/patient/callback
```

Register each callback on **its own client only**. Do not register the patient
callback on the workforce client or the reverse. The frontend sends the exact
redirect URI it started the flow with, and CARE compares it byte-for-byte
against the derived value before contacting Keycloak at all
(`test_redirect_uri_must_match_the_configured_principal_callback`).

Wildcards must not be used. If Keycloak's web-origins or post-logout redirect
lists are used, restrict them to the same single origin.

## 4. Secrets

| Value | Where it belongs |
|---|---|
| `KEYCLOAK_WORKFORCE_CLIENT_SECRET` | Secret Manager, `api` role only |
| `KEYCLOAK_PATIENT_CLIENT_SECRET` | Secret Manager, `api` role only |

Both are backend-only. Neither may enter a frontend bundle, a tracked
environment file or a Terraform variable file. In the dev environment they are
declared in `optional_secrets` for `api` and their values are added directly to
Secret Manager — see
`infrastructure/terraform/environments/dev/terraform.tfvars.example`.

Client IDs, the issuer URL and the public base URL are **public identifiers**
and belong in `extra_env`, not in secrets.

### Rotation

1. Add a new client secret in Keycloak, keeping the old one valid.
2. Add a new Secret Manager version for the affected variable.
3. Redeploy the `api` role so it picks up the new version.
4. Confirm a synthetic login through the affected client still succeeds.
5. Remove the old secret in Keycloak, then disable the old secret version.

Rotation needs no CARE code change and no user-visible downtime, because the
secret is read per process at startup.

## 5. Enrolling CARE subjects

**CARE never creates a `User` or a `Patient` because Keycloak produced a valid
token.** A token whose `sub` matches no enrolled record receives the same
generic failure as an invalid token. Enrolment is an explicit administrative
step on both models:

| CARE model | Field | Populated from |
|---|---|---|
| `care.users.User` | `keycloak_subject` | The `sub` claim of a `care-workforce` token |
| `care.emr.Patient` | `keycloak_subject` | The `sub` claim of a `care-patient` token |

Both fields are optional and unique. The workforce field is editable in Django
admin (`care/users/admin.py`). A workforce subject additionally resolves only
if the `User` is `is_active`; deactivating a user in CARE therefore revokes
Keycloak login for that person immediately, without touching Keycloak.

Do not reuse one Keycloak subject across both models. The exchanges are
separated by audience, so a shared subject cannot cross principal types, but
sharing it is still a modelling error.

## 6. Configuration to set

Set on the `api` role. Worker, scheduler and init roles neither validate nor
consume these values.

```text
KEYCLOAK_ENABLED=true
KEYCLOAK_ISSUER_URL=https://identity.example/realms/care
KEYCLOAK_WORKFORCE_CLIENT_ID=care-workforce
KEYCLOAK_WORKFORCE_CLIENT_SECRET=<from Secret Manager>
KEYCLOAK_PATIENT_CLIENT_ID=care-patient
KEYCLOAK_PATIENT_CLIENT_SECRET=<from Secret Manager>
KEYCLOAK_PUBLIC_BASE_URL=https://care-dev.example.org
```

The frontend needs the matching public build values — see
`care_fe/.example.env` and `docs/xii/architecture/07-configuration-reference.md`
§62.1. The frontend flag and the backend flag must be set together: a rendered
Keycloak choice that reaches an unmounted backend route is a misconfiguration,
not a supported state.

With `KEYCLOAK_ENABLED=true`, **every** value above is required. A missing one
aborts startup with a message naming the missing variables and never printing a
secret value (`test_startup_failure_never_prints_a_configured_secret`).

## 7. Testing before activation

Do this in dev, against a disposable Keycloak, before any staging or production
consideration.

1. **Contract tests, no server.** `care/users/tests/test_keycloak_service.py`
   covers discovery, issuer, both audiences, nonce, expiry, signature, redirect
   and an unreachable issuer at the service boundary;
   `care/users/tests/test_keycloak_flow.py` drives the mounted HTTP exchanges
   end to end against an OIDC double, proving an enrolled workforce subject
   receives a working CARE staff session and an enrolled patient subject
   receives a `PatientToken` scoped to exactly that patient. These run today and
   must stay green.
2. **Startup.** Set the complete configuration and confirm the process starts
   and `manage.py check` exits 0. CARE does not contact Keycloak at startup, so
   this succeeds even before the realm exists.
3. **Enrol synthetic subjects.** Create one synthetic staff user and one
   synthetic patient in dev, and set their `keycloak_subject` from the `sub`
   claim of a real token minted by the disposable realm. Use synthetic
   identities only — never real patient data.
4. **Walk both flows.** Confirm the workforce flow returns a CARE access and
   refresh pair and loads normal current-user authorization, and that the
   patient flow returns a `PatientToken` scoped to exactly that patient.
5. **Walk the negative cases.** Confirm a `care-patient` token is refused by the
   workforce exchange, an unenrolled subject receives a generic failure, and a
   deactivated `User` can no longer log in.
6. **Confirm rollback.** Set `KEYCLOAK_ENABLED=false`, redeploy, and confirm the
   routes disappear, the choice disappears and existing CARE login still works.

## 8. Enablement and rollback

Both directions are the same single flag.

```text
enable:   KEYCLOAK_ENABLED=true  + the six values in §6 + redeploy
rollback: KEYCLOAK_ENABLED=false + redeploy
```

Rollback is complete and needs nothing else. With the flag off CARE does not
contact Keycloak, mounts no Keycloak route, requires no Keycloak configuration,
and existing username/password plus CARE MFA login continues to work unchanged.
No migration is involved in either direction; the two `keycloak_subject`
columns are already present and simply go unused.

Existing CARE login paths are **not** removed by enabling Keycloak. Per
ADR-0010 §7, legacy paths are disabled and later removed only after their
replacements are proven and supported clients have migrated.

## 9. Ongoing operator responsibilities

Provisioning and operating the Keycloak service is outside the CARE
implementation. Before production activation the operator must document and own:

- **Backup** of the realm database and of realm/client configuration export,
  with a stated frequency and retention.
- **Restore**, rehearsed at least once, with a measured recovery time.
- **Upgrades** of Keycloak itself, including the deprecation policy the CARE
  deployment will follow.
- **Administrative access**: who holds realm-admin rights, how that access is
  authenticated, and how it is reviewed.
- **Secret rotation** on a stated schedule, per §4.
- **Availability**: Keycloak becomes a hard dependency of every login that uses
  it. While it is down, users on the Keycloak path cannot log in. The flag is
  the documented mitigation.
- **Audit and log retention** for authentication events on the Keycloak side.
  CARE records only principal type, provider and outcome, using opaque CARE
  identifiers.

## 10. What this guide deliberately does not do

- It does not create realm credentials, clients or secrets.
- It does not claim any live verification. No Keycloak runtime was used.
- It does not authorize production activation, which remains subject to
  ADR-0009 and ES-09.
- It does not map Keycloak roles to CARE roles. Existing CARE facility,
  organization, role and record authorization remains authoritative.

## Related documents

- `docs/xii/adr/ADR-0010-multichannel-authentication-with-keycloak.md`
- `docs/xii/adr/ADR-0009-controlled-production-activation.md`
- `docs/xii/implementation/ES-10-multichannel-authentication.md`
- `docs/xii/architecture/07-configuration-reference.md` §62.1
- Keycloak server administration: `https://www.keycloak.org/docs/latest/server_admin/`
- Keycloak OIDC layers: `https://www.keycloak.org/securing-apps/oidc-layers`
