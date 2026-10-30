"""Phase 2 signature-verification tests (offline, synthetic RSA keys and certificates generated in memory)."""
import datetime
import hashlib
import hmac
import json
import unittest

import _support as S
from _support import ALLOWED, b64, make_pki, sign_rs256
import g3_token_inspect as ti
import g3_verify as gv

NOW = 1_800_000_000
KEY, CERT, KID = make_pki()
OTHER_KEY, OTHER_CERT, _ = make_pki()
HEADER = {"alg": "RS256", "kid": KID, "typ": "JWT"}
VERIFIER = gv.FirebaseSignatureVerifier({KID: CERT})


def reason_of(token, verifier=VERIFIER, project=ALLOWED, now=NOW):
    try:
        verifier.verify(token, project, now)
    except (ti.TokenInspectionError, gv.CertificateRejected) as rejected:
        return rejected.reason
    raise AssertionError("token was accepted")


class Accepted(unittest.TestCase):
    def test_a_correctly_signed_token_is_verified_and_summarised_safely(self):
        verified = VERIFIER.verify(sign_rs256(KEY, HEADER), ALLOWED, NOW)
        self.assertIs(verified.verified, True)
        self.assertEqual(verified.claims.uid, "synthetic-uid-1")
        summary = gv.safe_summary(verified)
        self.assertEqual(summary, {"email_claim_present": True, "email_verified": "false", "sign_in_provider": "password", "exp_after_iat": True,
                                   "auth_time_present": True})
        shown = repr(summary) + repr(verified.claims)
        self.assertNotIn("synthetic-uid-1", shown)
        self.assertNotIn("g3-user@example.invalid", shown)

    def test_missing_email_claims_are_reported_as_absent(self):
        verified = VERIFIER.verify(sign_rs256(KEY, HEADER, email=..., email_verified=...), ALLOWED, NOW)
        summary = gv.safe_summary(verified)
        self.assertEqual((summary["email_claim_present"], summary["email_verified"]), (False, "absent"))


class SignatureChecks(unittest.TestCase):
    def test_invalid_signatures_are_rejected(self):
        good = sign_rs256(KEY, HEADER)
        h, p, s = good.split(".")
        flipped = s[:-4] + ("AAAA" if not s.endswith("AAAA") else "BBBB")
        tampered_payload = h + "." + b64(b'{"sub":"someone-else"}') + "." + s
        for bad in (h + "." + p + "." + flipped, tampered_payload, sign_rs256(OTHER_KEY, HEADER), h + "." + p + "." + b64(b"\x00" * 256),
                    h + "." + p + "." + b64(b"short")):
            self.assertEqual(reason_of(bad), "signature_invalid")

    def test_the_payload_is_not_even_parsed_before_the_signature_is_verified(self):
        # an unparsable payload AND a bad signature: the answer is about the signature, so the payload was never examined
        self.assertEqual(reason_of(sign_rs256(OTHER_KEY, HEADER, raw_payload=b"{{not json")), "signature_invalid")
        self.assertEqual(reason_of(sign_rs256(OTHER_KEY, HEADER, project="epicverse-prod")), "signature_invalid")

    def test_unknown_or_missing_kid_is_rejected(self):
        self.assertEqual(reason_of(sign_rs256(KEY, {"alg": "RS256", "kid": "other-kid", "typ": "JWT"})), "kid_unknown")
        self.assertEqual(reason_of(sign_rs256(KEY, {"alg": "RS256", "typ": "JWT"})), "kid_missing")
        self.assertEqual(reason_of(sign_rs256(KEY, {"alg": "RS256", "kid": "", "typ": "JWT"})), "kid_missing")
        self.assertEqual(reason_of(sign_rs256(KEY, {"alg": "RS256", "kid": 5, "typ": "JWT"})), "kid_missing")

    def test_algorithm_confusion_and_unsupported_algorithms_are_rejected(self):
        payload = sign_rs256(KEY, HEADER).split(".")[1]
        for alg in ("none", "None", "HS256", "HS512", "RS384", "RS512", "PS256", "ES256", "EdDSA", "", None, ["RS256"]):
            h = b64(json.dumps({"alg": alg, "kid": KID, "typ": "JWT"}).encode())
            self.assertEqual(reason_of(h + "." + payload + "."), "header_algorithm", alg)                    # unsigned
            mac = hmac.new(CERT.encode(), (h + "." + payload).encode(), hashlib.sha256).digest()             # HS256 keyed with the certificate text
            self.assertEqual(reason_of(h + "." + payload + "." + b64(mac)), "header_algorithm", alg)

    def test_malformed_tokens_are_rejected(self):
        good = sign_rs256(KEY, HEADER)
        for bad in (None, "", 5, b"x", "a.b", "a.b.c.d", "..", "!!!.!!!.!!!", good + ".extra", " " + good, "x" * 5000):
            self.assertIn(reason_of(bad), {"token_missing", "token_malformed", "header_malformed", "signature_malformed", "token_too_large"})

    def test_a_bad_clock_is_rejected(self):
        for now in (None, 1.5, "x", True):
            self.assertEqual(reason_of(sign_rs256(KEY, HEADER), now=now), "clock_invalid")


