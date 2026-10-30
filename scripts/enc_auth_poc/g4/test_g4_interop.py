"""G4 cross-language tests: the Dart sealer (tool/seal_cli.dart) against the G1 Python reference (envelope.open_credentials).

Offline. A 3072-bit key pair is generated in memory per run; only its PUBLIC half is given to Dart, and nothing secret is written
to disk or printed. Dummy accounts and random dummy passwords only. Credential values never appear in assertion messages.
"""
import base64
import hashlib
import json
import random
import string
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DART, run_seal_cli  # noqa: E402  (also puts the PoC folder on sys.path)

import envelope as E  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from nonces import NonceStore  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402

NUMBER_OF_ROUND_TRIPS = 2000
P256_SPKI = ("MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE3+q07fDE2QsRsbw7YtohIlb0HmxGyO7tsBS4fqfLl1qb9er78MjXzXcKRD3ROoHL5ck/"
             "pbCubIAWLnnsWCZD6w==")


class Clock:
    def __init__(self):
        self.t = 1_800_000_000

    def __call__(self):
        return self.t


def spki_b64(public_key):
    return base64.b64encode(public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()


def flip(value, index=10):
    return value[:index] + ("A" if value[index] != "A" else "B") + value[index + 1:]


ASCII = string.ascii_letters + string.digits + "!#$%&'()*+,-./:;<=>?@[]^_`{|}~ \"\\"
BMP = "äöüßéñçøÅ日本語中文한Δπя"
ASTRAL = "\U0001F600\U0001F512\U0001F680\U0001D11E"


def random_text(rng, length, astral_max=12):
    out, astral = [], 0
    for _ in range(length):
        pick = rng.random()
        if pick < 0.7:
            out.append(rng.choice(ASCII))
        elif pick < 0.93:
            out.append(rng.choice(BMP))
        elif astral < astral_max:
            out.append(rng.choice(ASTRAL))
            astral += 1
        else:
            out.append(rng.choice(ASCII))
    return "".join(out)


def random_email(rng):
    local = "".join(rng.choice(string.ascii_lowercase + string.digits + "._-") for _ in range(rng.randint(1, 40)))
    if rng.random() < 0.15:
        local += rng.choice(BMP)
    return local + "@" + rng.choice(["example.invalid", "tést.example.invalid", "a.b.c.example.invalid"])


class InteropBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DART:
            raise unittest.SkipTest("dart is not available")
        cls.priv = generate_test_private_key()
        cls.priv_other = generate_test_private_key()
        cls.pub = cls.priv.public_key()
        cls.spki = spki_b64(cls.pub)
        cls.spki_other = spki_b64(cls.priv_other.public_key())
        cls.kid = kid_for(cls.pub)
        cls.kid2 = "second-" + cls.kid

    def setUp(self):
        self.clock = Clock()
        self.nonces = NonceStore(ttl_seconds=120, max_outstanding=100_000, clock=self.clock)
        self.ring = E.KeyRing()
        self.ring.add(self.kid, self.priv)
        self.ring.add(self.kid2, self.priv)  # same private key under another id: only the authenticated data tells them apart

    def request(self, op, fields, nonce=None, kid=None, spki=None, ts=None):
        return {"spki": self.spki if spki is None else spki, "kid": kid or self.kid, "op": op, "nonce": nonce or self.nonces.issue(op),
                "ts": self.clock() if ts is None else ts, "fields": fields}

    def seal_one(self, op="login", fields=None, **kw):
        fields = fields or self.default_fields(op)
        out, err, code = run_seal_cli([self.request(op, fields, **kw)])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("envelope", out[0], out[0])
        return out[0]["envelope"]

    @staticmethod
    def default_fields(op):
        f = {"email": "poc-user@example.invalid", "password": "G4pw-dummy-" + "x" * 8}
        if op == "change_password":
            f["new_password"] = "G4pw-dummy-new"
        return f

    def open(self, raw, op="login"):
        return E.open_credentials(raw, self.ring, self.nonces, op)

    def assertCode(self, code, fn, *a, **kw):
        with self.assertRaises(E.EnvelopeError) as ctx:
            fn(*a, **kw)
        self.assertTrue(ctx.exception.code == code, "expected %s, got %s" % (code, ctx.exception.code))


class DartToPython(InteropBase):
    def test_2000_consecutive_dart_to_python_round_trips(self):
        rng = random.Random(20261031)
        ops = list(E.OPERATIONS)
        requests, expected = [], []
        for i in range(NUMBER_OF_ROUND_TRIPS):
            op = ops[i % 4]
            fields = {"email": random_email(rng), "password": random_text(rng, rng.randint(1, 100))}
            if op == "change_password":
                fields["new_password"] = random_text(rng, rng.randint(1, 100))
            requests.append(self.request(op, fields))
            expected.append((op, fields))
        out, err, code = run_seal_cli(requests)
        self.assertEqual((code, err, len(out)), (0, "", NUMBER_OF_ROUND_TRIPS))
        ivs, eks, failures = set(), set(), 0
        for (op, fields), result, req in zip(expected, out, requests):
            if "envelope" not in result:
                failures += 1
                continue
            raw = result["envelope"]
            env = json.loads(raw)
            ivs.add(env["iv"])
            eks.add(env["ek"])
            creds = self.open(raw, op)  # raises on any interop problem
            if not (creds.operation == op and creds.kid == self.kid and dict(creds.fields) == fields):
                failures += 1
            for secret in set(fields.values()):
                variants = [secret, secret.encode().hex(), base64.b64encode(secret.encode()).decode(),
                            base64.urlsafe_b64encode(secret.encode()).decode().rstrip("="), urllib.parse.quote(secret, safe="")]
                if any(v in raw for v in variants if len(v) >= 6):
                    failures += 1
        self.assertEqual(failures, 0)
        self.assertEqual((len(ivs), len(eks)), (NUMBER_OF_ROUND_TRIPS, NUMBER_OF_ROUND_TRIPS))  # fresh randomness every time
        print("  [interop] %d Dart-sealed envelopes opened by Python, all fields equal, no plaintext, all iv/ek distinct" % NUMBER_OF_ROUND_TRIPS)

    def test_validation_parity_with_python_on_random_boundary_inputs(self):
        rng = random.Random(7)
        pools = [ASCII, ASCII + BMP, ASCII + BMP + ASTRAL, "p", ASTRAL]
        controls = ["\t", "\n", "\x00", "\x7f", "\x1f"]
        requests, expected = [], []
        for i in range(600):
            op = rng.choice(list(E.OPERATIONS) + ["bogus"] * 1)
            names = list(E.OPERATIONS.get(op, ("email", "password")))
            if rng.random() < 0.1:
                names = names[:-1] if rng.random() < 0.5 else names + ["extra"]
            rng.shuffle(names)
            fields = {}
            for name in names:
                n = rng.choice([0, 1, 5, 100, 150, 170, 253, 254, 255, 256, 257, 300])
                pool = rng.choice(pools)
                text = "".join(rng.choice(pool) for _ in range(n))
                if name == "email":
                    text = (text[:max(n - 5, 0)] + "@x.io") if rng.random() < 0.8 else text
                if text and rng.random() < 0.07:
                    k = rng.randrange(len(text))
                    text = text[:k] + rng.choice(controls) + text[k + 1:]
                fields[name] = text
            req = self.request(op if op in E.OPERATIONS else "login", fields)
            req["op"] = op
            requests.append(req)
            try:
                E.seal_credentials(self.pub, self.kid, op, req["nonce"], dict(fields), now=req["ts"])
                expected.append("OK")
            except E.EnvelopeError as exc:
                expected.append(exc.code)
        out, err, code = run_seal_cli(requests)
        self.assertEqual((code, err, len(out)), (0, "", 600))
        mismatches = [i for i, (res, want) in enumerate(zip(out, expected)) if ("OK" if "envelope" in res else res.get("error")) != want]
        self.assertEqual(mismatches, [], "parity mismatch at request indexes")
        ok = expected.count("OK")
        for res, req, want in zip(out, requests, expected):
            if want == "OK":
                self.assertTrue(dict(self.open(res["envelope"], req["op"]).fields) == req["fields"])
        print("  [interop] 600 random boundary inputs: Dart and Python agree on every outcome (%d OK, %d refused)" % (ok, 600 - ok))
        self.assertGreater(ok, 40)
        self.assertGreater(600 - ok, 150)

    def test_error_output_contains_only_codes(self):
        secret = "G4pw-leak-" + "z" * 12
        bad = [{"email": "no-at-sign", "password": secret}, {"email": "a@b.io", "password": secret + "\n"},
               {"email": "a@b.io", "password": secret * 30}]
        out, err, code = run_seal_cli([self.request("login", f) for f in bad])
        self.assertEqual((code, err), (0, ""))
        for res in out:
            self.assertEqual(list(res), ["error"])
            self.assertIn(res["error"], {"MALFORMED", "TOO_LARGE"})
        self.assertNotIn(secret, json.dumps(out))

    def test_weak_and_foreign_keys_are_refused_by_dart(self):
        weak = spki_b64(generate_test_private_key(2048).public_key())
        for spki in (weak, P256_SPKI, "AAAA", ""):
            out, err, code = run_seal_cli([self.request("login", self.default_fields("login"), spki=spki)])
            self.assertEqual(err, "")
            self.assertIn(out[0].get("error"), {"MALFORMED", "INTERNAL"}, out[0])
            self.assertNotIn("envelope", out[0])


class NegativeOnDartEnvelopes(InteropBase):
    def tamper(self, raw, name, value):
        d = json.loads(raw)
        d[name] = value
        return json.dumps(d)

    def test_tampered_parts_fail_and_the_genuine_envelope_still_opens(self):
        raw = self.seal_one("login")
        d = json.loads(raw)
        for name in ("ct", "ek", "iv"):
            self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, name, flip(d[name])))
        # truncate / extend the ciphertext BYTES and re-encode, so the base64url text stays canonical whatever the length
        ct = base64.urlsafe_b64decode(d["ct"] + "=" * (-len(d["ct"]) % 4))
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ct", E.b64e(ct[:-6])))
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ct", E.b64e(ct + b"\x00\x01\x02")))
        other = self.nonces.issue("login")
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "nonce", other))
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "ts", d["ts"] + 1))
        self.assertTrue(self.open(raw).email == "poc-user@example.invalid")

    def test_key_id_is_authenticated(self):
        raw = self.seal_one("login")
        self.assertCode(E.DECRYPT_FAILED, self.open, self.tamper(raw, "kid", self.kid2))
        self.assertCode(E.UNKNOWN_KEY, self.open, self.tamper(raw, "kid", "no-such-key"))

    def test_envelope_sealed_to_another_key_is_rejected(self):
        raw = self.seal_one("login", spki=self.spki_other)
        self.assertCode(E.DECRYPT_FAILED, self.open, raw)

    def test_wrong_operation(self):
        raw = self.seal_one("login")
        for presented in ("signup", "reauth", "change_password"):
            self.assertCode(E.WRONG_OPERATION, self.open, raw, presented)
        self.assertCode(E.WRONG_OPERATION, self.open, self.tamper(raw, "op", "change_password"), "change_password")
        # operation bound by the cipher itself (nonce store bypassed)
        class AnyNonce:
            def now(self):
                return self_clock()
            def inspect(self, nonce, operation):
                pass
            def consume(self, nonce, operation):
                pass
        self_clock = self.clock
        self.assertCode(E.DECRYPT_FAILED, E.open_credentials, self.tamper(raw, "op", "signup"), self.ring, AnyNonce(), "signup")

    def test_replay_expiry_and_unknown_nonce(self):
        raw = self.seal_one("login")
        self.open(raw)
        self.assertCode(E.REPLAY, self.open, raw)
        late = self.seal_one("login")
        self.clock.t += 121
        self.assertCode(E.EXPIRED, self.open, late)
        self.assertCode(E.UNKNOWN_NONCE, self.open, self.seal_one("login", nonce=E.b64e(bytes(32))))
        self.clock.t = 1_800_000_000
        self.assertCode(E.EXPIRED, self.open, self.seal_one("login", ts=self.clock() - 301))
        self.assertCode(E.EXPIRED, self.open, self.seal_one("login", ts=self.clock() + 301))
        self.assertTrue(self.open(self.seal_one("login", ts=self.clock() - 60)).email)

    def test_malformed_and_oversized_envelopes(self):
        raw = self.seal_one("login")
        d = json.loads(raw)
        self.assertCode(E.TOO_LARGE, self.open, raw + " " * E.MAX_ENVELOPE_BYTES)
        self.assertCode(E.MALFORMED, self.open, "not json")
        self.assertCode(E.MALFORMED, self.open, json.dumps({**d, "extra": 1}))
        for name in d:
            m = dict(d)
            del m[name]
            self.assertCode(E.MALFORMED, self.open, json.dumps(m))
        d["v"] = 2
        self.assertCode(E.UNSUPPORTED_VERSION, self.open, json.dumps(d))

    def test_envelope_is_well_within_the_size_limit_and_canonical(self):
        raw = self.seal_one("change_password")
        d = json.loads(raw)
        self.assertLess(len(raw.encode()), E.MAX_ENVELOPE_BYTES)
        self.assertEqual(list(d), ["v", "kid", "op", "nonce", "ts", "ek", "iv", "ct"])
        self.assertEqual(len(base64.urlsafe_b64decode(d["ek"] + "==")), self.priv.key_size // 8)
        self.assertTrue(all("=" not in d[k] for k in ("ek", "iv", "ct")))


class PythonSideKeyIdAgreement(unittest.TestCase):
    def test_key_id_function_is_the_same_in_both_languages(self):
        priv = generate_test_private_key()
        der = priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        expected = "k" + base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode()[:12]
        self.assertEqual(kid_for(priv.public_key()), expected)


if __name__ == "__main__":
    unittest.main()
