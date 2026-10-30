"""Offline unit tests for the encrypted-credential envelope. Dummy accounts and in-memory test keys only; no network, no files.

Credential values are never put into assertion messages: comparisons use assertTrue(a == b) so a failure cannot print them.
"""
import contextlib
import io
import json
import secrets
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import envelope as E  # noqa: E402
from nonces import NonceStore  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402

EMAIL = "poc-user-1@example.invalid"


class Clock:
    def __init__(self, start=1_800_000_000):
        self.t = start

    def __call__(self):
        return self.t


def flip(value, index=10):
    """Changes one character of a base64url string without breaking its canonical form."""
    return value[:index] + ("A" if value[index] != "A" else "B") + value[index + 1:]


class EnvelopeTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv = generate_test_private_key()
        cls.priv2 = generate_test_private_key()
        cls.kid, cls.kid2 = kid_for(cls.priv.public_key()), kid_for(cls.priv2.public_key())

    def setUp(self):
        self.clock = Clock()
        self.nonces = NonceStore(ttl_seconds=120, clock=self.clock)
        self.ring = E.KeyRing()
        self.ring.add(self.kid, self.priv)
        self.password = "Pw-" + secrets.token_urlsafe(12)
        self.new_password = "Np-" + secrets.token_urlsafe(12)

    def fields(self, op):
        f = {"email": EMAIL, "password": self.password}
        if op == "change_password":
            f["new_password"] = self.new_password
        return f

    def make(self, op="login", fields=None, kid=None, key=None, nonce=None, ts=None, issue_for=None):
        nonce = nonce or self.nonces.issue(issue_for or op)
        return E.seal_credentials((key or self.priv).public_key(), kid or self.kid, op, nonce,
                                  fields or self.fields(op), now=self.clock() if ts is None else ts)

    def open(self, raw, op="login"):
        return E.open_credentials(raw, self.ring, self.nonces, op)

    def assertCode(self, code, fn, *a, **kw):
        with self.assertRaises(E.EnvelopeError) as ctx:
            fn(*a, **kw)
        self.assertTrue(ctx.exception.code == code, "expected %s, got %s" % (code, ctx.exception.code))
        return ctx.exception


class RoundTrip(EnvelopeTestBase):
    def test_round_trip_every_operation(self):
        for op in E.OPERATIONS:
            with self.subTest(op=op):
                creds = self.open(self.make(op), op)
                self.assertTrue(creds.operation == op and creds.kid == self.kid)
                self.assertTrue(creds.email == EMAIL and creds.password == self.password)
                self.assertTrue(creds.new_password == (self.new_password if op == "change_password" else None))

    def test_ciphertext_is_unreadable_and_randomised(self):
        a, b = self.make("login", nonce=self.nonces.issue("login")), self.make("login")
        for raw in (a, b):
            self.assertFalse(EMAIL in raw or self.password in raw)
            self.assertFalse(EMAIL.encode().hex() in raw)
        self.assertTrue(json.loads(a)["ct"] != json.loads(b)["ct"])

    def test_unicode_password_round_trip(self):
        self.password = "pässwörd-日本語-" + secrets.token_urlsafe(6)
        self.assertTrue(self.open(self.make("login")).password == self.password)

    def test_credentials_repr_hides_values(self):
        creds = self.open(self.make("change_password"), "change_password")
        for text in (repr(creds), str(creds), "%s" % (creds,)):
            self.assertFalse(EMAIL in text or self.password in text or self.new_password in text)


class Tampering(EnvelopeTestBase):
    def tamper(self, raw, name, value):
        d = json.loads(raw)
        d[name] = value
        return json.dumps(d)

    def test_modified_ciphertext_parts_fail_and_do_not_burn_the_nonce(self):
        raw = self.make("login")
        d = json.loads(raw)
        for name in ("ct", "ek", "iv"):
            with self.subTest(field=name):
                self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, name, flip(d[name])))
        # a forged request must not consume the genuine one
        self.assertTrue(self.open(raw).email == EMAIL)

    def test_authenticated_metadata_is_bound(self):
        raw = self.make("login")
        d = json.loads(raw)
        other = self.nonces.issue("login")
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "nonce", other))     # swap in another valid nonce
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ts", d["ts"] + 1))  # timestamp is authenticated
        self.assertTrue(self.open(raw).email == EMAIL)

    def test_truncated_and_extended_ciphertext(self):
        raw = self.make("login")
        d = json.loads(raw)
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ct", d["ct"][:-8]))
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ct", d["ct"] + "AAAA"))


