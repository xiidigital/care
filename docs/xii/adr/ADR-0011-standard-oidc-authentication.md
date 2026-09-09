# ADR-0011: Standard OIDC Authentication with Keycloak as a Reference Implementation

- **Status:** Accepted
- **Date:** 2026-09-07
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Amends:** ADR-0010 §2, §4 and §5 (see *Relationship with ADR-0010*)
- **Superseded by:** None

## Context

ADR-0010 selected CARE's login paths and shipped a dormant external
authentication adapter. That adapter works, is covered by tests, and has never
been activated: no Keycloak runtime exists, `KEYCLOAK_ENABLED` is false in every
environment, and no external subject has ever been enrolled against a real
issuer. ES-10 closed on that basis.

ADR-0010 made one simplifying assumption that is now the constraint: it treated
**Keycloak** as the unit of design rather than **OIDC**. The assumption is
visible throughout the implementation.

- `Patient.keycloak_subject` and `User.keycloak_subject` are single unique
  `CharField`s. They store an OIDC `sub` with no record of which issuer minted
  it.
- Configuration is a fixed six-variable block (`KEYCLOAK_ISSUER_URL`,
  `KEYCLOAK_WORKFORCE_CLIENT_ID`, …) that can describe exactly one issuer.
- The backend modules, the routes (`/api/v1/auth/keycloak/…/exchange/`), the
  frontend build variables (`REACT_KEYCLOAK_*`), the callback route and the
  React components are all named for a vendor.
- `PatientKeycloakExchangeView` resolves a principal with
  `Patient.objects.get(keycloak_subject=subject)` — **by subject alone**.

The last point is not a naming problem. An OIDC `sub` is unique only *within*
an issuer; the identifier the specification guarantees to be globally unique is
the pair `(iss, sub)`. Today that is harmless because exactly one issuer can be
configured. The moment CARE can be configured with a second issuer, a subject
lookup that ignores the issuer becomes an account-takeover primitive: any
issuer that can mint a token whose `sub` matches an enrolled CARE subject can
assume that principal. The single-issuer restriction is load-bearing security,
which means the design cannot be extended safely by configuration alone.

Meanwhile the requirement has moved. CARE is a Digital Public Good and is
deployed by organisations that already run an identity provider. Those
providers are Keycloak, Authentik, Zitadel, Azure Entra ID, Google Workspace,
Okta, Auth0 or a national health-sector IdP. They have one thing in common and
it is not a vendor: they speak OpenID Connect. An installation must be able to
point CARE at the issuer it already operates, without a CARE code change and
without adopting a second identity product.

At the same time, deployments that have **no** identity provider must keep
working exactly as they do now. CARE's own phone OTP must remain a complete,
supported way in. Firebase must remain optional. Neither Keycloak, nor Redis,
nor Firebase, nor any Google Cloud service may become a requirement of running
CARE.

ADR-0009 and ES-09 still govern production activation. This ADR selects an
authentication architecture; it does not activate production, authorise real
patient data, or approve any operator account.

## Decision

### 1. OIDC is the abstraction; a provider is configuration

CARE SHALL implement **standard OpenID Connect** — discovery, Authorization
Code with PKCE, and ID-token validation — as its only external authentication
protocol for browser login. CARE SHALL NOT contain vendor-specific
authentication code, vendor-specific settings, vendor-specific routes or
vendor-named identifiers in models, configuration, URLs or the frontend.

An OIDC provider SHALL be a **configuration record**, not a code path. CARE
SHALL support **zero or more** configured providers. The supported set is
whatever the operator configures; adding one SHALL require configuration and a
restart, never a source change, a migration or a new build.

Each provider record SHALL declare at least:

```text
id                 stable, operator-chosen slug; appears in stored identities
display_name       what the login screen calls it
issuer             the exact expected `iss` value
principal_type     workforce | patient   (a provider serves exactly one)
client_id          the audience CARE requires
client_secret      confidential-client credential, from the secret mechanism
enabled            per-provider switch
```

CARE SHALL derive `authorization_endpoint`, `token_endpoint`, `jwks_uri` and
`end_session_endpoint` from the issuer's discovery document. CARE SHALL NOT
accept hand-configured endpoints, and SHALL require every discovered endpoint
to share the issuer's scheme and host.

### 2. Keycloak is a reference implementation, not a dependency

Keycloak SHALL be CARE's **reference implementation**: the provider CARE tests
against, the provider its operator documentation walks through end to end, and
the provider a maintainer can stand up locally in a container to reproduce a
report. That is the whole of its privileged status.

