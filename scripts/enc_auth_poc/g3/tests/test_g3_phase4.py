"""Phase 4 offline tests: displaying KNOWN Firebase error codes safely, and the custom-token claim/signing-input harness (synthetic key only)."""
import json
import unittest

import _support as S
from _support import ALLOWED, b64, make_pki, sign_rs256
import g3_compat as compat
import g3_custom_token as ct
import g3_guard as guard
import g3_live_errors as errors
import g3_live_run as live
import g3_live_transport as lt
import g3_rest_client as rc
import g3_token_inspect as ti
import g3_verify as gv
from test_g3_phase2_live import FakeFirebase, SIGN_IN
from test_g3_phase3 import firebase_error

NOW = 1_800_000_000
SA = "g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com"


class ErrorCodeDisplay(unittest.TestCase):
    def test_every_known_code_is_displayed_as_is(self):
        self.assertGreaterEqual(len(rc.KNOWN_ERROR_CODES), 9)
        for code in rc.KNOWN_ERROR_CODES:
            self.assertEqual(live._fixed(code), code)

    def test_anything_else_in_upper_case_or_free_text_is_masked(self):
        for value in ("INVALID_LOGIN_CREDENTIALS : secret", "invalid_login_credentials ", "INVALID_PASSWORD\n", "API_KEY_INVALID", "SOME_NEW_CODE",
                      "Wrong Password for g3-user@example.invalid", "INVALID_LOGIN_CREDENTIALS" + "X", " INVALID_PASSWORD", "AIza" + "A" * 35, None, 3.5, b"X"):
            self.assertEqual(live._fixed(value), "other", repr(value))

    def test_existing_lowercase_display_rules_are_unchanged(self):
        for value, shown in ((True, "True"), (5, "5"), ("wrong_credentials", "wrong_credentials"), ("unrecognized", "unrecognized"), ("-", "-"), ("x" * 41, "other"),
                             ("Mixed", "other"), ("two words", "other")):
            self.assertEqual(live._fixed(value), shown)

    def test_a_live_style_run_now_prints_the_exact_known_code(self):
        errors._RAN.clear()
        lines, values = [], iter(["g3-user@example.invalid", S.synthetic_api_key()])
        server = FakeFirebase({SIGN_IN: firebase_error("INVALID_LOGIN_CREDENTIALS")})
        code = errors.run(interactive=True, getpass_fn=lambda p: next(values), input_fn=lambda p: ALLOWED, out=lines.append,
                          transport_factory=lambda a: lt.LiveTransport(a, server.factory), env={})
        out = "\n".join(lines)
        self.assertEqual(code, 0, out)
        self.assertIn("1 wrong password: outcome=wrong_credentials http_status=400 error_code=INVALID_LOGIN_CREDENTIALS", out)
        self.assertIn("2 nonexistent e-mail: outcome=wrong_credentials http_status=400 error_code=INVALID_LOGIN_CREDENTIALS", out)
        self.assertIn("case1_error_code=INVALID_LOGIN_CREDENTIALS case2_error_code=INVALID_LOGIN_CREDENTIALS", out)
        self.assertIn("enumeration_protection_effective=True", out)

    def test_an_unprotected_project_shows_the_two_different_codes(self):
        errors._RAN.clear()
        answers = iter([firebase_error("INVALID_PASSWORD"), firebase_error("EMAIL_NOT_FOUND")])

        class Sequenced(dict):
            def __getitem__(self, key):
                return next(answers)
        lines, values = [], iter(["g3-user@example.invalid", S.synthetic_api_key()])
        server = FakeFirebase(Sequenced())
        errors.run(interactive=True, getpass_fn=lambda p: next(values), input_fn=lambda p: ALLOWED, out=lines.append,
                   transport_factory=lambda a: lt.LiveTransport(a, server.factory), env={})
        out = "\n".join(lines)
        self.assertIn("case1_error_code=INVALID_PASSWORD case2_error_code=EMAIL_NOT_FOUND", out)
        self.assertIn("enumeration_protection_effective=False", out)

    def test_unknown_firebase_text_is_never_displayed(self):
        errors._RAN.clear()
        lines, values = [], iter(["g3-user@example.invalid", S.synthetic_api_key()])
        server = FakeFirebase({SIGN_IN: firebase_error("SOME_NEW_CODE : g3-user@example.invalid secret")})
        self.assertEqual(errors.run(interactive=True, getpass_fn=lambda p: next(values), input_fn=lambda p: ALLOWED, out=lines.append,
                                    transport_factory=lambda a: lt.LiveTransport(a, server.factory), env={}), 1)
        out = "\n".join(lines)
        self.assertIn("error_code=unrecognized", out)
        for word in ("SOME_NEW_CODE", "secret", "g3-user"):
            self.assertNotIn(word, out)


