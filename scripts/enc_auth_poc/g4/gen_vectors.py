"""Generates g4_dart/test/vectors.dart from the G1 Python implementation (the reference). Deterministic: running it twice gives the
same file, which test_g4_vectors_current.py checks. Dummy data and PUBLIC keys only; the private halves of the embedded test public
keys were discarded when the constants were created.

    python -B -I scripts/enc_auth_poc/g4/gen_vectors.py          (writes the file)
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POC = HERE.parent
sys.path.insert(0, str(POC))

import envelope as E  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

OUT = POC / "g4_dart" / "test" / "vectors.dart"

# Public keys only (SubjectPublicKeyInfo DER, standard base64).
RSA3072 = ("MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAqMNfZRLNg8iJkgmgi6JvF7xHH6VJMt/J9q/gHvTqmeIX7OqLKSnTh+OWc6O+HJlTewqRuymyKP7U42uLalAQuRpTB2Ye/bkkRSnjXi3a9tD+m/ZT5UVcrRTfw1AQDsiYuWrWRk1tKenIgiYAqcEhKrsWOPd7uourxg1IogvIs8d8quwpT62x+fMNGJzB0loQ17rliE66toogezcKOom7wg8xYJV1PxuybXWfcaLxnkPqDPBK/AaUDGx1GStMxV1Ay9mxlA4P6Tn3BL13pE67KfsHK1d6usBGrlHNDPPExkyVk/ywMPPUxVpi1OTPjLoyeq238oE8H/Muz0HK8sGPp0rPbrO4gu6w7N7RCvqqUfnQjaz6+shsy9M4WIAczvMHg8T1lmM7gxvVQhxrRBQ2qvsGa3ntAgX+7zDZta0k7X13WAMeE2UDchji3/Yg9dSf799hO+CU8xiHADqFVZSVEYJZ4kE4K2pACmFb5T69BQlIhT7jKpCdhC5v0kVnYtp1AgMBAAE=")
RSA2048 = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyXLtWkWRAP2wijKZEBtnaUyWICI55l2X+PVc/SUYIYLELzeKfI1A6Q1g7E36o8PFgBjrmaZtTE3bEWa/STBDu8pl00igE9/eWzpsrIlnw0Z1dYAis9b4VWRNNcaiTKlJaxxrMOxM7RCCfEdgvzJy+UrKyyRDd5p3zbN0EOgivfvdjEoXzdPPexLOwJEYgAvIlU6dtnURGTe4qDBdOcLfdhRSVuztWQTP/EHK6WHl5dPiaZWmcST9zvWj/hWTSsGPkZ1r510nIo0GgLfEFjpKunSLgCOUjgYV2hTQ6++8LjNLMN1KMVsvP2H91EvXKNfHci2Sm+ZNxw5X1pWBt8KNewIDAQAB")
RSA4096 = ("MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAo00yXGibY+qp0t0WD2q/u6ybPcap4E5PXWGps4V1sNzfco4pOwt/kEyxj1a9mzdJEc9ZpyThmt1h7/rr5pE3TqjuCIr0TxHgT+JwKMP0oqq8WexjJuHWMIm7HS67tAnbncZ/4SEEm2vMY7iSPqZKJ4bmDDex6L2hFUhSFxeHN9Pt9SM9woOM1qAqtBuxfOmfczbqQgb2tb2lLS8nR4xSzyeQLUEgaPypij0W9amOw8Cw+YAfuH4NVYKRGQ0Iay/wwdAaaigfMhYfSECnb4dDECj5Q3jERQqTExWK6ZVFglWcoH9RlWk9ZrhVDgNQtvhfnxDSDZoh3MSC+3hKcvhrTnRI48/ssdTllqtHuYR+nTjDOz/AeVAjzi8Xw8p7NQEJURpLRXEzZSaoLqf7eejFQu4yNzdLljy7GbjDo5OUDpXs+gT4jryMZyI990hSZT6qbmhg0FlSSw6VApiHeHSWA3qYmHQUA4EHNZK5ZPEcN8Gr3vTFMF4w7wXABa22nAq1oM1PkeunZJpkbx+OU2gMZzd9Vdo/L5XkKAT0qAsPEyMhLS1UbztBEVVocpDlq2xmnyqtUYYFQMma/KR4txZFFTDZ6gm04fk6P+qk9u+Wh/Bc8yexOqvHmLgeNU3QZglwIzcS0LGGon+wyo7oRuG6Z7SH6SmmeHg1EMgFtCdQHvsCAwEAAQ==")
P256 = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE3+q07fDE2QsRsbw7YtohIlb0HmxGyO7tsBS4fqfLl1qb9er78MjXzXcKRD3ROoHL5ck/pbCubIAWLnnsWCZD6w=="

NONCE = "A" * 43
TS = 1_800_000_000


def kid_for_der(der):
    return "k" + base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode("ascii")[:12]


def dq(s):
    """Dart single-quoted string literal using only ASCII source characters."""
    out = ["'"]
    for ch in s:
        o = ord(ch)
        if ch in "'\\$":
            out.append("\\" + ch)
        elif 0x20 <= o <= 0x7E:
            out.append(ch)
        else:
            out.append("\\u{%x}" % o)
    out.append("'")
    return "".join(out)


def dmap(d):
    return "{" + ", ".join("%s: %s" % (dq(k), dq(v)) for k, v in d.items()) + "}"


def result_of(fn):
    try:
        fn()
        return "OK"
    except E.EnvelopeError as exc:
        return exc.code


def validation_cases():
    pub = serialization.load_der_public_key(base64.b64decode(RSA3072))
    kid = kid_for_der(base64.b64decode(RSA3072))
    long_email = "a" * (254 - len("@x.io")) + "@x.io"
    cases = []

    def add(name, op, fields):
        code = result_of(lambda: E.seal_credentials(pub, kid, op, NONCE, dict(fields), now=TS))
        cases.append((name, op, fields, code))

    ok = {"email": "poc-user@example.invalid", "password": "Pw-dummy-1"}
    for op in E.OPERATIONS:
        f = dict(ok)
        if op == "change_password":
            f["new_password"] = "Np-dummy-2"
        add("valid " + op, op, f)
    add("email at 254", "login", {"email": long_email, "password": "x"})
    add("email at 255", "login", {"email": "a" + long_email, "password": "x"})
    add("email without @", "login", {"email": "no-at-sign", "password": "x"})
    add("email empty", "login", {"email": "", "password": "x"})
    add("email with newline", "login", {"email": "a@b\n.io", "password": "x"})
    add("email with DEL", "login", {"email": "a@b\x7f.io", "password": "x"})
    add("email unicode", "login", {"email": "üser@exämple.com", "password": "x"})
    add("password 1 char", "login", {"email": ok["email"], "password": "x"})
    add("password empty", "login", {"email": ok["email"], "password": ""})
    add("password 256 ascii", "login", {"email": ok["email"], "password": "p" * 256})
    add("password 257 ascii", "login", {"email": ok["email"], "password": "p" * 257})
    add("password 256 BMP runes", "login", {"email": ok["email"], "password": "日" * 256})
    add("password 257 BMP runes", "login", {"email": ok["email"], "password": "日" * 257})
    for n in (100, 150, 160, 165, 170, 200, 256, 257):
        add("password %d astral runes" % n, "login", {"email": ok["email"], "password": "\U0001F600" * n})
    add("password quotes backslash slash", "login", {"email": ok["email"], "password": 'a"b\\c/d\'e$f'})
    add("password spaces only", "login", {"email": ok["email"], "password": "   "})
    add("password leading/trailing spaces", "login", {"email": ok["email"], "password": "  pw  "})
    add("password tab", "login", {"email": ok["email"], "password": "a\tb"})
    add("password NUL", "login", {"email": ok["email"], "password": "a\x00b"})
    add("password DEL", "login", {"email": ok["email"], "password": "a\x7fb"})
    add("password mixed unicode", "login", {"email": ok["email"], "password": "pässwörd-日本語-\U0001F512"})
    add("email too long then password control (email first)", "login", {"email": "a" + long_email, "password": "a\tb"})
    add("password control then email too long (password first)", "login", {"password": "a\tb", "email": "a" + long_email})
    add("email too long and password too long", "login", {"email": "a" + long_email, "password": "p" * 257})
    add("signup missing password", "signup", {"email": ok["email"]})
    add("login with extra field", "login", {**ok, "new_password": "x"})
    add("change_password missing new_password", "change_password", dict(ok))
    add("change_password new_password empty", "change_password", {**ok, "new_password": ""})
    add("change_password new_password 257", "change_password", {**ok, "new_password": "n" * 257})
    add("change_password new_password control", "change_password", {**ok, "new_password": "n\nm"})
    add("unknown operation", "delete_account", dict(ok))
    add("empty fields", "login", {})
    add("reauth valid unicode", "reauth", {"email": ok["email"], "password": "\U0001F512secreté"})
    return cases


def sealbytes_cases():
    pub = serialization.load_der_public_key(base64.b64decode(RSA3072))
    kid = kid_for_der(base64.b64decode(RSA3072))
    cases = []

    def add(name, k, op, nonce):
        code = result_of(lambda: E._seal_bytes(pub, k, op, nonce, TS, b"{}")) if True else ""
        cases.append((name, k, op, nonce, code))

    add("valid", kid, "login", NONCE)
    add("kid empty", "", "login", NONCE)
    add("kid with space", "bad kid", "login", NONCE)
    add("kid 64 chars", "k" * 64, "login", NONCE)
    add("kid 65 chars", "k" * 65, "login", NONCE)
    add("kid non-ascii", "kü", "login", NONCE)
    add("kid trailing newline", kid + "\n", "login", NONCE)
    add("nonce 42 chars", kid, "login", "A" * 42)
    add("nonce 44 chars", kid, "login", "A" * 44)
    add("nonce bad char", kid, "login", "A" * 42 + "+")
    add("nonce empty", kid, "login", "")
    add("nonce trailing newline", kid, "login", "A" * 43 + "\n")
    add("operation unknown", kid, "register", NONCE)
    add("operation empty", kid, "", NONCE)
    return cases


def render():
    der3072, der2048, der4096, derp256 = (base64.b64decode(x) for x in (RSA3072, RSA2048, RSA4096, P256))
    lines = ["// GENERATED by scripts/enc_auth_poc/g4/gen_vectors.py from the G1 Python reference. Do not edit by hand.",
             "// Dummy data and PUBLIC keys only. ignore_for_file: prefer_single_quotes", ""]

    lines.append("const String rsa3072Spki = %s;" % dq(RSA3072))
    lines.append("const String rsa2048Spki = %s;" % dq(RSA2048))
    lines.append("const String rsa4096Spki = %s;" % dq(RSA4096))
    lines.append("const String p256Spki = %s;" % dq(P256))
    lines.append("const String rsa3072Kid = %s;" % dq(kid_for_der(der3072)))
    lines.append("const String rsa4096Kid = %s;" % dq(kid_for_der(der4096)))
    lines.append("const String rsa2048Kid = %s;" % dq(kid_for_der(der2048)))
    lines.append("const String vectorNonce = %s;" % dq(NONCE))
    lines.append("const int vectorTs = %d;" % TS)
    lines.append("")

    lines.append("// kid, operation, nonce, ts, expected AAD bytes (hex)")
    lines.append("const List<List<Object>> aadVectors = [")
    for kid, op, nonce, ts in [("k0123456789ab", "login", NONCE, TS), ("k0123456789ab", "signup", "B" * 43, 0),
                               ("k0123456789ab", "reauth", NONCE, 2 ** 40 - 1), ("k0123456789ab", "change_password", "_" * 43, 1),
                               ("a", "login", "-" * 43, 12345), ("k" * 64, "login", NONCE, TS), ("a.b_c-d", "reauth", NONCE, 99)]:
        lines.append("  [%s, %s, %s, %d, %s]," % (dq(kid), dq(op), dq(nonce), ts, dq(E._aad(kid, op, nonce, ts).hex())))
    lines.append("];")
    lines.append("")

    lines.append("// key, iv, aad, plaintext (all hex) and the expected ciphertext||tag (hex), from AES-256-GCM in Python")
    lines.append("const List<List<String>> gcmVectors = [")
    for key, iv, aad, pt in [("00" * 32, "00" * 12, E._aad("k", "login", NONCE, TS).hex(), b'{"email":"a@b","password":"x"}'.hex()),
                             ("0123456789abcdef" * 4, "a1" * 12, E._aad("kid-1", "signup", "C" * 43, 7).hex(), b"x".hex()),
                             ("ff" * 32, "01" * 12, "", b"".hex()),
                             ("5a" * 32, "9c" * 12, E._aad("kid-2", "change_password", "D" * 43, 99).hex(),
                              json.dumps({"email": "ü@x.io", "new_password": "n", "password": "p"}, separators=(",", ":"), sort_keys=True).encode().hex())]:
        ct = AESGCM(bytes.fromhex(key)).encrypt(bytes.fromhex(iv), bytes.fromhex(pt), bytes.fromhex(aad) if aad else b"")
        lines.append("  [%s, %s, %s, %s, %s]," % (dq(key), dq(iv), dq(aad), dq(pt), dq(ct.hex())))
    lines.append("];")
    lines.append("")

    lines.append("// input bytes (hex), expected base64url without padding")
    lines.append("const List<List<String>> b64uVectors = [")
    for raw in [b"", b"\x00", b"\x00\x00", b"\x00\x00\x00", b"\xfb", b"\xff\xff", b"\xfb\xff\xbf", bytes(range(32)), b"\xf8" * 5]:
        lines.append("  [%s, %s]," % (dq(raw.hex()), dq(E.b64e(raw))))
    lines.append("];")
    lines.append("")

    lines.append("// the plaintext JSON the Python sealer produces (compact, sorted keys, ensure_ascii)")
    lines.append("const List<List<Object>> plaintextVectors = [")
    for fields in [{"email": "a@b.io", "password": "x"},
                   {"email": "üser@exämple.com", "password": 'a"b\\c/d\'e$f'},
                   {"email": "a@b.io", "password": "päss-日本-\U0001F512"},
                   {"password": "p", "new_password": "n", "email": "e@x.io"}]:
        lines.append("  [%s, %s]," % (dmap(fields), dq(json.dumps(fields, separators=(",", ":"), sort_keys=True))))
    lines.append("];")
    lines.append("")

    lines.append("// name, operation, fields, expected result of the Python sealer: OK or an error code")
    lines.append("const List<List<Object>> validationCases = [")
    for name, op, fields, code in validation_cases():
        lines.append("  [%s, %s, %s, %s]," % (dq(name), dq(op), dmap(fields), dq(code)))
    lines.append("];")
    lines.append("")

    lines.append("// name, kid, operation, nonce, expected result of Python _seal_bytes")
    lines.append("const List<List<String>> sealBytesCases = [")
    for name, kid, op, nonce, code in sealbytes_cases():
        lines.append("  [%s, %s, %s, %s, %s]," % (dq(name), dq(kid), dq(op), dq(nonce), dq(code)))
    lines.append("];")
    return "\n".join(lines) + "\n"


def main():
    text = render()
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        return 0 if OUT.exists() and OUT.read_text(encoding="utf-8") == text else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("wrote %s (%d bytes)" % (OUT.relative_to(POC.parent.parent), len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