Keycloak SHALL NOT appear in a model field, a setting name, a URL, a frontend
variable, a component name, an exported symbol or a conditional branch. A CARE
deployment SHALL be able to run against a non-Keycloak issuer with no code
difference whatsoever, and CARE's test suite SHALL contain at least one
conformance test that a generic OIDC double satisfies without Keycloak
present.

The distinction the repository must hold: **testing against Keycloak is
evidence; requiring Keycloak is coupling.** This ADR buys the first and forbids
the second.

### 3. External identity is `(provider, issuer, subject)`

An external identity SHALL be stored as an explicit row, not as a column on the
principal, and its natural key SHALL be the triple:

```text
(provider_id, issuer, subject)
```

CARE SHALL enforce uniqueness on that triple, and SHALL resolve a principal by
matching **all three**. A lookup by `subject` alone SHALL NOT exist anywhere in
the codebase. Storing `issuer` on the row rather than reading it from live
configuration is deliberate: it makes a re-pointed provider record a
*mismatch* — the enrolled identity stops resolving and login fails closed —
instead of a silent re-binding of every enrolled account to a new issuer.

`email`, `phone_number`, `preferred_username`, `name` and every other claim
SHALL be treated as **non-authoritative profile data**. No claim other than the
triple above SHALL ever resolve a CARE principal.

Two identity tables SHALL exist, one per principal type, each with a real
foreign key to its principal:

```text
UserExternalIdentity     -> care.users.User
PatientExternalIdentity  -> care.emr.Patient
```

A polymorphic single table is rejected: CARE's two principal types have
different lifecycles, different authorization models and different deletion
semantics, and a generic foreign key would surrender database-level referential
integrity on the one join that decides who someone is.

A principal MAY hold several external identities — the same clinician arriving
through the hospital's Entra ID and through a regional Keycloak is one `User`
with two rows. An external identity SHALL belong to exactly one principal.

### 4. Identity and authorization are separate systems

An OIDC provider answers exactly one question: **which external subject is
this?** It SHALL NOT answer any of the following:

- whether a CARE principal exists;
- whether a CARE principal may be created;
- which facility, organization or role a principal holds;
- which patient record a principal may read;
- whether a principal is staff, a superuser, or clinically privileged.

CARE SHALL NOT read `roles`, `groups`, `realm_access`, `resource_access`,
`scope`, `permissions` or any equivalent claim for an authorization decision,
and SHALL NOT provide a configuration option that maps such a claim to a CARE
role. Existing facility, organization, role and record authorization remains
authoritative and unchanged.

CARE SHALL NOT auto-provision. A valid ID token from a configured, enabled
provider whose triple matches no enrolled identity SHALL produce a generic
authentication failure and SHALL NOT create a `User`, a `Patient`, a role
assignment or an identity row.

External tokens SHALL NOT be accepted by application APIs. Every OIDC login
SHALL terminate at a CARE exchange endpoint that issues the existing CARE
credential for the principal type — the staff access/refresh pair for a `User`,
a `PatientToken` for a `Patient` — and application APIs SHALL continue to
accept only those.

### 5. Account-linking rules

Linking an external identity to a CARE principal is the security-critical
operation in this design. The following rules are normative.

1. **No implicit linking by claim.** A matching `email`, `email_verified`,
   `phone_number` or `preferred_username` SHALL NOT create a link. Email is a
   claim an IdP controls and a user can often change; treating it as an
   identity key is the standard account-takeover path and is forbidden here.
2. **Linking is an authenticated, deliberate act.** A link SHALL be created
   only by (a) a CARE administrator with the appropriate existing permission,
   acting on a named principal; or (b) the principal itself, while already
   authenticated to CARE through an existing method, completing a full OIDC
   round trip and confirming the link. There is no third way.
3. **A patient link is never self-service against a clinical record.** A
   patient's link to an existing `Patient` record SHALL be created only through
   the administrative path (a) — CARE's existing patient-record custody rules
   decide who may do that. Path (b) is available to workforce principals.
4. **First writer wins, and collisions fail closed.** A triple already linked
   to a principal SHALL NOT be re-linked to another; the second attempt SHALL
   fail and SHALL be recorded.
5. **Subject is immutable.** An existing identity row's `subject` or `issuer`
   SHALL NOT be edited. Unlink and re-enrol instead; both are recorded.
6. **Unlinking is always available and never destructive.** Removing an
   identity row SHALL leave the principal, its records and its other login
   methods intact. A principal whose last identity row is removed falls back to
   its remaining enabled methods.
