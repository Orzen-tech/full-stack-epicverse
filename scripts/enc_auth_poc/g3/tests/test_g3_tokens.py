"""Token inspection tests (offline, SYNTHETIC tokens only). N: malformed JWT. O/P: wrong issuer/audience. Q: expired. R: invalid auth_time.
Also: decoding is never presented as verification."""
import copy
import json
import unittest

import _support as S
from _support import ALLOWED, b64, make_jwt
import g3_token_inspect as ti
from g3_guard import GuardError

NOW = 1_800_000_000


def claims(**changes):
    base = {"iss": "https://securetoken.google.com/" + ALLOWED, "aud": ALLOWED, "sub": "synthetic-uid-1", "user_id": "synthetic-uid-1",
            "iat": NOW - 60, "exp": NOW + 3540, "auth_time": NOW - 120, "firebase": {"identities": {}, "sign_in_provider": "password"}}
    base.update(changes)
    return {k: v for k, v in base.items() if v is not ...}


def reason(token, project=ALLOWED, now=NOW):
    with S_raises() as holder:
        ti.decode_unverified(token, project, now)
    return holder["reason"]


class S_raises:
    def __init__(self):
        self.holder = {}

    def __enter__(self):
        return self.holder

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError("token was accepted")
        if not issubclass(kind, (ti.TokenInspectionError, GuardError)):
            return False
        self.holder["reason"] = value.reason
        return True


class Accepted(unittest.TestCase):
    def test_a_well_formed_synthetic_token_is_decoded_but_never_marked_verified(self):
        got = ti.decode_unverified(make_jwt(), ALLOWED, NOW)
        self.assertIs(got.verified, False)
        self.assertEqual((got.uid, got.sign_in_provider, got.email, got.email_verified), ("synthetic-uid-1", "password", None, None))
        self.assertIn("verified=False", repr(got))

    def test_optional_email_claims_are_checked_for_type(self):
        got = ti.decode_unverified(make_jwt(claims(email="g3@example.invalid", email_verified=False)), ALLOWED, NOW)
        self.assertEqual((got.email, got.email_verified), ("g3@example.invalid", False))
        for bad in ({"email": 5}, {"email": ""}, {"email": "x" * 300}, {"email_verified": "true"}, {"email_verified": 1}):
            self.assertIn(reason(make_jwt(claims(**bad))), {"email_claim_invalid", "email_verified_claim_invalid"})


class MalformedTokens(unittest.TestCase):
    def test_N_malformed_tokens_are_rejected(self):
        good = make_jwt()
        h, p, s = good.split(".")
        for bad, why in ((None, "token_missing"), ("", "token_missing"), (12345, "token_missing"), (b"a.b.c", "token_missing"),
                         ("a.b", "token_malformed"), ("a.b.c.d", "token_malformed"), ("..", "header_malformed"), (h + "." + p, "token_malformed"),
                         (h + "." + p + ".", "signature_malformed"), (h + "." + p + ".!!!", "signature_malformed"),
                         ("!!!." + p + "." + s, "header_malformed"), (h + ".!!!." + s, "payload_malformed"),
                         (b64(b"not json") + "." + p + "." + s, "header_malformed"), (h + "." + b64(b"not json") + "." + s, "payload_malformed"),
                         (b64(b"[]") + "." + p + "." + s, "header_malformed"), (h + "." + b64(b"[1]") + "." + s, "payload_malformed"),
                         (b64(b"\xff\xfe") + "." + p + "." + s, "header_malformed"), (h + " ." + p + "." + s, "header_malformed"),
                         (h + "." + p + "." + s + "\n", "signature_malformed"), (h + "\n." + p + "." + s, "header_malformed"),
                         ("Bearer " + good, "header_malformed")):
            self.assertEqual(reason(bad), why, repr(bad)[:40])

    def test_oversized_tokens_are_rejected(self):
        self.assertEqual(reason("a" * (ti.MAX_TOKEN_CHARS + 1)), "token_too_large")
        self.assertEqual(reason(make_jwt(claims(sub="u" * 5000))), "token_too_large")

    def test_duplicate_keys_in_header_or_payload_are_rejected(self):
        dup_payload = b'{"iss":"x","iss":"https://securetoken.google.com/%s"}' % ALLOWED.encode()
        self.assertEqual(reason(make_jwt(raw_payload=dup_payload)), "payload_malformed")
        header = '{"alg":"RS256","alg":"none","kid":"k"}'
        token = b64(header.encode()) + "." + make_jwt().split(".")[1] + "." + make_jwt().split(".")[2]
        self.assertEqual(reason(token), "header_malformed")

    def test_unsigned_and_symmetric_algorithms_are_rejected(self):
        for alg in ("none", "None", "HS256", "RS512", "ES256", "", None, 5):
            self.assertEqual(reason(make_jwt(header={"alg": alg, "kid": "k", "typ": "JWT"})), "header_algorithm", alg)
        self.assertEqual(reason(make_jwt(header={"kid": "k"})), "header_algorithm")
        self.assertEqual(reason(make_jwt(header={"alg": "RS256", "typ": "JWT"})), "header_fields")                       # no key id
        self.assertEqual(reason(make_jwt(header={"alg": "RS256", "kid": "k", "typ": "JWE"})), "header_fields")

    def test_an_emulator_style_unsigned_token_is_rejected(self):
        header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        token = header + "." + b64(json.dumps(claims()).encode()) + "."
        self.assertIn(reason(token), {"signature_malformed", "header_algorithm"})


