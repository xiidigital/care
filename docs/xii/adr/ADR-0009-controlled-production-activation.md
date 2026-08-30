# ADR-0009: Controlled Production Activation and Frontend Delivery

- **Status:** Accepted
- **Date:** 2026-08-30
- **Decision Makers:** CARE Fork Maintainers
- **Supersedes:** None
- **Superseded by:** None

## Context

ADR-0008 and ES-08 established immutable backend artifacts, GitHub Actions,
Workload Identity Federation, staging acceptance and a protected production
promotion gate. The delivery chain has run successfully through publication,
staging deployment and promotion eligibility.

A production GCP environment and a Firebase-hosted frontend now exist and have
passed non-destructive smoke verification. That proves technical reachability;
it does not by itself authorize clinical use. Production still has no approved
operator account, patient login is intentionally disabled, the GitHub
`production` environment lacks the variables needed for automated promotion,
and the infrastructure identity still lacks its deferred project-level roles.

The frontend is a separately built static artifact. Its public build-time
configuration, hosting rules and telemetry choices must be reproducible without
committing credentials or relying on one maintainer's ignored files.

## Decision

Production SHALL have two distinct states:

1. **technically deployed** — infrastructure and artifacts exist and pass
   non-destructive verification;
2. **clinically activated** — an accountable operator has approved identities,
   data governance, recovery, monitoring and access controls for real patient
   care.

A technically deployed environment SHALL NOT be described as clinically ready
until the activation checklist in ES-09 is complete.

### Frontend delivery

- Frontend hosting configuration SHALL be versioned.
- Public production build inputs MAY be versioned in an example file; secret
  values SHALL NOT be committed.
- The actual `.env.production.local` SHALL remain ignored.
- Deployment SHALL name the Firebase project explicitly. No tracked default
  project selector is required.
- The frontend SHALL point to the selected backend using
  `REACT_CARE_API_URL`; backend CORS SHALL explicitly allow the frontend origin.
- Patient login SHALL remain disabled until an authorized reCAPTCHA site key,
  allowed domains and end-to-end authentication test exist.

### Telemetry and protected health information

- Error telemetry SHALL be opt-in. No third-party DSN or environment SHALL be
  embedded as a fallback in source.
- Enabling Sentry or another processor requires an approved account, retention
  policy, data-processing terms and a review preventing PHI from being sent in
  payloads, URLs, breadcrumbs or user context.
- Empty telemetry configuration SHALL disable telemetry without producing a
  runtime error.

### Identity and clinical bootstrap

- Fixture loaders SHALL remain development-only and SHALL NOT create production
  users or clinical records.
- The first production administrator SHALL be created by an explicit, audited,
  one-time operator procedure using an identity approved for clinical
  operations.
- The personal address used during infrastructure setup SHALL NOT be assumed to
  be the clinical owner merely because it can administer GCP.
- No real patient data SHALL be entered before the responsible organization has
  approved access, privacy, retention, backup and incident-response procedures.

### Delivery control

- Backend production releases SHALL continue to select immutable image digests.
- A manual deployment is valid recovery evidence but does not prove the GitHub
  production promotion path.
- Future automated production promotion requires the protected GitHub
  environment variables, the intended production service account grants and a
  successful gated smoke run.
- The deferred `care-infra` project roles remain a separate bootstrap action;
  they SHALL NOT be broadened merely to close documentation.

## Consequences

- Development remains the normal environment for fixtures and iterative work.
- Production may remain deployed but dormant while clinical prerequisites are
  incomplete.
- Frontend releases become repeatable from repository evidence without storing
  credentials.
- Clinical activation requires explicit human decisions that source code cannot
  safely infer.
- The production workflow and infrastructure plan identity remain visibly
  incomplete until an authorized operator finishes their configuration.

## Alternatives rejected

### Treat a successful smoke test as clinical readiness

Rejected because availability does not establish identity governance, privacy,
backup, support or incident response.

### Load development fixtures into production

Rejected because fixtures create synthetic identities and data with unsuitable
credentials and provenance.

### Keep frontend production configuration only on a workstation

Rejected because the deployed artifact could not be reconstructed or reviewed.

### Enable shared external telemetry by default

Rejected because ownership, retention and PHI handling would be implicit.

## Related documents

- `docs/xii/implementation/ES-09-production-readiness.md`
- `docs/xii/adr/ADR-0008-automated-continuous-integration.md`
- `docs/xii/architecture/08-continuous-delivery.md`
- `docs/xii/architecture/inventory/unresolved-items.md`