7. **Disabling a provider disables its identities.** When a provider record is
   removed or disabled, identities under it stop resolving immediately. Rows
   SHALL be retained, not deleted, so that re-enabling is not a re-enrolment.
8. **Every link, unlink, successful login and failed login SHALL be recorded**
   through CARE's existing audit trail, using opaque identifiers — never a raw
   token, a raw contact value, or a full subject.

### 6. Providers coexist and are configured independently

The three families of login method SHALL be independent switches, and SHALL
compose:

```text
CARE phone OTP      legacy/native; on by default; removable only by operator decision
Firebase            optional; patient only; phone and passwordless email link
OIDC providers      optional; zero or more; workforce and/or patient
```

Enabling one SHALL NOT disable another. A deployment SHALL be able to run any
subset, including the empty subset for the external providers, which is CARE's
behaviour today. With no OIDC provider configured, CARE SHALL NOT contact any
issuer, SHALL NOT mount OIDC routes, SHALL NOT render an OIDC choice, and SHALL
start and pass health checks with no OIDC configuration present at all.

When a provider is enabled, its configuration SHALL be validated at startup and
every failure during an exchange — discovery, issuer, audience, signature,
expiry, nonce, PKCE, redirect URI — SHALL fail closed.

Where a provider's discovery document advertises `end_session_endpoint`, CARE
MAY offer RP-initiated logout for that provider. CARE's own session SHALL end
locally and unconditionally on logout, whether or not the provider's endpoint
is reachable or advertised. Back-channel and front-channel logout are not
adopted by this ADR.

### 7. No new infrastructure requirement

This decision SHALL NOT make any of the following a requirement of running
CARE: Keycloak, any other identity provider, Firebase, Redis, or any Google
Cloud service. OIDC transaction material (state, nonce, PKCE verifier) SHALL
live in the user's browser for the duration of one login and SHALL NOT require
a shared server-side session store. Rate limiting SHALL continue to use CARE's
existing configurable backend, which already supports Redis, PostgreSQL and
disabled operation.

CARE SHALL run identically under Docker Compose, a traditional virtual machine,
Kubernetes and the GCP profile, consistent with ADR-0006. A local Keycloak
container SHALL be a **development and test convenience**, never a runtime
dependency of any environment.

## Alternatives considered

### Keep ADR-0010's Keycloak adapter and add providers later

Rejected. The current schema stores a subject with no issuer, and the patient
exchange resolves on that subject alone. Adding a second provider on top of
that schema creates a cross-issuer takeover the day it is configured. The fix
is the schema, and the schema is best fixed while nothing is enrolled — which
is exactly now.

### Rename `keycloak_subject` to `oidc_subject` and stop there

Rejected. It removes the vendor name and leaves the defect. A single unique
subject column still cannot represent two issuers, still cannot represent a
principal with two identities, and still resolves without an issuer.

### One generic identity table with a generic foreign key

Rejected. It gives up database-level referential integrity on the join that
decides who someone is, and it merges two principal types whose lifecycle,
authorization and deletion rules genuinely differ. Two narrow tables cost one
extra model and keep the constraint the database can actually enforce.

### Federate everything through one Keycloak instance

Keycloak brokers other IdPs well, and this is a legitimate deployment topology
that an operator may choose. Rejected as an *architecture* because it makes
Keycloak mandatory for every installation that wants any external login,
including installations that already run an IdP and have no wish to operate a
second one. The broker remains available to operators who want it; CARE does
not require it.

### Adopt a full identity framework (django-allauth, mozilla-django-oidc, …)

Rejected for this step. CARE needs an ID token validated against a pinned
issuer and audience, and a principal resolved from a triple. The libraries in
this space bring auto-provisioning, claim-to-role mapping and session models
that this ADR explicitly forbids, and disabling those defaults is a larger and
less legible surface than the exchange CARE already has. CARE SHALL continue to
use vetted primitives — `authlib` for JOSE, `requests` for transport — rather
than hand-rolled crypto.

### Accept ID tokens directly at application APIs

Rejected. It spreads issuer, audience, key-rotation and revocation concerns
across every endpoint, ties API authorization to an external token lifetime,
and removes the single boundary at which CARE decides who a subject is.

### Auto-provision principals from verified claims

Rejected. It hands an external IdP the ability to create clinical principals,
and with `email` as the linking key it hands anyone who can set an email claim
the ability to become an existing one.