class IssuerAudienceProject(unittest.TestCase):
    def test_O_wrong_issuer_is_rejected(self):
        for iss in ("https://securetoken.google.com/epicverse-prod", "https://securetoken.google.com/", "https://accounts.google.com",
                    "http://securetoken.google.com/" + ALLOWED, "https://securetoken.google.com/" + ALLOWED + "/", "https://evil.example/" + ALLOWED,
                    ALLOWED, None, 5, ...):
            self.assertEqual(reason(make_jwt(claims(iss=iss))), "issuer_mismatch", iss)

    def test_P_wrong_audience_is_rejected(self):
        for aud in ("epicverse-prod", "", ALLOWED + "x", [ALLOWED], [ALLOWED, "other"], None, 5, ...):
            self.assertEqual(reason(make_jwt(claims(aud=aud))), "audience_mismatch", aud)

    def test_a_token_from_another_project_is_rejected_even_if_self_consistent(self):
        other = make_jwt(project="epicverse-prod")
        self.assertEqual(reason(other), "issuer_mismatch")

    def test_the_expected_project_itself_must_be_the_allowed_one(self):
        self.assertEqual(reason(make_jwt(project="epicverse-prod"), project="epicverse-prod"), "project_not_allowed")
        self.assertEqual(reason(make_jwt(), project=None), "project_missing")

    def test_subject_must_be_a_plain_non_empty_string_matching_user_id(self):
        for changes in ({"sub": ""}, {"sub": 5}, {"sub": ...}, {"sub": "x" * 129}, {"user_id": "different"}):
            self.assertEqual(reason(make_jwt(claims(**changes))), "subject_invalid", changes)


class TimeClaims(unittest.TestCase):
    def test_Q_an_expired_token_is_rejected(self):
        self.assertEqual(reason(make_jwt(claims(iat=NOW - 7200, exp=NOW - 3600, auth_time=NOW - 7300))), "token_expired")
        self.assertEqual(reason(make_jwt(claims(exp=NOW)), now=NOW), "token_expired")                                        # not valid AT its expiry

    def test_R_invalid_auth_time_is_rejected(self):
        for value in (..., None, "1799999880", 1799999880.5, True, False, 0, -5, NOW + 10_000, NOW - 60 + 1):
            self.assertEqual(reason(make_jwt(claims(auth_time=value))), "auth_time_invalid", value)

    def test_other_time_claims_are_validated(self):
        for changes in ({"iat": "x"}, {"iat": ...}, {"iat": True}, {"exp": ...}, {"exp": 1.5}, {"iat": 0}, {"exp": NOW - 61}, {"exp": NOW + 10 ** 7}):
            self.assertEqual(reason(make_jwt(claims(**changes))), "time_claims_invalid", changes)
        self.assertEqual(reason(make_jwt(claims(iat=NOW + 10_000, exp=NOW + 12_000, auth_time=NOW + 9_000))), "auth_time_invalid")
        self.assertEqual(reason(make_jwt(claims(iat=NOW + 1_000, exp=NOW + 4_000, auth_time=NOW + 100))), "issued_in_future")

    def test_the_clock_must_be_an_integer(self):
        for bad in (None, 1.5, "now", True):
            self.assertEqual(reason(make_jwt(), now=bad), "clock_invalid")

    def test_the_firebase_claim_is_required(self):
        for changes in ({"firebase": ...}, {"firebase": "password"}, {"firebase": {}}, {"firebase": {"sign_in_provider": ""}},
                        {"firebase": {"sign_in_provider": 5}}):
            self.assertEqual(reason(make_jwt(claims(**changes))), "firebase_claim_invalid", changes)


class DecodingIsNotVerifying(unittest.TestCase):
    def test_claims_look_valid_but_the_signature_is_random_and_still_accepted_by_decode_only(self):
        # This documents the limitation: decode_unverified cannot tell a forged signature from a real one.
        forged = make_jwt(signature=b64(b"\x00" * 64))
        got = ti.decode_unverified(forged, ALLOWED, NOW)
        self.assertIs(got.verified, False)

    def test_verified_claims_cannot_be_constructed_outside_a_verifier(self):
        got = ti.decode_unverified(make_jwt(), ALLOWED, NOW)
        for key in (None, object(), True):
            with self.assertRaises(TypeError):
                ti.VerifiedClaims(got, _key=key)
        with self.assertRaises(TypeError):
            ti.VerifiedClaims(got)

    def test_phase1_verifier_always_refuses(self):
        with self.assertRaises(ti.SignatureVerificationUnavailable):
            ti.Phase1Verifier().verify(make_jwt(), ALLOWED, NOW)
        with self.assertRaises(NotImplementedError):
            ti.SignatureVerifier().verify(make_jwt(), ALLOWED, NOW)

    def test_the_decoder_has_no_verified_true_path(self):
        self.assertIs(ti.UnverifiedClaims.verified, False)
        self.assertIs(ti.VerifiedClaims.verified, True)
        import inspect
        self.assertNotIn("VerifiedClaims", inspect.getsource(ti.decode_unverified))


class Leakage(unittest.TestCase):
    def test_errors_never_contain_any_part_of_the_token(self):
        token = make_jwt(claims(iss="https://securetoken.google.com/epicverse-prod"))
        with self.assertRaises(ti.TokenInspectionError) as raised:
            ti.decode_unverified(token, ALLOWED, NOW)
        for piece in token.split("."):
            self.assertNotIn(piece[:12], str(raised.exception) + repr(raised.exception))
        self.assertNotIn("epicverse-prod", str(raised.exception))


class NoNetwork(unittest.TestCase):
    def test_no_network_use(self):
        self.assertEqual(S.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