class Replay(EnvelopeTestBase):
    def test_second_use_is_rejected(self):
        raw = self.make("login")
        self.open(raw)
        self.assertCode(E.REPLAY, self.open, raw)

    def test_concurrent_replay_has_exactly_one_winner(self):
        raw, results, gate = self.make("login"), [], threading.Barrier(16)

        def attempt():
            gate.wait()
            try:
                self.open(raw)
                results.append("ok")
            except E.EnvelopeError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=attempt) for _ in range(16)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertTrue(results.count("ok") == 1 and results.count(E.REPLAY) == 15, str(sorted(results)))

    def test_nonce_never_issued(self):
        fake = E.b64e(secrets.token_bytes(32))
        self.assertCode(E.UNKNOWN_NONCE, self.open, self.make("login", nonce=fake))

    def test_nonce_issued_for_another_operation(self):
        self.assertCode(E.WRONG_OPERATION, self.open, self.make("login", issue_for="change_password"))


class Expiry(EnvelopeTestBase):
    def test_nonce_expires(self):
        raw = self.make("login")
        self.clock.t += 121
        self.assertCode(E.EXPIRED, self.open, raw)

    def test_old_timestamp_is_rejected(self):
        self.assertCode(E.EXPIRED, self.open, self.make("login", ts=self.clock() - 301))

    def test_far_future_timestamp_is_rejected(self):
        self.assertCode(E.EXPIRED, self.open, self.make("login", ts=self.clock() + 301))

    def test_clock_skew_inside_the_window_is_accepted(self):
        self.assertTrue(self.open(self.make("login", ts=self.clock() - 60)).email == EMAIL)

    def test_used_nonce_is_reported_as_replay_until_purged(self):
        raw = self.make("login")
        self.open(raw)
        self.clock.t += 100
        self.assertCode(E.REPLAY, self.open, raw)


class WrongOperation(EnvelopeTestBase):
    def test_every_cross_operation_is_rejected(self):
        for sealed in E.OPERATIONS:
            for presented in E.OPERATIONS:
                if sealed == presented:
                    continue
                with self.subTest(sealed=sealed, presented=presented):
                    self.assertCode(E.WRONG_OPERATION, self.open, self.make(sealed), presented)

    def test_rewriting_the_operation_label_does_not_help(self):
        raw = json.loads(self.make("login"))
        raw["op"] = "change_password"
        # the nonce was issued for `login`
        self.assertCode(E.WRONG_OPERATION, self.open, json.dumps(raw), "change_password")

    def test_unknown_operation(self):
        raw = json.loads(self.make("login"))
        raw["op"] = "delete_account"
        self.assertCode(E.MALFORMED, self.open, json.dumps(raw), "login")
        self.assertCode(E.WRONG_OPERATION, self.nonces.issue, "nope")