## Consequences

### Positive

- Any conforming OIDC provider works with configuration only.
- CARE has no vendor in its authentication schema, settings, routes or UI.
- `(provider, issuer, subject)` removes cross-issuer subject collision by
  construction rather than by a single-issuer restriction.
- A principal can hold several identities, which is what real deployments and
  provider migrations need.
- Clinical authorization stays in CARE, unreachable from an IdP claim.
- Deployments with no IdP are unaffected; phone OTP remains complete.
- No new infrastructure requirement is introduced.

### Negative

- A schema change and a rename land in one step; both are pre-adoption, but
  they are not free.
- ES-10's operator guide, environment examples and frontend build variables all
  need rewriting.
- Multi-provider configuration is more to document, validate and test than a
  fixed six-variable block.
- Explicit linking is more operator work than auto-provisioning, permanently.
  That is the point, and it should be stated as a cost rather than hidden.
- Two identity tables mean two admin surfaces and two sets of tests.

### Risks and mitigations

- **Cross-issuer subject collision** — resolve on the full triple; no
  subject-only lookup exists; a conformance test asserts that two providers
  sharing a `sub` resolve to different principals or to none.
- **Claim-based takeover** — no claim other than the triple resolves a
  principal; linking requires an administrator or an already-authenticated
  principal.
- **Confused-deputy across principal types** — a provider record declares one
  `principal_type`; a patient-audience token entering the workforce exchange is
  rejected on audience before any lookup.
- **Token substitution** — pinned `iss`, `aud` equal to the provider's
  `client_id`, `azp` checked when `aud` is multi-valued, signature verified
  against discovered JWKS, RS/ES algorithms only, `none` and HMAC rejected.
- **JWKS fetch as an availability and DoS surface** — cached with a TTL,
  bounded single refresh on an unknown `kid`, short timeouts, per-IP rate limit
  on every exchange endpoint.
- **Misconfiguration** — startup validation for enabled providers; incomplete
  configuration hides the method rather than offering a dead end.
- **Silent re-binding** — `issuer` is stored on the identity row, so pointing a
  provider elsewhere breaks resolution loudly instead of re-binding accounts.
- **Provider outage** — CARE's own methods remain enabled and independent;
  disabling a provider is a configuration change and a restart.

## Security implications

The threat this design is built against is **account takeover through the
identity layer**, and the controls are:

| Control | Requirement |
|---|---|
| Principal resolution | Only `(provider_id, issuer, subject)`; no claim-based lookup exists |
| Auto-provisioning | Forbidden for `User`, `Patient`, roles and identity rows |
| Claim-based authorization | Forbidden; no role/group/scope claim is read |
| Linking | Administrator, or the principal itself while already authenticated |
| Patient record linking | Administrative path only |
| Re-linking | Refused; recorded |
| Subject/issuer mutation | Forbidden; unlink and re-enrol |
| Browser flow | Authorization Code + PKCE (S256); `state` and `nonce` single-use, tab-scoped, cleared on first read |
| Redirect URI | Exact byte-for-byte match against the provider record |
| Discovery | Over TLS; `issuer` claim must equal configuration; every endpoint must share the issuer origin |
| Signature | Discovered JWKS; RS/ES only; `none` and HMAC rejected |
| Audience | `aud` must equal `client_id`; `azp` checked when `aud` is multi-valued |
| Expiry | `exp`/`iat` enforced with bounded clock skew |
| Failure responses | Uniform and generic; no enumeration of principals, providers or reasons |
| Logging | No raw token, no full contact value, no PKCE verifier, no nonce; opaque CARE identifiers only |
| Secrets | Client secrets through the existing secret mechanism; never in a frontend bundle or a tracked file |
| Rate limiting | Per-IP on every exchange endpoint, through CARE's configurable backend |
| Audit | Link, unlink, success and failure recorded with principal type, provider id and outcome |

Local HTTP is accepted for `localhost`, `127.0.0.1` and `::1` only, to keep a
containerised development issuer usable; every other issuer must be HTTPS.

## Portability implications

- **No mandatory service.** Zero configured providers is a supported, tested
  production state. Keycloak, Firebase, Redis and GCP services all remain
  optional.
- **Browser-held transaction state.** No shared server-side session store is
  required, so the design works on a single VM, on Compose, across Kubernetes
  replicas and on scale-to-zero Cloud Run identically.
- **Configuration, not code.** Providers are declared through the existing
  configuration mechanism, honouring ADR-0006's runtime profiles: the same
  image runs everywhere and differs only by configuration.
