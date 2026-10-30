# head_port - lab verification of the encrypted-auth gateway as integrated into `backend/` (LOCAL ONLY)

The gateway (G6a/G6b/G7) now lives in `backend/` itself (disabled by default). This folder holds the **adapted copies** of the G6b/G7 lab harness and
integration tests that run against the backend of the *working tree* instead of the hash-gated old-lineage build trees. The originals in `g6a/`,
`g6b/` and `g7/` are unchanged (their build gates and pinned patch hashes still describe the `d733776` lineage).

## What differs from the originals
* **Tree under test**: `tools/export_backend_tree.py` exports exactly what Git sees of `backend/` (tracked + new, not ignored); the lab mounts that copy.
  `PORT_TREE=<dir containing backend/>` overrides it (for example the export of another checkout).
* **Proofs** use HEAD's own system: the lab helper issues a proof with `user_db.issue_email_verification_proof`; "age a proof" edits
  `email_verification_proofs_v2`; the proof lifetime asserted is HEAD's 15 minutes; the verify-request tests look at `mfa_login_challenges`.
* **Paths**: the copies find the shared PoC modules (`testkeys`, `envelope`, `g2`, `g4`) in the real `scripts/enc_auth_poc`.

## Run (Windows, Docker Desktop running, images `epicverse-backend:tests-c185263` and `postgres:17-alpine` present locally; nothing is pulled)
```
cd scripts/enc_auth_poc/head_port/g6b/tests
python -B -I -m unittest discover -s . -p "test_integration_proof_email_uid.py"      (also: ..._ratelimit, ..._reliability, test_g6a_regression_on_g6b_tree)
cd ../../g7/tests
python -B -I -m unittest discover -s . -p "test_gateway_with_real_adapter.py"
python -B -I scripts/enc_auth_poc/head_port/local_flutter/run_local_e2e.py           (from the repository root; Flutter app E2E, 4 variants)
```
The image carries the backend's own pins (firebase-admin 6.2.0, httpx 0.25.2, fastapi 0.103.2, cryptography 50.0.2). The backend's own pytest suite
(`backend/tests`, including `test_enc_auth_port.py`) runs in the same image with `--network none`:
`docker run --rm --network none -v <export>:/src:ro -w /src/backend --entrypoint python epicverse-backend:tests-c185263 -m pytest -p no:cacheprovider -q tests`.

## Not covered
Real Firebase (covered once by G8), Cloud Run, production data and migrations, iOS.
