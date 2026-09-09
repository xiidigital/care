# OIDC provider guide

- **Status:** Prepared. No provider is configured in any CARE environment.
- **Applies to:** ADR-0011 and ES-11.
- **Audience:** the operator who will connect CARE to an identity provider.

CARE speaks standard OpenID Connect. Connecting a provider is a
**configuration and deployment change only** — no source edit, no migration,
no new application build. This guide is the complete list of what you create
outside CARE and what you set inside it.

**Zero providers is a supported production state.** With none configured CARE
mounts no OIDC route, contacts no issuer and requires no OIDC setting. If your
installation has no identity provider, you need nothing on this page: CARE's
own phone OTP is a complete, supported way in.

> **Production enablement remains governed by ADR-0009 and ES-09.** This guide
> does not authorize it.

---

## 1. What CARE requires of an issuer

Any conforming OpenID Connect provider works. CARE has been tested against
Keycloak (see §7) and against a standards-faithful double; nothing in CARE
knows which product is answering.

| Requirement | Detail |
|---|---|
| Discovery | `<issuer>/.well-known/openid-configuration` must be reachable from CARE |
| `issuer` claim | Must equal the configured `issuer` exactly, ignoring a trailing `/` |
| Endpoint origin | `authorization_endpoint`, `token_endpoint`, `jwks_uri` must share the issuer's scheme and host |
| Transport | `https` — plain `http` is accepted only for `localhost`/`127.0.0.1`/`::1` |
| Flow | Authorization Code with PKCE (S256) |
| Client type | Confidential: CARE authenticates to the token endpoint with a client secret |
| Signing | RS or ES family, published at `jwks_uri`. `none` and HMAC are refused |
| ID token claims | `iss`, `aud`, `exp`, `iat`, `nonce`, `sub` — all required |
| Audience | `aud` must equal the client id; when `aud` is multi-valued, `azp` must too |
| Clock skew | 30 seconds of leeway |

CARE validates **only** the token contract. It does not read roles, groups,
scopes or any other claim, and it will not be made to: see §4.

## 2. What you create at the provider

One **client per CARE principal type** you want to serve. A provider record in
CARE declares exactly one `principal_type`, and that separation is what keeps a
patient token out of the workforce exchange.

For each client:

- confidential (not public), with a client secret;
- Authorization Code flow enabled, implicit and direct-access disabled;
- PKCE required, method S256;
- exactly one registered redirect URI, from §3.

You may serve both principal types from one realm/tenant, or from separate
ones. CARE does not care: the clients, audiences and redirect URIs provide the
separation on their own.

## 3. Callback URLs

CARE derives these from `OIDC_PUBLIC_BASE_URL` and compares them **byte for
byte** at the exchange. Register them exactly as written:

```text
<OIDC_PUBLIC_BASE_URL>/auth/oidc/workforce/callback
<OIDC_PUBLIC_BASE_URL>/auth/oidc/patient/callback
```

`OIDC_PUBLIC_BASE_URL` is a bare origin: `https://care.example`, with no path,
query or fragment.

## 4. What CARE will not do

These are decisions, not gaps. A request to change one is a request to
supersede ADR-0011, and should be handled as one.

- **No claim grants anything.** CARE reads no `roles`, `groups`,
  `realm_access`, `resource_access`, `scope` or equivalent. There is no setting
  that maps a claim to a CARE role.
- **No auto-provisioning.** A valid token whose identity is not enrolled
  creates no user, no patient, no role and no identity row. It receives the
  same generic failure as a forged signature.
- **No linking by email.** `email`, `phone_number` and `preferred_username` are
  profile data, never identity. Linking is explicit (§6).
- **No external token at an application API.** Every OIDC login terminates at a
  CARE exchange that issues an ordinary CARE credential.

## 5. What you set inside CARE

Exactly one of:

```bash
OIDC_PROVIDERS='[{"id":"clinic-sso","display_name":"Clinic SSO",
  "issuer":"https://sso.example.org/realms/care",
  "principal_type":"workforce","client_id":"care-workforce",
  "client_secret":"...","enabled":true}]'

OIDC_PROVIDERS_FILE=/var/run/secrets/care/oidc-providers.json
```

Prefer the file form wherever a secret manager can mount it, so client secrets
never sit in an environment listing. Setting both is refused at startup.

| Field | Required | Notes |
|---|---|---|
| `id` | yes | 1–32 chars, lowercase letters, digits, `-`, `_`. **Stored on every identity it authenticates** — treat it as permanent |
| `display_name` | yes | What the login screen calls it. Shown to users as text |
| `issuer` | yes | The exact expected `iss` |
| `principal_type` | yes | `workforce` or `patient` |
| `client_id` | yes | The audience CARE requires |
| `client_secret` | yes | From your secret mechanism |
| `enabled` | no | Default true |
| `scopes` | no | Default `openid profile email`; must include `openid` |
| `allow_rp_logout` | no | Default false |

Plus:

```bash
OIDC_PUBLIC_BASE_URL=https://care.example
OIDC_DISCOVERY_CACHE_SECONDS=3600
OIDC_JWKS_CACHE_SECONDS=3600
```