- **Discovery, not hardcoding.** CARE learns endpoints from the issuer, so a
  provider upgrade or endpoint move needs no CARE change.
- **Standard-only surface.** CARE uses no Keycloak admin API, no Keycloak token
  extension and no vendor claim, so a deployment can move between providers by
  re-enrolling identities under a new provider record.
- **Local test issuer is disposable.** The Keycloak container used for tests is
  a fixture. Deleting it degrades the test suite, not any environment.

## Relationship with OTP and Firebase

CARE's phone OTP is **not** legacy in the sense of deprecated. It is the method
that requires no external dependency, and for a clinic with no IdP it is the
only method that works. It remains supported, remains on by default, and is
removed only by an explicit operator decision — never as a side effect of
enabling another provider.

Firebase remains exactly what ADR-0010 §3 made it: an optional, patient-only
proof-of-contact step for SMS and passwordless email link, whose ID token CARE
verifies server-side before issuing a `PatientToken`. It is **not** modelled as
an OIDC provider. It is not a general-purpose IdP for CARE, it does not carry a
`principal_type`, and it does not participate in the identity-row model, which
would imply an account-linking story Firebase is not being used for. This ADR
changes nothing about Firebase.

The three families remain independently switchable and compose freely:

```text
OTP only                     no external dependency; today's default
OTP + Firebase               patients get SMS/email; workforce unchanged
OTP + OIDC                   workforce SSO; patients keep OTP
OTP + Firebase + OIDC        every method available
OIDC only                    operator has disabled OTP after migration
none of the three            not a valid state; at least one method must be enabled
```

Startup SHALL refuse a configuration in which no login method is enabled for a
principal type that CARE serves.

## Identity vs authorization boundary

Stated once, plainly, because it is the decision most likely to be eroded by a
later convenience request:

> **Authentication tells CARE *which subject* is present. CARE alone decides
> what that subject may do.**

The boundary is enforced structurally, not by convention:

- The exchange endpoint's only output is a CARE credential for an
  **already-enrolled, already-authorized** principal.
- Enrolment is a separate, deliberate, recorded act.
- No code path reads an authorization-shaped claim, and none may be added
  without superseding this ADR.
- Application APIs cannot accept an external token at all, so there is no
  endpoint at which a claim could become a permission.

A request to "just map the IdP's `care-admin` group to CARE's administrator
role" is a request to supersede this ADR, and should be handled as one.

## Why OIDC is the abstraction and Keycloak is only a reference implementation

OIDC is the abstraction because it is the **narrowest contract that satisfies
the requirement**. What CARE needs from an identity provider is: a discovery
document, an authorization endpoint that supports code + PKCE, a token
endpoint, a JWKS, and an ID token carrying `iss`, `sub`, `aud`, `exp`, `iat`
and `nonce`. That list is the OIDC Core specification, it is implemented by
every provider CARE's deployers actually run, and it is stable across vendor
releases. Everything Keycloak adds beyond it — realms, the admin REST API,
`realm_access`, identity brokering, its authentication-flow model — is either
an operator concern CARE has no business reading, or a vendor extension that
would make a non-Keycloak deployment a code change.

Keycloak is the reference implementation because a specification is not
evidence. "Standards-compliant" is a claim that only a real server can settle,
and the disciplined way to hold both properties at once is to define the
contract in standard terms and then prove it against a real, free,
self-hostable server that anyone can reproduce. Keycloak is that server: it is
open source, it is what many CARE-shaped deployments already run, and it
containerises well enough to sit in a test fixture.

The invariant that keeps the two apart: **CARE's code and configuration must
never be able to tell which provider is on the other end.** If a future change
needs to know, it has left the abstraction, and it needs a new ADR rather than
a special case.

## Relationship with ADR-0010

ADR-0010 remains **Accepted**. This ADR amends it as follows.

| ADR-0010 | Status under ADR-0011 |
|---|---|
| §1 two principal types | **Unchanged.** `User` and `Patient` stay separate and neither authenticates into the other. |
| §2 dormant Keycloak adapter, one issuer, two fixed clients | **Amended.** Generalised to zero-or-more configured OIDC providers; the one-flag dormancy property is preserved as "no provider configured". |
| §3 Firebase patient paths | **Unchanged.** |
| §4 `keycloak_subject` columns, "no external-identity registry" | **Superseded.** Replaced by two `(provider, issuer, subject)` identity tables. ADR-0010 judged a registry unnecessary *for one issuer*; that premise no longer holds. |
| §5 explicit exchanges, explicit enrolment, no auto-provisioning | **Retained and strengthened** by the account-linking rules in §5 above. |
| §6 proportional security and abuse controls | **Retained**, extended for multi-provider concerns. |
| §7 dormant until enabled; ADR-0009 governs production | **Unchanged.** |

