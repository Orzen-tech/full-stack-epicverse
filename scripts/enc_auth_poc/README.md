# Encrypted credential envelope: offline proof of concept (G1)

Status: **offline prototype**. It is not connected to Firebase, Google Cloud, a database, the network, the environment or any
application code, and nothing in `backend/` or `frontend/` imports it. No plaintext fallback exists anywhere in this folder.

## Goal

The email and password of a **login, signup, re-authentication or password change** are sealed on the client into an envelope
that only the gateway can open, so a TLS-intercepting proxy captures ciphertext. This folder proves the envelope and its replay
protection in isolation (step G1 of the plan). It is not the gateway.

## Design

One JSON object (all values strings except `v` and `ts`):

```
{"v":1,"kid":"k...","op":"login","nonce":"<43 chars>","ts":1800000000,"ek":"...","iv":"...","ct":"..."}
```

* `ek` = RSA-OAEP (SHA-256, MGF1-SHA-256, fixed context label) of a fresh random 32-byte key. RSA keys must be at least 3072 bits.
* `ct` = AES-256-GCM of the JSON plaintext, with a fresh random 12-byte `iv`; the 16-byte tag is the tail of `ct`.
* **Authenticated data** = `"EVENC"`, the version, and the key id, operation, nonce and timestamp, each length-prefixed. Changing
  any of them makes decryption fail. The OAEP label also binds the wrap to this protocol.
* `op` is one of `login`, `signup`, `reauth`, `change_password`. Each has an exact field list (`email`, `password`, plus
  `new_password` for `change_password`, where `password` is the current one). Missing or extra fields are rejected.
* **Nonce:** the server issues a random 32-byte nonce **for one operation**; it is valid for 120 s and usable once. The timestamp must
  also be within 300 s of the server clock. Only a SHA-256 of the nonce is stored.
* **Order of checks** (`open_credentials`): size and strict JSON, version, key id, field formats, operation, timestamp, read-only
  nonce check, then RSA/AES decryption, then the **atomic** nonce consume, then plaintext validation. A forged or tampered request
  therefore cannot burn a genuine user's nonce, and cheap checks run before the RSA operation.
* **Limits:** envelope 8192 bytes, plaintext 2048 bytes, email 254 and password 256 characters, no control characters.
* **Errors** carry only a fixed code (`EnvelopeError.code`), never request data. Every RSA or AES failure is the single code
  `DECRYPT_FAILED`. A real gateway should return **one generic external error** for all codes.
* `Credentials.__repr__` hides its values. No module prints, logs, reads files or reads the environment (checked by tests).

Only the `cryptography` package is used (RSA-OAEP, AES-GCM, SHA-256, OS randomness). No primitive is implemented here.

## Files

| File | Purpose |
|---|---|
| `envelope.py` | seal (client side) and open (server side), key ring, strict validation |
| `nonces.py` | TEST-ONLY in-memory nonce store (issue, inspect, atomic consume, expiry, capacity) |
| `testkeys.py` | TEST-ONLY in-memory key generation; keys are never serialised, written or printed |
| `tests/test_envelope.py` | round trip, tampering, replay (including 16 concurrent attempts), expiry, wrong operation, key ids, malformed, oversized, no leakage |
| `tests/test_hygiene.py` | static checks: no key material, only crypto and standard-library imports, no network, files or environment access |

## Run (offline, no installation, nothing written to disk)

From the repository root:

```bash
python -B -I -m unittest discover -s scripts/enc_auth_poc/tests -v
```

Requires Python 3.11 and the `cryptography` package that the backend already depends on. `-B` stops bytecode files from being created.

## Limitations (read these)

* **It does not protect against a compromised device or runtime instrumentation.** The password exists in clear on the phone
  before it is sealed, and anything that can hook the app can read it there.
* It does not stop someone who can reach the gateway from sending their **own** credentials, so rate limiting and attestation are still needed.
* Encryption to a static server key gives **no forward secrecy**: a leaked private key exposes recorded envelopes (which is why
  the nonce window and key rotation matter).
* The nonce store is in memory and single process. It is a stand-in for a shared database table.
* The public key must be **embedded in the app**. A key fetched over TLS could be replaced by an interception proxy.
* The server's timing and error behaviour have not been analysed beyond using one error code for RSA/AES failure.
* The Python implementation is the only one. A Dart implementation must reproduce the format exactly and pass cross-language known-answer vectors.
* Not reviewed by an independent cryptographer.

## Needed before this can become a gateway (later gates, none done here)

1. Dart client port and cross-language test vectors (Python seals, Dart opens, and the reverse).
2. Persistent nonce table with a **unique name** (not one used by the unreleased `main` backend) and an atomic consume.
3. Gateway routes that verify the password through supported Identity Toolkit APIs and mint a custom token (see the Phase 1
   feasibility report: email claim, throttling, `signBlob`, disabled users, re-authentication semantics are unverified).
4. Key storage and rotation (Cloud KMS or Secret Manager), key ids in the app, and an operations runbook.
5. Its own rate limiter (per email, IP and device); the existing OTP limiter file must stay unchanged.
6. A fail-closed policy with a server-side switch; never silently fall back to the plaintext SDK path.
7. Logging redaction for any new field names on both client and server.
8. Burp Suite verification on a release build with test accounts.
9. Backend integration as an additive patch on the deployed lineage (`d733776` plus the four reviewed patches), following `scripts/mfa_additive/`.