Startup validates every enabled provider and **refuses to start** on a bad one,
naming the provider id and the failing field. It never echoes a secret.

## 6. Enrolling people

An account is reachable through a provider only after an identity is linked to
it. This is the security-critical step: linking decides whose CARE account an
external subject reaches, and the exchange honours that answer indefinitely.

**Workforce, by an administrator:**

```bash
python manage.py link_external_identity \
  --provider-id clinic-sso --username jsmith --subject <sub> \
  --linked-by <your-username>
```

Or through the Django admin, on the user's page.

**Workforce, self-service:** a user already signed in to CARE opens their
account settings and links a provider by completing a normal round trip. CARE
links the subject to *them* — a target named in the request is ignored.

**Patients, administrator only:**

```bash
python manage.py link_patient_external_identity \
  --provider-id patient-sso --patient <patient-external-id> --subject <sub> \
  --linked-by <your-username>
```

There is no self-service path to a clinical record. `--linked-by` is required
because a patient link with no recorded actor is a link nobody is accountable
for.

Rules the system enforces for you:

- a subject already linked is **never** moved to another account; unlink first;
- a subject is immutable — correcting one is unlink-then-link, both recorded;
- unlinking is immediate, and never touches the account or its other methods;
- disabling a provider stops its identities resolving; the rows are kept, so
  re-enabling is not a re-enrolment.

Where do you get `<sub>`? From the provider: it is the `sub` claim, stable per
user per issuer. Most providers show it as the user's internal id. Do **not**
use an email address.

## 7. Worked example: Keycloak

Keycloak is CARE's reference implementation — the provider its conformance
suite runs against. Nothing here is required; it is one product's spelling of
§2.

1. Create a realm, e.g. `care`.
2. Create client `care-workforce`: Client authentication **on**, Standard flow
   **on**, Direct access grants **off**. Under Advanced, set *Proof Key for
   Code Exchange Code Challenge Method* to `S256`.
3. Valid redirect URI: `https://care.example/auth/oidc/workforce/callback`.
4. Copy the client secret from the Credentials tab.
5. Repeat as `care-patient` with the patient callback, if you serve patients
   this way.
6. Issuer is `https://<keycloak-host>/realms/care`.

A disposable local Keycloak for testing is in `docker-compose.oidc.yaml`:

```bash
make oidc-up      # CARE_OIDC_PORT=8082 make oidc-up if 8081 is taken
make test-oidc
make oidc-down
```

That container is a test fixture. It is not deployed to any CARE environment,
and deleting it degrades the test suite and nothing else.

### Other providers

The same six required fields apply. What changes is only where you find them:

| Provider | Issuer |
|---|---|
| Keycloak | `https://<host>/realms/<realm>` |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant>/v2.0` |
| Authentik | `https://<host>/application/o/<slug>/` |
| Zitadel | `https://<instance>` |
| Okta | `https://<org>.okta.com/oauth2/<server>` |

## 8. Rotation, rollback and outage

**Rotate a secret:** change it at the provider, update
`OIDC_PROVIDERS_FILE`, restart. Sessions already issued are CARE credentials
and are unaffected.

**Roll back:** remove `OIDC_PROVIDERS` / `OIDC_PROVIDERS_FILE` and restart.
Every OIDC route disappears, no choice is rendered, identity rows are retained,
and OTP and Firebase are untouched. This is the first response to any incident.

**Disable one provider:** set its `enabled` to `false`. Its identities stop
resolving; other providers are unaffected.

**Provider outage:** CARE's own methods stay enabled and independent. If a
provider's issuer becomes unreachable, CARE hides it from the login screen
rather than offering a button that cannot work.

## 9. What this design does not solve

State it to your security reviewer rather than discovering it later.

**A CARE session outlives a deprovisioning at the provider** by at most the
CARE credential's lifetime. CARE has no back-channel logout and does no token
introspection, so disabling someone at the IdP does not end a session already
in progress. To revoke access immediately, unlink the identity **and**
deactivate the CARE account — the second is what actually stops the existing
session.

**RP-initiated logout is best-effort.** Where a provider advertises
`end_session_endpoint` and the provider record sets `allow_rp_logout`, CARE
offers the URL. CARE's own session ends locally and unconditionally either way.

## 10. Your responsibilities

CARE does not operate your identity provider. Before production, document:

- backup and restore of the provider's own database;
- upgrade procedure and tested rollback;
- administrator access and its own MFA;
- secret rotation schedule;
- what happens to CARE access when someone leaves.

## Related documents

- `docs/xii/adr/ADR-0011-standard-oidc-authentication.md`
- `docs/xii/implementation/ES-11-standard-oidc-authentication.md`
- `docs/xii/adr/ADR-0009-controlled-production-activation.md`
- `docs/xii/architecture/07-configuration-reference.md` §62.1
- OpenID Connect Core 1.0: `https://openid.net/specs/openid-connect-core-1_0.html`
- RFC 9700, OAuth 2.0 Security Best Current Practice:
  `https://datatracker.ietf.org/doc/html/rfc9700`
