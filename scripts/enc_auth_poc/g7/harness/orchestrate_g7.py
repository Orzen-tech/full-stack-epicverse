"""Lab for the G7 tests: the G6b lab (unchanged) with its backend tree built by the G7 build script, and two extra backend variants:
  g7real     real-Firebase adapter selected, wired to a FAKE Firebase world inside the container (server_g7.py), no network;
  g7misconf  real-Firebase adapter selected but deliberately inconsistent configuration (must fail closed).
Import this module BEFORE the G6b test support so that `Lab` there is the subclass. Nothing here talks to Firebase or Google.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
G7 = HERE.parent
POC = G7.parent
sys.path.insert(0, str(POC))
sys.path.insert(0, str(POC / "g2"))
sys.path.insert(0, str(POC / "g6b" / "harness"))

import orchestrate as o  # noqa: E402  (the G6b lab)

PROJECT = "epicverse-fake-project"
SIGNER = "g7-signer@%s.iam.gserviceaccount.com" % PROJECT
FAKE_API_KEY = "AIza" + "C" * 35
_REAL = {"ENC_AUTH_IDENTITY_BACKEND": "firebase", "ENC_AUTH_FIREBASE_PROJECT_ID": PROJECT, "FIREBASE_PROJECT_ID": PROJECT,
         "ENC_AUTH_FIREBASE_WEB_API_KEY": FAKE_API_KEY, "ENC_AUTH_SIGNER_SERVICE_ACCOUNT": SIGNER, "FIREBASE_AUTH_EMULATOR_HOST": ""}
o.BACKENDS["g7real"] = (8140, 18140, "g7_real", {**o.HIGH, **_REAL})
o.BACKENDS["g7misconf"] = (8141, 18141, "g7_misconf", {**o.HIGH, **_REAL, "FIREBASE_PROJECT_ID": "a-different-project"})
G7_VARIANTS = ("g7real", "g7misconf")


class Lab7(o.Lab):
    def build_tree(self):
        self.tree.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([o.GIT_BASH, o.posix(G7 / "build_tree_enc_auth_g7.sh"), o.posix(self.tree)], capture_output=True, text=True)
        if res.returncode != 0:
            raise o.LabError("hash-gated G7 build refused or failed: " + res.stderr.strip()[-300:])

    def _start_backend(self, name, port, db, overrides):
        if name not in G7_VARIANTS:
            return super()._start_backend(name, port, db, overrides)
        original = o.docker

        def rewriting(*args, **kwargs):
            if "/h/server_g6b.py" in args:                       # run the G7 wrapper instead, with this folder mounted next to the G6b one
                args = list(args)
                args[args.index("/h/server_g6b.py")] = "/g7/server_g7.py"
                args[args.index(o.IMAGE):args.index(o.IMAGE)] = ["-v", "%s:/g7:ro" % HERE]
            return original(*args, **kwargs)
        o.docker = rewriting
        try:
            return super()._start_backend(name, port, db, overrides)
        finally:
            o.docker = original


o.Lab = Lab7