The vendor-named settings, columns, routes, modules and frontend variables
introduced under ADR-0010 SHALL be renamed rather than dual-supported. No
environment has ever enabled them and no subject has ever been enrolled, so
there is no deployed configuration to preserve and a compatibility shim would
only carry the vendor name forward. ES-10's Keycloak activation guide SHALL be
rewritten as a provider-agnostic guide with a Keycloak worked example.

## Implementation handoff

Execution is specified in ES-11. The intended order:

1. introduce the provider configuration record, its validation and its loader;
2. add the two identity tables and migrate the two `keycloak_subject` columns
   into them, then remove the columns;
3. generalise the exchange to resolve on the full triple, with JWKS caching,
   `azp` handling and an algorithm allowlist;
4. rename backend modules, settings and routes to provider-agnostic names;
5. add the administrative and self-service linking paths with their audit
   records;
6. rename and generalise the frontend configuration, transaction helper,
   callback route and components; render providers from configuration;
7. add the local Keycloak fixture and the provider-agnostic OIDC double, and
   run the same conformance suite against both;
8. rewrite the operator guide, the configuration reference and the environment
   examples;
9. prove the OTP × Firebase × OIDC matrix, including the all-disabled state.

## Implementation status

- [x] Architectural decision accepted.
- [x] Provider configuration record and startup validation.
- [x] `UserExternalIdentity` and `PatientExternalIdentity` with the triple
      constraint.
- [x] Data migration from `keycloak_subject`; vendor columns removed. Forward,
      backward and forward again proved against a disposable database seeded on
      both principal types.
- [x] Triple-based resolution; no subject-only lookup remains.
- [x] JWKS caching with bounded refresh, RS/ES allowlist, `azp` handling.
- [x] Vendor-agnostic backend modules, settings and routes.
- [x] Administrative and self-service linking, with audit records that never
      carry the subject.
- [x] Vendor-agnostic frontend: providers come from the backend, and the
      authorization endpoint comes from the issuer's discovery document.
- [x] Local Keycloak fixture and generic OIDC double; the same 20 conformance
      assertions pass against both.
- [x] Provider-agnostic operator guide with a Keycloak worked example
      (`docs/xii/operations/oidc-provider-guide.md`).
- [x] OTP × Firebase × OIDC matrix proven in dev, and a real end-to-end login
      against a real issuer (`docs/xii/operations/oidc-dev-acceptance.md`).
      Firebase remains verified in half, for the reason ES-10 §13 records.
- [ ] Production authentication approved and enabled. *(Governed by ADR-0009
      and ES-09; out of scope here.)*

## Related documents and sources

- `docs/xii/adr/ADR-0006-portable-runtime-profiles.md`
- `docs/xii/adr/ADR-0009-controlled-production-activation.md`
- `docs/xii/adr/ADR-0010-multichannel-authentication-with-keycloak.md`
- `docs/xii/implementation/ES-09-production-readiness.md`
- `docs/xii/implementation/ES-10-multichannel-authentication.md`
- `docs/xii/implementation/ES-11-standard-oidc-authentication.md`
- `docs/xii/operations/oidc-provider-guide.md`
- `config/keycloak.py`, `config/keycloak_service.py`, `config/keycloak_views.py`,
  `config/keycloak_urls.py`
- `config/firebase_auth*.py`
- `config/patient_otp_authentication.py`, `config/patient_otp_token.py`
- `care/emr/models/patient.py`, `care/users/models.py`
- OpenID Connect Core 1.0: `https://openid.net/specs/openid-connect-core-1_0.html`
- OpenID Connect Discovery 1.0:
  `https://openid.net/specs/openid-connect-discovery-1_0.html`
- OpenID Connect RP-Initiated Logout 1.0:
  `https://openid.net/specs/openid-connect-rpinitiated-1_0.html`
- RFC 7636, PKCE: `https://datatracker.ietf.org/doc/html/rfc7636`
- RFC 9700, Best Current Practice for OAuth 2.0 Security:
  `https://datatracker.ietf.org/doc/html/rfc9700`
- Keycloak securing applications with OIDC:
  `https://www.keycloak.org/securing-apps/oidc-layers`
