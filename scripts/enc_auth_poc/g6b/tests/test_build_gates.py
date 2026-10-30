"""The G6b hash-gated build: it must refuse any tampered, changed or missing link of the chain (production patches, build scripts, the
G6a build script and patch, the G6b patch), a wrong baseline, a non-empty destination, an unexpected patch conflict and a changed OTP
limiter; and the finished tree must differ from the G6a tree in exactly the intended files. Offline; builds only into temporary
directories. The tamper layouts live in a temporary folder INSIDE g6b/ (the build needs the real repository for `git archive`) and
are removed afterwards."""
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import BUILD, G6A, G6B, POC, REPO, _posix, bash  # noqa: E402

MFA = POC.parent / "mfa_additive"
EXPECTED_SHA = {
    "mfa_additive.patch": "edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2",
    "mfa_enforcement.patch": "7f3030064bb3dbae84646e64f337f84eef02f94404c414a4ac514db3ef160104",
    "mfa_logging_privacy.patch": "98fc386d154ce7b21b688eb4aab7897e593c9fb86597442891cc996130f821a4",
    "email_verification_proof.patch": "39d5da68992e7520480fdfad3155dd853ac0b1ea8d3eb24ccf056c63350ae49d",
    "build_tree_proof.sh": "59e1ee36866fc05c74c63ad17d9e4e933040a5c0678de2e7984775a004a65d3f",
}
G6A_SHA = {"enc_auth_gateway.patch": "12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98",
           "build_tree_enc_auth.sh": "33e65232016756d6c13931391d58bf3037670ccab3c7e373d149425e38d03fbd"}
LIMITER_SHA = "ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54"
# files that differ between the G6a tree and the G6b tree
EXPECTED_CHANGED_FROM_G6A = {
    "app/api/enc_gateway.py", "app/api/routes.py", "app/core/config.py", "app/services/enc_auth_identity.py", "app/services/enc_auth_store.py",
    "app/services/enc_auth_diag.py", "app/services/enc_auth_retry.py", "app/services/enc_auth_limits_eval.py",
}
SCRATCH = G6B / "_gate_tmp"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r", b"")).hexdigest()


def run_build(script, out):
    return subprocess.run([bash(), _posix(script), _posix(out)], capture_output=True, text=True)


def changed_files(base, full):
    changed = set()
    for p in (full / "backend").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(full / "backend").as_posix()
            q = base / "backend" / rel
            if not q.exists() or q.read_bytes() != p.read_bytes():
                changed.add(rel)
    return changed


class Chain(unittest.TestCase):
    def test_every_earlier_link_is_byte_identical_to_the_reviewed_one(self):
        for name, expected in EXPECTED_SHA.items():
            self.assertEqual(sha(MFA / name), expected, name)
        for name, expected in G6A_SHA.items():
            self.assertEqual(sha(G6A / name), expected, "G6a " + name)

    def test_the_official_build_succeeds_and_the_rate_limiter_is_unchanged(self):
        out = Path(tempfile.mkdtemp(prefix="g6b_gate_"))
        try:
            res = run_build(BUILD, out)
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            self.assertIn("enc_auth_gateway_g6b", res.stdout)
            self.assertEqual(sha(out / "backend/app/services/otp_rate_limiter.py"), LIMITER_SHA)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_exactly_the_intended_files_differ_from_the_g6a_tree(self):
        base, full = Path(tempfile.mkdtemp(prefix="g6b_base_")), Path(tempfile.mkdtemp(prefix="g6b_full_"))
        try:
            self.assertEqual(run_build(G6A / "build_tree_enc_auth.sh", base).returncode, 0)
            self.assertEqual(run_build(BUILD, full).returncode, 0)
            self.assertEqual(changed_files(base, full), EXPECTED_CHANGED_FROM_G6A)
            gone = [p for p in (base / "backend").rglob("*") if p.is_file() and not (full / "backend" / p.relative_to(base / "backend")).exists()]
            self.assertEqual(gone, [])                                       # nothing deleted
        finally:
            shutil.rmtree(base, ignore_errors=True)
            shutil.rmtree(full, ignore_errors=True)

    def test_the_g6b_patch_is_in_place_edits_plus_three_new_pure_modules(self):
        text = (G6B / "enc_auth_gateway_g6b.patch").read_text(encoding="utf-8")
        self.assertNotIn("+++ /dev/null", text)
        self.assertEqual(text.count("--- /dev/null"), 3)
        headers = {l[6:].replace("backend/", "", 1) for l in text.splitlines() if l.startswith("+++ b/")}
        self.assertEqual(headers, EXPECTED_CHANGED_FROM_G6A)
        self.assertFalse([h for h in headers if "otp_rate_limiter" in h])    # the OTP limiter is not touched (it is only imported from)

    def test_a_non_empty_output_directory_is_refused(self):
        out = Path(tempfile.mkdtemp(prefix="g6b_nonempty_"))
        try:
            (out / "x").write_text("x")
            res = run_build(BUILD, out)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("must be empty", res.stderr)
            self.assertEqual([p.name for p in out.iterdir()], ["x"])
        finally:
            shutil.rmtree(out, ignore_errors=True)


