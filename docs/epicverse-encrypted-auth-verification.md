# EpicVerse encrypted email/password authentication (G0–G8): verification record

Record prepared: 2026-10-29. Updated: 2026-10-30 (sections 10 to 13 added; sections 1, 3, 4, 6.4 and 8 amended).

## 1. Purpose and scope

This file records what evidence exists for the live and end-to-end results of the encrypted-authentication work
(gates G0–G8), and how strong each piece of evidence is. It covers only that work.

It does **not** establish production readiness. It does **not** claim coverage of the Firebase client SDK or of
real-Firebase client behaviour (see section 8).

The Flutter client work is committed as `05b1239` ("feat(security): add encrypted auth G0-G8 Flutter flow") on
`origin/main`. It is off by default: `ENC_AUTH_MODE` defaults to `off`.

As of 2026-10-30 the backend gateway port and the proof-of-concept folder are committed on the local `main` branch only
(`c66fbf6`, `1affd11`, then this document in `97cdb77`); none of the three has been pushed (section 12).

## 2. Evidence labels

| Label | Meaning |
|---|---|
| **RAW-VERIFIED** | The raw tool or terminal output, or the source text itself, was seen and is cited here. |
| **REPORTED** | Stated by the project owner. The raw output is not available. This is never upgraded to RAW-VERIFIED anywhere in this file. |
| **UNRESOLVED** | Open, inconsistent, or not yet explained. |

## 3. Evidence sources

- **Transcript:** the Claude Code session file
  `%USERPROFILE%\.claude\projects\D--Company-Projects-full-stack-epicverse-1\66308809-444e-4c17-9d63-9fd5e9765f12.jsonl`.
  "Line N" below means line N of that file. Times are UTC. This is a local file, not part of the repository.
- **Local E2E log (TEMPORARY, may be unavailable):**
  `%LOCALAPPDATA%\Temp\claude\D--Company-Projects-full-stack-epicverse-1\66308809-444e-4c17-9d63-9fd5e9765f12\scratchpad\e2e_run.log`.
  This file sits in a temporary scratch folder that can be cleaned up at any time, and it may already have been deleted.
  It is not a durable record; the transcript is the longer-lived source, and neither is stored in the repository.
- **Current session transcript (added 2026-10-30):**
  `%USERPROFILE%\.claude\projects\D--Company-Projects-full-stack-epicverse-1\301c3c1c-5604-4099-9727-8d9c395a20c0.jsonl`.
  Sections 10 to 12 cite it as "current transcript, line N"; "old transcript" means the file named above. Both are local files, not part of the repository.
- **Local backup (added 2026-10-30):** `D:\Company_Projects\EpicVerse-G0G8-Backup-20261030-154927` (manifest SHA-256
  `132fa1335ccb44e64e2bd903ba2447d47638a5db74b31c7ed0d28da49c9b0f4d`). It preserves the evidence logs cited below
  (`evidence\s6_run.log`, `evidence\s7_verify.log`, `evidence\e2e_run.log`), the release artifacts and the deployed-backend provenance.
  It is on the same disk as the repository and contains an unscanned copy of `.git`, so it is local-only.
- No G3 or G8 result or log file exists in the repository or in the source backup. The G8 runner prints its report to the
  terminal only and writes no file.

## 4. Status summary

