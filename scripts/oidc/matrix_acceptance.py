"""Walk the ES-11 §10 matrix against real processes and print the evidence.

Each row starts CARE with that row's configuration and asks what the process
actually does: does it start, and which authentication routes exist. Routes are
decided once at URLConf construction from the settings the process was started
with, so nothing here can be answered by patching settings in-process.

Run from the repository root:

    DATABASE_URL=... python scripts/oidc/matrix_acceptance.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PATHS = {
    "otp": "/api/v1/otp/login/",
    "staff": "/api/v1/auth/login/",
    "firebase": "/api/v1/auth/firebase/patient/exchange/",
    "oidc_workforce": "/api/v1/auth/oidc/workforce/exchange/",
    "oidc_patient": "/api/v1/auth/oidc/patient/exchange/",
    "providers": "/api/v1/auth/providers/",
}

ISSUER = os.environ.get("CARE_OIDC_LIVE_ISSUER", "https://identity.example/realms/care")


def provider(pid, principal):
    return {
        "id": pid,
        "display_name": pid,
        "issuer": ISSUER,
        "principal_type": principal,
        "client_id": f"care-{principal}",
        "client_secret": "acceptance-secret",
    }


WORKFORCE = json.dumps([provider("clinic-sso", "workforce")])
PATIENT = json.dumps([provider("patient-sso", "patient")])
BOTH = json.dumps(
    [provider("clinic-sso", "workforce"), provider("patient-sso", "patient")]
)
TWO_WORKFORCE = json.dumps(
    [
        provider("clinic-sso", "workforce"),
        {**provider("regional-sso", "workforce"), "issuer": f"{ISSUER}-b"},
    ]
)

FIREBASE_ON = {"FIREBASE_AUTH_ENABLED": "true", "FIREBASE_AUTH_PROJECT_ID": "care-dev"}
FIREBASE_OFF = {"FIREBASE_AUTH_ENABLED": "false"}
BASE_URL = {"OIDC_PUBLIC_BASE_URL": "https://care.example"}

ROWS = [
    ("1", "OTP alone -- CARE's default", {"OIDC_PROVIDERS": "", **FIREBASE_OFF}),
    ("2", "OTP + Firebase", {"OIDC_PROVIDERS": "", **FIREBASE_ON}),
    (
        "3",
        "OTP + workforce provider",
        {"OIDC_PROVIDERS": WORKFORCE, **BASE_URL, **FIREBASE_OFF},
    ),
    (
        "4",
        "OTP + patient provider",
        {"OIDC_PROVIDERS": PATIENT, **BASE_URL, **FIREBASE_OFF},
    ),
    (
        "5",
        "OTP + Firebase + both providers",
        {"OIDC_PROVIDERS": BOTH, **BASE_URL, **FIREBASE_ON},
    ),
    (
        "6",
        "Firebase replaces a retired OTP",
        {"OIDC_PROVIDERS": "", "CARE_PATIENT_OTP_ENABLED": "false", **FIREBASE_ON},
    ),
    (
        "7",
        "providers only, OTP retired",
        {
            "OIDC_PROVIDERS": BOTH,
            **BASE_URL,
            "CARE_PATIENT_OTP_ENABLED": "false",
            **FIREBASE_OFF,
        },
    ),
    (
        "8",
        "two workforce issuers",
        {"OIDC_PROVIDERS": TWO_WORKFORCE, **BASE_URL, **FIREBASE_OFF},
    ),
    (
        "9",
        "no patient method at all -- must refuse",
        {"OIDC_PROVIDERS": "", "CARE_PATIENT_OTP_ENABLED": "false", **FIREBASE_OFF},
    ),
    (
        "10",
        "provider disabled mid-flight",
        {
            "OIDC_PROVIDERS": json.dumps(
                [{**provider("clinic-sso", "workforce"), "enabled": False}]
            ),
            **BASE_URL,
            **FIREBASE_OFF,
        },
    ),
]

PROBE = (
    "import django;django.setup()\n"
    "from django.urls import Resolver404, resolve\n"
    f"for name, path in {PATHS!r}.items():\n"
    "    try:\n"
    "        resolve(path); print('MOUNTED', name)\n"
    "    except Resolver404:\n"
    "        print('ABSENT', name)\n"
)


def run(extra_env):
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.local",
        **extra_env,
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", PROBE],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def main() -> int:
    failures = 0
    print(f"{'row':>4}  {'starts':<7}  routes")
    print("-" * 78)
    for row, label, extra in ROWS:
        result = run(extra)
        started = result.returncode == 0
        if not started:
            # The last ImproperlyConfigured line is the message; the first is
            # the `raise` statement echoed in the traceback.
            reasons = [
                line
                for line in result.stderr.splitlines()
                if "ImproperlyConfigured:" in line
            ]
            detail = (
                reasons[-1].split("ImproperlyConfigured:")[-1].strip()[:62]
                if reasons
                else "(no reason captured)"
            )
        else:
            mounted = [
                line.split()[1]
                for line in result.stdout.splitlines()
                if line.startswith("MOUNTED")
            ]
            detail = ", ".join(mounted)
        print(f"{row:>4}  {'yes' if started else 'REFUSED':<7}  {detail}")
        print(f"      {label}")
        # Row 9 is the only configuration CARE must refuse.
        if (row == "9") == started:
            failures += 1
            print(f"      !! unexpected: row {row} started={started}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
