# G4: Dart sealer for the encrypted-credential envelope (offline proof of concept)

A standalone **pure-Dart package** (`../g4_dart`) that builds the version 1 envelope of the G1 Python reference
(`../envelope.py`), plus the Python tests that prove the two agree. It is **not part of the Flutter app**: no app file, `pubspec.yaml`
or `pubspec.lock` references it (a test checks this), it makes no network, file or Firebase call, and it contains **no plaintext
fallback**: when anything is wrong, `sealCredentials` throws an `EnvelopeError` carrying only a code, and there is no other
function in the package that handles credentials.

## Layout

| Path | Purpose |
|---|---|
| `../g4_dart/lib/src/validation.dart` | operations and exact field sets, limits, rune-based length rules (match Python) |
| `../g4_dart/lib/src/encoding.dart` | base64url (no padding), the AAD builder, and the plaintext JSON built byte-for-byte like Python's `json.dumps(..., sort_keys=True)` |
| `../g4_dart/lib/src/spki_key.dart` | strict DER SubjectPublicKeyInfo RSA key parser (>= 3072 bits, byte-for-byte canonical) and the key id |
| `../g4_dart/lib/src/envelope.dart` | `sealCredentials`: AES-256-GCM payload, RSA-OAEP-SHA256 (MGF1-SHA256, label `epicverse-enc-auth-v1`) key wrap |
| `../g4_dart/test/` | Dart suite (known-answer vectors generated from Python, validation parity, key parsing, envelope structure) |
| `../g4_dart/tool/` | `seal_cli.dart` (used by the Python interop tests), `oaep_gate.dart` (the interoperability gate) |
| `gen_vectors.py` | writes `g4_dart/test/vectors.dart` from the Python reference (deterministic; public keys and dummy data only) |
| `oaep_gate.py` | the RSA-OAEP interoperability gate (2000 trials plus label controls) |
| `test_g4_interop.py` | Dart seals, Python opens: 2000 round trips, 600 randomized parity cases, negative tests |
| `test_g4_static.py` | no network, file, Firebase or logging code; only the cached `pointycastle`; the app does not reference the PoC |
| `test_g4_vectors_and_suite.py` | vectors are current, the Dart suite passes, the gate passes |

## Dependencies (offline)

Only `pointycastle` 4.0.0 from the local pub cache (with its cached `collection`, `convert`, `typed_data`). Resolved with
`dart pub get --offline`; nothing was downloaded. The `test` package is **not** used (its dependencies are not all cached), so the
Dart tests use a tiny assertion harness.

## Run (Windows, from the repository root)

```powershell
cd scripts\enc_auth_poc\g4_dart ; dart pub get --offline        # once; creates pubspec.lock and .dart_tool (git-ignored)
cd ..\..\..                                                    # back to the repository root
python -B -I scripts\enc_auth_poc\g4\oaep_gate.py              # the interoperability gate
python -B -I -m unittest discover -s scripts/enc_auth_poc/g4 -p "test_g4_*.py" -v
```

(`dart` here is the one bundled with the local Flutter SDK, `D:\flutter sdk\flutter\bin\cache\dart-sdk\bin\dart.exe`.)

## What was checked (see the report for the numbers of the last run)

* **Gate:** Dart pointycastle RSA-OAEP-SHA256 with the custom label decrypts in Python/OpenSSL for 2000 of 2000 random keys, every `ek`
  has the full modulus length, a wrong label is rejected, and Dart without the label is rejected when Python expects it.
* **Round trips:** 2000 consecutive Dart-sealed envelopes (all four operations, ASCII, BMP and astral text) are opened by
  `open_credentials` with identical fields; every `iv` and `ek` is distinct; no plaintext, hex, base64 or URL-encoded credential
  appears in any serialized envelope.
* **Parity:** 600 random boundary inputs (lengths 0 to 300, emoji, control characters, wrong field sets) give the same outcome in
  Dart and Python.
* **Negative:** tampering with `ct`, `ek`, `iv`, `kid`, `nonce` or `ts`; wrong operation; replay; expired nonce and timestamp;
  unknown key id; envelope sealed to another key; truncated, oversized and malformed envelopes.

## Limitations (read these)

* **Not protection against a compromised device or runtime instrumentation**: the password is in clear in the app before it is sealed.
* The library's own documentation says its OAEP implements the older RFC 2437 and is not compatible with RFC 8017. **In practice it
  interoperated with OpenSSL in every test here**, but that rests on tests, not on a guarantee from the library; keep the gate and
  the interop tests in any future build.
* pointycastle is a pure-Dart implementation: AES uses lookup tables (a theoretical cache-timing concern) and Dart strings holding
  the password cannot be wiped. The AES key is zeroed after use on a best-effort basis.
* The key is parsed from DER the app must embed; the app has no key-distribution or rotation mechanism yet.
* The client clock must be within 300 s of the server's, or the gateway rejects the request.
* Only the sealing direction is implemented in Dart (the client never decrypts).
* Pure-Dart RSA speed and binary-size impact on a phone are **not measured**.
* Types are stricter than the wire format: the Dart API takes `Map<String, String>`, so Python's wrong-type cases cannot occur.
* Timestamp range (0 to 2^40 - 1) is enforced on the client; Python's sealer leaves that check to the server.

## Unresolved G3 blockers (real Firebase) for production integration

None of these is answered by G1 to G4. All remain open and block wiring this into login, signup, re-authentication or password change:

1. Real `email` / `email_verified` / provider claims in custom-token sessions (the backend reads the token email at several routes).
2. Real custom-token signing and acceptance (the emulator accepts unsigned tokens).
3. Whether refresh stops after revoke or a password change for custom-token sessions (the emulator does not).
4. Recent-login behaviour and window, and whether a gateway re-authentication satisfies it.
5. Password policy enforcement and authentication throttling from a shared server address.
6. A minimum-app-version mechanism (none exists), so the plaintext path cannot be retired.
7. Fail-closed policy, the key rotation scheme, and a server-side kill switch.

## Strength of the tests

The Dart sealer was mutation-tested on throwaway copies (15 deliberate breaks, for example: wrong AAD version byte, timestamp or
operation dropped from the AAD, OAEP label removed, 96-bit GCM tag, constant IV, UTF-16 instead of rune counting, missing control-character,
`@`, size, key-size or timestamp checks, unsorted or unescaped plaintext, non-strict key parsing). **All 15 were caught.** The
removed-label mutant is caught only by the Python interop tests, which is why those tests (and the gate) must stay.
