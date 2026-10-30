# G6a: encrypted-credential gateway, backend patch (LOCAL TEST ONLY)

A hash-gated, additive backend patch, `enc_auth_gateway.patch`, applied after the four production patches, plus a local lab that tests it
against the **Firebase Auth emulator**. It accepts the G1/G4 encrypted envelope (protocol version 1, unchanged) for login, signup,
password re-authentication and password change.

**Not production code. Not deployed. Not wired into the Flutter app.** The gateway is **disabled by default**, its identity adapter
refuses to talk to anything but a local Firebase Auth emulator, and nothing here proves real Firebase behaviour (see "Unresolved G3
blockers").

## Baseline (verified from local Git)

`d733776` + `mfa_additive.patch` + `mfa_enforcement.patch` + `mfa_logging_privacy.patch` + `email_verification_proof.patch`, in that
order, then `enc_auth_gateway.patch`. `build_tree_enc_auth.sh` refuses to build if any of these differs from the reviewed bytes
(SHA-256 of each patch, of `build_tree_proof.sh`, of the new patch, and the base commit id), and re-checks that
`otp_rate_limiter.py` is still byte-identical (`ded0a04a…`) before and after the new patch. It reuses the existing, unchanged
`build_tree_proof.sh`. The unreleased `main` backend is **not** the baseline.

```bash
bash scripts/enc_auth_poc/g6a/build_tree_enc_auth.sh <empty-output-dir>      # -> <dir>/backend (a local TEST tree)
```

The patch adds 5 files and changes 3 (`routes.py`: one include and two call sites; `config.py`: settings; `user_db.py`: one schema hook).
No existing file is deleted. `backend/scripts/requirements.txt` is not touched (`requirements-g6a.txt` records the tested `cryptography`
pin instead).

## Endpoints (under `/api/v1`)

| Endpoint | Request | Success reply |
|---|---|---|
| `POST /auth/enc/nonce` | `{"op": "login\|signup\|reauth\|change_password"}` | `{"nonce", "ttl", "server_time"}` |
| `POST /auth/enc/login` | body = the envelope, exactly | `{"custom_token"}` |
| `POST /auth/enc/signup` | envelope + header `X-Email-Proof: evp1_...` | `{"custom_token", "created": true}` or `{"custom_token", "created": false, "recovered": true}` |
| `POST /auth/enc/reauth` | envelope + `Authorization: Bearer <ID token>` | `{"custom_token"}` |
| `POST /auth/enc/change_password` | envelope + `Authorization: Bearer <ID token>` | `{"password_changed": true, "custom_token", "sessions_revoked"}` |

Every envelope problem (tampering, wrong key, wrong operation, expired or replayed nonce, malformed, oversized) answers the **same**
`400 {"error": "AUTH_FAILED"}`. Credential problems use the Firebase-style codes the app already maps: `invalid-credential`,
`email-already-in-use`, `weak-password`, `user-disabled`, `too-many-requests` (429 + `Retry-After`), plus `password-unchanged` and
`email-proof-invalid`. Anything unconfigured, unreachable or non-local answers `503 {"error": "AUTH_UNAVAILABLE"}`. All replies are `no-store`.

## Signup (corrected design)

1. The app validates the invite and verifies the e-mail by OTP, as today; the OTP step yields the one-time proof (kept in memory).
2. `POST /auth/enc/nonce`, seal `{email, password}`, `POST /auth/enc/signup` with the proof in a **separate header** (the envelope keeps
   its exact eight fields).
3. The gateway opens the envelope, **checks without consuming** that the proof exists, is unexpired, unconsumed and belongs to that e-mail
   (D1), enforces the app's password policy (D3), and **creates** the user with Admin `create_user(email_verified=False)` (D2). It never
   calls a password sign-in before the account exists.
4. If the e-mail already exists (an unfinished earlier signup or a lost response) it verifies the supplied password against the existing
   account; success returns a token for that same UID (`recovered`), **every** failure (wrong password, disabled, no password provider,
   limiter) is the same `email-already-in-use`.
5. A custom token is minted only for a UID the identity service reported. The app then signs in with it, calls `/sync-user` and
   `/auth/mark-verified` exactly as before; the proof is consumed only there.

## Other flows

* **Login:** limiter, open envelope, server-side password check, uid from the identity service, disabled accounts refused, token.
* **Re-authentication / password change:** the bearer ID token is verified including revocation and disabled-user checks; the account's e-mail
  must equal the envelope's; the password-verified UID must equal the token's UID; any mismatch is `invalid-credential` and counts as a failure.
  Password change also checks the new password against the policy and that it differs, updates it through Admin, revokes refresh tokens, and mints a
  token. If the update's outcome is unknown the reply says `password_changed: "unknown"` (never `false`); if only the revocation fails
  `sessions_revoked: false`; if only minting fails `{"password_changed": true, "custom_token": null, "retry_signin": true}`.
* **D6 e-mail helper** (`/sync-user`, `/auth/mark-verified`): unchanged while the gateway is disabled; when enabled the e-mail comes from the
  verified UID (claim cross-checked, or looked up through Admin when absent); a missing, disabled or inconsistent account is refused. Other
  routes that read the token e-mail (`/auth/email/verify-request`, `/verify-confirm`) are **not** covered, by scope.

## Safety properties

* RSA-OAEP-SHA256 / MGF1-SHA256 / label `epicverse-enc-auth-v1`, AES-256-GCM, AAD, base64url: identical to `../envelope.py`
  (a differential test compares the two on valid and mutated envelopes).
* Nonces: server-issued, operation-bound, 120 s, only a SHA-256 stored (table `enc_auth_nonces`), consumed by one atomic `UPDATE`.
* Own limiter (`enc_auth_limits`, keys hashed with an HMAC): requests per IP, signups per IP, failed passwords per e-mail and per IP.
  The existing OTP limiter is neither used nor changed.
* Logs carry only fixed outcome codes; error bodies carry no request values; the lab scans all backend logs and error bodies for every
  dummy credential, proof and token it used.
* Key ring supports several key ids at once (rotation overlap); a retired id is refused.
* Table names were checked against the deployed lineage and `main`; the schema check fails closed on a name collision.

## Local lab (`harness/`)

`orchestrate.py` starts the cached `firebase-tools` Auth emulator on 127.0.0.1:9099 (demo project, no login, dead proxies, no telemetry), an
**internal** Docker network (no route out), Postgres, ten backend variants (main, disabled, no key, rate-limit, key rotation, retired key,
no database, no hash secret, non-demo project, no emulator), and a fixed-target relay that reaches only host:9099. It verifies the isolation
(probes to public IPs and DNS fail), publishes ports on 127.0.0.1 only, pulls nothing, and removes only what it created. Keys are generated in
memory per run. `server_g6a.py` is a test-only runner (captures e-mails, issues proofs through the production code path, injects faults).

```powershell
python -B -I -m unittest discover -s scripts/enc_auth_poc/g6a/tests -p "test_*.py" -v     # starts and stops the lab itself
python -B -I scripts/enc_auth_poc/g6a/harness/run_existing_suites.py original --no-flutter   # existing MFA / email-proof harness, unchanged
python -B -I scripts/enc_auth_poc/g6a/harness/run_existing_suites.py gateway                 # the email-proof suite against the gateway tree
```

## Results of the last run (all against the local emulator, never real Firebase)

* G6a suite: **79 tests, 0 failures** (unit parity 7, build gates 7, static 14, lab integration 51 including two Dart-sealer tests).
  The lab integration suite was run 6 times in a row without a failure after one transient `503` in an earlier run that could not be
  reproduced (the lab's TCP relay was then made to retry its connection; the suite now prints how many identity-unavailable events the
  backend logged, expected 0).
* The password policy matches the real Flutter `PasswordValidator` (run through the Dart SDK) on 2,500 random and about 85 hand-picked inputs.
* The server envelope module gives the same outcome as the G1 reference on 700 valid and mutated envelopes.
* Mutation check of the gateway: 13 deliberate breaks, 12 caught by the tests; one (`uid_mismatch_check_removed`) is an unreachable
  defence-in-depth check, because it would need two Firebase accounts with the same e-mail.
* Unchanged regressions: G1 46, G2 33, G4 145 Dart + 21 Python (2,000 Dart-to-Python round trips), the complete existing MFA /
  enforcement / privacy / email-proof harness (all suites, 0 failures), and its email-proof suite (100 checks) against the gateway tree.

## Limitations and unresolved G3 blockers (real Firebase)

Everything above is verified only against the **emulator**. These stay **UNVERIFIED** and block any production use: the `email` /
`email_verified` / provider claims of real custom-token sessions; real custom-token signing and the signing permission (`signBlob`);
whether refresh stops after revoke or a password change; recent-login behaviour and whether a gateway re-authentication satisfies it;
whether Admin create/update enforce the project password policy; authentication throttling and signup quotas from shared server
addresses; a dedicated server API key; real REST error codes with enumeration protection; Admin `create_user` permission for the
production service account. There is no production identity mode in this patch by design. There is still no minimum-app-version mechanism.
