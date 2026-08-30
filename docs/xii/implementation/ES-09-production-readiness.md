# ES-09: Production Readiness, Frontend Delivery and Controlled Clinical Activation

- **Status:** Technical closeout implemented; clinical activation deferred
- **Related ADR:** ADR-0009: Controlled Production Activation and Frontend Delivery
- **Depends on:** ES-08 delivery chain and accepted staging evidence
- **Working environment after closeout:** dev

## 1. Objective

Close the gap between deployed infrastructure and a controlled clinical
service. ES-09 records what is already proven, makes frontend delivery
reproducible, and separates code-complete work from operator-owned activation.

ES-09 does not authorize production accounts, real patient data, reCAPTCHA,
third-party telemetry, GitHub environment changes or broader GCP permissions.

## 2. Verified baseline

### Backend delivery

The ES-08 GitHub chain completed through the protected production eligibility
gate:

| stage | evidence | result |
|---|---|---|
| build and publish | GitHub Actions run `32406259063` | success |
| staging deploy and acceptance | GitHub Actions run `32411111409` | success |
| production eligibility | GitHub Actions run `32412389243` | eligible; held at gate |

The accepted staging artifact was
`sha256:90c634ef669d6f0fe45c7757892b1de0b0ef49a8e24a592f4cd54189fc513c2d`
from source `bd45a537432ce6e9a7f1752de5fbb41fe9fe50b2`.

The production backend was subsequently provisioned and deployed manually. Its
API, worker isolation, init image and non-destructive smoke checks passed. The
manual deployment does not count as proof of the GitHub production workflow.

### Frontend delivery

The frontend was built against the production API and published to
`https://care-prod-jsgaviota-2608.web.app`. Browser reachability, backend CORS,
security/cache headers and API compatibility were verified. Patient login is
disabled and external Sentry telemetry is not configured.

The frontend repository records the hosting rules, the public production build
inputs and an operator procedure. The actual local production environment file
remains ignored.

### Infrastructure state

- Bootstrap state was recovered and verified with a zero-change plan.
- Dev and staging state were initialized and verified.
- Dev smoke verification passed against its deployed digest.
- Staging smoke verification passed against its actual deployed digest.
- The production scheduler alert now uses the execution log emitted by Cloud
  Scheduler rather than a nonexistent metric.

## 3. Boundaries

The following are deliberately prohibited during technical closeout:

- running fixtures in staging or production;
- creating a production administrator without an approved owner;
- entering real patient data;
- enabling patient login without reCAPTCHA and an authentication test;
- enabling telemetry without a PHI review and approved processor;
- granting broad roles simply to make automation green;
- changing production again merely to satisfy this document.

Development remains the environment for fixtures and product adjustment.

## 4. Clinical activation checklist

Clinical activation remains **blocked by operator decisions**, not by missing
application code. Before real clinical use, an accountable organization SHALL
complete and record:

- [ ] named service owner and clinical owner;
- [ ] approved first administrator identity and audited creation procedure;
- [ ] least-privilege staff roles and account recovery procedure;
- [ ] privacy notice, data-retention policy and lawful processing basis;
- [ ] backup, restore and recovery-time verification;
- [ ] incident response, support contact and escalation path;
- [ ] monitoring destinations and alert recipients;
- [ ] production domain and TLS ownership, if replacing the Firebase hostname;
- [ ] reCAPTCHA account, allowed domains and patient-login verification, if
      patient login is required;
- [ ] telemetry processor approval and PHI-safe configuration, if telemetry is
      required;
- [ ] final clinical workflow acceptance using non-patient test records;
- [ ] documented go-live approval.

Until every applicable item is complete, production is technically deployed but
not approved for real clinical data.

## 5. Automation prerequisites still open

These items may be completed independently of clinical activation:

1. Configure the protected GitHub `production` environment with the exact
   non-secret GCP variables expected by the workflows.
2. Verify the production deploy identity has only the roles declared by the
   production root.
3. Exercise the gated GitHub promotion with an already accepted digest and
   retain the smoke evidence.
4. Apply the reviewed bootstrap option that grants `care-infra` its enumerated
   project roles, then rerun the CI infrastructure plan. This is D12 and remains
   an explicit operator bootstrap action.

No GitHub repository secret or service-account key is required for these steps.

## 6. Completion criteria

The ES-09 technical closeout is complete when:

- [x] ADR-0009 records the deployment-versus-activation boundary;
- [x] stale ES-08 production statements are corrected without erasing their
      historical context;
- [x] Firebase hosting rules are versioned;
- [x] the production frontend's public build inputs are reproducible from a
      tracked example;
- [x] Sentry is opt-in with no hardcoded external DSN;
- [x] patient login remains explicitly disabled in the production example;
- [x] fixture use remains forbidden outside development;
- [x] code and configuration validation pass;
- [x] focused local commits record backend and frontend changes.

Clinical activation is a later operator milestone. It is intentionally not
claimed by this technical completion status.

## 7. Next work

Continue feature and fixture work in dev. When an accountable production owner
is ready, execute section 4 as a controlled activation review and section 5 as a
separate automation change window.