| Item | Status |
|---|---|
| G3 Phase 5: live custom-token test | **RAW-VERIFIED** |
| G8 Stage A: first live attempt stopped by the DNS guard (partial output) | **RAW-VERIFIED (partial, owner-pasted)** |
| G8 Stage A: second live attempt, check A06 failed | **RAW-VERIFIED (owner-pasted)** |
| G8 Stage A: later 7/7 pass | **REPORTED** (raw output unavailable; accepted as reported-only by the project owner, section 6.5) |
| G8 Stage B: 25/25 | **REPORTED** (raw output unavailable; accepted as reported-only by the project owner, section 6.5) |
| G8 Stage B: request count, 31 reported versus 34 predicted | **UNRESOLVED** (accepted as an unresolved limitation by the project owner, section 6.5) |
| G8 Stage C: 3/3 | **REPORTED** (raw output unavailable; accepted as reported-only by the project owner, section 6.5) |
| G8 live modes disabled in the current lab source | **RAW-VERIFIED** for the current source only; the lockdown after the live runs is **REPORTED** |
| Local G7 end-to-end run (four runs) and lab cleanup | **RAW-VERIFIED** (log is temporary, see section 3) |
| Backend revision `enc-auth-g7-2`: tagged-URL checks, 10 of 12 passed; the 2 failures are explained (section 10.1) | **RAW-VERIFIED** |
| Fresh-account run on the tagged URL: 8 PASS, 1 FAIL as scripted (section 10.2) | **RAW-VERIFIED**; revocation of old sessions is **not claimed** |
| `enc-auth-g7-2` serving 100% of traffic at 2026-10-30 06:51 UTC (section 10.3) | **RAW-VERIFIED** for that moment only; who moved the traffic is **REPORTED** |
| Production AAB `1.0.12 (26)`: signature, contents and configuration checks (section 11) | **RAW-VERIFIED** for 22 of 23 logged checks; the 23rd is explained |
| Upload of that AAB to Google Play | **UNRESOLVED** (no evidence in this record) |
| Commits, offline suites, backup and trial restore (section 12) | **RAW-VERIFIED** (terminal output of 2026-10-30) |

## 5. G3 Phase 5: live custom-token test (test project `epicverse-g3-auth-test`)

**Status: RAW-VERIFIED.**

**Source:** transcript line 7477 (2026-10-23 09:43 UTC), the pasted terminal output of `g3_live_custom_token.py`.

Observed in that output:

- Exactly 5 HTTPS requests ("requests used: 5 of 5; live approval closed"). No private key was used or downloaded.
- Password sign-in, public certificates, `signBlob`, custom-token sign-in and refresh all returned `outcome=ok` (HTTP 200).
  The custom-token sign-in reported `is_new_user=False`.
- The login ID token, the custom-token ID token and the refreshed ID token all reported `signature_verified=True`,
  `uid_matches_expected=True`, `email_claim_present=True`, `email_verified=false`, `auth_time_present=True`,
  `auth_time_is_recent=True` and `exp_after_iat=True`.
- `sign_in_provider` was `password` for the login token and `custom` for the custom-token and refreshed tokens.
- Login versus custom-token comparison: `uid_unchanged=True`, `auth_time_preserved=True`, `provider_unchanged=False`.
  Custom-token versus refreshed comparison: `uid_unchanged=True`, `auth_time_preserved=True`, `provider_unchanged=True`.
- Backend-assumptions line: the deployed `/sync-user` and `/auth/mark-verified` read the e-mail claim directly;
  `g6_helper_needed=False`, `provider_is_custom=True`, `recent_login_fresh=True`.

Related offline evidence: the offline G3 suite (239 tests) passed on 2026-10-23 (transcript lines 7430 and 8121)
and again on 2026-10-28 (exit code 0, no external network attempt made).

**Limitations:**

- One dummy account in one test project, one run.
- The custom token was signed through an impersonated one-hour access token for a dedicated signer service account.
  The production gateway signs with the Cloud Run runtime identity, which this run did not exercise.
- The observed `email_verified` value was `false` only; a verified account was not tested.
- The raw output exists only as a transcript record, not as a saved file in the repository.

## 6. G8 live stages (test project `epicverse-g3-auth-test`)

The G8 harness limits the live run to 45 requests in total: Stage A 8, Stage B 34, cleanup 3 (see
`scripts/enc_auth_poc/g8/README.md`). The live stages were run by the project owner in their own terminal.

### 6.1 Stage A (existing dummy account, no writes, cap 8 requests)

