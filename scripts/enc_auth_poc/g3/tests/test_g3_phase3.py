"""Phase 3 offline tests: approval budgets, the two-request error-behaviour run (fake Firebase behind the REAL live transport), sanitised
error codes, and the custom-token observation helpers (synthetic signed tokens). No socket is ever opened."""
import json
import unittest

import _support as S
from _support import ALLOWED, make_pki, sign_rs256
import g3_compat as compat
import g3_guard as guard
import g3_live_errors as errors
import g3_live_transport as lt
import g3_rest_client as rc
import g3_verify as gv
from test_g3_phase2_live import FakeFirebase, SIGN_IN, REFRESH, CERTS, request

NOW = 1_800_000_000


def firebase_error(code, status=400):
    return (status, json.dumps({"error": {"code": status, "message": code}}).encode())


class Budgets(unittest.TestCase):
    def test_the_error_budget_allows_exactly_two_sign_ins_and_nothing_else(self):
        a = guard.grant_live_approval(ALLOWED, ALLOWED, True, guard.ERROR_TEST_BUDGET)
        a.consume(*SIGN_IN)
        a.consume(*SIGN_IN)
        for pair in (SIGN_IN, REFRESH, CERTS):
            with self.assertRaises(guard.LiveModeDisabled):
                a.consume(*pair)
        self.assertEqual(a.used, 2)

    def test_budgets_cannot_name_unapproved_requests_or_exceed_three_uses(self):
        for bad in ({}, {("POST", "https://oauth2.googleapis.com/token"): 1}, {("GET", guard.SIGN_IN_URL): 1}, {SIGN_IN: 0}, {SIGN_IN: 4},
                    {SIGN_IN: True}, {SIGN_IN: "2"}, {SIGN_IN: -1}):
            with self.assertRaises(guard.LiveModeDisabled, msg=str(bad)):
                guard.grant_live_approval(ALLOWED, ALLOWED, True, bad)

    def test_the_default_budget_is_still_each_request_once(self):
        a = guard.grant_live_approval(ALLOWED, ALLOWED, True)
        for pair in (SIGN_IN, REFRESH, CERTS):
            a.consume(*pair)
        with self.assertRaises(guard.LiveModeDisabled):
            a.consume(*SIGN_IN)

    def test_a_third_sign_in_is_refused_before_a_connection_exists(self):
        server = FakeFirebase({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        transport = lt.LiveTransport(guard.grant_live_approval(ALLOWED, ALLOWED, True, guard.ERROR_TEST_BUDGET), server.factory)
        transport(request("POST", guard.SIGN_IN_URL))
        transport(request("POST", guard.SIGN_IN_URL))
        with self.assertRaises(guard.LiveModeDisabled):
            transport(request("POST", guard.SIGN_IN_URL))
        self.assertEqual(len(server.connections), 2)


class SanitisedCodes(unittest.TestCase):
    def sign_in(self, answer):
        server = FakeFirebase({SIGN_IN: answer})
        client = rc.FirebaseRestClient(guard.load_config(ALLOWED, S.synthetic_api_key(), env={}), lt.LiveTransport(
            guard.grant_live_approval(ALLOWED, ALLOWED, True, guard.ERROR_TEST_BUDGET), server.factory))
        return client.sign_in(S.synthetic_email(), S.synthetic_password())

    def test_known_codes_are_reported_with_status(self):
        for code, outcome in (("INVALID_LOGIN_CREDENTIALS", rc.WRONG_CREDENTIALS), ("INVALID_PASSWORD", rc.WRONG_CREDENTIALS),
                              ("EMAIL_NOT_FOUND", rc.UNKNOWN_EMAIL), ("USER_DISABLED", rc.USER_DISABLED), ("TOO_MANY_ATTEMPTS_TRY_LATER", rc.THROTTLED)):
            d = self.sign_in(firebase_error(code)).detail()
            self.assertEqual((d["outcome"], d["http_status"], d["error_code"]), (outcome, 400, code))

    def test_unknown_codes_and_free_text_are_never_echoed(self):
        for message in ("SOME_NEW_CODE : secret detail", "API_KEY_INVALID", "lower case text with secret", ""):
            d = self.sign_in(firebase_error(message, 400)).detail()
            self.assertIn(d["error_code"], {"unrecognized", "-"})
            self.assertNotIn("secret", json.dumps(d))
        self.assertEqual(self.sign_in(firebase_error("X", 503)).detail()["error_code"], "-")

    def test_diagnostic_keeps_its_original_fixed_shape(self):
        self.assertEqual(set(self.sign_in(firebase_error("INVALID_LOGIN_CREDENTIALS")).diagnostic()), {"operation", "outcome", "http_class", "cause"})


class ErrorRun(unittest.TestCase):
    def setUp(self):
        errors._RAN.clear()
        self.lines, self.email, self.api_key = [], "g3-user@example.invalid", S.synthetic_api_key()

    def execute(self, answers, typed=ALLOWED, interactive=True, env=None, key=None):
        server = FakeFirebase(answers)
        values = iter([self.email, self.api_key if key is None else key])
        prompts = []

        def input_fn(prompt):
            prompts.append(prompt)
            if isinstance(typed, BaseException):
                raise typed
            return typed

        code = errors.run(interactive=interactive, getpass_fn=lambda p: prompts.append(p) or next(values), input_fn=input_fn, out=self.lines.append,
                          transport_factory=lambda a: lt.LiveTransport(a, server.factory), env={} if env is None else env)
        self.server, self.prompts = server, prompts
        return code

    def text(self):
        return "\n".join(self.lines)

    def bodies(self):
        return [json.loads(c.sent[2]) for c in self.server.connections]

    def test_protected_project_gives_identical_answers_and_exactly_two_requests(self):
        code = self.execute({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        out = self.text()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.server.connections), 2)
        self.assertTrue(all(c.host == "identitytoolkit.googleapis.com" and c.sent[0] == "POST" for c in self.server.connections))
        self.assertIn("identical_status_and_error_code=True", out)
        self.assertIn("enumeration_protection_effective=True", out)
        self.assertIn("requests used: 2 of 2", out)

    def test_unprotected_project_is_detected_as_different(self):
        answers = iter([firebase_error("INVALID_PASSWORD"), firebase_error("EMAIL_NOT_FOUND")])

        class Sequenced(dict):
            def __getitem__(self, key):
                return next(answers)
        code = self.execute(Sequenced())
        out = self.text()
        self.assertEqual(code, 0, out)
        self.assertIn("identical_status_and_error_code=False", out)
        self.assertIn("enumeration_protection_effective=False", out)

    def test_the_first_request_uses_the_dummy_email_and_the_second_a_nonexistent_invalid_address(self):
        self.execute({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        first, second = self.bodies()
        self.assertEqual(first["email"], self.email)
        self.assertTrue(second["email"].startswith("g3-nonexistent-") and second["email"].endswith("@example.invalid"))
        self.assertNotEqual(first["password"], second["password"])
        self.assertTrue(first["password"].startswith("Wp-") and len(first["password"]) > 12)

    def test_no_real_password_is_ever_asked_for(self):
        self.execute({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        self.assertEqual(len([p for p in self.prompts if "assword" in p]), 0)
        self.assertEqual(len(self.prompts), 3)                                           # e-mail, API key (hidden), project-id confirmation

    def test_an_unexpected_first_answer_stops_before_the_second_request(self):
        for answer in ((200, json.dumps({"idToken": "x", "refreshToken": "y", "expiresIn": "3600", "localId": "u"}).encode()), firebase_error("PERMISSION_DENIED", 403),
                       firebase_error("X", 503), (302, b""), firebase_error("USER_DISABLED"), firebase_error("TOO_MANY_ATTEMPTS_TRY_LATER"), (200, b"junk")):
            errors._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.execute({SIGN_IN: answer}), 1)
            self.assertEqual(len(self.server.connections), 1, answer[0])
            self.assertIn("case 2 is NOT sent", self.text())

    def test_an_unexpected_second_answer_is_reported_as_failure(self):
        answers = iter([firebase_error("INVALID_LOGIN_CREDENTIALS"), firebase_error("API_KEY_INVALID", 400)])

        class Sequenced(dict):
            def __getitem__(self, key):
                return next(answers)
        self.assertEqual(self.execute(Sequenced()), 1)
        self.assertEqual(len(self.server.connections), 2)

    def test_cancellation_non_interactive_and_bad_input_send_nothing(self):
        answers = {SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")}
        for typed in ("", "no", "yes", ALLOWED.upper(), "epicverse-prod", EOFError(), KeyboardInterrupt()):
            errors._RAN.clear()
            self.assertEqual(self.execute(answers, typed=typed), 3, repr(typed))
            self.assertEqual(self.server.connections, [])
        errors._RAN.clear()
        self.assertEqual(self.execute(answers, interactive=False), 2)
        self.assertEqual((self.prompts, self.server.connections), ([], []))
        for key in ("", "not-a-key"):
            errors._RAN.clear()
            self.assertEqual(self.execute(answers, key=key), 2)
            self.assertEqual(self.server.connections, [])
        for env in ({"GOOGLE_APPLICATION_CREDENTIALS": "x"}, {"GOOGLE_CLOUD_PROJECT": "epicverse-prod"}, {"FIREBASE_CONFIG": "{}"}):
            errors._RAN.clear()
            self.assertEqual(self.execute(answers, env=env), 2)
            self.assertEqual((self.prompts, self.server.connections), ([], []))

    def test_a_second_run_in_one_process_is_refused(self):
        answers = {SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")}
        self.assertEqual(self.execute(answers), 0)
        self.assertEqual(self.execute(answers), 2)

    def test_nothing_secret_is_printed(self):
        self.execute({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        out = self.text()
        sent = [body["password"] for body in self.bodies()] + [body["email"] for body in self.bodies()]
        for secret in [self.email, self.api_key] + sent:
            self.assertNotIn(secret, out)

    def test_unexpected_exceptions_are_reported_without_details(self):
        server = FakeFirebase({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        server.factory = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SECRET-detail"))
        values = iter([self.email, self.api_key])
        code = errors.run(interactive=True, getpass_fn=lambda p: next(values), input_fn=lambda p: ALLOWED, out=self.lines.append,
                          transport_factory=lambda a: lt.LiveTransport(a, server.factory), env={})
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET-detail", self.text())


class CustomTokenObservations(unittest.TestCase):
    KEY, CERT, KID = make_pki()
    VERIFIER = gv.FirebaseSignatureVerifier({KID: CERT})
    HEADER = {"alg": "RS256", "kid": KID, "typ": "JWT"}

    def verified(self, **changes):
        return self.VERIFIER.verify(sign_rs256(self.KEY, self.HEADER, **changes), ALLOWED, NOW)

    def custom(self, **changes):
        base = {"email": ..., "email_verified": ..., "firebase": {"identities": {}, "sign_in_provider": "custom"}}
        base.update(changes)
        return self.verified(**base)

    def test_a_custom_token_session_without_the_email_claim_flags_the_helper_requirement(self):
        obs = compat.observe(self.custom(), "synthetic-uid-1")
        self.assertEqual((obs["email_claim_present"], obs["email_verified"], obs["sign_in_provider"], obs["uid_matches_expected"]),
                         (False, "absent", "custom", True))
        needs = compat.backend_assumptions(obs)
        self.assertTrue(needs["g6_helper_needed"])
        self.assertFalse(needs["sync_user_reads_email_claim_directly"])
        self.assertTrue(needs["provider_is_custom"])

    def test_a_password_session_with_the_claim_satisfies_the_direct_reads(self):
        obs = compat.observe(self.verified(), "synthetic-uid-1")
        needs = compat.backend_assumptions(obs)
        self.assertEqual((obs["email_claim_present"], obs["email_verified"], obs["sign_in_provider"]), (True, "false", "password"))
        self.assertTrue(needs["sync_user_reads_email_claim_directly"] and not needs["g6_helper_needed"])

    def test_uid_mismatch_and_staleness_are_detected(self):
        self.assertFalse(compat.observe(self.custom(), "someone-else")["uid_matches_expected"])
        stale = compat.observe(self.custom(iat=NOW - 60, auth_time=NOW - 4000, exp=NOW + 3540), "synthetic-uid-1")
        self.assertFalse(stale["auth_time_is_recent"])
        self.assertFalse(compat.backend_assumptions(stale)["recent_login_fresh"])

    def test_refresh_comparison(self):
        a, b = self.custom(), self.custom(iat=NOW - 30, exp=NOW + 3570)
        self.assertEqual(compat.compare_sessions(a, b), {"uid_unchanged": True, "auth_time_preserved": True, "iat_not_earlier": True,
                                                         "provider_unchanged": True, "email_claim_presence_unchanged": True})
        changed = compat.compare_sessions(a, self.custom(auth_time=NOW - 10, iat=NOW - 5))
        self.assertFalse(changed["auth_time_preserved"])

    def test_only_signature_verified_claims_can_be_observed(self):
        import g3_token_inspect as ti
        unverified = ti.decode_unverified(sign_rs256(self.KEY, self.HEADER), ALLOWED, NOW)
        for bad in (unverified, {"uid": "x"}, None):
            with self.assertRaises(TypeError):
                compat.observe(bad, "x")

    def test_observations_contain_no_values(self):
        obs = compat.observe(self.verified(), "synthetic-uid-1")
        text = json.dumps(obs) + json.dumps(compat.backend_assumptions(obs))
        self.assertNotIn("synthetic-uid-1", text)
        self.assertNotIn("example.invalid", text)

    def test_no_custom_token_operation_is_approved_anywhere(self):
        self.assertFalse([u for _, u in guard.APPROVED_REQUESTS if "CustomToken" in u or "custom" in u.lower()])
        with self.assertRaises(guard.GuardError):
            guard.validate_url("https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken")


class NoNetwork(unittest.TestCase):
    def test_no_socket_attempt(self):
        self.assertEqual(S.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
