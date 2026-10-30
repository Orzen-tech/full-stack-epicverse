# G3 custom-token signing review and compatibility test plan (PLAN ONLY - nothing here mints, signs or sends anything)

Based on the local code (G6a/G6b identity adapter, G3 guard, Phase 2/3 evidence). No cloud request was made to investigate permissions;
anything that depends on Google's side is marked **UNVERIFIED**.

## 1. What the gateway needs
A Firebase custom token is a JWT (RS256) with `iss = sub = <service-account e-mail>`, `aud = Identity Toolkit`, `uid`, `iat`, `exp`. Firebase
accepts it at `accounts:signInWithCustomToken` if the signature verifies against that service account's public key. **Whoever can sign can
impersonate any user of the project**, so the signer is the security-critical choice.

## 2. Signing options (local evidence: the G6 adapter currently signs through the Admin SDK, i.e. a private key or Google-managed signing)
| Option | Private key on disk? | Needs | Main risk |
|---|---|---|---|
| A. **IAM `signBlob`** (Google signs; the key never leaves Google) | **No** | a service account; the IAM Service Account Credentials API enabled; `roles/iam.serviceAccountTokenCreator` on that account for the CALLER (and for the account itself when it signs as itself); a short-lived caller credential | the caller credential: it must not be your everyday Google login, or it can probably reach production too |
| B. Short-lived key of a role-less service account | yes, for minutes | Console key creation, deletion right after | key theft while it exists; can mint tokens for any uid of this project |
| C. Key of the default `firebase-adminsdk` account | yes | Console download | the key is also a broad Firebase Admin (users can be created/deleted) |

### Recommendation: A, with an impersonated short-lived token (no downloaded private key)
* A dedicated service account `g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com` with **no project roles**.
* It is granted `Service Account Token Creator` **on itself only** (so it may call `signBlob` for itself), and your own account is granted the same role on it (so you can obtain a one-hour access token **as that service account**). That token can do nothing except sign for this one account; it is NOT your personal login and has no access to production.
* You obtain the token yourself in your own terminal (for example by impersonating the service account with your gcloud) and paste it at a **hidden prompt**; my code never runs gcloud, never reads ADC, never stores the token. It lives in memory for one run (at most an hour).
* One new request class is needed and must be approved separately: `POST https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/<account>:signBlob` (body: the signing input, base64). Plus `POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken`.
* Spark: IAM and service accounts are free. **UNVERIFIED:** that enabling the IAM Credentials API on a Spark project needs no billing account (it normally does not for this API, but I have not confirmed it for this project). Enabling the API is a cloud change and needs your explicit approval.
* **UNVERIFIED:** that Firebase accepts custom tokens signed (through `signBlob`) by a service account that has no Firebase role. The documented Admin-SDK-on-Google-Cloud path signs this way, which is encouraging but not proof for a role-less account.

### Unresolved prerequisites for A (you do these in the Console / your terminal; nothing is done by Claude)
1. Create the service account (no project roles).
2. Enable the IAM Service Account Credentials API on `epicverse-g3-auth-test`.
3. Grant `Service Account Token Creator` on the account to itself and to your own Google account.
4. Confirm the project is still on Spark with no billing account afterwards.
5. At run time: produce a one-hour impersonated access token for that account and paste it at the hidden prompt.

### Fallback: B (only if A is blocked)
Same account, a Console-created key used for minutes and deleted straight after; the runner reads it from a path typed at a hidden prompt into memory only
and signs locally. Never C.

## 3. Rules for the future signer (enforced in code, tested offline)
* Service account must end `@epicverse-g3-auth-test.iam.gserviceaccount.com`; any other address aborts before a request (`g3_custom_token.validate_service_account`).
* Fixed synthetic uid, lifetime at most one hour, no e-mail claims injected by us (so the test shows what Firebase itself returns), reserved claims refused.
* Guard allowlist extended by exactly the two new request kinds, with single-use budgets, only after approval.

## 4. Test plan for the next (separately approved) live phase
| # | Question | How it is observed (after signature verification) |
|---|---|---|
| 1 | Does Firebase accept the token, and which uid? | sign-in succeeds; `uid_matches_expected` |
| 2 | Is there an `email` claim? | `email_claim_present` (emulator: yes; real Firebase probably no - **UNVERIFIED**) |
| 3 | `email_verified` behaviour | `absent` / `true` / `false` |
| 4 | `sign_in_provider` | expected `custom` |
| 5 | `auth_time` | fresh at sign-in (`auth_time_is_recent`) |
| 6 | Refresh compatibility | refresh works; uid and `auth_time` preserved (`compare_sessions`) |
| 7 | EpicVerse backend assumptions | `backend_assumptions`: deployed `/sync-user` and `/auth/mark-verified` read the e-mail claim directly; without it only the G6a/G6b `resolve_token_email` (Admin lookup by the verified uid) works |
| 8 | Recent-login / MFA logic | `recent_login_fresh`; code keyed on `sign_in_provider == "password"` would see `custom` |

Offline pieces already in place: `g3_custom_token.py` (claims, signing input, assembly, injected signer interface; no real signer),
`g3_compat.py` (`observe`, `compare_sessions`, `backend_assumptions`), and tests with synthetic keys and tokens.

## 5. Remaining approvals
Your signing-method decision; the Console steps above (service account, API enablement, role grants); approval of the two endpoints; a signer
module and a live runner (not written). Account-mutation tests (password change, revocation, disabling) remain out of scope.

---

