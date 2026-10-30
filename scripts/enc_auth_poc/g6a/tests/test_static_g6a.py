"""Static guarantees for the G6a gateway patch and the repository: logging safety, no external endpoints, safe defaults, and no change to
any tracked file, branch or stash. Offline."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import G6A, REPO, tree_dir  # noqa: E402
import approved_integration  # noqa: E402  (the single list of approved integration changes)

NEW_MODULES = ["app/api/enc_gateway.py", "app/services/enc_auth_envelope.py", "app/services/enc_auth_identity.py",
               "app/services/enc_auth_policy.py", "app/services/enc_auth_store.py"]
ALLOWED_LOG_EXPRESSIONS = {"op", "outcome", "safe_exc(exc)", "safe_exc(e)"}


def source(rel):
    return (tree_dir() / "backend" / rel).read_text(encoding="utf-8")


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


class Logging(unittest.TestCase):
    def test_every_print_in_the_new_code_logs_only_fixed_codes(self):
        seen = 0
        for rel in NEW_MODULES + ["app/services/user_db.py"]:
            for line in source(rel).splitlines():
                if "ENC-AUTH" not in line or "print(" not in line:
                    continue
                seen += 1
                for expression in re.findall(r"\{([^{}]*)\}", line):
                    self.assertIn(expression.strip(), ALLOWED_LOG_EXPRESSIONS, "%s logs {%s}" % (rel, expression))
        self.assertGreaterEqual(seen, 3)

    def test_no_logging_module_and_no_credential_names_in_log_helpers(self):
        for rel in NEW_MODULES:
            text = source(rel)
            self.assertNotIn("import logging", text, rel)
            for line in text.splitlines():
                if "_log(" in line and not line.lstrip().startswith("def "):
                    code_only = re.sub(r"\"[^\"]*\"", "", line)          # fixed outcome strings are fine; identifiers are not
                    self.assertNotRegex(code_only, r"creds|password|email|token|proof|nonce|uid", rel + ": " + line.strip())

    def test_error_text_never_includes_request_values(self):
        for rel in ("app/services/enc_auth_envelope.py",):
            text = source(rel)
            self.assertIn("Carries only a fixed error code", text)
            self.assertNotRegex(text, r"EnvelopeError\(f[\"']")        # no formatted messages


class Endpoints(unittest.TestCase):
    def test_the_only_identity_url_is_the_local_emulator_form(self):
        for rel in NEW_MODULES:
            for n, line in enumerate(source(rel).splitlines(), 1):
                if "googleapis.com" in line or "https://" in line:
                    self.assertIn("http://{host}/identitytoolkit.googleapis.com/v1", line, "%s:%d" % (rel, n))
        self.assertNotIn("https", source("app/services/enc_auth_identity.py").replace("# https", ""))

    def test_local_only_guard_requires_emulator_demo_project_and_no_credential_file(self):
        text = source("app/services/enc_auth_identity.py")
        for needle in ('FIREBASE_AUTH_EMULATOR_HOST', 'startswith("demo-")', "GOOGLE_APPLICATION_CREDENTIALS", "trust_env=False",
                       "ENC_AUTH_ALLOWED_IDENTITY_HOSTS"):
            self.assertIn(needle, text)

    def test_new_code_has_no_other_network_clients(self):
        for rel in NEW_MODULES:
            text = source(rel)
            for word in ("import requests", "import urllib", "import socket", "aiohttp", "smtplib"):
                self.assertNotIn(word, text, rel)


class Defaults(unittest.TestCase):
    def test_safe_defaults(self):
        config = source("app/core/config.py")
        self.assertRegex(config, r"ENC_AUTH_ENABLED: bool = False")
        self.assertRegex(config, r"ENC_AUTH_REQUIRE_EMAIL_PROOF: bool = True")
        self.assertRegex(config, r'ENC_AUTH_KEYS: str = ""')
        self.assertRegex(config, r'ENC_AUTH_HASH_SECRET: str = ""')
        self.assertRegex(config, r'ENC_AUTH_ALLOWED_IDENTITY_HOSTS: str = "127.0.0.1,localhost"')

    def test_the_gateway_has_no_plaintext_or_downgrade_path(self):
        text = source("app/api/enc_gateway.py")
        for word in ("signInWithEmailAndPassword", "legacy", "fallback", "plaintext_ok", "ENC_AUTH_OPTIONAL"):
            self.assertNotIn(word, text)
        # a password is only ever read from the decrypted envelope object
        self.assertNotIn("Form(", text)
        self.assertNotIn("request.form", text)

    def test_gateway_state_is_none_unless_every_requirement_holds(self):
        text = source("app/api/enc_gateway.py")
        self.assertIn("if not settings.ENC_AUTH_ENABLED or not settings.ENC_AUTH_HASH_SECRET or not ident.ready():", text)

    def test_custom_tokens_are_minted_only_from_identity_service_uids(self):
        text = source("app/api/enc_gateway.py")
        arguments = set(re.findall(r"_mint\((\w+)\)", text)) - {"uid"}      # `uid` is the definition parameter and the verified uid
        self.assertLessEqual(arguments, {"value"})                       # `value` = the uid the identity service verified (recovery)
        self.assertGreaterEqual(text.count("_mint("), 5)
        for forbidden in ("creds.uid", "fields[\"uid\"]", "request.headers.get(\"x-uid", "json()[\"uid\"]"):
            self.assertNotIn(forbidden, text)


class RepositoryState(unittest.TestCase):
    def test_no_tracked_file_is_modified_or_staged(self):
        # Narrowed on approval (local encrypted-auth Flutter integration): ONLY the files in approved_integration may differ from HEAD.
        changed = set(git("diff", "--name-only").splitlines())
        self.assertEqual(changed - approved_integration.APPROVED_TRACKED_CHANGES - approved_integration.APPROVED_BACKEND_MODIFIED, set())
        self.assertEqual(git("diff", "--cached", "--name-only"), "")

    def test_nothing_outside_the_poc_folder_is_untracked_or_changed(self):
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO)), [])

    def test_no_branches_or_stashes_were_added(self):
        self.assertEqual(len(git("branch", "--list").splitlines()), 11)
        self.assertEqual(len(git("stash", "list").splitlines()), 2)
        self.assertEqual(len(git("worktree", "list").splitlines()), 1)

    def test_the_flutter_app_and_production_backend_are_untouched(self):
        # Narrowed on approval (encrypted-auth gateway integrated into backend/): ONLY the 14 approved backend paths may differ, with the verified content;
        # every other backend change is still reported.
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO, "backend")), [])
        self.assertEqual(approved_integration.backend_content_problems(REPO), [])
        self.assertEqual(approved_integration.unapproved(approved_integration.porcelain(REPO, "frontend")), [])


if __name__ == "__main__":
    unittest.main()
