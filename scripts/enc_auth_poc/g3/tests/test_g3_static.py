"""Static checks for G3 Phase 1: no network or credential code paths, no hard-coded secrets, no CLI/live entry point, nothing outside g3/
changed, earlier phases byte-identical. Offline; reads files and asks Git read-only questions."""
import ast
import hashlib
import re
import subprocess
import sys
import unittest

import _support as S
sys.path.insert(0, str(S.G3.parent))
import approved_integration  # noqa: E402  (the single list of approved integration changes)

G3 = S.G3
POC = G3.parent
REPO = POC.parent.parent
MODULES = ["g3_guard.py", "g3_rest_client.py", "g3_token_inspect.py"]
ALLOWED_IMPORTS = {
    "g3_guard.py": {"os", "re", "dataclasses", "urllib"},
    "g3_rest_client.py": {"getpass", "json", "re", "urllib", "dataclasses", "g3_guard", "g3_token_inspect"},
    "g3_token_inspect.py": {"base64", "binascii", "json", "g3_guard"},
}
FORBIDDEN_WORDS = ("import requests", "urllib.request", "http.client", "import socket", "import ssl", "subprocess", "firebase_admin", "google.auth",
                   "google.oauth2", "argparse", "sys.argv", "import logging","os.system", "eval(", "exec(", "import pickle", "service_account.Credentials",
                   "ApplicationDefault", "refresh_token=" + "\"", "firebase.json", ".firebaserc")
PINNED = {
    "envelope.py": "8bea03a024b471c9504236cc09e96ad4d852106263370023267e1b0142c188f6",
    "nonces.py": "0ac6d1b12ab25b9d87371ff88790a787d05dd637456bd7049d4036a19f693baf",
    "testkeys.py": "af17ca7f05fefe86b8ca56dde0661b736f6c86c07e3a333660c1f8d652621b38",
    "g6a/enc_auth_gateway.patch": "12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98",
    "g6a/build_tree_enc_auth.sh": "33e65232016756d6c13931391d58bf3037670ccab3c7e373d149425e38d03fbd",
    "g6b/enc_auth_gateway_g6b.patch": "69abe816d9d1c354904e53f6ce5ed1ad2704dccaaeab19ea5a6fcd1bc531f4dc",
    "g6b/build_tree_enc_auth_g6b.sh": "9e486515c9735bb70ede1cafc2ff41dee021931c725b9b0aa47d28e0e0aeee64",
    "g4/oaep_gate.py": "a3abc81104b61b3a78b2a242bcf25ce7d3850cfa54c36d01a587e083ce5cdade",
    "g2/run_g2.py": "61f2127478be2219884c3f58d9840f72ac8043da628762b156b8b52a8ab817d2",
}


def source(name):
    return (G3 / name).read_text(encoding="utf-8")


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