## 6. Phase 5: IAM `signBlob` architecture, facts vs assumptions (no cloud request was made to check any of this)

| Question | Answer | Status |
|---|---|---|
| Which permission does `signBlob` need? | `iam.serviceAccounts.signBlob` on the target service account (contained in `roles/iam.serviceAccountTokenCreator`) | **Documented fact** (from my knowledge of Google's IAM docs; not re-read today) |
| Which principal needs it? | The identity that AUTHENTICATES the `signBlob` call. In the recommended design that is the signer service account itself (we call with an access token minted for it), so **the signer needs Token Creator on itself**. | Follows from the documented rule; **not exercised** |
| Who needs `getAccessToken` (to obtain that token)? | Your own Google account needs Token Creator **on the signer account** (permission `iam.serviceAccounts.getAccessToken`) so it can impersonate it. | Documented fact |
| Does the signer need project-level roles? | None for `signBlob` itself. **UNVERIFIED:** calling an API may also need `serviceusage.services.use` on the consumer project; if the first live call answers `permission_denied`, the diagnostic category will say so and the fix would be a separate approval (for example `roles/serviceusage.serviceUsageConsumer`) | **Assumption** |
| Must the IAM Credentials API be enabled? | Yes, `iamcredentials.googleapis.com` on the project; a disabled API shows as category `service_disabled` | Documented fact |
| Does it need billing on Spark? | IAM Credentials has no per-call charge, and service accounts are free. **UNVERIFIED** whether enabling the API in this Spark project asks for a billing account. If it does: STOP, do not upgrade. | **Unresolved** |
| Does Firebase accept a custom token signed through `signBlob` by a service account without Firebase roles? | The Admin SDK signs this way on Google Cloud, which is encouraging. **UNVERIFIED** for a role-less dedicated account in this project. | **Unresolved - only the live test can answer it** |
| Does `signInWithCustomToken` create an account? | Yes if the uid does not exist (Firebase reports `isNewUser`). The runner therefore asks for the dummy account's EXISTING uid and reports `is_new_user`. | Documented behaviour; **DESTRUCTIVE TO TEST DATA** if the uid is new |

## 7. Exact Console steps you would do later (none done now; all in project `epicverse-g3-auth-test`)
1. Google Cloud Console -> IAM & Admin -> Service Accounts -> **Create service account** `g3-signer`; grant it **no roles** at project level.
2. APIs & Services -> Library -> **IAM Service Account Credentials API** -> Enable. Watch for any request to enable billing: if one appears, stop and tell me.
3. IAM & Admin -> Service Accounts -> `g3-signer` -> **Permissions** tab -> Grant access: principal `g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com`, role **Service Account Token Creator** (so it can sign for itself).
4. Same tab: grant your own Google account the role **Service Account Token Creator** on `g3-signer` (so you can impersonate it).
5. Firebase Console -> Authentication -> Users: copy the **UID** of the dummy account (you will type it at a hidden prompt; it is not sent to me).
6. Confirm Usage and billing still shows **Spark** with no billing account.
7. At run time, in your own terminal, create a one-hour access token for `g3-signer` by impersonation and paste it at the hidden prompt (the runner never calls gcloud or reads credentials).
8. Afterwards: remove the two Token Creator grants and disable the API (or delete the service account).

## 8. Proposed live requests for the future run (disabled now: `CUSTOM_TOKEN_LIVE_APPROVED = False`)
Exactly **4**, once each, no retries, project id typed to confirm:
1. `POST https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com:signBlob` (bearer token, no API key)
2. `POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken`
3. `POST https://securetoken.googleapis.com/v1/token`
4. `GET https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com`

Code in place (all offline-tested, never run live): `g3_iam_signer.py`, `sign_in_with_custom_token` in `g3_rest_client.py`, `g3_live_custom_token.py` (refuses before any prompt), guard rules `grant_custom_token_approval`, `validate_service_account`, `sign_blob_url`.

---

## 9. BLOCKER found before the live run: the dummy account's uid cannot be independently established
`signInWithCustomToken` CREATES a user when the uid does not exist. The runner takes the uid from a hidden prompt, and nothing the runner can reach
without an extra request proves that the typed value is the existing account's uid:
* Phase 2 verified the dummy account's `sub`, but the runner printed only booleans and stored nothing, so no trusted local copy exists.
* A value typed twice, or checked for shape, proves typing consistency, not existence.
* Looking it up through Firebase needs either the Admin API (not approved) or one more request.

Therefore the custom-token exchange stays disabled (`CUSTOM_TOKEN_LIVE_APPROVED = False`) and no Phase 5 request has been made.

### Options (need a separate decision)
* **A (recommended): one extra approved-endpoint request first.** Before signing anything, send the already-approved password sign-in (Phase 2 endpoint, dummy account, hidden prompts), verify the returned ID token against Firebase's certificates, and compare its `sub` with the typed uid locally. Only on a match continue with signBlob, exchange, refresh and certificates (the certificates would be fetched once and reused). Cost: 5 requests in total instead of 4, plus the dummy password as one more hidden prompt, and a small code change (the guard budget and the runner order). This establishes the uid from Firebase's own signed answer.
* B: copy the uid from Firebase Console -> Authentication -> Users and type it carefully. Cheapest, but it is a human copy, not independent evidence; a typo would create a stray user in the test project (reported afterwards as `is_new_user=True`, harmless to anything else but not what was intended).
* C: Admin lookup (`accounts:lookup`) - needs Admin credentials; not approved and not recommended.