class TamperedChain(unittest.TestCase):
    """Copies the whole chain into a layout inside g6b/_gate_tmp, damages ONE thing, and expects a refusal."""

    @classmethod
    def setUpClass(cls):
        if SCRATCH.exists():
            raise RuntimeError("%s already exists; refusing to touch it" % SCRATCH)
        SCRATCH.mkdir()
        cls.out_root = Path(tempfile.mkdtemp(prefix="g6b_chain_out_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        shutil.rmtree(cls.out_root, ignore_errors=True)

    def layout(self, name):
        base = SCRATCH / name
        mfa, g6a, g6b = base / "scripts/mfa_additive", base / "scripts/enc_auth_poc/g6a", base / "scripts/enc_auth_poc/g6b"
        for d in (mfa, g6a, g6b):
            d.mkdir(parents=True)
        for item in EXPECTED_SHA:
            shutil.copy2(MFA / item, mfa / item)
        for item in G6A_SHA:
            shutil.copy2(G6A / item, g6a / item)
        shutil.copy2(BUILD, g6b / BUILD.name)
        shutil.copy2(G6B / "enc_auth_gateway_g6b.patch", g6b / "enc_auth_gateway_g6b.patch")
        return {"mfa": mfa, "g6a": g6a, "g6b": g6b}

    def build(self, dirs, name):
        out = self.out_root / name
        return run_build(dirs["g6b"] / BUILD.name, out), out

    def assert_refused_before_building(self, res, out, needle):
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(needle, res.stderr)
        self.assertFalse(out.exists() and any(out.iterdir()), "nothing may be built")

    def test_every_link_of_the_chain_is_hash_gated(self):
        cases = [("mfa", n) for n in EXPECTED_SHA] + [("g6a", n) for n in G6A_SHA] + [("g6b", "enc_auth_gateway_g6b.patch")]
        for i, (where, victim) in enumerate(cases):
            dirs = self.layout("t%d" % i)
            target = dirs[where] / victim
            target.write_bytes(target.read_bytes() + b"\n# tampered\n")
            res, out = self.build(dirs, "t%d" % i)
            self.assert_refused_before_building(res, out, "hash mismatch")
            self.assertIn(victim, res.stderr)

    def test_a_missing_link_is_refused(self):
        cases = [("mfa", "email_verification_proof.patch"), ("g6a", "enc_auth_gateway.patch"), ("g6a", "build_tree_enc_auth.sh"),
                 ("g6b", "enc_auth_gateway_g6b.patch")]
        for i, (where, victim) in enumerate(cases):
            dirs = self.layout("m%d" % i)
            (dirs[where] / victim).unlink()
            res, out = self.build(dirs, "m%d" % i)
            self.assert_refused_before_building(res, out, "not found")

    def test_a_wrong_baseline_commit_is_refused(self):
        dirs = self.layout("base")
        script = dirs["g6b"] / BUILD.name
        text = script.read_text(encoding="utf-8")
        script.write_text(text.replace("BASE_COMMIT=d7337763e03ca6c4c5ff234708fbb5162937d194", "BASE_COMMIT=" + "0" * 40), encoding="utf-8", newline="\n")
        res, out = self.build(dirs, "base")
        self.assert_refused_before_building(res, out, "base commit")

    def test_a_g6b_patch_that_does_not_apply_is_refused(self):
        dirs = self.layout("conflict")
        patch = dirs["g6b"] / "enc_auth_gateway_g6b.patch"
        text = patch.read_text(encoding="utf-8")
        mutated = re.sub(r"^( .*)$", lambda m: m.group(1) + "X", text, count=3, flags=re.M)      # break three context lines
        self.assertNotEqual(mutated, text)
        patch.write_text(mutated, encoding="utf-8", newline="\n")
        script = dirs["g6b"] / BUILD.name
        pinned = re.sub(r"^G6B_PATCH_SHA256=.*$", "G6B_PATCH_SHA256=" + sha(patch), script.read_text(encoding="utf-8"), flags=re.M)
        script.write_text(pinned, encoding="utf-8", newline="\n")                                  # the pin matches: only the CONFLICT remains
        res, _ = self.build(dirs, "conflict")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("hash mismatch", res.stderr)
        self.assertTrue("does not apply cleanly" in res.stderr or "FAILED" in res.stderr or "failed" in res.stderr.lower(), res.stderr[-300:])

    def test_a_patch_that_changes_the_otp_limiter_is_refused(self):
        dirs = self.layout("limiter")
        patch = dirs["g6b"] / "enc_auth_gateway_g6b.patch"
        extra = ("--- a/backend/app/services/otp_rate_limiter.py\n+++ b/backend/app/services/otp_rate_limiter.py\n")
        base_text = subprocess.run(["git", "-C", str(REPO), "show", "d733776:backend/app/services/otp_rate_limiter.py"], capture_output=True).stdout
        lines = base_text.decode("utf-8").replace("\r", "").splitlines(keepends=True)
        hunk = "@@ -1,%d +1,%d @@\n" % (min(3, len(lines)), min(3, len(lines)) + 1) + "".join(" " + l for l in lines[:3]) + "+# tampered\n"
        patch.write_text(patch.read_text(encoding="utf-8") + extra + hunk, encoding="utf-8", newline="\n")
        script = dirs["g6b"] / BUILD.name
        script.write_text(re.sub(r"^G6B_PATCH_SHA256=.*$", "G6B_PATCH_SHA256=" + sha(patch), script.read_text(encoding="utf-8"), flags=re.M),
                          encoding="utf-8", newline="\n")
        res, _ = self.build(dirs, "limiter")
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue("limiter" in res.stderr or "does not apply" in res.stderr, res.stderr[-300:])


if __name__ == "__main__":
    unittest.main()