class DefenceInDepth(EnvelopeTestBase):
    """Bindings that the nonce store would also catch, tested alone so a regression in the cipher layer is not masked."""

    class AnyNonce:
        """Accepts every nonce: removes the nonce store's own operation binding from the picture."""
        def now(self):
            return 1_800_000_000
        def inspect(self, nonce, operation):
            pass
        def consume(self, nonce, operation):
            pass

    def test_operation_is_authenticated_by_the_cipher_itself(self):
        nonce = self.nonces.issue("login")
        d = json.loads(self.make("login", nonce=nonce))
        d["op"] = "signup"
        self.assertCode(E.DECRYPT_FAILED, E.open_credentials, json.dumps(d), self.ring, self.AnyNonce(), "signup")

    def test_key_id_is_authenticated_by_the_cipher_itself(self):
        self.ring.add(self.kid2, self.priv)  # same private key under a second id: only the AAD can tell them apart
        d = json.loads(self.make("login"))
        d["kid"] = self.kid2
        self.assertCode(E.DECRYPT_FAILED, E.open_credentials, json.dumps(d), self.ring, self.AnyNonce(), "login")

    def test_rsa_wrap_is_bound_to_the_context_label(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, key, iv = self.nonces.issue("login"), AESGCM.generate_key(bit_length=256), b"\x01" * 12
        ts = self.clock()
        pt = json.dumps(self.fields("login"), separators=(",", ":")).encode()
        ct = AESGCM(key).encrypt(iv, pt, E._aad(self.kid, "login", nonce, ts))
        no_label = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
        ek = self.priv.public_key().encrypt(key, no_label)
        raw = json.dumps({"v": 1, "kid": self.kid, "op": "login", "nonce": nonce, "ts": ts,
                          "ek": E.b64e(ek), "iv": E.b64e(iv), "ct": E.b64e(ct)})
        self.assertCode(E.DECRYPT_FAILED, self.open, raw)
        # control: the same construction with the real label is accepted, so the failure above is caused by the label alone
        ek_ok = self.priv.public_key().encrypt(key, E._oaep())
        raw_ok = json.dumps({**json.loads(raw), "ek": E.b64e(ek_ok)})
        self.assertTrue(self.open(raw_ok).email == EMAIL)


class KeyIds(EnvelopeTestBase):
    def test_unknown_key_id(self):
        self.assertCode(E.UNKNOWN_KEY, self.open, self.make("login", kid=self.kid2, key=self.priv2))

    def test_known_id_with_the_wrong_key(self):
        # envelope sealed to key 2 but labelled with key 1's id
        self.assertCode(E.DECRYPT_FAILED, self.open, self.make("login", kid=self.kid, key=self.priv2))

    def test_rotation_overlap_and_retirement(self):
        self.ring.add(self.kid2, self.priv2, active=False)
        old, new = self.make("login"), self.make("login", kid=self.kid2, key=self.priv2)
        self.assertTrue(self.open(old).kid == self.kid and self.open(new).kid == self.kid2)
        self.ring.remove(self.kid)
        self.assertCode(E.UNKNOWN_KEY, self.open, self.make("login"))

    def test_weak_keys_are_refused(self):
        weak = generate_test_private_key(2048)
        with self.assertRaises(ValueError):
            self.ring.add("weak", weak)
        self.assertCode(E.MALFORMED, E.seal_credentials, weak.public_key(), "weak", "login", self.nonces.issue("login"),
                        self.fields("login"))


class Malformed(EnvelopeTestBase):
    def test_not_json_or_wrong_json_type(self):
        for raw in ("", "not json", "[]", "null", "42", '"x"', "{", b"\xff\xfe", "{}"):
            with self.subTest(raw=str(raw)[:12]):
                self.assertCode(E.MALFORMED, self.open, raw)

    def test_non_text_input(self):
        for raw in (None, 5, {}, object()):
            self.assertCode(E.MALFORMED, self.open, raw)

    def test_missing_extra_and_duplicate_fields(self):
        d = json.loads(self.make("login"))
        for name in list(d):
            with self.subTest(missing=name):
                m = dict(d)
                del m[name]
                self.assertCode(E.MALFORMED, self.open, json.dumps(m))
        self.assertCode(E.MALFORMED, self.open, json.dumps({**d, "extra": "x"}))
        dup = json.dumps(d)[:-1] + ',"v":1}'
        self.assertCode(E.MALFORMED, self.open, dup)

    def test_wrong_types_and_values(self):
        d = json.loads(self.make("login"))
        cases = {"v": ["1", None, True, 1.0], "kid": [1, None, "bad kid!", "", "x" * 65], "op": [1, None, ["login"]],
                 "nonce": [1, None, "short", d["nonce"] + "A"], "ts": ["1", None, 1.5, True, -1, 2 ** 40],
                 "ek": [1, None, "!!", d["ek"][:-1] + "="], "iv": [1, None, "AAAA", "!!!!"], "ct": [1, None, "", "AA"]}
        for name, values in cases.items():
            for value in values:
                with self.subTest(field=name, value=repr(value)[:20]):
                    m = dict(d)
                    m[name] = value
                    with self.assertRaises(E.EnvelopeError):
                        self.open(json.dumps(m))

    def test_unsupported_version(self):
        d = json.loads(self.make("login"))
        d["v"] = 2
        self.assertCode(E.UNSUPPORTED_VERSION, self.open, json.dumps(d))

    def test_non_finite_numbers_and_deep_nesting(self):
        d = json.loads(self.make("login"))
        compact = json.dumps(d, separators=(",", ":"))
        self.assertTrue('"ts":%d' % d["ts"] in compact)
        self.assertCode(E.MALFORMED, self.open, compact.replace('"ts":%d' % d["ts"], '"ts":NaN'))
        self.assertCode(E.MALFORMED, self.open, "[" * 4000 + "]" * 4000)

    def test_bad_plaintext_schemas(self):
        for fields in ({"email": EMAIL}, {"email": EMAIL, "password": "x", "extra": "y"}, {"email": 1, "password": "x"},
                       {"email": "no-at-sign", "password": "x"}, {"email": EMAIL, "password": ""},
                       {"email": EMAIL, "password": "a\x00b"}, {"email": EMAIL, "password": "a\nb"}):
            with self.subTest(fields=sorted(map(str, fields))):
                self.assertCode(E.MALFORMED, E.seal_credentials, self.priv.public_key(), self.kid, "login",
                                self.nonces.issue("login"), fields)
        self.assertCode(E.MALFORMED, E.seal_credentials, self.priv.public_key(), self.kid, "login",
                        self.nonces.issue("login"), {"email": EMAIL, "password": "x", "new_password": "y"})

    def test_server_rejects_a_bad_plaintext_that_the_client_sealer_would_refuse(self):
        for plaintext in (b"not json", b"[]", b'{"email":"a@b","password":"x","extra":"y"}', b'{"email":"nope","password":"x"}'):
            with self.subTest(plaintext=plaintext[:12]):
                raw = E._seal_bytes(self.priv.public_key(), self.kid, "login", self.nonces.issue("login"), self.clock(), plaintext)
                self.assertCode(E.MALFORMED, self.open, raw)


class Oversized(EnvelopeTestBase):
    def test_envelope_over_the_limit(self):
        self.assertCode(E.TOO_LARGE, self.open, "x" * (E.MAX_ENVELOPE_BYTES + 1))
        self.assertCode(E.TOO_LARGE, self.open, b"x" * (E.MAX_ENVELOPE_BYTES + 1))

    def test_client_refuses_long_fields(self):
        for f in ({"email": "a" * 250 + "@x.io", "password": "x"}, {"email": EMAIL, "password": "p" * (E.MAX_PASSWORD_CHARS + 1)}):
            self.assertCode(E.TOO_LARGE, E.seal_credentials, self.priv.public_key(), self.kid, "login",
                            self.nonces.issue("login"), f)

    def test_field_at_the_limit_is_accepted(self):
        self.password = "p" * E.MAX_PASSWORD_CHARS
        self.assertTrue(self.open(self.make("login")).password == self.password)

    def test_server_refuses_an_oversized_plaintext_even_if_the_client_did_not(self):
        big = json.dumps({"email": EMAIL, "password": "p" * (E.MAX_PLAINTEXT_BYTES + 100)}).encode()
        raw = E._seal_bytes(self.priv.public_key(), self.kid, "login", self.nonces.issue("login"), self.clock(), big)
        self.assertCode(E.TOO_LARGE, self.open, raw)

    def test_plaintext_at_exactly_the_cap_is_not_a_size_error(self):
        pad = E.MAX_PLAINTEXT_BYTES - len(json.dumps({"email": EMAIL, "password": ""}, separators=(",", ":")))
        plaintext = json.dumps({"email": EMAIL, "password": "p" * pad}, separators=(",", ":")).encode()
        self.assertTrue(len(plaintext) == E.MAX_PLAINTEXT_BYTES)
        raw = E._seal_bytes(self.priv.public_key(), self.kid, "login", self.nonces.issue("login"), self.clock(), plaintext)
        self.assertCode(E.TOO_LARGE, self.open, raw)  # the password field itself exceeds MAX_PASSWORD_CHARS

    def test_nonce_store_capacity(self):
        store = NonceStore(max_outstanding=3, clock=self.clock)
        for _ in range(3):
            store.issue("login")
        self.assertCode(E.CAPACITY, store.issue, "login")
        self.clock.t += 400  # everything expired and purged
        store.issue("login")


class Leakage(EnvelopeTestBase):
    def test_nothing_is_printed_and_errors_carry_no_values(self):
        out, err = io.StringIO(), io.StringIO()
        texts = []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.open(self.make("login"))
            raw = self.make("login")
            d = json.loads(raw)
            d["ct"] = flip(d["ct"])
            for bad in (json.dumps(d), raw + "x", "garbage", self.make("signup")):
                try:
                    self.open(bad)
                except E.EnvelopeError as exc:
                    texts += [str(exc), repr(exc), exc.code, repr(exc.args)]
        self.assertTrue(out.getvalue() == "" and err.getvalue() == "")
        self.assertTrue(texts)
        for text in texts:
            self.assertFalse(EMAIL in text or self.password in text)
            self.assertTrue(text in {E.DECRYPT_FAILED, E.MALFORMED, E.WRONG_OPERATION, "EnvelopeError('%s')" % E.DECRYPT_FAILED,
                                     "EnvelopeError('%s')" % E.MALFORMED, "EnvelopeError('%s')" % E.WRONG_OPERATION,
                                     repr((E.DECRYPT_FAILED,)), repr((E.MALFORMED,)), repr((E.WRONG_OPERATION,))})


if __name__ == "__main__":
    unittest.main()
