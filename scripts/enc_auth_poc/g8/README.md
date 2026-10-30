# G8 - bounded real-Firebase compatibility test harness for the G7 adapter (OFFLINE ONLY, LIVE EXECUTION DISABLED)

G8 prepares - and only prepares - a small, strictly bounded live test of the G7 real-Firebase identity adapter against the one test project
`epicverse-g3-auth-test`. **Status: LOCKED DOWN.** The live tests completed (Stage A 7/7, Stage B 25/25, cleanup 3/3 with the disposable account deleted). As committed,
`LIVE_EXECUTION_APPROVED = False` and `STAGE_APPROVED = {"A": False, "B": False, "C": False}` in `g8_guard.py`: every mode refuses before any prompt or network call.
Re-enabling any mode needs a new explicit approval. Access tokens are never obtained by the code: they are pasted at hidden prompts.

## Files
| File | Purpose |
|---|---|
| `g8_guard.py` | The egress guard: hardcoded project, exact host + path allowlist, HTTPS only, bounded timeouts, no proxy, no redirect, per-kind quotas and a hard cap, three transport layers feeding ONE budget, socket backstop, fail-closed `cap_guaranteed()`. |
| `g8_protect.py` | Account protection: `RunState`, `GuardedIdentity`, `GuardedAccountOps`, per-class mutation confirmations. |
| `g8_revocation.py` | Pure classifier for the whole-second revocation timestamp edge cases (`revoked` / `valid` / `failed` / `ambiguous`). |
| `g8_stages.py` | Stage A (dummy, read-only), Stage B (disposable account), Stage C (cleanup). Collaborators are injected. |
| `g8_wiring.py` | Builds the real ports around one `Egress`: pinned G7 tree, bearer-credential Admin app (no ADC, no key file), G3 session facade, Admin account ops. |
| `g8_live_run.py` | The interactive runner (hidden prompts, typed confirmations). Refuses to do anything as committed. |
| `tests/` | 157 offline tests (see below). `loopback_probe.py` is a subprocess that can reach 127.0.0.1 only. |
| `tools/run_mutants.py` | Test-of-the-tests: breaks a copy of the harness 29 ways; every defect must be caught. |

## What is reused, not rewritten
* G7 `FirebaseIdentity` + `HttpxIdentityToolkit` + `IamSignBlobPort` + `FirebaseAdminPort` (hash-pinned build, `ADAPTER_PINS`);
* G3 `FirebaseRestClient` (refresh, custom-token exchange, certificates), `FirebaseSignatureVerifier`, `assert_request_safe`, `Secret`, `LiveTransport`;
* the G7 fake Firebase world and network-denial layer for every test.

## The request cap (task item 7)
Every request that any library in the process is about to send is charged to one `Egress` BEFORE it is sent:
1. **urllib3 `HTTPConnectionPool._make_request`** - called once per wire attempt. This is where the Firebase Admin SDK and google-auth traffic is counted, **including
   the SDK's own retries** (`firebase_admin` uses `Retry(connect=1, read=1, status=4, 500/503)` for every method): proven against the real SDK over a fake wire and
   against a real loopback server (charged == requests the server saw, for 503 retries, connect retries and read retries). Redirects are never followed; SDK default
   timeouts (google-auth: 120 s, or none) are clamped to connect 5 s / read 10 s.
2. **httpx transport** (G7 sign-in and signBlob ports): `retries=0`, `trust_env=False`, no redirects; the adapter's own retries (`ENC_AUTH_RETRY_MAX`, forced to 0 by the
   runner) are charged too.
3. **G3's stdlib transport** (refresh, exchange, certificates), after G3's own request validation.

Below them, a **socket backstop**: names resolve only for the four allowed hosts, connections only to addresses resolved for them, connections capped at the stage total,
loopback (asyncio's internal pair) capped separately. `cap_guaranteed()` is `False` unless all of it is installed, the library versions are the validated ones
(`urllib3 2.x`, `requests 2.x`, `httpx 0.28.x`, `firebase_admin 6.4.x`), adapter retries are 0 and the environment has no proxy, TLS override, credential or emulator
variable. **The runner refuses to read a secret or open a socket otherwise.** Any refusal trips the guard; every later request is refused as well.

Quotas (measured, not assumed; the SDK reads a record back after `create_user` and `update_user`): Stage A 8, Stage B 34, cleanup 3 - 45 in total, which is also
`GLOBAL_REQUEST_CAP`. There is deliberately no slack: an unexpected extra SDK request stops the run.

## Stages
| Mode | Account | Writes | Requests | Confirmation |
|---|---|---|---|---|
| Stage A | the existing dummy account | **none, ever** (every mutating method and every mutating request is refused) | 8 | project id + stage phrase |
| Stage B | ONE disposable account the run creates (`g8-<16 hex>@example.com`, generated password; the dummy identity is not even given to this stage) | create (+1 duplicate probe), password update, revoke, disable, enable - all on that account only | 34 | project id + stage phrase + FOUR separate phrases |
| Cleanup | the Stage B account, found by its printed handle | delete that account only | 3 | project id + stage phrase + delete phrase |

Protection layers for the dummy account: (1) Stage A has no mutation quota at the transport; (2) `GuardedIdentity` refuses every mutating method in Stage A;
(3) Stage B never receives the dummy uid or e-mail; mutations need `uid == created_uid`; update/delete request bodies must name exactly the bound disposable uid;
(4) a custom token is minted only for a uid confirmed to exist in this run (the exchange creates unknown accounts) and `isNewUser` must be exactly `False`, else a critical stop;
(5) cleanup adopts only an account whose e-mail has the generated shape and whose uid is not the dummy's (the cleanup run is given the dummy uid at a hidden prompt, used only as a digest to refuse).

## Revocation timestamp edge cases
Firebase stores `validSince` in whole seconds and rejects a token only if `iat < validSince`, so a token issued in the revoke's own second is accepted. The classifier
returns `ambiguous` (never a pass) when timestamps are within `SKEW_SECONDS` (2 s) of the revoke window, when the answer was an error/outage, or when facts are missing;
`failed` when the answer contradicts a provable ordering. The stage waits `SETTLE_SECONDS` (3.5 s) before the fresh sign-in. A run with any `ambiguous` check is not clean.

## Cleanup
Stage B never deletes. It prints a **cleanup handle** (`g8-<16 hex>`, a random token, not an e-mail or uid) - also after a cancel, Ctrl-C or an unknown create outcome. The
separate cleanup mode needs its own approval constant and its own typed confirmation. Not executed here.

## Tests
`python -B -I -m unittest discover -s tests` from this folder, with the network-denial layer from G7 active (any socket or name resolution raises, except 127.0.0.1 in the
subprocess probe). Highlights: guard policy (shapes, quotas, concurrency), SDK request shapes per operation with the real `firebase_admin`, SDK retries counted, loopback
wire proof, stage scripts on the fake world with exact request counts, UID protection, cancellation, fault handling, ambiguity, no secret in any output, static
checks (only `.py`/`.md`, no key/ADC/gcloud/subprocess/logging, constants literally `False`).

## What this cannot prove / remaining before live
* Real Firebase behaviour: password-policy enforcement ("Notify"), `check_revoked` timing, throttling thresholds, `signBlob` with the intended topology, minimal Admin permissions.
* The `g8-admin` service account, its role and the impersonated one-hour access token do not exist yet (needs IAM work - NOT done).
* The wiring (`g8_wiring.py` / `execute`) is only exercised piecewise offline; its first full run will be the live one.
* The Admin SDK request cap relies on the validated library versions; a library upgrade requires re-running the loopback proof and updating `VALIDATED`.
