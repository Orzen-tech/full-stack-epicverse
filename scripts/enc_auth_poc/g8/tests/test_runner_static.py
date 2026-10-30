"""The interactive runner (disabled as committed; order of refusals; separate approvals per mode), the wiring helpers that can be exercised
without a network, and static guarantees over the G8 sources. Offline; the G7 network-denial layer is active."""
import ast
import asyncio
import contextlib
import hashlib
import re
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _g8 as t
from _tree import ATTEMPTS, REPO, run

import approved_integration
import g3_guard as g3
import g8_guard as guard
import g8_live_run as runner
import g8_protect as protect
import g8_stages as stages
import g8_wiring as wiring
from g8_guard import LiveSafetyStop

SOURCES = sorted(p for p in t.G8.glob("*.py"))
def code_without_docstrings(path):
    """The executable code of a source file: comments are gone (ast) and so are docstrings, which may legitimately NAME what is forbidden."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


CODE = {p.name: code_without_docstrings(p) for p in SOURCES}


class FakeIO(runner.IO):
    def __init__(self, tty=True, visible=(), hidden=()):
        self.visible_calls, self.hidden_calls, self.lines = [], [], []
        self._visible, self._hidden = list(visible), list(hidden)
        super().__init__(hidden=self._take_hidden, visible=self._take_visible, is_tty=lambda: tty, out=self.lines.append)

    def _take_visible(self, prompt):
        self.visible_calls.append(prompt)
        if not self._visible:
            raise AssertionError("unexpected visible prompt")
        return self._visible.pop(0)

    def _take_hidden(self, prompt):
        self.hidden_calls.append(prompt)
        raise AssertionError("a secret must not be requested here")


def approve(*stages_):
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(guard, "LIVE_EXECUTION_APPROVED", True))
    stack.enter_context(mock.patch.dict(guard.STAGE_APPROVED, {s: True for s in stages_}))
    return stack


def code_of(coro):
    try:
        run(coro)
    except LiveSafetyStop as stop:
        return stop.code
    return None


class Disabled(unittest.TestCase):
    def tearDown(self):
        guard.uninstall_all()

    def test_as_committed_every_mode_refuses_before_any_prompt_or_socket(self):
        for mode in ("stage-a", "stage-b", "cleanup"):
            io = FakeIO()
            self.assertEqual(code_of(runner.execute(mode, io, "no-such-tree")), "live_execution_not_approved")
            self.assertEqual((io.visible_calls, io.hidden_calls), ([], []))
        self.assertEqual(ATTEMPTS, [])
        self.assertEqual(ATTEMPTS, [])
        self.assertIsNone(guard._installed["urllib3"])

    def test_main_prints_one_fixed_code_and_exits_2_without_any_prompt(self):
        for mode in ("stage-a", "stage-b", "cleanup"):
            io = FakeIO()
            self.assertEqual(runner.main(["g8_live_run.py", mode], io), 2)
            self.assertEqual((io.lines, io.visible_calls, io.hidden_calls), (["G8 stopped: live_execution_not_approved"], [], []))

    def test_unknown_mode_and_bad_usage(self):
        for argv in (["x"], ["x", "stage-z"], ["x", "stage-a", "extra"]):
            io = FakeIO()
            self.assertEqual(runner.main(argv, io), 2)
            self.assertIn("usage", io.lines[0])

    def test_each_mode_needs_its_own_approval(self):
        io = FakeIO(tty=False)
        with approve("B"):
            self.assertEqual(code_of(runner.execute("stage-b", io, "x")), "not_interactive")                # past the approval gate, for B only
            self.assertEqual(code_of(runner.execute("stage-a", io, "x")), "live_execution_not_approved")
            self.assertEqual(code_of(runner.execute("cleanup", io, "x")), "live_execution_not_approved")
        with mock.patch.dict(guard.STAGE_APPROVED, {"A": True, "B": True, "C": True}):
            for mode in ("stage-a", "stage-b", "cleanup"):                                               # every mode switch on, the global switch off
                self.assertEqual(code_of(runner.execute(mode, FakeIO(), "x")), "live_execution_not_approved")


class Order(unittest.TestCase):
    """With the approvals patched on (offline test only), every later refusal still happens before a secret is requested."""

    def tearDown(self):
        guard.uninstall_all()

    def go(self, mode, io, env=None, tree=None, patches=()):
        with approve(mode_stage(mode)):
            for p in patches:
                p.start()
            try:
                return code_of(runner.execute(mode, io, tree or str(t._tree.tree_dir()), env))
            finally:
                for p in patches:
                    p.stop()

    def test_not_interactive(self):
        io = FakeIO(tty=False)
        self.assertEqual(self.go("stage-a", io), "not_interactive")
        self.assertEqual(io.visible_calls, [])

    def test_environment_problems_refuse_first(self):
        for env, code in (({"HTTPS_PROXY": "http://p:1"}, "proxy_environment"), ({"SSL_CERT_FILE": "x"}, "tls_override_environment"),
                          ({"GOOGLE_APPLICATION_CREDENTIALS": "x"}, "credential_environment")):
            io = FakeIO()
            self.assertEqual(self.go("stage-a", io, env=env), code)
            self.assertEqual((io.visible_calls, io.hidden_calls), ([], []))

    def test_a_tree_with_other_bytes_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="g8_tree_"))
        try:
            for rel in wiring.ADAPTER_PINS:
                target = tmp / "backend" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((t._tree.tree_dir() / "backend" / rel).read_bytes() + b"\n# changed\n")
            io = FakeIO()
            self.assertEqual(self.go("stage-a", io, env={}, tree=str(tmp)), "adapter_hash_mismatch")
            self.assertEqual(io.visible_calls, [])
            shutil.rmtree(tmp / "backend")
            self.assertEqual(self.go("stage-a", FakeIO(), env={}, tree=str(tmp)), "adapter_hash_mismatch")          # missing files
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_typed_confirmations_come_before_any_secret(self):
        P, B = guard.ALLOWED_PROJECT_ID, runner.MUTATION_PHRASES
        cases = [("stage-a", ["wrong-project"], "project_not_confirmed"),
                 ("stage-a", [P, "yes"], "stage_not_confirmed"),
                 ("stage-b", [P, runner.PHRASES["B"], B["create"], "no", "no", "no"], "mutation_not_confirmed"),
                 ("stage-b", [P, runner.PHRASES["B"], B["create"], "no", B["revoke"], B["disable"]], "mutation_not_confirmed"),
                 ("stage-b", [P, runner.PHRASES["B"], B["create"], B["password"], B["revoke"], "disable"], "mutation_not_confirmed"),
                 ("cleanup", [P, runner.PHRASES["C"], "no"], "mutation_not_confirmed")]
        for mode, typed, code in cases:
            io = FakeIO(visible=list(typed))
            self.assertEqual(self.go(mode, io, env={}), code, mode)
            self.assertEqual(io.hidden_calls, [])

    def test_stage_a_asks_for_no_mutation_phrase_and_stage_b_for_four_separate_ones(self):
        self.assertEqual(runner.STAGE_MUTATIONS["A"], ())
        self.assertEqual(set(runner.STAGE_MUTATIONS["B"]), {"create", "password", "revoke", "disable"})
        self.assertEqual(runner.STAGE_MUTATIONS["C"], ("delete",))
        self.assertEqual(len(set(runner.MUTATION_PHRASES.values())), 5)
        self.assertEqual(len(set(runner.PHRASES.values())), 3)

    def test_a_cap_that_cannot_be_guaranteed_stops_before_any_secret_and_removes_the_hooks(self):
        io = FakeIO(visible=[guard.ALLOWED_PROJECT_ID, runner.PHRASES["A"], t.SIGNER])
        patch = mock.patch.object(guard, "cap_guaranteed", return_value=(False, ["urllib3_hook_missing"]))
        self.assertEqual(self.go("stage-a", io, env={}, patches=[patch]), "cap_not_guaranteed:urllib3_hook_missing")
        self.assertEqual(io.hidden_calls, [])
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])

    def test_retries_are_switched_off_in_the_adapter_before_the_cap_is_judged(self):
        io = FakeIO(visible=[guard.ALLOWED_PROJECT_ID, runner.PHRASES["A"], t.SIGNER])
        seen = []
        patch = mock.patch.object(guard, "cap_guaranteed", side_effect=lambda env=None, versions=None, retry_max=None: seen.append(retry_max) or (False, ["x"]))
        self.go("stage-a", io, env={}, patches=[patch])
        self.assertEqual(seen, [0])

    def test_a_signer_from_another_project_is_refused_before_any_secret(self):
        io = FakeIO(visible=[guard.ALLOWED_PROJECT_ID, runner.PHRASES["A"], "sa@other-project.iam.gserviceaccount.com"])
        with self.assertRaises(g3.GuardError):
            self.go("stage-a", io, env={})
        self.assertEqual(io.hidden_calls, [])
        self.assertIsNone(guard._installed["urllib3"])

    def test_disposable_credentials_are_generated_and_shaped(self):
        a, b = runner.generate_disposable(), runner.generate_disposable()
        self.assertRegex(a["email"], protect.DISPOSABLE_EMAIL_RE.pattern)
        self.assertNotEqual(a["email"], b["email"])
        self.assertNotEqual(a["password"], a["new_password"])
        self.assertGreaterEqual(len(a["password"]), 20)


def mode_stage(mode):
    return runner.MODES[mode]


class WiringOffline(unittest.TestCase):
    def test_the_pinned_adapter_hashes_match_the_hash_gated_g7_build(self):
        for rel, expected in wiring.ADAPTER_PINS.items():
            path = t._tree.tree_dir() / "backend" / rel
            self.assertEqual(hashlib.sha256(path.read_bytes().replace(b"\r", b"")).hexdigest(), expected, rel)

    def test_verify_tree_accepts_the_built_tree(self):
        adapter, settings = wiring.verify_tree(t._tree.tree_dir())
        self.assertTrue(hasattr(adapter, "FirebaseIdentity"))
        self.assertTrue(hasattr(settings, "ENC_AUTH_RETRY_MAX"))

    def test_configure_settings_turns_retries_off_and_pins_the_project(self):
        settings = types.SimpleNamespace(ENC_AUTH_RETRY_MAX=2, GOOGLE_APPLICATION_CREDENTIALS="x")
        wiring.configure_settings(settings, t.SIGNER, g3.Secret(t.API_KEY))
        self.assertEqual(settings.ENC_AUTH_RETRY_MAX, 0)
        self.assertEqual((settings.ENC_AUTH_FIREBASE_PROJECT_ID, settings.FIREBASE_PROJECT_ID), (guard.ALLOWED_PROJECT_ID,) * 2)
        self.assertEqual(settings.GOOGLE_APPLICATION_CREDENTIALS, "")
        with self.assertRaises(g3.GuardError):
            wiring.configure_settings(settings, "sa@other.iam.gserviceaccount.com", g3.Secret(t.API_KEY))

    def test_the_adapter_accepts_the_configured_settings_as_ready(self):
        from app.core.config import settings
        with mock.patch.object(settings, "ENC_AUTH_RETRY_MAX", 2), mock.patch.object(settings, "ENC_AUTH_FIREBASE_PROJECT_ID", ""), \
                mock.patch.object(settings, "FIREBASE_PROJECT_ID", ""), mock.patch.object(settings, "ENC_AUTH_SIGNER_SERVICE_ACCOUNT", ""), \
                mock.patch.object(settings, "ENC_AUTH_FIREBASE_WEB_API_KEY", ""), mock.patch.object(settings, "GOOGLE_APPLICATION_CREDENTIALS", ""):
            wiring.configure_settings(settings, t.SIGNER, g3.Secret(t.API_KEY))
            with mock.patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("FIREBASE_AUTH_EMULATOR_HOST", None)
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
                self.assertIsNone(t.real.config_problem())

    def test_account_ops_map_sdk_calls_and_hide_library_text(self):
        calls = []

        class NotFound(Exception):
            pass

        class FakeAuth:
            UserNotFoundError = NotFound

            @staticmethod
            def update_user(uid, **kw):
                calls.append(("update", uid, kw))

            @staticmethod
            def delete_user(uid, **kw):
                calls.append(("delete", uid, kw))
                raise RuntimeError("secret library text user@example.com")

            @staticmethod
            def get_user_by_email(email, **kw):
                if email == "gone@example.com":
                    raise NotFound()
                if email == "boom@example.com":
                    raise RuntimeError("secret library text")
                return types.SimpleNamespace(uid="uid9")

        ops = wiring.FirebaseAccountOps("app-object", FakeAuth)
        run(ops.set_disabled("uid9", 1))
        self.assertEqual(calls[0], ("update", "uid9", {"disabled": True, "app": "app-object"}))
        with self.assertRaises(wiring.OpsUnavailable) as failure:
            run(ops.delete_user("uid9"))
        self.assertNotIn("secret", str(failure.exception) + repr(failure.exception))
        self.assertIsNone(failure.exception.__cause__)
        self.assertEqual(run(ops.find_uid("a@example.com")), "uid9")
        self.assertIsNone(run(ops.find_uid("gone@example.com")))
        with self.assertRaises(wiring.OpsUnavailable):
            run(ops.find_uid("boom@example.com"))

    def test_g3_session_maps_results_and_verifies_claims_locally(self):
        import g3_rest_client as rc

        class FakeClient:
            def __init__(self):
                self.calls = []

            def fetch_public_certificates(self):
                return rc.Result("certificates", rc.OK, "2xx", data={"kid": "PEM"}, status=200)

            def refresh(self, secret):
                return rc.Result("refresh", rc.TOKEN_INVALID, "4xx", status=400, error_code="TOKEN_EXPIRED")

            def sign_in_with_custom_token(self, token):
                return rc.Result("custom_token_sign_in", rc.OK, "2xx", tokens=rc.Tokens("a.b.c", "refresh", "3600", None, None, False), status=200)

        session = wiring.G3Session(FakeClient(), clock=lambda: 1_700_000_000)
        refused = run(session.refresh(g3.Secret("r" * 30)))
        self.assertEqual((refused.ok, refused.code), (False, "TOKEN_EXPIRED"))
        exchanged = run(session.exchange("x.y.z" * 6))
        self.assertTrue(exchanged.ok)
        self.assertIs(exchanged.is_new_user, False)
        self.assertEqual(code_of(session.claims("a.b.c")), "certificates_not_loaded")
        with mock.patch("g3_verify.FirebaseSignatureVerifier") as verifier:
            run(session.load_certs())
            verifier.return_value.verify.side_effect = __import__("g3_token_inspect").TokenInspectionError("signature_invalid")
            self.assertIsNone(run(session.claims("a.b.c")))

    def test_certificate_download_failure_is_a_stop_not_a_pass(self):
        import g3_rest_client as rc

        class Failing:
            def fetch_public_certificates(self):
                return rc.Result("certificates", rc.SERVICE_ERROR, "5xx", cause="http_5xx", status=503)
        self.assertEqual(code_of(wiring.G3Session(Failing()).load_certs()), "certificates_unavailable")

    def test_static_token_provider_hands_back_the_pasted_token(self):
        provider = wiring.static_token_provider(t.real, g3.Secret("ya29." + "z" * 40))
        self.assertEqual(run(provider.access_token()), "ya29." + "z" * 40)


class Static(unittest.TestCase):
    def test_only_python_and_markdown_files(self):
        for path in t.G8.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                self.assertIn(path.suffix, {".py", ".md"}, path.name)

    def test_no_key_adc_gcloud_subprocess_logging_or_environment_discovery_in_the_harness(self):
        forbidden = ("gcloud", "load_pem_private_key", "service_account.Credentials", "from_service_account", "ApplicationDefault", "google.auth.default",
                     "auth.default(", "subprocess", "logging", "getenv(", "os.system", "eval(", "exec(", "pickle", "shelve", "open(", "write_text", "write_bytes",
                     "firebase_admin.credentials.Certificate", "credentials.Certificate")
        for name, code in CODE.items():
            for word in forbidden:
                self.assertNotIn(word, code, "%s uses %s" % (name, word))

    def test_environment_is_read_only_through_the_one_checked_parameter(self):
        for name, code in CODE.items():
            uses = re.findall(r"os\.environ", code)
            self.assertEqual(len(uses), 1 if name == "g8_guard.py" else 0, name)

    def test_print_exists_only_as_the_default_output_of_the_interactive_runner(self):
        for name, code in CODE.items():
            self.assertEqual(len(re.findall(r"\bprint\b", code)), 1 if name == "g8_live_run.py" else 0, name)

    def test_secrets_are_read_with_hidden_prompts_and_never_from_argv_or_the_environment(self):
        code = CODE["g8_live_run.py"]
        for label in ("Web API key", "Signer access token", "Admin access token", "Dummy account uid", "Dummy account e-mail", "Dummy account password", "Dummy account uid, used only to REFUSE it"):
            self.assertRegex(code, r"""io\.hidden\(['"]%s[^'"]*\(hidden\)""" % label)
        self.assertNotIn("sys.argv[", code.replace("main(sys.argv)", ""))
        self.assertEqual(len(re.findall(r"getpass\.getpass", code)), 1)

    def test_test_only_hooks_are_not_reachable_from_live_code(self):
        for name in ("g8_live_run.py", "g8_wiring.py", "g8_stages.py", "g8_protect.py"):
            self.assertNotIn("_loopback_test_port", CODE[name], name)
            self.assertNotIn("_test_port", CODE[name], name)

    def test_the_approval_constants_are_literally_false(self):
        text = (t.G8 / "g8_guard.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^LIVE_EXECUTION_APPROVED = (\w+)", text, re.M), ["False"])
        self.assertEqual(re.findall(r"^STAGE_APPROVED = (\{.*\})", text, re.M), ['{"A": False, "B": False, "C": False}'])

    def test_https_literals_are_absent_from_the_harness_sources(self):
        for name, code in CODE.items():
            self.assertEqual(re.findall(r"https?://[^\s\"']+", code), [], name)

    def test_hosts_are_exactly_the_four_google_hosts_g3_and_g7_already_use(self):
        self.assertEqual(guard.ALLOWED_HOSTS, frozenset({"identitytoolkit.googleapis.com", "securetoken.googleapis.com", "www.googleapis.com",
                                                         "iamcredentials.googleapis.com"}))
        self.assertEqual(set(g3.ALLOWED_ENDPOINTS) | {"iamcredentials.googleapis.com"}, guard.ALLOWED_HOSTS)

    def test_the_harness_changes_no_tracked_file_outside_the_approved_integration(self):
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO)), [])
        status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "--", "backend"], capture_output=True, text=True).stdout
        self.assertEqual(approved_integration.unapproved(status), [])               # only the 14 approved backend paths (encrypted-auth gateway integration)
        self.assertEqual(approved_integration.backend_content_problems(REPO), [])

    def test_the_earlier_phases_are_byte_identical_where_they_are_pinned(self):
        g7 = t.G7
        self.assertEqual(hashlib.sha256((g7 / "enc_auth_gateway_g7_firebase.patch").read_bytes().replace(b"\r", b"")).hexdigest(),
                         re.search(r"G7_PATCH_SHA256=([0-9a-f]{64})", (g7 / "build_tree_enc_auth_g7.sh").read_text(encoding="utf-8")).group(1))

    def test_the_g3_constants_are_untouched(self):
        self.assertIs(g3.LIVE_MODE_ENABLED, False)
        self.assertEqual(g3.ALLOWED_PROJECT_ID, "epicverse-g3-auth-test")


if __name__ == "__main__":
    unittest.main()