- **First live attempt: RAW-VERIFIED (partial, owner-pasted), transcript line 9632.** It stopped before any check:
  `stage=A clean=False stop=dns_host_not_allowed`, `requests total=1 sign_in=1`. It was investigated and fixed offline.
- **Second live attempt: RAW-VERIFIED (owner-pasted), transcript line 9667.** `stage=A clean=False stop=-`; checks A01–A05
  passed, **A06 failed**, A07 passed; `requests total=7 certs=2 custom_exchange=1 lookup=1 sign_blob=1 sign_in=2`.
  The failure was analysed offline (transcript line 9718); the likely cause identified was a clock-skew difference between
  the Admin SDK's zero-tolerance token check and the 300-second tolerance used elsewhere. That cause is an **inference,
  not a proven result**. No assertion was weakened.
- **Later passing run: REPORTED.** The owner reports that all 7 checks passed using exactly 8 requests (transcript lines
  9722, 9875, 9950). The raw report is **not available**.
- The "A01–A07 pass" lines at transcript line 9711 come from an offline run against fake network ports, not from Firebase.

### 6.2 Stage B (one disposable account, cap 34 requests)

- **REPORTED:** 25/25 checks passed, `clean=True`, 31 requests used, and a cleanup handle was printed (transcript line 9999).
  The raw report is **not available**. The handle is not reproduced in this file.
- **UNRESOLVED: 31 requests reported versus 34 predicted** by the offline request model. An untested hypothesis was
  recorded at transcript line 10015: cached certificate fetches inside the Admin SDK. The run stayed within the 34-request cap
  and was reported clean, but the difference has not been explained or confirmed.

### 6.3 Stage C (cleanup of the Stage B disposable account only, cap 3 requests)

- **REPORTED:** 3/3 checks passed, `stage=C clean=True stop=-`, 3 cleanup requests (transcript line 10019).
  The raw report is **not available**. This status is reported-only and is not upgraded here.

### 6.4 State of the live-mode switches (qualified)

- **Current source (RAW-VERIFIED, inspected 2026-10-29):** in `scripts/enc_auth_poc/g8/g8_guard.py`,
  `LIVE_EXECUTION_APPROVED = False` (line 38) and `STAGE_APPROVED = {"A": False, "B": False, "C": False}` (line 41).
  The offline G8 test `tests/test_runner_static.py` checks that `LIVE_EXECUTION_APPROVED` is literally `False` (line 355);
  the offline G8 suites passed on 2026-10-28 (exit code 0).
- **What this does not prove:** it shows the state of the source on the dates above, not the state during or immediately
  after the live runs. That the switches were set back to `False` after the live stages is **REPORTED** by the owner
  (transcript line 10019). Until 2026-10-30 the `scripts/enc_auth_poc/` folder was not under version control, so its
  state could change without any Git record. It is now committed locally in `1affd11` (not pushed); changes after that commit are recorded by Git, but the
  state between the live runs and that commit is not.

### 6.5 Project owner decision (recorded 2026-10-29)

- The project owner has decided to **accept the later G8 Stage A run (7/7), Stage B (25/25) and Stage C (3/3) as
  REPORTED-ONLY**, because the raw output of the passing runs is unavailable. These items stay **REPORTED**. The decision does
  not change their evidence status and does not make any of them RAW-VERIFIED.
- The project owner has also decided to **accept the Stage B request-count discrepancy (31 reported versus 34 predicted) as an
  unresolved limitation** of this PoC record. The discrepancy has **not** been explained or resolved, and the hypothesis in
  section 6.2 remains untested.
- Nothing is removed or reinterpreted: the raw Stage A failures in section 6.1 (the DNS-guard stop and the failed A06), all
  evidence references and all limitations stay as written.
- This is a decision about how this record treats the gaps. It is not a statement of production readiness (see section 8).

## 7. Local G7 end-to-end run (Docker lab and Firebase Auth Emulator)

