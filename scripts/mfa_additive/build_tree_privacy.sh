#!/usr/bin/env bash
# Materializes the PRIVACY backend:
#     deployed backend (commit d733776)  +  mfa_additive.patch  +  mfa_enforcement.patch  +  mfa_logging_privacy.patch
# Usage: bash build_tree_privacy.sh <empty-output-dir>      -> <output-dir>/backend is ready to test.
# Read-only on the repository: it only runs `git archive` and `patch` into the output directory.
# The earlier patches, build_tree.sh and build_tree_enforcement.sh are NOT modified by this script.
set -eu
OUT=${1:?usage: build_tree_privacy.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)

# Both earlier patches must be byte-identical to the reviewed and deployed ones. Hashed with line endings normalised so a
# Windows checkout cannot cause a false alarm.
ADDITIVE_SHA256=edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2
ENFORCEMENT_SHA256=7f3030064bb3dbae84646e64f337f84eef02f94404c414a4ac514db3ef160104
LIMITER_SHA256=ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54
if command -v sha256sum >/dev/null 2>&1; then SHA() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
else SHA() { tr -d '\r' < "$1" | shasum -a 256 | cut -d' ' -f1; }; fi
check() { [ "$(SHA "$2")" = "$3" ] || { echo "$1 hash mismatch (expected $3, got $(SHA "$2")); refusing to build" >&2; exit 1; }; }
check "mfa_additive.patch" "$HERE/mfa_additive.patch" "$ADDITIVE_SHA256"
check "mfa_enforcement.patch" "$HERE/mfa_enforcement.patch" "$ENFORCEMENT_SHA256"
[ -f "$HERE/mfa_logging_privacy.patch" ] || { echo "mfa_logging_privacy.patch not found" >&2; exit 1; }

[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }
mkdir -p "$OUT"
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive d733776 backend | tar -x -C "$OUT"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_additive.patch"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_enforcement.patch"
# Check the privacy patch applies cleanly BEFORE touching the tree, then apply it.
patch -p2 -d "$OUT/backend" --dry-run -s < "$HERE/mfa_logging_privacy.patch"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_logging_privacy.patch"
# The OTP rate limiter must stay byte-identical to the deployed one.
got=$(tr -d '\r' < "$OUT/backend/app/services/otp_rate_limiter.py" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -d' ' -f1)
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py differs from the deployed one; refusing" >&2; exit 1; }
echo "built: $OUT/backend  (d733776 + mfa_additive.patch + mfa_enforcement.patch + mfa_logging_privacy.patch)"
