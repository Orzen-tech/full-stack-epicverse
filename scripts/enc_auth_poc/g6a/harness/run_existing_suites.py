"""Runs the EXISTING, unmodified production-equivalent MFA / enforcement / privacy / email-proof harness (scripts/mfa_additive/run_local_tests.sh)
and, separately, the same email-verification-proof suite against the GATEWAY tree (gateway disabled by default, so nothing may change).

  python -B -I run_existing_suites.py original [extra run_local_tests.sh options]   # the original script, byte for byte
  python -B -I run_existing_suites.py gateway                                       # a derived copy built in a temporary directory

The original script and its harness are only read. The derived copy replaces ONE line (the tree built for backend "X", which the
--only-proof mode tests) with the gateway-patched tree; it lives in a temporary directory and is deleted afterwards.
That harness uses its own isolated Docker network (mfa-*) with no internet and removes everything it created on exit.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
G6A = HERE.parent
MFA = G6A.parent.parent / "mfa_additive"
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"


def posix(path):
    p = Path(path).resolve()
    return "/" + p.drive[0].lower() + p.as_posix()[2:]


def main(argv):
    mode, extra = argv[0], argv[1:]
    env = dict(os.environ, DART_SUPPRESS_ANALYTICS="true")
    if mode == "original":
        return subprocess.run([GIT_BASH, posix(MFA / "run_local_tests.sh"), *extra], env=env).returncode
    if mode != "gateway":
        raise SystemExit(__doc__)
    text = (MFA / "run_local_tests.sh").read_text(encoding="utf-8")
    needle = 'bash "$HERE/build_tree_proof.sh" "$W/src_X"'
    if text.count(needle) != 1:
        raise SystemExit("run_local_tests.sh changed shape; refusing to derive a copy")
    derived = text.replace(needle, 'bash "%s" "$W/src_X"' % posix(G6A / "build_tree_enc_auth.sh"))
    derived = re.sub(r'^HERE=.*$', 'HERE="%s"' % posix(MFA), derived, count=1, flags=re.M)
    tmp = Path(tempfile.mkdtemp(prefix="g6a_existing_"))
    try:
        script = tmp / "run_on_gateway_tree.sh"
        script.write_text(derived, encoding="utf-8", newline="\n")
        return subprocess.run([GIT_BASH, posix(script), "--only-proof", *extra], env=env).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--help"]))