def g3_files():
    return [p for p in G3.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


class NoNetworkOrCredentialCode(unittest.TestCase):
    def test_imports_are_limited_to_the_allowed_set(self):
        for name in MODULES:
            tree = ast.parse(source(name))
            top_level = set()
            for node in tree.body:
                if isinstance(node, ast.Import):
                    top_level |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    top_level.add((node.module or "").split(".")[0])
            self.assertLessEqual(top_level, ALLOWED_IMPORTS[name], name)

    def test_the_http_client_is_imported_only_inside_the_disabled_real_transport(self):
        tree = ast.parse(source("g3_rest_client.py"))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for node in ast.walk(cls):
                if isinstance(node, ast.Import) and any(a.name == "httpx" for a in node.names):
                    self.assertEqual(cls.name, "HttpxTransport")
        text = source("g3_rest_client.py")
        init = text[text.index("class HttpxTransport"):text.index("def __call__")]
        self.assertLess(init.index("guard.require_live_mode()"), init.index("import httpx"))         # the gate comes first

    def test_real_transport_settings_are_the_safe_ones(self):
        text = source("g3_rest_client.py")
        for needle in ("follow_redirects=False", "trust_env=False", "http2=False", "httpx.Timeout(connect=", "iter_bytes()", "guard.assert_request_safe(request)"):
            self.assertIn(needle, text)

    def test_forbidden_constructs_are_absent(self):
        for name in MODULES:
            text = source(name)
            for word in FORBIDDEN_WORDS:
                self.assertNotIn(word, text, "%s contains %s" % (name, word))

    def test_no_printing_no_file_access_no_entry_point(self):
        for name in MODULES:
            tree = ast.parse(source(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                    self.assertNotIn(called, {"print", "open", "system", "popen", "input_", "__import__"}, "%s calls %s" % (name, called))
            self.assertNotIn("__main__", source(name), name)

    def test_the_environment_is_read_in_exactly_one_place(self):
        self.assertEqual(source("g3_guard.py").count("os.environ"), 1)
        for name in MODULES[1:]:
            self.assertNotIn("os.environ", source(name))
            self.assertNotIn("getenv", source(name))

    def test_live_mode_is_a_hard_coded_false_and_nothing_flips_it(self):
        self.assertEqual(len(re.findall(r"^LIVE_MODE_ENABLED = False$", source("g3_guard.py"), re.M)), 1)
        for path in g3_files():
            if path.suffix == ".py" and path.name != "test_g3_static.py":
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"LIVE_MODE_ENABLED\s*=\s*True", path.name)

    def test_the_allowed_project_id_is_defined_once_and_no_module_names_another_project(self):
        self.assertEqual(source("g3_guard.py").count('ALLOWED_PROJECT_ID = "epicverse-g3-auth-test"'), 1)
        for name in MODULES:
            ids = re.findall(r"epicverse[a-z0-9-]*", source(name))
            self.assertLessEqual(set(ids), {"epicverse-g3-auth-test"}, name)


class NoSecretsInFiles(unittest.TestCase):
    PATTERNS = {
        "web api key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
        "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),
        "private key marker": re.compile("PRIVATE" + r" KEY-----"),
        "service account": re.compile(r'"type"\s*:\s*"service_' + 'account"|client' + '_email|private_key' + '_id'),
        "google token": re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
        "refresh token literal": re.compile(r"1//[A-Za-z0-9_-]{30,}"),
    }

    def test_no_secret_shaped_text_in_any_g3_file(self):
        for path in g3_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in self.PATTERNS.items():
                self.assertIsNone(pattern.search(text), "%s looks like a %s" % (path.name, label))

    def test_only_reserved_example_addresses_appear(self):
        for path in g3_files():
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if "://" not in l and "//" not in l and "robot/v1/metadata/x509" not in l]       # URL tricks are not addresses
            for address in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", chr(10).join(lines)):
                self.assertTrue(address.endswith((".invalid", "@example.com")) or "gserviceaccount.com" in address,"%s: %s" % (path.name, address[:30]))

    def test_the_folder_holds_only_python_and_markdown(self):
        self.assertEqual({p.suffix for p in g3_files()}, {".py", ".md"})


class Integrity(unittest.TestCase):
    def test_earlier_phases_are_byte_identical(self):
        for rel, expected in PINNED.items():
            data = (POC / rel).read_bytes().replace(b"\r", b"")
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected, rel)

    def test_no_tracked_file_changed_nothing_staged_nothing_outside_the_poc_folder(self):
        # Narrowed on approval (local encrypted-auth Flutter integration): ONLY the files in approved_integration may differ from HEAD.
        self.assertEqual(set(git("diff", "--name-only").splitlines()) - approved_integration.APPROVED_TRACKED_CHANGES - approved_integration.APPROVED_BACKEND_MODIFIED, set())
        self.assertEqual(git("diff", "--cached", "--name-only"), "")
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO)), [])

    def test_no_branches_stashes_or_worktrees_were_added(self):
        self.assertEqual(len(git("branch", "--list").splitlines()), 11)
        self.assertEqual(len(git("stash", "list").splitlines()), 2)
        self.assertEqual(len(git("worktree", "list").splitlines()), 1)

    def test_the_flutter_app_and_production_backend_are_untouched(self):
        # Narrowed on approval (encrypted-auth gateway integrated into backend/): ONLY the 14 approved backend paths may differ, with the verified content;
        # every other backend change is still reported.
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO, "backend")), [])
        self.assertEqual(approved_integration.backend_content_problems(REPO), [])
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO, "frontend")), [])