**Status: RAW-VERIFIED** (from the temporary log named in section 3 and from the session transcript).

- Date and time: 2026-10-28, 12:24:07 to 12:26:24 (local time).
- Command (from the repository root): `python -B -I scripts/enc_auth_poc/local_flutter/run_local_e2e.py`
- Exit code: 0.

| Run | Passed | Skipped | Failed |
|---|---|---|---|
| Valid key (`ENC_AUTH_MODE=required`) | 7 | 1 (skipped by design) | 0 |
| Fail-closed: mismatched key id | 1 | 7 | 0 |
| Fail-closed: malformed public key | 1 | 7 | 0 |
| Fail-closed: missing key | 1 | 7 | 0 |

In each fail-closed run the single passing test checks that no sealer exists and that every operation fails with
`enc-auth-unavailable` without a request reaching the lab. The 7 skipped tests in those runs are the valid-key tests.

**Cleanup (verified after the run):** the runner printed "lab removed"; no `g6b-*` container and no `g6b-net` network remained;
port 9099 was free; an unrelated Postgres container was left running untouched; the repository's HEAD and working-tree
status were unchanged.

## 8. What this record does not establish

- **Production readiness.** Nothing here approves an `ENC_AUTH_MODE=required` release. Section 10 records that the backend revision
  `enc-auth-g7-2` was serving 100% of traffic at one observed moment, but that is a point-in-time observation of the backend only. It does not show
  that the client build in section 11 was released, or that the rollout is safe for older client versions. Further validation outside this record
  would be required first.
- **Whether the code in the repository is the code that is deployed.** It is not (section 13).
- **Firebase client SDK behaviour.** The local E2E replaces `signInWithCustomToken` with the emulator's REST equivalent
  (see the header of `test/enc_auth/enc_auth_local_e2e_test.dart`). Real-Firebase behaviour of password change,
  revocation, disabled users, recent-login rules and throttling is not covered by it. The G8 live stages exercised the
  G7 adapter against a real test project through a bounded harness, but their raw reports are unavailable (REPORTED only).
- **Adapter and backend scope of the local E2E.** It uses the emulator identity adapter against the deployed-lineage G6b
  backend tree, not the real-Firebase adapter and not the HEAD-based backend port.
- **Any G8 pass as raw evidence.** The later Stage A run, Stage B and Stage C remain REPORTED.

## 9. Closing the open items

The project owner has accepted the items below as limitations of this PoC record (section 6.5). They remain documented
limitations; the evidence gaps are not considered resolved.

- **G8 raw output:** if the terminal output of the passing Stage A run and of the Stage B and Stage C runs still exists
  (PowerShell window or scrollback), saving it would allow upgrading those entries. Until then they stay REPORTED (accepted as reported-only).
- **Stage B request count:** compare the live request counts with the offline model to explain 31 versus 34, or accept
  the difference as recorded. It remains UNRESOLVED (accepted as an unresolved limitation) until then.

## 10. Backend revision `enc-auth-g7-2` (Cloud Run, production project)

Added 2026-10-30. The gateway revision was built from the deployed-lineage tree described in section 13.

### 10.1 Tagged-URL verification

**Status: RAW-VERIFIED.** Source: `evidence\s6_run.log` in the backup (run of 2026-10-29) and old transcript line 19361.

- The tagged URL maps to the revision while it had 0% of traffic. The script refused any other host; the main URL was not used.
- 10 of 12 checks passed: valid-key login and reauth (HTTP 200, custom token returned), the custom token accepted by Identity Toolkit, wrong key and
  tampered ciphertext and tampered header all rejected (HTTP 400 `AUTH_FAILED`), legacy plaintext Firebase sign-in, legacy `GET /user/<uid>` and
  `GET /auth/session-status`, and the account unaffected afterwards.
