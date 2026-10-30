"""G7 build gates and static guarantees: the hash-gated build refuses tampering, the patch changes exactly what it should (the emulator adapter
only gains an appended selection block), the limiter is untouched, and the adapter source contains no key, ADC, gcloud, logging or stray
endpoint. Offline. The tamper layouts live in g7/_gate_tmp (the build needs the real repository for `git archive`) and are removed afterwards."""
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import BUILD, G7, POC, REPO, bash, posix, tree_dir  # noqa: E402
import approved_integration  # noqa: E402  (the single list of approved integration changes; _tree puts the PoC folder on sys.path)

G6A, G6B, MFA = POC / "g6a", POC / "g6b", POC.parent / "mfa_additive"
PATCH = G7 / "enc_auth_gateway_g7_firebase.patch"
LIMITER_SHA = "ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54"
PINNED = {
    G6A / "enc_auth_gateway.patch": "12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98",
    G6A / "build_tree_enc_auth.sh": "33e65232016756d6c13931391d58bf3037670ccab3c7e373d149425e38d03fbd",
    G6B / "enc_auth_gateway_g6b.patch": "69abe816d9d1c354904e53f6ce5ed1ad2704dccaaeab19ea5a6fcd1bc531f4dc",
    G6B / "build_tree_enc_auth_g6b.sh": "9e486515c9735bb70ede1cafc2ff41dee021931c725b9b0aa47d28e0e0aeee64",
}
EXPECTED_CHANGED = {"app/api/enc_gateway.py", "app/core/config.py", "app/services/enc_auth_diag.py", "app/services/enc_auth_identity.py",
                    "app/services/enc_auth_identity_firebase.py", "app/services/enc_auth_store.py", "scripts/requirements.txt"}      # the last one: the cryptography pin, appended by the build script
SCRATCH = G7 / "_gate_tmp"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r", b"")).hexdigest()


def run_build(script, out):
    return subprocess.run([bash(), posix(script), posix(out)], capture_output=True, text=True)


def adapter_source():
    return (tree_dir() / "backend" / "app/services/enc_auth_identity_firebase.py").read_text(encoding="utf-8")


class Chain(unittest.TestCase):
    def test_earlier_links_are_byte_identical(self):
        for path, expected in PINNED.items():
            self.assertEqual(sha(path), expected, path.name)

    def test_the_official_build_succeeds_the_limiter_is_unchanged_and_exactly_seven_files_differ_from_the_g6b_tree(self):
        base, full = Path(tempfile.mkdtemp(prefix="g7_base_")), Path(tempfile.mkdtemp(prefix="g7_full_"))
        try:
            self.assertEqual(run_build(G6B / "build_tree_enc_auth_g6b.sh", base).returncode, 0)
            res = run_build(BUILD, full)
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            self.assertEqual(sha(full / "backend/app/services/otp_rate_limiter.py"), LIMITER_SHA)
            changed = set()
            for p in (full / "backend").rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    rel = p.relative_to(full / "backend").as_posix()
                    q = base / "backend" / rel
                    if not q.exists() or q.read_bytes() != p.read_bytes():
                        changed.add(rel)
            self.assertEqual(changed, EXPECTED_CHANGED)
            self.assertEqual([p for p in (base / "backend").rglob("*") if p.is_file() and not (full / "backend" / p.relative_to(base / "backend")).exists()], [])
            old = (base / "backend/app/services/enc_auth_identity.py").read_bytes()
            new = (full / "backend/app/services/enc_auth_identity.py").read_bytes()
            self.assertTrue(new.startswith(old), "the emulator adapter must be unchanged: the selection block may only be APPENDED")
            self.assertIn(b"ENC_AUTH_IDENTITY_BACKEND", new[len(old):])
            # the ONLY dependency change: `cryptography==50.0.2` appended to requirements.txt, every earlier byte untouched, nothing else added
            old_req, new_req = (base / "backend/scripts/requirements.txt").read_bytes(), (full / "backend/scripts/requirements.txt").read_bytes()
            self.assertTrue(new_req.startswith(old_req))
            self.assertEqual(new_req[len(old_req):].replace(b"\r", b"").strip(), b"cryptography==50.0.2")
            self.assertEqual([l for l in new_req.replace(b"\r", b"").splitlines() if b"cryptography" in l.lower()], [b"cryptography==50.0.2"])
            self.assertEqual(len(new_req.splitlines()), len(old_req.splitlines()) + 1)
            self.assertTrue(new_req.endswith(b"\n"))
        finally:
            shutil.rmtree(base, ignore_errors=True)
            shutil.rmtree(full, ignore_errors=True)

    def test_the_patch_removes_only_two_existing_lines(self):
        text = PATCH.read_text(encoding="utf-8")
        removed = [l for l in text.splitlines() if l.startswith("-") and not l.startswith("---")]
        self.assertEqual(len(removed), 1, removed)                       # the single gateway line that now asks the adapter for its own cause
        self.assertIn('"not_local"', removed[0])
        self.assertNotIn("otp_rate_limiter", "".join(l for l in text.splitlines() if l.startswith("+++ ")))

    def test_a_non_empty_destination_is_refused(self):
        out = Path(tempfile.mkdtemp(prefix="g7_nonempty_"))
        try:
            (out / "x").write_text("x")
            res = run_build(BUILD, out)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("must be empty", res.stderr)
        finally:
            shutil.rmtree(out, ignore_errors=True)


