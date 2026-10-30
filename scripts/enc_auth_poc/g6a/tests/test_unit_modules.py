"""Host-side unit tests of the PURE backend modules (no database, no Firebase, no network):

  * enc_auth_policy.py is compared with the REAL Flutter PasswordValidator, run through the Dart SDK on the same inputs;
  * enc_auth_envelope.py (the server port) is compared with the G1 reference on hundreds of valid and mutated envelopes.
Dummy values only. Credential values never appear in assertion messages.
"""
import asyncio
import json
import random
import string
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import G6A, load_module  # noqa: E402  (also puts the PoC folder on sys.path)

import envelope as G1  # noqa: E402  the G1 reference implementation
from nonces import NonceStore as G1NonceStore  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

DART = r"D:\flutter sdk\flutter\bin\cache\dart-sdk\bin\dart.exe"
SPECIALS = "!@#$%^&*()_+-=[]{};:'\"\\|,.<>/?~`"


def dart_verdicts(values):
    if not Path(DART).exists():
        raise unittest.SkipTest("dart not available")
    res = subprocess.run([DART, str(G6A / "harness" / "password_probe.dart")], input=json.dumps(values) + "\n", capture_output=True,
                         text=True, encoding="utf-8", timeout=300, env={**__import__("os").environ, "DART_SUPPRESS_ANALYTICS": "true"})
    assert res.returncode == 0, res.stderr[-300:]
    return json.loads(res.stdout)


class PolicyParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_module("app/services/enc_auth_policy.py", "g6a_policy")

    def test_hand_picked_boundary_and_unicode_cases_match_the_real_validator(self):
        cases = ["", "a", "Aa1!", "Aa1!xyz", "Aa1!xyzw", "Aa1!xyz" + "w" * 300, "aA1!aA1", "aA1!aA1a",
                 # UTF-16 length: an emoji is 2 code units in Dart, 1 code point in Python
                 "\U0001F600\U0001F600\U0001F600\U0001F600", "A\U0001F600\U0001F600\U0001F600a1!", "Aa1!\U0001F600\U0001F600",
                 "Aa1!\U0001F600", "Ab1\U0001F600\U0001F600\U0001F600!",
                 # non-ASCII letters and digits do NOT satisfy the ASCII classes
                 "\u00c9\u00e9\u00e9\u00e9!\u00e9\u00e9\u00e9", "\u0661\u0662\u0663Aa!xxxx", "\uff21\uff41\uff11!\uff41\uff41\uff41\uff41",
                 "ABCDEFG1!", "abcdefg1!", "ABCDefgh!", "ABCDefgh1", "ABCDefgh1 ", "ABCDefg1 ", "Abcdefg1\u20ac", "Abcdefg1\u00a1",
                 # every special character, alone
                 *["Abcdefg1" + ch for ch in SPECIALS],
                 # characters that are not in the list
                 "Abcdefg1 ", "Abcdefg1\t", "Abcdefg1\u00a7", "Abcdefg1\u2022", "Abcdefg1\u00d7",
                 # common-password list (all lowercase, so earlier rules already reject them) and case/Unicode lowering
                 "password", "PASSWORD", "Password123", "password123!", "Password123!", "12345678", "qwerty", "letmein", "WELCOME",
                 "I\u0307LOVEYOU", "\u0130lOveyou1!", "Passw0rd!", "P@ssw0rd", "p@ssw0rd1", "Welcome1!", "admin", "ADMIN123!",
                 "Aa1!" * 64, "A" * 256, "a" * 256 + "A1!", "\U0001F512" * 140 + "Aa1!"]
        got, want = [self.policy.validate(v) for v in cases], dart_verdicts(cases)
        mismatches = [i for i, (g, w) in enumerate(zip(got, want)) if g != w]
        self.assertEqual(mismatches, [], "policy differs from the Dart validator for case indexes")
        self.assertGreater(sum(1 for w in want if w is None), 15)   # the set exercises both outcomes
        self.assertGreater(sum(1 for w in want if w is not None), 30)

    def test_2500_random_strings_match_the_real_validator(self):
        rng = random.Random(20261101)
        pools = [string.ascii_letters + string.digits + SPECIALS, string.ascii_lowercase, string.ascii_uppercase + string.digits,
                 string.ascii_letters + "\u00e9\u00c9\u65e5\u0661\U0001F600\U0001F512 \t", SPECIALS + string.digits, "aA1!"]
        cases = []
        for _ in range(2500):
            pool = rng.choice(pools)
            cases.append("".join(rng.choice(pool) for _ in range(rng.choice([0, 1, 4, 6, 7, 8, 9, 12, 40]))))
        want = dart_verdicts(cases)
        mismatches = [i for i, c in enumerate(cases) if self.policy.validate(c) != want[i]]
        self.assertEqual(mismatches, [], "policy differs from the Dart validator for random case indexes")
        self.assertGreater(sum(1 for w in want if w is None), 50)

    def test_messages_and_helper(self):
        self.assertEqual(self.policy.validate(None), "Password is required")
        self.assertTrue(self.policy.meets_policy("Abcdefg1!"))
        self.assertFalse(self.policy.meets_policy("abcdefg1!"))