- **The 2 failures** are the two "legacy mark-verified, bogus proof" checks, which received HTTP 200. The follow-up check printed
  `email_verified = True` for the test account (old transcript line 19349), so the endpoint answered without testing the proof. Those two
  checks therefore did not exercise the refusal path in that run; the refusal path is covered by section 10.2.
- Signup, `change_password` and the mismatched-claim test were not run in this run (old transcript line 19361).
- The account used is the test account from the earlier approved live stage. It lives in the production Firebase project and database.

### 10.2 Fresh-account run on the tagged URL

**Status: RAW-VERIFIED** (terminal output, current transcript line 1008, 2026-10-30 05:43 UTC). Main URL not used; 20 requests; 8 PASS, 1 FAIL, 5 INFO.

- Encrypted login HTTP 200. `sync-user` returned HTTP 403 on the first resumed attempt (current transcript line 978, "invite/email problem" category only)
  and HTTP 200 on the next run; this record does not explain the first refusal.
- `mark-verified` with a bogus proof: HTTP 403, account still unverified. `mark-verified` without a proof (the old-client case): HTTP 403, still unverified.
- `change_password`: HTTP 200 with `password_changed` and `sessions_revoked` both true. Old password rejected, new password accepted (encrypted login);
  legacy Firebase sign-in fails with the old password and works with the new one.
- **The 1 failure** is the scripted check "old refresh token rejected", which received HTTP 403 where the script expected a different outcome.
  A later read-only lookup of the account's revocation timestamp was run in the same session; its raw output was not re-located for this update.
  **Revocation of old sessions is therefore not claimed here.**
- **Observation, not a verdict:** the old ID token was still accepted by the backend (HTTP 200). The source text shows why this can happen:
  `api/dependencies.py:22` verifies bearer tokens with `auth.verify_id_token(token)` and no revocation check (current transcript line 1036), while the gateway
  verifies with `check_revoked=True`. An ID token issued before a password change can stay valid on the legacy path until it expires, normally about one hour.
- The test account was left in place; cleanup needs its own approval.

### 10.3 Traffic at one moment

**Status: RAW-VERIFIED for that moment only.** Source: current transcript line 1307 (2026-10-30 06:51:12 UTC).

- Traffic: 100% to `enc-auth-g7-2`; it was also the latest ready revision.
- Last 12 hours: 78 requests (48 from the Dart app) and 0 responses with a 5xx status. `/api/v1/auth/enc/nonce` returned 404 on
  `mfa-enforce-log-1` (9 times) and `email-proof-2` (once), and 200 on `enc-auth-g7-2` (5 times). `[ENC-AUTH]` log lines: login ok 7, login refused 3,
  `change_password` ok 1, signup ok 1.
- That the traffic move was made by the project owner is **REPORTED**; the command output is not part of this record. The state after 06:51 UTC was not observed.
- Earlier tests through the tagged URL touched production data: the tagged URL and the main URL use the same production Firebase project and database.

## 11. Release candidate: Android App Bundle `1.0.12 (26)`

**Status: RAW-VERIFIED for 22 of the 23 logged checks.** Source: `evidence\s7_verify.log` (preserved in the backup) and old transcript lines 19711 and 19746.

| Property | Observed |
|---|---|
| Package, version | `com.kriyora.epicverse`, versionName `1.0.12`, versionCode `26`; not debuggable |
| AAB | 54,135,438 bytes, SHA-256 `e67cf8cfd119a0f4545be099c7da1dda4f2a3486375ec5bd86516aececa55097` |
| Signer | SHA-256 `6ef6161b8e125a143cce533930fe20eff37f42131c0a160df1fd57e0c9a565b3`, equal to the upload certificate and not the Android debug certificate |
| Network security config | 2 domain blocks, no cleartext, no loopback host; equals the production source including all pins |
| Lab mode | No lab string in any of the three AOT libraries; no debug kernel; no `usesCleartextTraffic` |
| Encrypted-auth configuration | The three ABIs contain the approved key id `k1UVTYjINgKKr`, the approved public key, the production URL and `EncryptedPasswordAuth`; none contains `LegacyPasswordAuth`, the tagged test host or a private-key marker |
| Symbols and mapping | `native-debug-symbols.zip` (18,784,606 bytes) and an R8 `mapping.txt` (21,907,981 bytes) were produced |

