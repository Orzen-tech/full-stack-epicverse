#!/usr/bin/env bash
# Materializes the LOCAL TEST backend for the encrypted-credential gateway:
#     deployed backend (commit d733776) + mfa_additive.patch + mfa_enforcement.patch + mfa_logging_privacy.patch
#     + email_verification_proof.patch + enc_auth_gateway.patch
# Usage: bash build_tree_enc_auth.sh <empty-output-dir>      -> <output-dir>/backend is ready to test.
# Read-only on the repository (it only runs `git archive` and `patch` into the output directory, through the existing, unchanged
# build_tree_proof.sh). It REFUSES to build if the base commit, any of the four production patches, the existing build script, or
# the new patch differs from the reviewed bytes, and it re-checks that the OTP rate limiter is still byte-identical afterwards.
# The result is a LOCAL TEST tree. It is not a deployment artifact: the gateway is disabled by default and refuses to talk to
# anything but a local Firebase Auth emulator.
set -eu
OUT=${1:?usage: build_tree_enc_auth.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)
MFA="$HERE/../../mfa_additive"

BASE_COMMIT=d7337763e03ca6c4c5ff234708fbb5162937d194
ADDITIVE_SHA256=edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2
ENFORCEMENT_SHA256=7f3030064bb3dbae84646e64f337f84eef02f94404c414a4ac514db3ef160104
PRIVACY_SHA256=98fc386d154ce7b21b688eb4aab7897e593c9fb86597442891cc996130f821a4
EMAIL_PROOF_SHA256=39d5da68992e7520480fdfad3155dd853ac0b1ea8d3eb24ccf056c63350ae49d
BUILD_PROOF_SHA256=59e1ee36866fc05c74c63ad17d9e4e933040a5c0678de2e7984775a004a65d3f
LIMITER_SHA256=ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54
ENC_AUTH_PATCH_SHA256=12a52c1621dd6bef75a03d92a8477726f3c980bcb75e9f5a47278c0ef6d20b98

if command -v sha256sum >/dev/null 2>&1; then SHA() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
else SHA() { tr -d '\r' < "$1" | shasum -a 256 | cut -d' ' -f1; }; fi
check() { [ -f "$2" ] || { echo "$1 not found; refusing to build" >&2; exit 1; }
          [ "$(SHA "$2")" = "$3" ] || { echo "$1 hash mismatch (expected $3, got $(SHA "$2")); refusing to build" >&2; exit 1; }; }

check "mfa_additive.patch" "$MFA/mfa_additive.patch" "$ADDITIVE_SHA256"
check "mfa_enforcement.patch" "$MFA/mfa_enforcement.patch" "$ENFORCEMENT_SHA256"
check "mfa_logging_privacy.patch" "$MFA/mfa_logging_privacy.patch" "$PRIVACY_SHA256"
check "email_verification_proof.patch" "$MFA/email_verification_proof.patch" "$EMAIL_PROOF_SHA256"
check "build_tree_proof.sh" "$MFA/build_tree_proof.sh" "$BUILD_PROOF_SHA256"
check "enc_auth_gateway.patch" "$HERE/enc_auth_gateway.patch" "$ENC_AUTH_PATCH_SHA256"

ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
[ "$(git -C "$ROOT" rev-parse "$BASE_COMMIT^{commit}" 2>/dev/null)" = "$BASE_COMMIT" ] || { echo "base commit $BASE_COMMIT not found; refusing to build" >&2; exit 1; }
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }

bash "$MFA/build_tree_proof.sh" "$OUT" >/dev/null     # d733776 + the four production patches, with its own gates (unchanged script)
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py differs from the deployed one before the gateway patch; refusing" >&2; exit 1; }

patch -p2 -d "$OUT/backend" --dry-run -s < "$HERE/enc_auth_gateway.patch"      # must apply cleanly BEFORE touching the tree
patch -p2 -d "$OUT/backend" -s < "$HERE/enc_auth_gateway.patch"
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py changed by the gateway patch; refusing" >&2; exit 1; }
echo "built: $OUT/backend  (d733776 + mfa_additive + mfa_enforcement + mfa_logging_privacy + email_verification_proof + enc_auth_gateway)"
