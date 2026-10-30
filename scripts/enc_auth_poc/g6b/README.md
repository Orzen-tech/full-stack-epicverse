# G6b - encrypted-authentication gateway hardening (LOCAL ONLY)

Approved decisions D-A to D-E, implemented **offline** on top of G6a. Nothing here is deployed, integrated into Flutter, committed or
connected to any cloud service. The gateway is disabled by default and its identity adapter refuses anything but a local Firebase Auth
**emulator**. Emulator behaviour is not real Firebase behaviour (G3 is still open).

## What is in this folder

| Path | Purpose |
|---|---|
| `enc_auth_gateway_g6b.patch` | One patch on top of the G6a tree: 3 new pure modules + in-place edits of 5 files (`enc_gateway.py`, `enc_auth_identity.py`, `enc_auth_store.py`, `routes.py`, `config.py`). |
| `build_tree_enc_auth_g6b.sh` | Hash-gated build: `d733776` + mfa_additive + mfa_enforcement + mfa_logging_privacy + email_verification_proof + G6a + G6b. |
| `harness/` | G6b-only lab: copy of the G6a lab, extended (fault-injecting relay, process-suspension of the emulator, Postgres stop/start, pool hold, diagnostics capture). G6a is untouched. |
| `tests/` | Unit, build-gate, static and integration tests, plus the G6a functional suite run against the G6b tree. |
| `tools/` | `make_patch.py`, `pin_patch_hash.py`, `run_mutants.py` (test-of-the-tests). |

`bash build_tree_enc_auth_g6b.sh <empty-dir>` refuses (and builds nothing) when: the base commit is wrong; any earlier patch, the production
build script, the G6a build script, the G6a patch or the G6b patch differs from the pinned bytes; a dependency is missing; the destination is
not empty; the G6b patch does not apply cleanly; or the OTP rate limiter (`ded0a04a...`) is not byte-identical afterwards.

## Option D - proof hardening (no protocol change)

Unchanged: G1/G4 envelope, the `X-Email-Proof` header, no new envelope fields or operations, the shared 30-minute proof expiry, one-time
consumption in `/auth/mark-verified`.

1. **Gateway-side maximum age** `ENC_AUTH_PROOF_MAX_AGE_SECONDS` (default 600): the gateway accepts a proof for signup/recovery only for 10 minutes after it was issued.
2. **First verified UID is recorded** in `enc_auth_proof_claims(proof_hash, uid, created_at)`; `proof_hash` is an HMAC (`ENC_AUTH_HASH_SECRET`) of the raw proof, never the raw proof or the plain proof hash.
3. The record is written **only after Firebase confirmed the account** (created it, or verified the password of the existing one in recovery) and **before a custom token is returned**. `INSERT ... ON CONFLICT DO NOTHING` + `SELECT` makes it atomic: exactly one UID can own a proof.
4. `/auth/mark-verified` (only when the gateway is enabled) refuses a UID that does not own the claimed proof with the same 403 as an unknown proof, **before** the unchanged consume step, so the proof stays unconsumed.
5. A failed creation claims nothing; if the account was created but the claim could not be stored, no token is returned (503) and a retry with the same password recovers and binds it.
6. No client-supplied marker is needed: the claim exists only if the gateway handled that proof.

**Residual risks (Option D does not remove them):** a stolen proof used *first* at the gateway by an attacker (the attacker becomes the owner);
a proof used entirely outside the gateway (direct Firebase signup) is not claimed; active TLS interception of the header; mark-verified still has no proof-of-possession.
Those need a protocol decision (e.g. binding the verify step to the encrypted channel) - out of scope here.

## 503 hardening (the original transient 503 is NOT claimed to be explained)

* Every gateway response carries `X-Enc-Request-Id` (12 random hex digits). Every 503 also carries `X-Enc-Cause` from a closed list (`reset`, `refused`, `http_503`, `read_timeout`, `db_unavailable`, `pool`, `timeout`, `sdk_error`, `disabled`, ...).
* One log line per gateway outcome: `[ENC-AUTH] rid=... op=... outcome=... stage=... cause=... [ms=...]`; values outside the closed vocabularies are replaced by `other`, so no credential, e-mail, proof, token, UID or envelope content can reach a log.
* Stage timings and retry/unavailable counters (`enc_auth_diag`), relay connection counters (lab relay), failure-time capture in the lab before teardown.
* **Bounded retries only for idempotent reads/verifications** (password check, account lookup, ID-token check): at most 2 retries (about 100 ms and 250 ms, jittered), total budget 1500 ms from the first attempt, only for connect/refused/reset/dns/connect-timeout and HTTP 502/503/504 (and SDK "unavailable"). Never for credential answers, other 4xx, permission errors, read timeouts, account creation, password update, revocation, token minting or proof consumption.
* A transport failure, an unexpected 4xx/5xx or a deadline never produces a credential error and never counts as a wrong password.
* Overall request deadline (`ENC_AUTH_REQUEST_DEADLINE_SECONDS`, 20 s), database acquire timeout (5 s), bounded Admin SDK http timeout (8 s, private SDK app) instead of the SDK's 120 s.
* `change_password` is truthful: before the update was sent nothing changed; after it was sent and the outcome is not known the answer says `password_changed: "unknown"`; after it succeeded a timeout still answers `password_changed: true` with `retry_signin`.

## Rate-limit policy (anti lock-out)

Counters over fixed 600-second windows (`ENC_AUTH_WINDOW_SECONDS`), all keys HMACs (no e-mail or IP stored), all counted **before** the password is verified in one transaction (so concurrent guesses cannot overshoot) and **given back** unless the identity service answered with a real credential failure.

| Scope | Default | Meaning |
|---|---|---|
| pair (e-mail + IP) | 5 | one address guessing one account |
| ip (all e-mails) | 20 | one address spraying many accounts |
| email (all IPs) | 30 | distributed guessing; a lock-out needs failures from at least 6 addresses (was 5 failures from one) |
| global | 2000 | above it: **abuse mode**, the per-IP limit drops to 2, so only addresses that already failed are restricted |

Shared by login, signup recovery, reauth and change-password (and by e-mail/UID mismatch failures). Refused attempts are released immediately, successes and transport failures leave no trace. Trade-offs: a distributed attacker who reaches the per-e-mail aggregate can still block the owner until the window ends (the cost is now many addresses, not one); in abuse mode an address with two earlier failures is refused even with the right password; fixed windows allow up to 2x a limit across a boundary. Request limits (`ENC_AUTH_IP_LIMIT` 120, `ENC_AUTH_SIGNUP_IP_LIMIT` 10) are unchanged.

## Running

```
python -B -I -m unittest discover -s tests -p "test_unit_modules.py"            # pure modules
python -B -I -m unittest discover -s tests -p "test_build_gates.py"             # hash-gated build, tamper tests
python -B -I -m unittest discover -s tests -p "test_static_g6b.py"
python -B -I -m unittest discover -s tests -p "test_integration_*.py"           # needs Docker Desktop + the cached firebase-tools (one lab per module)
python -B -I -m unittest discover -s tests -p "test_g6a_regression_on_g6b_tree.py"
python -B -I tools/run_mutants.py                                               # deliberate defects must be caught
python -B -I harness/run_existing_suites.py g6b                                 # existing e-mail-proof suite against the G6b tree
```

The lab uses only local Docker (`--internal` network `g6b-net`, containers `g6b-*`), the local emulator on 127.0.0.1:9099, loopback-only
ports 18110-18139, and removes only what it started. Cost: INR 0.