- Built offline from the committed blobs of `05b1239`, not from the working tree; the uncommitted local-lab files are not in it.
- **The 23rd check** ("pub get --offline log: resolved, no download") failed in the logged run. The log contains the heading line `Downloading packages...` and ends with
  `Got dependencies!`; the assistant analysed the failure as a faulty check (old transcript line 19711) and reported all checks passing after correcting it
  (line 19746). The preserved log is the first run; the corrected run's raw output was not re-read for this update.
- An earlier build with versionCode 25 was rejected for the wrong version and must not be uploaded.
- **Not established:** that the bundle was uploaded to Google Play, its review or rollout state, and Play App Signing status. **UNRESOLVED.**

## 12. Source state, commits and preservation

**Status: RAW-VERIFIED** (terminal output of 2026-10-30).

- Before this update was committed, local `main` was 3 commits ahead of `origin/main` (still at `05b1239`); nothing has been pushed:
  `c66fbf6` backend gateway port (14 files), `1affd11` proof-of-concept folder (147 files), `97cdb77` the first version of this document.
  Five files of a debug-only local-lab mode (`api_client.dart`, `main.dart` and three new files) are deliberately left uncommitted.
- Two pins in `scripts/enc_auth_poc/approved_integration.py` were refreshed after reviewing the diffs: `enc_auth_store.py` (+2 lines, an early return while the
  gateway is disabled) and `test_enc_auth_port.py` (+45/-5 lines, new schema tests). This document's path was allow-listed by its exact name.
- Offline static suites: 180 tests passed in an isolated copy without the local-lab files. In a clean clone of the committed state, the approved-integration
  (66), G7 (14) and G8 (33) suites passed; G3, G6a and G6b had one failure each, a check that the repository has 11 branches (a clone has one), and those three tests
  pass in the live repository. Docker-based and live-Firebase suites were not run for this update.
- Backup: `D:\Company_Projects\EpicVerse-G0G8-Backup-20261030-154927` verified (6,862 manifest entries), and a trial restore from it passed `-VerifyRestore`
  (HEAD, status, stashes, index hash and every file). A fresh checkout does not reproduce the working-tree files byte for byte under `core.autocrlf=true`
  (65 files differ in line endings), which is why the backup keeps byte-exact copies of the tracked files. It is on the same disk as the repository, and
  its `.git` copy is not scanned for secrets.

## 13. The deployed backend versus the repository

- **Deployed lineage (RAW-VERIFIED by hashing the preserved copies on 2026-10-30):** the uploaded tarball `g7.tgz` is 608,748 bytes with SHA-256
  `4e03c16fb9a54a5bba27e76554a3a6e533d03daf0d118162603487b531e98cb6`; the base archive (`archive2.tgz`, identical to `proof2.tgz`) has SHA-256
  `7e96f49c75f0352e497aafe0c07d65cdc7251866f4461fc1d9db3a595664e764`.
- **The repository port is not byte-identical to it.** In `c66fbf6`, `enc_gateway.py` checks email verification through `email_verification_proof_is_live` and the
  `email_verification_proofs_v2` table, where the deployed build uses `email_ownership_proofs`, and `routes.py`, `config.py` and `user_db.py` differ more widely.
  The eight `enc_auth_*` service files and `requirements.txt` were found identical to the deployed ones in an earlier comparison; that comparison was not repeated for this update.
- **Consequence:** a backend build made from `main` would not be the build that production runs. Closing this gap, by aligning the port with the deployed
  lineage or by redeploying from the repository, is outside this record.
