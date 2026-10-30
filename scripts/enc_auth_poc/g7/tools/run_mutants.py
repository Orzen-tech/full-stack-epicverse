"""Test-of-the-tests for the G7 adapter: breaks a COPY of the built tree on purpose (one defect at a time) and checks that the offline unit tests
FAIL. Nothing in the repository is modified. A missed mutant means the tests are too weak.

    python -B -I run_mutants.py [name ...]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
G7 = HERE.parent
TESTS = G7 / "tests"
sys.path.insert(0, str(TESTS))
from _tree import BUILD, bash, posix  # noqa: E402

ADAPTER = "app/services/enc_auth_identity_firebase.py"

CREATE_OLD = 'uid = await base._timed("create_user", lambda: self._admin.create_user(email, password))'
CREATE_NEW = '''uid = None
            for _n in range(3):
                try:
                    uid = await base._timed("create_user", lambda: self._admin.create_user(email, password))
                    break
                except PortError:
                    if _n == 2:
                        raise'''
UPDATE_OLD = 'await base._timed("update_password", lambda: self._admin.set_password(uid, new_password))'
UPDATE_NEW = '''for _n in range(3):
                try:
                    await base._timed("update_password", lambda: self._admin.set_password(uid, new_password))
                    break
                except PortError:
                    if _n == 2:
                        raise'''
SIGN_OLD = 'signature = await base._timed("mint", lambda: self._signer.sign_blob(data))'
SIGN_NEW = '''signature = None
            for _n in range(3):
                try:
                    signature = await base._timed("mint", lambda: self._signer.sign_blob(data))
                    break
                except PortError:
                    if _n == 2:
                        raise'''
INSTALL_OLD = '''    else:
        namespace.update({"ready": lambda: False,'''
INSTALL_NEW = '''    elif False:
        namespace.update({"ready": lambda: False,'''
PRINT_OLD = '''        status = response.status_code
        if status == 200:'''
PRINT_NEW = '''        print(self._key)
        status = response.status_code
        if status == 200:'''

MUTANTS = {
    "create-user-retried": (CREATE_OLD, CREATE_NEW, "account creation is never retried"),
    "password-update-retried": (UPDATE_OLD, UPDATE_NEW, "password update is never retried"),
    "sign-failure-retried": (SIGN_OLD, SIGN_NEW, "signing is never retried"),
    "update-outage-reported-as-weak": ('raise base.IdentityUnavailable("update_password", failure.cause) from None', "raise base.IdentityRejected() from None",
                                       "an outage must not look like a definitive refusal"),
    "token-check-skips-revocation": ("check_revoked=True", "check_revoked=False", "ID tokens are checked for revocation"),
    "unknown-backend-falls-back-to-emulator": (INSTALL_OLD, INSTALL_NEW, "a typo in the backend name fails closed"),
    "emulator-host-ignored": ('if (os.environ.get("FIREBASE_AUTH_EMULATOR_HOST") or "").strip():', "if False:", "the Admin SDK must never silently talk to an emulator"),
    "key-file-allowed": ('if settings.GOOGLE_APPLICATION_CREDENTIALS or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):', "if False:", "keyless only"),
    "api-key-in-print": (PRINT_OLD, PRINT_NEW, "nothing is printed"),
    "redirects-followed": ("follow_redirects=False, verify=True", "follow_redirects=True, verify=True", "redirects are never followed"),
    "tls-verification-off": ("follow_redirects=False, verify=True", "follow_redirects=False, verify=False", "TLS verification stays on"),
    "credential-code-retried": ('raise base.IdentityUnavailable("verify_password", failure.cause) from None',
                                'raise base.IdentityUnavailable("verify_password", "http_503") from None', "retry only for transient causes"),
}


def run(name):
    old, new, what = MUTANTS[name]
    tmp = Path(tempfile.mkdtemp(prefix="g7_mutant_"))
    try:
        res = subprocess.run([bash(), posix(BUILD), posix(tmp)], capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit("build failed: " + res.stderr[-300:])
        target = tmp / "backend" / ADAPTER
        text = target.read_bytes().decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit("pattern for %s not found exactly once (%d)" % (name, text.count(old)))
        target.write_bytes(text.replace(old, new).encode("utf-8"))
        env = dict(os.environ, G7_TREE=str(tmp))
        res = subprocess.run([sys.executable, "-B", "-I", "-m", "unittest", "discover", "-s", str(TESTS), "-p", "test_adapter_unit.py"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
        output = res.stdout + res.stderr
        failed = [line for line in output.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
        return res.returncode != 0, failed[:2], what
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(names):
    names = names or list(MUTANTS)
    caught = 0
    for name in names:
        ok, failed, what = run(name)
        caught += ok
        print("%-42s %-7s breaks: %s\n      %s" % (name, "CAUGHT" if ok else "MISSED", what, " | ".join(f[:110] for f in failed)), flush=True)
    print("\nmutants caught: %d / %d" % (caught, len(names)))
    return 0 if caught == len(names) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