class MemoryNonces:
    """Async in-memory nonce store with the G1 semantics (the database store is tested in the integration tests)."""

    def __init__(self, clock):
        self.clock, self.ttl, self.rows = clock, 120, {}

    def now(self):
        return int(self.clock())

    async def issue(self, op):
        import secrets
        nonce = G1.b64e(secrets.token_bytes(32))
        self.rows[nonce] = [op, self.now() + self.ttl, False]
        return nonce

    def _check(self, nonce, op, env):
        row = self.rows.get(nonce)
        if row is None:
            raise env.EnvelopeError(env.UNKNOWN_NONCE)
        if row[0] != op:
            raise env.EnvelopeError(env.WRONG_OPERATION)
        if row[2]:
            raise env.EnvelopeError(env.REPLAY)
        if self.now() > row[1]:
            raise env.EnvelopeError(env.EXPIRED)

    async def inspect(self, nonce, op):
        self._check(nonce, op, self.env)

    async def consume(self, nonce, op):
        self._check(nonce, op, self.env)
        self.rows[nonce][2] = True


class EnvelopePortParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = load_module("app/services/enc_auth_envelope.py", "g6a_envelope")
        cls.priv = generate_test_private_key()
        cls.kid = kid_for(cls.priv.public_key())

    def test_protocol_constants_equal_the_g1_reference(self):
        for name in ("VERSION", "OPERATIONS", "MAX_ENVELOPE_BYTES", "MAX_PLAINTEXT_BYTES", "MAX_EMAIL_CHARS", "MAX_PASSWORD_CHARS",
                     "MAX_TS_SKEW_SECONDS", "RSA_MIN_BITS", "AES_KEY_BYTES", "IV_BYTES", "TAG_BYTES", "_OAEP_LABEL", "_AAD_MAGIC"):
            self.assertEqual(getattr(self.port, name), getattr(G1, name), name)
        self.assertEqual(self.port._ENVELOPE_FIELDS, G1._ENVELOPE_FIELDS)
        self.assertEqual(self.port._aad("k1", "login", "A" * 43, 7), G1._aad("k1", "login", "A" * 43, 7))

    def test_error_codes_equal_the_g1_reference(self):
        for name in ("MALFORMED", "TOO_LARGE", "UNSUPPORTED_VERSION", "UNKNOWN_KEY", "WRONG_OPERATION", "EXPIRED", "UNKNOWN_NONCE",
                     "REPLAY", "DECRYPT_FAILED", "CAPACITY"):
            self.assertEqual(getattr(self.port, name), getattr(G1, name), name)

    def test_outcomes_match_the_g1_reference_on_valid_and_mutated_envelopes(self):
        rng = random.Random(99)
        clock = lambda: 1_800_000_000  # noqa: E731
        g1_ring = G1.KeyRing()
        g1_ring.add(self.kid, self.priv)
        port_ring = self.port.KeyRing()
        port_ring.add(self.kid, self.priv)
        other = generate_test_private_key()
        agreements = {"ok": 0, "rejected": 0}

        async def run():
            for i in range(700):
                op = rng.choice(list(G1.OPERATIONS))
                fields = {"email": "dummy%d@example.invalid" % i, "password": "Pw-%d-%s" % (i, "x" * rng.randint(1, 30))}
                if op == "change_password":
                    fields["new_password"] = "New-%d" % i
                g1_nonces = G1NonceStore(clock=clock)
                port_nonces = MemoryNonces(clock)
                port_nonces.env = self.port
                nonce = g1_nonces.issue(op)
                port_nonces.rows[nonce] = [op, port_nonces.now() + 120, False]
                key = rng.choice([self.priv, self.priv, self.priv, other])
                raw = G1.seal_credentials(key.public_key(), self.kid, op, nonce, fields, now=clock())
                expected_op = rng.choice([op, op, op, "login"])
                mutation = rng.randrange(14)
                d = json.loads(raw)
                if mutation == 1:
                    d["ct"] = d["ct"][:10] + ("A" if d["ct"][10] != "A" else "B") + d["ct"][11:]
                elif mutation == 2:
                    d["ts"] += rng.choice([1, -1, 400, -400])
                elif mutation == 3:
                    d["kid"] = rng.choice(["nope", self.kid + "x"])
                elif mutation == 4:
                    d["op"] = rng.choice(list(G1.OPERATIONS))
                elif mutation == 5:
                    d.pop(rng.choice(list(d)))
                elif mutation == 6:
                    d["extra"] = 1
                elif mutation == 7:
                    d["v"] = rng.choice([2, "1", None, True])
                elif mutation == 8:
                    d["nonce"] = rng.choice(["short", G1.b64e(bytes(32)), nonce + "A"])
                elif mutation == 9:
                    d["iv"] = rng.choice(["AAAA", "", "!!!!", d["iv"] + "A"])
                elif mutation == 10:
                    d["ek"] = d["ek"][:-4]
                elif mutation == 11:
                    d["ts"] = rng.choice(["1", 1.5, -1, 2 ** 40])
                elif mutation == 12:
                    raw = rng.choice(["", "{", "[]", "null", raw + " " * 9000, raw.replace('"v"', '"v"', 1) + "x"])
                    d = None
                if d is not None:
                    raw = json.dumps(d)
                try:
                    g1_result = ("ok", dict(G1.open_credentials(raw, g1_ring, g1_nonces, expected_op).fields))
                except G1.EnvelopeError as exc:
                    g1_result = ("err", exc.code)
                try:
                    port_result = ("ok", dict((await self.port.open_credentials(raw, port_ring, port_nonces, expected_op)).fields))
                except self.port.EnvelopeError as exc:
                    port_result = ("err", exc.code)
                self.assertEqual(g1_result, port_result, "outcome differs for case %d (mutation %d)" % (i, mutation))
                agreements["ok" if g1_result[0] == "ok" else "rejected"] += 1

        asyncio.run(run())
        self.assertGreater(agreements["ok"], 50)
        self.assertGreater(agreements["rejected"], 200)

    def test_keyring_loading_never_echoes_key_material(self):
        pem = self.priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        ring = self.port.load_keyring(json.dumps({self.kid: pem, "second": pem}))
        self.assertEqual(ring.kids(), sorted([self.kid, "second"]))
        for bad in ("", "{}", "[]", "not json", json.dumps({"k": "not a pem"}), json.dumps({"bad kid": pem}), json.dumps({"k": 5}),
                    json.dumps({"k": pem.replace("MII", "MIX", 1)})):
            with self.assertRaises(self.port.KeyConfigError) as ctx:
                self.port.load_keyring(bad)
            self.assertNotIn(pem[40:80], str(ctx.exception))
        weak = generate_test_private_key(2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                               serialization.NoEncryption()).decode()
        with self.assertRaises(self.port.KeyConfigError):
            self.port.load_keyring(json.dumps({"weak": weak}))


if __name__ == "__main__":
    unittest.main()