class Phase2Modules(unittest.TestCase):
    NEW = ["g3_live_transport.py", "g3_verify.py", "g3_live_run.py", "g3_live_errors.py", "g3_compat.py", "g3_custom_token.py", "g3_iam_signer.py", "g3_live_custom_token.py"]
    IMPORTS = {"g3_live_transport.py": {"http", "socket", "ssl", "urllib", "g3_guard", "g3_rest_client"},
               "g3_verify.py": {"cryptography", "g3_token_inspect", "g3_guard"},
               "g3_live_errors.py": {"getpass", "os", "secrets", "sys", "g3_guard", "g3_rest_client", "g3_live_run", "g3_live_transport"},
               "g3_compat.py": {"g3_token_inspect"},
               "g3_custom_token.py": {"base64", "json", "g3_guard"},
               "g3_iam_signer.py": {"base64", "binascii", "json", "re", "g3_custom_token", "g3_guard", "g3_rest_client"},
               "g3_live_custom_token.py": {"getpass", "hmac", "os", "sys", "time", "g3_compat", "g3_custom_token", "g3_guard", "g3_iam_signer", "g3_rest_client",
                                           "g3_token_inspect", "g3_verify", "g3_live_run", "g3_live_transport"},
               "g3_live_run.py": {"getpass", "sys", "time", "os", "g3_guard", "g3_rest_client", "g3_token_inspect", "g3_verify", "g3_live_transport"}}

    def test_imports_are_limited(self):
        for name in self.NEW:
            tree = ast.parse(source(name))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    found.add((node.module or "").split(".")[0])
            self.assertLessEqual(found, self.IMPORTS[name], name)

    def test_only_the_transport_can_open_connections_and_only_through_an_approval(self):
        text = source("g3_live_transport.py")
        self.assertIn("self._approval.consume(request.method, request.url)", text)
        self.assertLess(text.index("guard.assert_request_safe(request)"), text.index("self._approval.consume"))
        self.assertLess(text.index("self._approval.consume"), text.index("self._factory("))
        for name in ("g3_guard.py", "g3_rest_client.py", "g3_token_inspect.py", "g3_verify.py", "g3_live_run.py"):
            for word in ("HTTPSConnection", "socket.socket", "create_connection", "urlopen", "http.client"):
                self.assertNotIn(word, source(name).replace("HttpxTransport", ""), "%s mentions %s" % (name, word))

    def test_tls_is_never_weakened_anywhere(self):
        for name in self.NEW + MODULES:
            text = source(name)
            for word in ("CERT_NONE", "_create_unverified_context", "check_hostname = False", "verify=False", "verify_mode ="):
                self.assertNotIn(word, text, name)
        self.assertIn("ssl.create_default_context()", source("g3_live_transport.py"))

    def test_the_entry_point_is_interactive_only_and_takes_no_arguments_or_switches(self):
        text = source("g3_live_run.py")
        for needle in ("sys.stdin.isatty()", "sys.stdout.isatty()", "grant_live_approval", "getpass.getpass"):
            self.assertIn(needle, text)
        for forbidden in ("sys.argv", "argparse", "--live", "environ.get", "getenv", "LIVE_MODE_ENABLED"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count("__main__"), 1)                       # only the entry-point clause
        errors_text = source("g3_live_errors.py")
        for needle in ("sys.stdin.isatty()", "sys.stdout.isatty()", "grant_live_approval", "getpass.getpass", "ERROR_TEST_BUDGET"):
            self.assertIn(needle, errors_text)
        for forbidden in ("sys.argv", "argparse", "environ.get", "getenv", "LIVE_MODE_ENABLED"):
            self.assertNotIn(forbidden, errors_text)
        self.assertEqual(errors_text.count("__main__"), 1)
        custom_text = source("g3_live_custom_token.py")
        for needle in ("sys.stdin.isatty()", "sys.stdout.isatty()", "grant_custom_token_approval", "getpass.getpass", "CUSTOM_TOKEN_LIVE_APPROVED"):
            self.assertIn(needle, custom_text)
        for forbidden in ("sys.argv", "argparse", "environ.get", "getenv", "LIVE_MODE_ENABLED"):
            self.assertNotIn(forbidden, custom_text)
        self.assertEqual(custom_text.count("__main__"), 1)
        self.assertLess(custom_text.index("CUSTOM_TOKEN_LIVE_APPROVED is not True"), custom_text.index("getpass_fn("))          # the disabled check precedes every prompt
        self.assertLess(custom_text.index("ENABLING_PHRASE"), custom_text.index("getpass_fn("))
        self.assertLess(custom_text.index("uid check:"), custom_text.index("signer_mod.IamSignBlobSigner("))                  # uid verified before anything is signed
        self.assertLess(custom_text.index("signer_mod.IamSignBlobSigner("), custom_text.index("client.sign_in_with_custom_token("))
        for name in ("g3_live_transport.py", "g3_verify.py", "g3_compat.py", "g3_custom_token.py", "g3_iam_signer.py") + tuple(MODULES):
            self.assertNotIn("__main__", source(name), name)

    def test_the_global_switch_is_still_off_and_unused(self):
        self.assertIn("LIVE_MODE_ENABLED = False", source("g3_guard.py"))
        for name in self.NEW:
            self.assertNotRegex(source(name), r"LIVE_MODE_ENABLED\s*=")
            self.assertNotRegex(source(name), r"CUSTOM_TOKEN_LIVE_APPROVED\s*=")
        guard_text = source("g3_guard.py")
        self.assertEqual(len(re.findall(r"^CUSTOM_TOKEN_LIVE_APPROVED = False$", guard_text, re.M)), 1)
        self.assertEqual(len(re.findall(r"CUSTOM_TOKEN_LIVE_APPROVED\s*=", guard_text)), 1)

    def test_no_admin_billing_or_credential_code_exists_and_the_iam_and_custom_token_names_stay_in_their_modules(self):
        everywhere = ("firebase_admin", "oauth2.googleapis.com", "accounts:update", "accounts:delete", "accounts:signUp", "accounts:sendOobCode",
                      "cloudbilling", "ApplicationDefault", "google.auth", "createAuthUri", "load_pem_private_key", "private_key")
        phase5_only = ("iamcredentials", "serviceAccounts", "signInWithCustomToken")
        phase5_modules = {"g3_guard.py", "g3_iam_signer.py", "g3_live_custom_token.py", "g3_rest_client.py"}
        for name in self.NEW + MODULES:
            text = source(name)
            for word in everywhere:
                self.assertNotIn(word, text, "%s mentions %s" % (name, word))
            if name not in phase5_modules:
                for word in phase5_only:
                    self.assertNotIn(word, text, "%s mentions %s" % (name, word))

    def test_exactly_three_requests_are_approved(self):
        import g3_guard
        self.assertEqual(len(g3_guard.APPROVED_REQUESTS), 3)
        self.assertEqual({m for m, _ in g3_guard.APPROVED_REQUESTS}, {"POST", "GET"})
        self.assertEqual(sorted(u.split("/")[2] for _, u in g3_guard.APPROVED_REQUESTS),
                         ["identitytoolkit.googleapis.com", "securetoken.googleapis.com", "www.googleapis.com"])


class NoNetworkAttempts(unittest.TestCase):
    def test_T_no_network_access_was_attempted_by_any_test_in_this_process(self):
        self.assertEqual(S.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
