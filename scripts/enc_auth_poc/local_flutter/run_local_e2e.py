"""Local end-to-end run of the Flutter encrypted-auth client against the isolated G6b lab (LOCAL ONLY).

Starts the existing G6b lab (hash-gated build of the backend, Firebase Auth EMULATOR on 127.0.0.1:9099, an --internal Docker network with
Postgres and one backend container), then runs the app's Flutter test `test/enc_auth/enc_auth_local_e2e_test.dart` against it, then tears
down only what it started. No production service, Firebase project, credential or secret is involved: keys are generated in memory for
the run (the private half goes to the backend container's environment only; the public half is passed to Flutter as --dart-define).

    python -B -I scripts/enc_auth_poc/local_flutter/run_local_e2e.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POC = HERE.parent
REPO = POC.parent.parent
APP = REPO / "frontend" / "EpicVerseApp"
FLUTTER = Path(r"D:\flutter sdk\flutter\bin\flutter.bat")
sys.path.insert(0, str(POC))
sys.path.insert(0, str(POC / "g6b" / "harness"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from testkeys import kid_for  # noqa: E402
from orchestrate import BACKENDS, Lab, pem  # noqa: E402


class EncLab(Lab):
    """The G6b lab, but the server keyring uses the DERIVED key id (what the app computes from the public key) instead of a label."""

    def keys_json(self, kids):
        return json.dumps({kid_for(self.keys[k].public_key()): pem(self.keys[k]) for k in kids})


def main():
    if not FLUTTER.exists():
        raise SystemExit("flutter not found at the expected local path")
    lab = EncLab(variants=["main"])
    try:
        lab.up()
        public = lab.public_key("g6b-key-1")
        der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        import base64
        port = BACKENDS["main"][1]
        defines = {
            "ENC_E2E_API": "http://127.0.0.1:%d/api/v1" % port,
            "ENC_E2E_LAB": "http://127.0.0.1:%d" % port,
            "ENC_E2E_EMULATOR": "http://127.0.0.1:9099",
            "ENC_AUTH_KID": kid_for(public),
            "ENC_AUTH_PUBLIC_KEY_B64": base64.b64encode(der).decode("ascii"),
        }
        base_defines = {k: v for k, v in defines.items() if k.startswith("ENC_E2E_")}
        env = dict(os.environ, DART_SUPPRESS_ANALYTICS="true")
        test_file = "test/enc_auth/enc_auth_local_e2e_test.dart"
        valid = dict(defines, ENC_AUTH_MODE="required")                                   # the production path: EncAuthRuntime builds the sealer itself
        # Fail-closed variants: a required build whose key configuration is missing, malformed or does not match must have no sealer at all.
        invalid = {
            "mismatched key id": dict(base_defines, ENC_AUTH_MODE="required", ENC_AUTH_KID="kWRONGWRONGxx", ENC_AUTH_PUBLIC_KEY_B64=defines["ENC_AUTH_PUBLIC_KEY_B64"], ENC_E2E_EXPECT_FAIL_CLOSED="true"),
            "malformed public key": dict(base_defines, ENC_AUTH_MODE="required", ENC_AUTH_KID=defines["ENC_AUTH_KID"], ENC_AUTH_PUBLIC_KEY_B64="AAAA", ENC_E2E_EXPECT_FAIL_CLOSED="true"),
            "missing key": dict(base_defines, ENC_AUTH_MODE="required", ENC_E2E_EXPECT_FAIL_CLOSED="true"),
        }
        print("lab is up (emulator + backend); running the Flutter end-to-end test ...", flush=True)
        worst = 0
        for name, chosen in [("valid key (ENC_AUTH_MODE=required)", valid)] + list(invalid.items()):
            command = [str(FLUTTER), "test", test_file, "--reporter", "expanded"] + ["--dart-define=%s=%s" % item for item in chosen.items()]
            print("---- run: %s" % name, flush=True)
            worst = max(worst, subprocess.run(command, cwd=str(APP), env=env).returncode)
        return worst
    finally:
        lab.down()
        print("lab removed", flush=True)


if __name__ == "__main__":
    sys.exit(main())
