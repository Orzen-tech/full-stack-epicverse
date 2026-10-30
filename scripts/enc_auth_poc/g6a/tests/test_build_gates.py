"""The hash-gated build: it must refuse any tampered link of the chain, and the finished tree must differ from the production-equivalent
tree in exactly the intended files. Offline; builds only into temporary directories outside the repository."""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import BUILD, G6A, POC, REPO, _posix, bash  # noqa: E402

MFA = POC.parent / "mfa_additive"
EXPECTED_SHA = {
    "mfa_additive.patch": "edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2",
    "mfa_enforcement.patch": "7f3030064bb3dbae84646e64f337f84eef02f94404c414a4ac514db3ef160104",
    "mfa_logging_privacy.patch": "98fc386d154ce7b21b688eb4aab7897e593c9fb86597442891cc996130f821a4",
    "email_verification_proof.patch": "39d5da68992e7520480fdfad3155dd853ac0b1ea8d3eb24ccf056c63350ae49d",
    "build_tree_proof.sh": "59e1ee36866fc05c74c63ad17d9e4e933040a5c0678de2e7984775a004a65d3f",
}
LIMITER_SHA = "ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54"
EXPECTED_CHANGED = {
    "app/api/enc_gateway.py", "app/api/routes.py", "app/core/config.py", "app/services/enc_auth_envelope.py",
    "app/services/enc_auth_identity.py", "app/services/enc_auth_policy.py", "app/services/enc_auth_store.py", "app/services/user_db.py",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r", b"")).hexdigest()


def run_build(script, out):
    return subprocess.run([bash(), _posix(script), _posix(out)], capture_output=True, text=True)


class Chain(unittest.TestCase):
    def test_the_production_patches_and_build_script_are_byte_identical_to_the_reviewed_ones(self):
        for name, expected in EXPECTED_SHA.items():
            self.assertEqual(sha(MFA / name), expected, name)

    def test_the_official_build_succeeds_and_the_rate_limiter_is_unchanged(self):
        out = Path(tempfile.mkdtemp(prefix="g6a_gate_"))
        try:
            res = run_build(BUILD, out)
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            self.assertEqual(sha(out / "backend/app/services/otp_rate_limiter.py"), LIMITER_SHA)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_exactly_the_intended_files_differ_from_the_production_equivalent_tree(self):
        base, full = Path(tempfile.mkdtemp(prefix="g6a_base_")), Path(tempfile.mkdtemp(prefix="g6a_full_"))
        try:
            self.assertEqual(run_build(MFA / "build_tree_proof.sh", base).returncode, 0)
            self.assertEqual(run_build(BUILD, full).returncode, 0)
            changed = set()
            for p in (full / "backend").rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    rel = p.relative_to(full / "backend").as_posix()
                    q = base / "backend" / rel
                    if not q.exists() or q.read_bytes() != p.read_bytes():
                        changed.add(rel)
            self.assertEqual(changed, EXPECTED_CHANGED)
            missing = [p for p in (base / "backend").rglob("*") if p.is_file() and not (full / "backend" / p.relative_to(base / "backend")).exists()]
            self.assertEqual(missing, [])                                  # nothing deleted
        finally:
            shutil.rmtree(base, ignore_errors=True)
            shutil.rmtree(full, ignore_errors=True)

    def test_the_patch_is_additive_in_shape(self):
        text = (G6A / "enc_auth_gateway.patch").read_text(encoding="utf-8")
        self.assertNotIn("+++ /dev/null", text)
        self.assertEqual(text.count("--- /dev/null"), 5)                  # five new files
        headers = [l[6:] for l in text.splitlines() if l.startswith("+++ b/")]
        self.assertEqual({h.replace("backend/", "", 1) for h in headers}, EXPECTED_CHANGED)
        self.assertNotIn("otp_rate_limiter", "".join(headers))          # the OTP limiter is not part of the patch

    def test_a_non_empty_output_directory_is_refused(self):
        out = Path(tempfile.mkdtemp(prefix="g6a_nonempty_"))
        try:
            (out / "x").write_text("x")
            res = run_build(BUILD, out)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("must be empty", res.stderr)
            self.assertEqual([p.name for p in out.iterdir()], ["x"])
        finally:
            shutil.rmtree(out, ignore_errors=True)


class TamperedChain(unittest.TestCase):
    """Copies the build chain into a temporary layout, damages ONE link, and expects a refusal before anything is built."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="g6a_chain_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def layout(self, name):
        base = self.root / name
        mfa, g6a = base / "scripts/mfa_additive", base / "scripts/enc_auth_poc/g6a"
        mfa.mkdir(parents=True)
        g6a.mkdir(parents=True)
        for item in EXPECTED_SHA:
            shutil.copy2(MFA / item, mfa / item)
        shutil.copy2(BUILD, g6a / BUILD.name)
        shutil.copy2(G6A / "enc_auth_gateway.patch", g6a / "enc_auth_gateway.patch")
        return mfa, g6a

    def attempt(self, name, victim, where):
        mfa, g6a = self.layout(name)
        target = (mfa if where == "mfa" else g6a) / victim
        data = target.read_bytes()
        target.write_bytes(data + b"\n# tampered\n")
        out = self.root / (name + "_out")
        res = run_build(g6a / BUILD.name, out)
        self.assertNotEqual(res.returncode, 0, victim)
        self.assertIn("hash mismatch", res.stderr, victim)
        self.assertIn(victim, res.stderr)
        self.assertFalse(out.exists() and any(out.iterdir()), "nothing may be built")

    def test_every_link_of_the_chain_is_gated(self):
        for i, victim in enumerate(EXPECTED_SHA):
            self.attempt("t%d" % i, victim, "mfa")
        self.attempt("t_gateway", "enc_auth_gateway.patch", "g6a")

    def test_a_missing_link_is_refused(self):
        mfa, g6a = self.layout("missing")
        (mfa / "email_verification_proof.patch").unlink()
        res = run_build(g6a / BUILD.name, self.root / "missing_out")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not found", res.stderr)


if __name__ == "__main__":
    unittest.main()