class ClaimBuilding(unittest.TestCase):
    def test_valid_claims_have_the_firebase_shape(self):
        claims = ct.build_claims(SA, "synthetic-uid-1", NOW)
        self.assertEqual(claims, {"iss": SA, "sub": SA, "aud": ct.AUDIENCE, "iat": NOW, "exp": NOW + 3600, "uid": "synthetic-uid-1"})
        self.assertEqual(ct.build_claims(SA, "u", NOW, lifetime=60)["exp"], NOW + 60)
        self.assertEqual(ct.build_claims(SA, "u", NOW, developer_claims={"role": "test"})["claims"], {"role": "test"})

    def test_service_accounts_outside_the_test_project_are_refused(self):
        for bad in ("g3-signer@epicverse-prod.iam.gserviceaccount.com", "g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com.evil.example",
                    "g3-signer@epicverse-g3-auth-test.gserviceaccount.com", "firebase-adminsdk@appspot.gserviceaccount.com", "a@b@epicverse-g3-auth-test.iam.gserviceaccount.com",
                    "@epicverse-g3-auth-test.iam.gserviceaccount.com", "G3-Signer@epicverse-g3-auth-test.iam.gserviceaccount.com",
                    "x" * 70 + "@epicverse-g3-auth-test.iam.gserviceaccount.com", "g3 signer@epicverse-g3-auth-test.iam.gserviceaccount.com", "", None, 5):
            with self.assertRaises(ct.CustomTokenError, msg=repr(bad)):
                ct.build_claims(bad, "u", NOW)

    def test_bad_uids_clocks_and_lifetimes_are_refused(self):
        for uid in ("", None, 5, "x" * 129, "has space", "tab\t", "é", "line\nbreak"):
            with self.assertRaises(ct.CustomTokenError, msg=repr(uid)):
                ct.build_claims(SA, uid, NOW)
        for now in (None, 0, -1, 1.5, True, "1"):
            with self.assertRaises(ct.CustomTokenError):
                ct.build_claims(SA, "u", now)
        for lifetime in (0, 59, 3601, 10 ** 6, True, 1.5, None):
            with self.assertRaises(ct.CustomTokenError):
                ct.build_claims(SA, "u", NOW, lifetime=lifetime)

    def test_developer_claims_cannot_override_reserved_names_or_inject_email(self):
        for name in ("email", "email_verified", "aud", "iss", "sub", "uid", "exp", "iat", "auth_time", "firebase", "nonce"):
            with self.assertRaises(ct.CustomTokenError, msg=name):
                ct.build_claims(SA, "u", NOW, developer_claims={name: "x"})
        for bad in ("text", ["a"], {1: "x"}, {"big": "x" * 900}):
            with self.assertRaises(ct.CustomTokenError):
                ct.build_claims(SA, "u", NOW, developer_claims=bad)

    def test_errors_contain_no_values(self):
        with self.assertRaises(ct.CustomTokenError) as raised:
            ct.build_claims("secret-account@epicverse-prod.iam.gserviceaccount.com", "secret-uid", NOW)
        self.assertNotIn("secret", str(raised.exception))


