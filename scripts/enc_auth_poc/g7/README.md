# G7 - selectable real-Firebase identity adapter for the encrypted gateway (OFFLINE ONLY)

The encrypted-authentication gateway (G6a/G6b) talks to its identity service through one module, `enc_auth_identity.py`, which today only works
against the Firebase Auth **emulator**. G7 adds a second, separately selectable adapter for **real Firebase**, built entirely behind injected
ports and tested only with fakes. **Nothing here connects to Firebase, IAM or any Google service, and nothing here is proven to work against
real Firebase.** It is a preparation step.

## What is in this folder
| Path | Purpose |
|---|---|
| `enc_auth_gateway_g7_firebase.patch` | One patch on top of the G6b tree: 1 new module (`enc_auth_identity_firebase.py`), 4 small edits (settings, 6 diagnostic cause codes, a 1-line gateway tweak, and an **appended** selection block in `enc_auth_identity.py`), plus a 2-line guard in `enc_auth_store.ensure_schema`: while `ENC_AUTH_ENABLED` is false (the default) the gateway runs NO schema work at all - no `enc_auth_*` table, no index. G6a/G6b patches are untouched. |
| `build_tree_enc_auth_g7.sh` | Hash-gated build: builds the G6b tree through the unchanged G6b script, then applies this patch, then appends the one dependency line `cryptography==50.0.2` to `backend/scripts/requirements.txt` (own line-ending style kept, refused if cryptography is already named, every earlier byte verified untouched). Refuses on any changed link, a missing file, a non-empty destination, a patch conflict or a changed OTP limiter. |
| `harness/` | `fake_firebase.py` (in-memory fake accounts, REST, Admin and signBlob ports), `server_g7.py` + `orchestrate_g7.py` (the G6b Docker lab with two extra variants, no network). |
| `tests/` | 52 offline unit/contract tests, 22 gateway-level tests, 13 build/static tests. |
| `tools/` | `make_patch.py`, `pin_patch_hash.py`, `run_mutants.py` (deliberate defects must be caught), `run_g6b_regression_on_g7.py` (the unchanged G6b integration tests on the G7 tree). |

## Selection and fail-closed behaviour
`ENC_AUTH_IDENTITY_BACKEND` = `emulator` (default: the existing adapter, byte-for-byte unchanged) | `firebase` | **anything else = every operation fails closed**
(a typo can never fall back to the emulator). With `firebase`, `ready()` is true only if ALL hold, otherwise every endpoint answers 503 with cause `not_configured`:
* `ENC_AUTH_FIREBASE_PROJECT_ID` is set and equals `FIREBASE_PROJECT_ID`, and is not a `demo-` project;
* `FIREBASE_AUTH_EMULATOR_HOST` is **not** set (the Admin SDK would silently talk to an emulator);
* no key file is configured (`GOOGLE_APPLICATION_CREDENTIALS` unset) - keyless only, the runtime identity is used;
* `ENC_AUTH_FIREBASE_WEB_API_KEY` has the Web API key shape;
* `ENC_AUTH_SIGNER_SERVICE_ACCOUNT` is `<id>@<project>.iam.gserviceaccount.com` for that same project.

## The three ports (each injectable; fakes are used in every test)
1. **Identity Toolkit REST** (`accounts:signInWithPassword`): the only place a password is verified. One attempt per call, retried only for 502/503/504 and transport trouble, at most twice.
2. **Firebase Admin SDK**: get user, create user (always `email_verified=false`), set password, revoke refresh tokens, verify ID token **with revocation and disabled-user checks**.
3. **IAM `signBlob`**: Google signs the custom token (same header/claims builder that was proven live in G3; a parity test compares them byte for byte). No private key exists in the process.

## Semantics preserved from the emulator adapter
Same function names and exception classes, so the gateway is unchanged: unknown e-mail and wrong password give the identical refusal; a disabled account gets no token even
with the right password; throttling is a 429 and never counts as a wrong password; creation, password update, revocation and signing are **never retried**; reads are retried at
most twice inside the existing budget; every failure becomes a fixed (stage, cause) code, never a library message, URL, key, token or password.

## Re-authentication and password change (security-sensitive)
* A custom-token session is **never** treated as proof of the password. Every `login`, `reauth` and `change_password` still verifies the password server-side through the REST port in
  that same request, and `reauth`/`change_password` additionally need a bearer ID token that passes the revocation-aware check and belongs to the same uid as the password.
* Password change uses the Admin SDK **only after** that verification, then revokes all refresh tokens, then signs a fresh custom token. If the update outcome is unknown the answer
  says so (`password_changed: "unknown"`); if revocation or signing fails after a successful update the answer says exactly that.
* The Firebase client rule "recent sign-in required" is replaced by a stronger check (the current password, proven now). **Not verified live:** that revoking refresh tokens and immediately
  exchanging a new custom token behaves as intended within the same second (the tests wait one second, as the emulator tests do).

## What the tests prove, and what they cannot
They prove the adapter's logic: request shapes, error mapping, retry rules, ordering (update -> revoke -> sign), fail-closed configuration, no leaks, the unchanged emulator path.
They cannot prove real Firebase behaviour: password-policy enforcement, `check_revoked` timing, disabled-user answers, throttling thresholds, `signBlob` with a runtime service account.

## Proposal only: what production would need (nothing here was done)
* A signer service account with **no roles**; the Cloud Run runtime service account gets `roles/iam.serviceAccountTokenCreator` on that signer only.
* The IAM Service Account Credentials API enabled in the production project.
* The runtime service account allowed the Firebase Authentication admin operations the Admin port uses (exact minimal permissions to be confirmed).
* Secret Manager entries for the Web API key, `ENC_AUTH_KEYS` and `ENC_AUTH_HASH_SECRET`; settings `ENC_AUTH_ENABLED=true`, `ENC_AUTH_IDENTITY_BACKEND=firebase`, project id and signer.
* Email-enumeration protection ON; the Web API key must not be restricted to HTTP referrers (server calls carry none).
* The gateway code exists only as patches over the deployed lineage (commit `d733776` + four patches), not in `backend/`: that integration decision is separate.
