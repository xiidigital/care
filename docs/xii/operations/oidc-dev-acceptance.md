# ES-11 dev acceptance record

- **Status:** Complete for OIDC and for the coexistence matrix. Firebase is
  verified in half, for the reason ES-10 already recorded.
- **Date:** 2026-09-09
- **Environment:** local, against a containerised Keycloak 26.4
  (`docker-compose.oidc.yaml`).
- **Production:** unchanged. Nothing here authorizes it; ADR-0009 and ES-09
  still govern.
- **Data:** synthetic only. No real patient data was used.

---

## 1. What was proved, and where the evidence lives

| Claim | Evidence |
|---|---|
| CARE speaks standard OIDC, not a vendor | `care/users/tests/test_oidc_conformance.py` — the same 20 assertions pass against a generic double and against real Keycloak |
| A real login works end to end | `care/users/tests/test_oidc_dev_acceptance.py` — 5 tests, real authorization code, real CARE credential, real API call |
| The matrix behaves as specified | `scripts/oidc/matrix_acceptance.py`, output in §3 |
| Rows 1 and 9 stay true forever | `care/users/tests/test_external_auth_startup.py` |
| Migrations are reversible | Forward/backward/forward against a disposable database seeded on both principal types (ES-11 §7.2) |

## 2. The end-to-end login

Run against the containerised issuer, in the order the security story requires
— because "auto-provisioning is off" is the claim that matters most and the one
easiest to believe without checking.

1. **An unenrolled subject is refused.** A genuine, issuer-signed identity for
   `synthetic-clinician` reaches the workforce exchange and receives 401. No
   `User`, no `UserExternalIdentity`, nothing created.
2. **An administrator enrols it**, using the `sub` claim the provider issued.
3. **The same person logs in** and receives an ordinary CARE access/refresh
   pair, which then authenticates a real call to
   `/api/v1/users/getcurrentuser/` and returns the enrolled user. The identity
   row records `last_login_at`.

Also proved live, with genuine issuer-signed tokens rather than crafted ones:

- a patient-audience code is refused at the workforce exchange;
- an unlinked identity stops working immediately, which is how access is
  actually withdrawn;
- an enrolled patient reaches its own record and no other;
- no `Patient` is created for an unenrolled subject.

```bash
make oidc-up                     # CARE_OIDC_PORT=8082 if 8081 is taken
CARE_OIDC_LIVE_ISSUER=http://localhost:8082/realms/care-test \
  python manage.py test care.users.tests.test_oidc_dev_acceptance --keepdb
make oidc-down
```

## 3. The matrix (ES-11 §10)

Each row starts a real process with that row's configuration and is asked which
authentication routes exist. Routes are decided once at URLConf construction
from the settings the process started with, so none of this can be faked
in-process.

```text
 row  starts   routes
------------------------------------------------------------------------------
   1  yes      otp, staff
      OTP alone -- CARE's default
   2  yes      otp, staff, firebase
      OTP + Firebase
   3  yes      otp, staff, oidc_workforce, providers
      OTP + workforce provider
   4  yes      otp, staff, oidc_patient, providers
      OTP + patient provider
   5  yes      otp, staff, firebase, oidc_workforce, oidc_patient, providers
      OTP + Firebase + both providers
   6  yes      staff, firebase
      Firebase replaces a retired OTP
   7  yes      staff, oidc_workforce, oidc_patient, providers
      providers only, OTP retired
   8  yes      otp, staff, oidc_workforce, providers
      two workforce issuers
   9  REFUSED  No patient login method is enabled. Set CARE_PATIENT_OTP_ENABL
      no patient method at all -- must refuse
  10  yes      otp, staff
      provider disabled mid-flight
```

Row 1 is the one that matters most: it is CARE's default, and it must stay
green forever. No OIDC route, no issuer contacted, no OIDC setting required.

Row 9 is the only configuration CARE refuses.

Row 10 shows a disabled provider mounting nothing — its identity rows are kept,
so re-enabling is not a re-enrolment.

### A defect this run found

Rows 6 and 7 originally still mounted `/api/v1/otp/login/` and
`/api/v1/otp/send/` with `CARE_PATIENT_OTP_ENABLED=false`. The flag gated
startup validation and the rendered button, but not the route — and a retired
login that still answers is not retired, because a client that knows the URL
does not read buttons. `config/api_router.py` now unmounts it, and
`test_external_auth_startup.py` holds the line.

`otp/patient` and `otp/slots` deliberately stay mounted in every row. Their
names are historical: they are the authenticated patient's own APIs, reached
with a `PatientToken` whoever issued it, so a Firebase or OIDC patient needs
them exactly as much as an OTP one.

## 4. What is not proved here

Stated rather than implied, because a half-verified claim recorded as complete
is worse than an open one.

**Firebase is verified in half.** Its gating, startup validation, route
mounting and SMS country policy are covered; a live Firebase login is not
re-run here. ES-10 §13 records why: Firebase escalates its invisible reCAPTCHA
to a visual challenge under headless automation, and solving CAPTCHAs is out of
bounds. ES-10's own verification against the real dev project stands, and
ES-11 changed nothing about Firebase.

**Frontend journeys are asserted, not driven against a live issuer.** The
Playwright OIDC tests stub the provider list and assert what is rendered and
where the browser is sent. Completing a login in a browser needs a served build
and a reachable issuer together, which is a staging exercise, not a local one.

**Staging and production are untouched.** No deployment, no `tofu apply`, no
GitHub environment change, no new GCP permission.

**The T19 limitation stands.** A CARE session outlives a deprovisioning at the
provider by up to the CARE credential's lifetime. See
`docs/xii/operations/oidc-provider-guide.md` §9.

## 5. Reproducing this

```bash
make oidc-up
CARE_OIDC_LIVE_ISSUER=http://localhost:8082/realms/care-test \
  python manage.py test care.users.tests.test_oidc_conformance \
                        care.users.tests.test_oidc_dev_acceptance --keepdb
python scripts/oidc/matrix_acceptance.py
make oidc-down
```

Without the container, both suites skip and the rest of the backend suite is
unaffected — which is the point. Nothing in CARE requires an identity provider.

## Related documents

- `docs/xii/adr/ADR-0011-standard-oidc-authentication.md`
- `docs/xii/implementation/ES-11-standard-oidc-authentication.md`
- `docs/xii/operations/oidc-provider-guide.md`
- `docs/xii/implementation/ES-09-production-readiness.md`