class SigningInput(unittest.TestCase):
    KEY, CERT, KID = make_pki()

    class Synthetic(ct.Signer):
        def __init__(self, key):
            self.key, self.signed = key, []

        def sign(self, signing_input):
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            self.signed.append(signing_input)
            return self.key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    def test_the_signing_input_is_header_dot_payload_rs256(self):
        data = ct.signing_input(ct.build_claims(SA, "u", NOW))
        header, payload = data.decode().split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        import base64
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(header))), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(payload)))["uid"], "u")
        self.assertNotIn(b"=", data)

    def test_a_token_assembled_with_a_synthetic_signer_verifies_and_is_not_an_id_token(self):
        signer = self.Synthetic(self.KEY)
        token = ct.mint_with(signer, SA, "synthetic-uid-1", NOW)
        self.assertEqual(len(signer.signed), 1)
        h, p, s = token.split(".")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        import base64
        self.KEY.public_key().verify(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), (h + "." + p).encode(), padding.PKCS1v15(), hashes.SHA256())
        for check in (lambda: ti.decode_unverified(token, ALLOWED, NOW),):          # a custom token must never be mistaken for a Firebase ID token
            with self.assertRaises(ti.TokenInspectionError):
                check()
        with self.assertRaises(ti.TokenInspectionError):
            gv.FirebaseSignatureVerifier({self.KID: self.CERT}).verify(token, ALLOWED, NOW)

    def test_invalid_signers_and_signatures_are_refused(self):
        for bad in (None, object(), lambda d: b"x" * 256, "signer"):
            with self.assertRaises(ct.CustomTokenError):
                ct.mint_with(bad, SA, "u", NOW)
        data = ct.signing_input(ct.build_claims(SA, "u", NOW))
        for sig in (b"", b"short", b"x" * 2000, "text", None):
            with self.assertRaises(ct.CustomTokenError):
                ct.assemble(data, sig)
        for data_bad in (b"no-dot", b"a.b.c", "text", None):
            with self.assertRaises(ct.CustomTokenError):
                ct.assemble(data_bad, b"x" * 256)

    def test_the_base_signer_has_no_implementation_and_no_real_signer_exists(self):
        with self.assertRaises(NotImplementedError):
            ct.Signer().sign(b"x")
        text = __import__("pathlib").Path(ct.__file__).read_text(encoding="utf-8")
        for word in ("private_key", "serviceAccounts", "signBlob", "import requests", "socket", "http.client", "import http", "urlopen", "firebase_admin",
                     "load_pem_private_key", "import os", "environ"):
            self.assertNotIn(word, text)


class CustomTokenSessionExpectations(unittest.TestCase):
    """What a verified custom-token session and its refresh should look like when the live phase eventually runs (SYNTHETIC tokens)."""
    KEY, CERT, KID = make_pki()
    VERIFIER = gv.FirebaseSignatureVerifier({KID: CERT})
    HEADER = {"alg": "RS256", "kid": KID, "typ": "JWT"}

    def session(self, **changes):
        base = {"email": ..., "email_verified": ..., "firebase": {"identities": {}, "sign_in_provider": "custom"}}
        base.update(changes)
        return self.VERIFIER.verify(sign_rs256(self.KEY, self.HEADER, **base), ALLOWED, NOW)

    def test_the_expected_real_firebase_shape_is_recognised(self):
        first, refreshed = self.session(), self.session(iat=NOW - 30, exp=NOW + 3570)
        obs = compat.observe(first, "synthetic-uid-1")
        self.assertEqual((obs["email_claim_present"], obs["email_verified"], obs["sign_in_provider"], obs["uid_matches_expected"], obs["auth_time_is_recent"]),
                         (False, "absent", "custom", True, True))
        self.assertTrue(all(compat.compare_sessions(first, refreshed).values()))
        needs = compat.backend_assumptions(obs)
        self.assertTrue(needs["g6_helper_needed"] and not needs["sync_user_reads_email_claim_directly"])

    def test_deviations_are_detected(self):
        first = self.session()
        self.assertFalse(compat.compare_sessions(first, self.session(sub="synthetic-uid-2", user_id="synthetic-uid-2"))["uid_unchanged"])
        self.assertFalse(compat.compare_sessions(first, self.session(auth_time=NOW - 5, iat=NOW))["auth_time_preserved"])
        with_email = self.session(email="g3-user@example.invalid", email_verified=True)
        self.assertTrue(compat.observe(with_email, "synthetic-uid-1")["email_claim_present"])
        self.assertEqual(compat.observe(with_email, "synthetic-uid-1")["email_verified"], "true")
        self.assertFalse(compat.compare_sessions(first, with_email)["email_claim_presence_unchanged"])


class NoNetworkOrSigning(unittest.TestCase):
    def test_no_network_was_attempted(self):
        self.assertEqual(S.ATTEMPTS, [])

    def test_no_custom_token_or_iam_endpoint_is_approved(self):
        self.assertEqual(len(guard.APPROVED_REQUESTS), 3)
        for url in ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/x:signBlob",
                    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"):
            with self.assertRaises(guard.GuardError):
                guard.validate_url(url)


if __name__ == "__main__":
    unittest.main()
