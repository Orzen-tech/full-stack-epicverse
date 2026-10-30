# EpicVerse encrypted email/password authentication (G0–G8): verification record

Record prepared: 2026-10-29

## 1. Purpose and scope

This file records what evidence exists for the live and end-to-end results of the encrypted-authentication work
(gates G0–G8), and how strong each piece of evidence is. It covers only that work.

It does **not** establish production readiness. It does **not** claim coverage of the Firebase client SDK or of
real-Firebase client behaviour (see section 8).

The Flutter client work is committed as `05b1239` ("feat(security): add encrypted auth G0-G8 Flutter flow") on
`origin/main`. It is off by default: `ENC_AUTH_MODE` defaults to `off`.

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
  (transcript line 10019). The `scripts/enc_auth_poc/` folder is not under version control (it is neither committed nor
  pushed), so this state can change without any Git record.

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

- **Production readiness.** Nothing here approves an `ENC_AUTH_MODE=required` release or shows that the encrypted-authentication
  gateway is live in production. Further validation outside this record would be required first.
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
