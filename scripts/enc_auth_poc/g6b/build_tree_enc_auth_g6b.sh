#!/usr/bin/env bash
# Materializes the LOCAL TEST backend for the G6b gateway hardening:
#     deployed backend (commit d733776) + mfa_additive + mfa_enforcement + mfa_logging_privacy + email_verification_proof
#     + enc_auth_gateway.patch (G6a) + enc_auth_gateway_g6b.patch (G6b)
# Usage: bash build_tree_enc_auth_g6b.sh <empty-output-dir>      -> <output-dir>/backend is ready to test.
# Read-only on the repository. It builds the G6a tree through the unchanged G6a build script (which itself gates the base commit,
# the four production patches, build_tree_proof.sh, the OTP limiter and the G6a patch) and ADDITIONALLY refuses if the G6a build
# script or G6a patch bytes differ, if the G6b patch differs from the reviewed bytes, if a dependency is missing, if the output
# directory is not empty, if the G6b patch does not apply cleanly, or if the OTP limiter is not byte-identical afterwards.
# LOCAL TEST tree only, not a deployment artifact.
set -eu
OUT=${1:?usage: build_tree_enc_auth_g6b.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)
G6A="$HERE/../g6a"
MFA="$HERE/../../mfa_additive"

BASE_COMMIT=d7337763e03ca6c4c5ff234708fbb5162937d194
ADDITIVE_SHA256=edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2
ENFORCEMENT_SHA256=7f3030064bb3dbae84646e64f337f84eef02f94404c414a4ac514db3ef160104
PRIVACY_SHA256=98fc386d154ce7b21b688eb4aab7897e593c9fb86597442891cc996130f821a4
EMAIL_PROOF_SHA256=39d5da68992e7520480fdfad3155dd853ac0b1ea8d3eb24ccf056c63350ae49d
BUILD_PROOF_SHA256=59e1ee36866fc05c74c63ad17d9e4e933040a5c0678de2e7984775a004a65d3f
LIMITER_SHA256=ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54
G6A_BUILD_SHA256=33e65232016756d6c13931391d58bf3037670ccab3c7e373d149425e38d03fbd
G6A_PATCH_SHA256=12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98
G6B_PATCH_SHA256=69abe816d9d1c354904e53f6ce5ed1ad2704dccaaeab19ea5a6fcd1bc531f4dc

if command -v sha256sum >/dev/null 2>&1; then SHA() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
else SHA() { tr -d '\r' < "$1" | shasum -a 256 | cut -d' ' -f1; }; fi
check() { [ -f "$2" ] || { echo "$1 not found; refusing to build" >&2; exit 1; }
          [ "$(SHA "$2")" = "$3" ] || { echo "$1 hash mismatch (expected $3, got $(SHA "$2")); refusing to build" >&2; exit 1; }; }

check "mfa_additive.patch" "$MFA/mfa_additive.patch" "$ADDITIVE_SHA256"
check "mfa_enforcement.patch" "$MFA/mfa_enforcement.patch" "$ENFORCEMENT_SHA256"
check "mfa_logging_privacy.patch" "$MFA/mfa_logging_privacy.patch" "$PRIVACY_SHA256"
check "email_verification_proof.patch" "$MFA/email_verification_proof.patch" "$EMAIL_PROOF_SHA256"
check "build_tree_proof.sh" "$MFA/build_tree_proof.sh" "$BUILD_PROOF_SHA256"
check "build_tree_enc_auth.sh (G6a)" "$G6A/build_tree_enc_auth.sh" "$G6A_BUILD_SHA256"
check "enc_auth_gateway.patch (G6a)" "$G6A/enc_auth_gateway.patch" "$G6A_PATCH_SHA256"
check "enc_auth_gateway_g6b.patch" "$HERE/enc_auth_gateway_g6b.patch" "$G6B_PATCH_SHA256"

ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
[ "$(git -C "$ROOT" rev-parse "$BASE_COMMIT^{commit}" 2>/dev/null)" = "$BASE_COMMIT" ] || { echo "base commit $BASE_COMMIT not found; refusing to build" >&2; exit 1; }
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }

bash "$G6A/build_tree_enc_auth.sh" "$OUT" >/dev/null     # d733776 + four production patches + G6a patch, with its own gates (unchanged)
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py differs from the deployed one before the G6b patch; refusing" >&2; exit 1; }

patch -p2 -d "$OUT/backend" --dry-run -s < "$HERE/enc_auth_gateway_g6b.patch" || { echo "G6b patch does not apply cleanly; refusing" >&2; exit 1; }
patch -p2 -d "$OUT/backend" -s < "$HERE/enc_auth_gateway_g6b.patch"
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py changed by the G6b patch; refusing" >&2; exit 1; }
echo "built: $OUT/backend  (d733776 + mfa_additive + mfa_enforcement + mfa_logging_privacy + email_verification_proof + enc_auth_gateway + enc_auth_gateway_g6b)"
