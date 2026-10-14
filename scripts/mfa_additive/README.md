# Additive MFA backend (local implementation, not deployed)

`mfa_additive.patch` turns the **deployed** backend (commit `d733776`) into the additive release described in the MFA plan:
it adds the server-side MFA routes (`/auth/session-status`, `/auth/mfa/challenge|verify|logout`,
`/user/mfa/enable-request|enable-confirm|disable`), closes the legacy `/user/update-mfa` bypass, and carries the Excel-loader
fix. It also modifies `user_db.py` so that `/sync-user` can no longer change MFA state or the verified email of an existing user (it updates only display name, language and picture, and new accounts always start with MFA off). It does **not** touch the OTP rate limiter, `main.py`, config or dependencies, and needs no database change
(the MFA tables already exist in production).

```bash
bash scripts/mfa_additive/build_tree.sh <empty-dir>        # d733776 + patch  ->  <empty-dir>/backend
bash scripts/mfa_additive/run_local_tests.sh               # backend tests + Flutter contract tests (needs Docker + Flutter)
bash scripts/mfa_additive/run_local_tests.sh --no-flutter  # backend tests only
```

`run_local_tests.sh` is local only: an internet-less Docker network, a throwaway Postgres, a fake identity service and
captured (never sent) email, with fake users. Everything it creates is removed on exit. `harness/` is test infrastructure
and is never part of a deployment. The deployed `backend/` folder on `main` is the newer, unreleased code (`e121583`) and is
**not** what this patch targets.