class Tamper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRATCH.exists():
            raise RuntimeError("%s already exists; refusing to touch it" % SCRATCH)
        SCRATCH.mkdir()
        cls.out_root = Path(tempfile.mkdtemp(prefix="g7_chain_out_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        shutil.rmtree(cls.out_root, ignore_errors=True)

    def layout(self, name):
        base = SCRATCH / name
        mfa, g6a, g6b, g7 = base / "scripts/mfa_additive", base / "scripts/enc_auth_poc/g6a", base / "scripts/enc_auth_poc/g6b", base / "scripts/enc_auth_poc/g7"
        for d in (mfa, g6a, g6b, g7):
            d.mkdir(parents=True)
        for item in ("mfa_additive.patch", "mfa_enforcement.patch", "mfa_logging_privacy.patch", "email_verification_proof.patch", "build_tree_proof.sh"):
            shutil.copy2(MFA / item, mfa / item)
        for item in ("enc_auth_gateway.patch", "build_tree_enc_auth.sh"):
            shutil.copy2(G6A / item, g6a / item)
        for item in ("enc_auth_gateway_g6b.patch", "build_tree_enc_auth_g6b.sh"):
            shutil.copy2(G6B / item, g6b / item)
        shutil.copy2(BUILD, g7 / BUILD.name)
        shutil.copy2(PATCH, g7 / PATCH.name)
        return {"g6b": g6b, "g7": g7, "g6a": g6a, "mfa": mfa}

    def build(self, dirs, name):
        out = self.out_root / name
        return run_build(dirs["g7"] / BUILD.name, out), out

    def test_every_link_is_gated_before_anything_is_built(self):
        cases = [("g7", PATCH.name), ("g6b", "enc_auth_gateway_g6b.patch"), ("g6b", "build_tree_enc_auth_g6b.sh")]
        for i, (where, victim) in enumerate(cases):
            dirs = self.layout("t%d" % i)
            target = dirs[where] / victim
            target.write_bytes(target.read_bytes() + b"\n# tampered\n")
            res, out = self.build(dirs, "t%d" % i)
            self.assertNotEqual(res.returncode, 0, victim)
            self.assertIn("hash mismatch", res.stderr)
            self.assertFalse(out.exists() and any(out.iterdir()))

    def test_a_missing_link_is_refused(self):
        for i, (where, victim) in enumerate([("g7", PATCH.name), ("g6b", "enc_auth_gateway_g6b.patch"), ("g6b", "build_tree_enc_auth_g6b.sh")]):
            dirs = self.layout("m%d" % i)
            (dirs[where] / victim).unlink()
            res, out = self.build(dirs, "m%d" % i)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("not found", res.stderr)

    def test_a_patch_that_does_not_apply_is_refused(self):
        dirs = self.layout("conflict")
        patch = dirs["g7"] / PATCH.name
        mutated = re.sub(r"^( .*)$", lambda m: m.group(1) + "X", patch.read_text(encoding="utf-8"), count=3, flags=re.M)
        patch.write_text(mutated, encoding="utf-8", newline="\n")
        script = dirs["g7"] / BUILD.name
        script.write_text(re.sub(r"^G7_PATCH_SHA256=.*$", "G7_PATCH_SHA256=" + sha(patch), script.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8", newline="\n")
        res, _ = self.build(dirs, "conflict")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("hash mismatch", res.stderr)
        self.assertTrue("does not apply" in res.stderr or "FAILED" in res.stderr, res.stderr[-200:])


class AdapterSource(unittest.TestCase):
    def test_no_key_adc_gcloud_logging_or_printing(self):
        source = adapter_source()
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
        for word in ("gcloud", "load_pem_private_key", "service_account.Credentials", "from_service_account", "logging.info(", "logging.debug(", "logging.warning(", "logging.error(", "logger.", "print(", "subprocess", "getenv("):
            self.assertNotIn(word, code, word)
        self.assertEqual(len(re.findall(r"logging\.getLogger\(_name\)\.setLevel\(logging\.WARNING\)", source)), 1)

    def test_only_the_two_google_endpoints_and_the_token_audience_are_named(self):
        urls = set(re.findall(r"https?://[^\s\"'%)]+", adapter_source()))
        self.assertEqual({u.rstrip(".,") for u in urls}, {"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
                                                           "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/",
                                                           "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
                                                           "https://www.googleapis.com/auth/cloud-platform"})
        self.assertNotIn("http://", adapter_source())

    def test_the_adapter_never_names_the_emulator_or_a_default_project(self):
        source = adapter_source()
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
        self.assertEqual(code.count("FIREBASE_AUTH_EMULATOR_HOST"), 1)            # only in the refusal
        self.assertNotIn("localhost", code)
        self.assertNotIn("127.0.0.1", code)

    def test_the_gateway_schema_work_is_skipped_entirely_while_the_gateway_is_disabled(self):
        store = (tree_dir() / "backend" / "app/services/enc_auth_store.py").read_text(encoding="utf-8")
        body = store.split("async def ensure_schema(conn):", 1)[1].split("def _nonce_hash", 1)[0]
        first = [l.strip() for l in body.split('"""', 2)[2].splitlines() if l.strip()][:2]
        self.assertEqual(first[0], "if not settings.ENC_AUTH_ENABLED:")
        self.assertTrue(first[1].startswith("return"))
        self.assertEqual(body.count("CREATE TABLE IF NOT EXISTS enc_auth_"), 3)                              # the enabled path is unchanged: three tables
        user_db = (tree_dir() / "backend" / "app/services/user_db.py").read_text(encoding="utf-8")
        self.assertEqual(user_db.count("ensure_enc_auth_schema"), 2)                 # the import and the single guarded call
        self.assertNotIn("CREATE TABLE IF NOT EXISTS enc_auth", user_db)

    def test_retries_wrap_only_the_three_idempotent_reads(self):
        self.assertEqual(sorted(re.findall(r'base\._retry\("(\w+)"', adapter_source())), ["get_account", "verify_password", "verify_token"])

    def test_defaults_are_safe(self):
        config = (tree_dir() / "backend/app/core/config.py").read_text(encoding="utf-8")
        for pattern in (r'ENC_AUTH_IDENTITY_BACKEND: str = "emulator"', r'ENC_AUTH_FIREBASE_PROJECT_ID: str = ""', r'ENC_AUTH_FIREBASE_WEB_API_KEY: str = ""',
                        r'ENC_AUTH_SIGNER_SERVICE_ACCOUNT: str = ""', r"ENC_AUTH_ENABLED: bool = False"):
            self.assertRegex(config, pattern)

    def test_g7_changes_no_flutter_or_production_backend_file(self):
        # Narrowed on approval (encrypted-auth gateway integrated into backend/): only the 14 approved backend paths, with the verified content.
        status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "--", "backend"], capture_output=True, text=True).stdout
        self.assertEqual(approved_integration.unapproved(status), [])
        self.assertEqual(approved_integration.backend_content_problems(REPO), [])


if __name__ == "__main__":
    unittest.main()