class ClaimChecksAfterSignature(unittest.TestCase):
    def test_validly_signed_but_wrong_claims_are_rejected(self):
        cases = {"issuer_mismatch": {"iss": "https://securetoken.google.com/epicverse-prod"}, "audience_mismatch": {"aud": "epicverse-prod"},
                 "token_expired": {"iat": NOW - 7200, "exp": NOW - 3600, "auth_time": NOW - 7300}, "auth_time_invalid": {"auth_time": "yesterday"},
                 "subject_invalid": {"sub": ""}, "time_claims_invalid": {"exp": NOW - 100}}
        for why, changes in cases.items():
            self.assertEqual(reason_of(sign_rs256(KEY, HEADER, **changes)), why)

    def test_the_expected_project_must_be_the_allowed_one(self):
        with self.assertRaises(Exception):
            VERIFIER.verify(sign_rs256(KEY, HEADER, project="epicverse-prod"), "epicverse-prod", NOW)

    def test_audience_and_issuer_must_equal_the_test_project_exactly(self):
        self.assertEqual(reason_of(sign_rs256(KEY, HEADER, aud=[ALLOWED])), "audience_mismatch")
        self.assertEqual(reason_of(sign_rs256(KEY, HEADER, iss="https://securetoken.google.com/" + ALLOWED + "/")), "issuer_mismatch")


class Certificates(unittest.TestCase):
    def test_malformed_certificates_are_rejected(self):
        for pem in ("", "garbage", CERT[:-40], CERT.replace("BEGIN", "BEGIN X"), "x" * 9000, 5, CERT[:100] + "AAAA" + CERT[104:]):      # truncated, mislabelled, oversized, wrong type, corrupted DER
            self.assertEqual(reason_of(sign_rs256(KEY, HEADER), gv.FirebaseSignatureVerifier({KID: pem})), "certificate_malformed")

    def test_expired_not_yet_valid_weak_and_non_rsa_certificates_are_rejected(self):
        utc = datetime.timezone.utc
        _, expired, _ = make_pki(not_before=datetime.datetime(2020, 1, 1, tzinfo=utc), not_after=datetime.datetime(2021, 1, 1, tzinfo=utc))
        _, future, _ = make_pki(not_before=datetime.datetime(2035, 1, 1, tzinfo=utc), not_after=datetime.datetime(2036, 1, 1, tzinfo=utc))
        weak_key, weak, _ = make_pki(bits=1024)
        _, ec_cert, _ = make_pki(curve=True)
        for pem, why in ((expired, "certificate_not_valid_now"), (future, "certificate_not_valid_now"), (weak, "certificate_key_unacceptable"),
                         (ec_cert, "certificate_key_unacceptable")):
            self.assertEqual(reason_of(sign_rs256(KEY, HEADER), gv.FirebaseSignatureVerifier({KID: pem})), why)
        self.assertEqual(reason_of(sign_rs256(weak_key, HEADER), gv.FirebaseSignatureVerifier({KID: weak})), "certificate_key_unacceptable")

    def test_an_empty_or_wrong_certificate_set_cannot_build_a_verifier(self):
        for bad in ({}, None, [], "x"):
            with self.assertRaises(gv.CertificateRejected):
                gv.FirebaseSignatureVerifier(bad)

    def test_the_wrong_certificate_for_a_kid_does_not_verify(self):
        self.assertEqual(reason_of(sign_rs256(KEY, HEADER), gv.FirebaseSignatureVerifier({KID: OTHER_CERT})), "signature_invalid")

    def test_errors_do_not_contain_certificate_or_token_text(self):
        token = sign_rs256(OTHER_KEY, HEADER)
        with self.assertRaises(ti.TokenInspectionError) as raised:
            VERIFIER.verify(token, ALLOWED, NOW)
        text = str(raised.exception) + repr(raised.exception)
        self.assertNotIn(token[:20], text)
        self.assertNotIn("BEGIN", text)


class NoNetwork(unittest.TestCase):
    def test_nothing_touched_the_network(self):
        self.assertEqual(S.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
