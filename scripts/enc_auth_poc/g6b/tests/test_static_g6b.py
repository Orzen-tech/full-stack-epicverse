"""Static guarantees for the G6b hardening patch and the repository: logging safety, no external endpoints, safe defaults, retry scope, UID
rules, no plaintext limiter keys, and no change to any tracked file, branch or stash. Offline."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import REPO, tree_dir  # noqa: E402
import approved_integration  # noqa: E402  (the single list of approved integration changes)

NEW_MODULES = ["app/api/enc_gateway.py", "app/services/enc_auth_envelope.py", "app/services/enc_auth_identity.py",
               "app/services/enc_auth_policy.py", "app/services/enc_auth_store.py", "app/services/enc_auth_diag.py",
               "app/services/enc_auth_retry.py", "app/services/enc_auth_limits_eval.py"]


def source(rel):
    return (tree_dir() / "backend" / rel).read_text(encoding="utf-8")


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


class Logging(unittest.TestCase):
    def test_the_only_print_in_the_gateway_modules_is_the_closed_vocabulary_log_line(self):
        prints = []
        for rel in NEW_MODULES:
            for n, line in enumerate(source(rel).splitlines(), 1):
                if "print(" in line:
                    prints.append((rel, n, line.strip()))
        self.assertEqual([(r, l) for r, _, l in prints], [("app/services/enc_auth_diag.py", "print(line(op, outcome, stage, cause, milliseconds), flush=True)")])

    def test_the_schema_hook_log_prints_only_a_scrubbed_exception_text(self):
        for line in source("app/services/user_db.py").splitlines():
            if "ENC-AUTH" in line:
                self.assertEqual(line.strip(), 'print(f"[ENC-AUTH] schema unavailable: {safe_exc(e)}", flush=True)')

    def test_no_logging_module_and_no_request_values_reach_the_log_helpers(self):
        for rel in NEW_MODULES:
            text = source(rel)
            self.assertNotIn("import logging", text, rel)
        for n, line in enumerate(source("app/api/enc_gateway.py").splitlines(), 1):
            if re.search(r"diag\.(log|note)\(", line) or "_unavailable(" in line and not line.lstrip().startswith("def "):
                code_only = re.sub(r"\"[^\"]*\"", "", line)                       # fixed code strings are fine; identifiers are not
                self.assertNotRegex(code_only, r"creds|password|email|token|proof|nonce|\buid\b|\bip\b", "enc_gateway.py:%d %s" % (n, line.strip()))

    def test_the_diagnostics_module_accepts_only_codes_from_closed_sets(self):
        text = source("app/services/enc_auth_diag.py")
        self.assertIn("_stage(stage)", text)
        self.assertIn("_cause(cause)", text)
        imports = [line.strip() for line in text.splitlines() if line.startswith(("import ", "from "))]
        self.assertEqual(imports, ["import contextvars", "import secrets", "from collections import Counter"])   # standard library only

    def test_error_text_never_includes_request_values(self):
        self.assertNotRegex(source("app/services/enc_auth_envelope.py"), r"EnvelopeError\(f[\"']")
        for rel in ("app/services/enc_auth_identity.py", "app/services/enc_auth_store.py"):
            self.assertNotRegex(source(rel), r"(IdentityUnavailable|StoreUnavailable)\(f[\"']", rel)
            self.assertNotRegex(source(rel), r"raise \w+\(str\(exc\)", rel)


class RetryScope(unittest.TestCase):
    def test_retries_wrap_only_the_idempotent_reads_and_verifications(self):
        text = source("app/services/enc_auth_identity.py")
        wrapped = re.findall(r"_timed\(\"(\w+)\", lambda: _retry\(", text)
        self.assertEqual(sorted(wrapped), ["get_account", "verify_password", "verify_token"])
        for non_idempotent in ("create_user", "update_password", "revoke", "mint"):
            segment = re.search(r"_timed\(\"%s\", lambda: asyncio\.to_thread" % non_idempotent, text)
            self.assertIsNotNone(segment, non_idempotent)                           # called once, without _retry
        self.assertEqual(text.count("_retry("), 4)                                    # the definition + three call sites

    def test_retry_limits_are_bounded_by_the_module_itself(self):
        text = source("app/services/enc_auth_retry.py")
        self.assertIn("attempt >= max_retries or not is_transient(exc)", text)
        self.assertIn("> budget_ms", text)
        self.assertEqual(source("app/services/enc_auth_identity.py").count("TRANSIENT_CAUSES = frozenset("), 1)

    def test_is_transient_requires_both_the_identity_error_type_and_a_listed_cause(self):
        text = source("app/services/enc_auth_identity.py")
        self.assertIn("return isinstance(exc, IdentityUnavailable) and exc.cause in TRANSIENT_CAUSES", text)

    def test_transient_causes_do_not_include_credential_or_permission_outcomes(self):
        text = source("app/services/enc_auth_identity.py")
        line = re.search(r"TRANSIENT_CAUSES = frozenset\(\{(.*?)\}\)", text, re.S).group(1)
        for forbidden in ("http_4xx", "read_timeout", "sdk_error", "sdk_timeout", "unexpected"):
            self.assertNotIn(forbidden, line)

    def test_the_rest_error_classifier_treats_unknown_codes_as_service_errors(self):
        text = source("app/services/enc_auth_identity.py")
        self.assertIn('return "service"', text)
        self.assertNotIn("startswith(\"INVALID", text)


class UidRules(unittest.TestCase):
    def test_custom_tokens_are_minted_only_from_identity_service_uids(self):
        text = source("app/api/enc_gateway.py")
        arguments = set(re.findall(r"_mint\((\w+)\)", text)) - {"uid"}
        self.assertLessEqual(arguments, {"value"})
        self.assertGreaterEqual(text.count("_mint("), 5)
        for forbidden in ("creds.uid", "fields[\"uid\"]", "request.headers.get(\"x-uid", "json()[\"uid\"]"):
            self.assertNotIn(forbidden, text)

    def test_the_uid_comparison_for_reauth_and_change_password_is_still_there(self):
        text = source("app/api/enc_gateway.py")
        self.assertIn('if uid != caller["uid"]:', text)

    def test_claims_are_bound_after_the_identity_service_confirmed_the_account_and_before_a_token_is_minted(self):
        text = source("app/api/enc_gateway.py")
        signup = text[text.index("async def _signup"):text.index("async def _recover")]
        self.assertLess(signup.index("ident.create_user"), signup.index("store.bind_claim"))
        self.assertLess(signup.index("store.bind_claim"), signup.index("_mint(uid)"))
        recover = text[text.index("async def _recover"):text.index("async def _reauth_or_change")]
        self.assertLess(recover.index("_check_password"), recover.index("store.bind_claim"))
        self.assertLess(recover.index("store.bind_claim"), recover.index("_mint(value)"))

    def test_mark_verified_consults_the_claim_before_the_unchanged_consume_step(self):
        text = source("app/api/routes.py")
        route = text[text.index("async def mark_email_verified_route"):text.index('@router.post("/auth/email/verify-request")')]
        self.assertLess(route.index("enc_store.claim_allows"), route.index("email_verify.consume_proof"))
        self.assertIn("RESULT_INVALID", route)

    def test_the_email_routes_use_the_verified_uid_helper(self):
        text = source("app/api/routes.py")
        self.assertNotIn('email_verify.norm_email(current_user.get("email"))', text)     # no remaining direct claim reads
        self.assertGreaterEqual(text.count("await resolve_token_email(current_user)"), 4)


class LimiterRules(unittest.TestCase):
    def test_every_limiter_key_is_an_hmac_and_no_plaintext_is_written(self):
        text = source("app/services/enc_auth_store.py")
        self.assertIn("hmac.new(secret", text)
        for line in text.splitlines():
            if "INSERT INTO enc_auth_limits" in line:
                self.assertIn("key_hash", line) if "(" in line else None
        self.assertNotRegex(text, r"enc_auth_limits[^\n]*email")

    def test_the_existing_otp_limiter_is_not_imported_by_the_store(self):
        self.assertNotIn("otp_rate_limiter", source("app/services/enc_auth_store.py"))
        self.assertNotIn("otp_send_rate_limits", source("app/services/enc_auth_store.py"))


class Endpoints(unittest.TestCase):
    def test_the_only_identity_url_is_the_local_emulator_form(self):
        for rel in NEW_MODULES:
            for n, line in enumerate(source(rel).splitlines(), 1):
                if "googleapis.com" in line or "https://" in line:
                    self.assertIn("http://{host}/identitytoolkit.googleapis.com/v1", line, "%s:%d" % (rel, n))

    def test_local_only_guard_requires_emulator_demo_project_and_no_credential_file(self):
        text = source("app/services/enc_auth_identity.py")
        for needle in ("FIREBASE_AUTH_EMULATOR_HOST", 'startswith("demo-")', "GOOGLE_APPLICATION_CREDENTIALS", "trust_env=False",
                       "ENC_AUTH_ALLOWED_IDENTITY_HOSTS"):
            self.assertIn(needle, text)

    def test_the_private_sdk_app_is_created_only_after_the_local_guard_and_has_a_bounded_timeout(self):
        text = source("app/services/enc_auth_identity.py")
        self.assertIn('"httpTimeout": float(settings.ENC_AUTH_SDK_TIMEOUT_SECONDS)', text)
        for call in re.findall(r"\n(?:async )?def (\w+)\(", text):
            body = text[text.index("def %s(" % call):]
            body = body[:body.index("\n\n\n")] if "\n\n\n" in body else body
            if "_admin_app()" in body and call != "_admin_app":
                self.assertIn("local_base_url()", body, call)

    def test_new_code_has_no_other_network_clients(self):
        for rel in NEW_MODULES:
            text = source(rel)
            for word in ("import requests", "import urllib", "aiohttp", "smtplib", "socket.socket(", "create_connection", "open_connection"):
                self.assertNotIn(word, text, rel)
            if "import socket" in text:                                              # only the identity adapter, only for exception TYPES
                self.assertEqual(rel, "app/services/enc_auth_identity.py")
                self.assertEqual(re.findall(r"socket\.(\w+)", text), ["gaierror"])


class Defaults(unittest.TestCase):
    def test_safe_defaults(self):
        config = source("app/core/config.py")
        for pattern in (r"ENC_AUTH_ENABLED: bool = False", r"ENC_AUTH_REQUIRE_EMAIL_PROOF: bool = True", r'ENC_AUTH_KEYS: str = ""',
                        r'ENC_AUTH_HASH_SECRET: str = ""', r'ENC_AUTH_ALLOWED_IDENTITY_HOSTS: str = "127.0.0.1,localhost"',
                        r"ENC_AUTH_PROOF_MAX_AGE_SECONDS: int = 600", r"ENC_AUTH_PAIR_FAIL_LIMIT: int = 5", r"ENC_AUTH_IP_FAIL_LIMIT: int = 20",
                        r"ENC_AUTH_EMAIL_FAIL_LIMIT: int = 30", r"ENC_AUTH_GLOBAL_FAIL_LIMIT: int = 2000", r"ENC_AUTH_ABUSE_IP_FAIL_LIMIT: int = 2",
                        r"ENC_AUTH_RETRY_MAX: int = 2", r"ENC_AUTH_RETRY_BUDGET_MS: int = 1500"):
            self.assertRegex(config, pattern)

    def test_the_gateway_has_no_plaintext_or_downgrade_path(self):
        text = source("app/api/enc_gateway.py")
        for word in ("signInWithEmailAndPassword", "legacy", "fallback", "plaintext_ok", "ENC_AUTH_OPTIONAL", "Form(", "request.form"):
            self.assertNotIn(word, text)

    def test_the_gateway_state_still_requires_every_condition(self):
        text = source("app/api/enc_gateway.py")
        for needle in ("if not settings.ENC_AUTH_ENABLED:", "if not settings.ENC_AUTH_HASH_SECRET:", "if not ident.ready():"):
            self.assertIn(needle, text)

    def test_with_the_gateway_disabled_the_claim_check_is_a_no_op(self):
        text = source("app/services/enc_auth_store.py")
        body = text[text.index("async def claim_allows"):]
        self.assertLess(body.index("if not settings.ENC_AUTH_ENABLED:"), body.index("claim_owner"))
        self.assertIn("return True", body[:body.index("claim_owner")])


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

    def test_g6a_files_are_unchanged(self):
        import hashlib
        G6A = Path(__file__).resolve().parent.parent.parent / "g6a"
        self.assertEqual(hashlib.sha256((G6A / "enc_auth_gateway.patch").read_bytes().replace(b"\r", b"")).hexdigest(),
                         "12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98")
        self.assertEqual(hashlib.sha256((G6A / "build_tree_enc_auth.sh").read_bytes().replace(b"\r", b"")).hexdigest(),
                         "33e65232016756d6c13931391d58bf3037670ccab3c7e373d149425e38d03fbd")


if __name__ == "__main__":
    unittest.main()
