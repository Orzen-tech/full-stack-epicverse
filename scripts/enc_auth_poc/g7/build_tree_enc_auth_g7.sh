#!/usr/bin/env bash
# Materializes the LOCAL TEST backend with the selectable real-Firebase identity adapter (G7):
#     deployed backend (commit d733776) + mfa_additive + mfa_enforcement + mfa_logging_privacy + email_verification_proof
#     + enc_auth_gateway.patch (G6a) + enc_auth_gateway_g6b.patch (G6b) + enc_auth_gateway_g7_firebase.patch (G7)
#     + the cryptography pin (see below)
# Usage: bash build_tree_enc_auth_g7.sh <empty-output-dir>      -> <output-dir>/backend is ready to test.
# Read-only on the repository. The earlier chain is built through the UNCHANGED G6b build script (which gates the base commit, the four
# production patches, the G6a patch and the OTP limiter); this script additionally refuses if the G6b build script or patch, or the G7 patch,
# differs from the reviewed bytes, if the destination is not empty, if the G7 patch does not apply cleanly, or if the OTP limiter changed.
# LOCAL TEST tree only, not a deployment artifact. The G7 patch changes no behaviour unless ENC_AUTH_IDENTITY_BACKEND is set.
# The ONLY dependency change of every G7 build: backend/scripts/requirements.txt gains the line `cryptography==50.0.2` (the gateway imports
# cryptography; until now it arrived unpinned through firebase-admin). It is appended by this script, keeping the file's own line ending,
# refused if cryptography is already named there, and verified to leave every earlier byte untouched. No other dependency is touched.
set -eu
OUT=${1:?usage: build_tree_enc_auth_g7.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)
G6B="$HERE/../g6b"

LIMITER_SHA256=ded0a04aaa7b4e6ec1ecd9d4b215eb01309a7af2733f9e714c9191276d54db54
G6B_BUILD_SHA256=9e486515c9735bb70ede1cafc2ff41dee021931c725b9b0aa47d28e0e0aeee64
G6B_PATCH_SHA256=69abe816d9d1c354904e53f6ce5ed1ad2704dccaaeab19ea5a6fcd1bc531f4dc
G7_PATCH_SHA256=bdf13be3ca1eccb0bfd7a8232afe6a535640fbc486dbbf673f4a7f642ef5768b
CRYPTOGRAPHY_PIN=cryptography==50.0.2

if command -v sha256sum >/dev/null 2>&1; then SHA() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
else SHA() { tr -d '\r' < "$1" | shasum -a 256 | cut -d' ' -f1; }; fi
check() { [ -f "$2" ] || { echo "$1 not found; refusing to build" >&2; exit 1; }
          [ "$(SHA "$2")" = "$3" ] || { echo "$1 hash mismatch (expected $3, got $(SHA "$2")); refusing to build" >&2; exit 1; }; }

check "build_tree_enc_auth_g6b.sh (G6b)" "$G6B/build_tree_enc_auth_g6b.sh" "$G6B_BUILD_SHA256"
check "enc_auth_gateway_g6b.patch (G6b)" "$G6B/enc_auth_gateway_g6b.patch" "$G6B_PATCH_SHA256"
check "enc_auth_gateway_g7_firebase.patch" "$HERE/enc_auth_gateway_g7_firebase.patch" "$G7_PATCH_SHA256"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }

bash "$G6B/build_tree_enc_auth_g6b.sh" "$OUT" >/dev/null     # everything up to and including G6b, with its own gates (unchanged)
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py differs from the deployed one before the G7 patch; refusing" >&2; exit 1; }

patch -p2 -d "$OUT/backend" --dry-run -s < "$HERE/enc_auth_gateway_g7_firebase.patch" || { echo "G7 patch does not apply cleanly; refusing" >&2; exit 1; }
patch -p2 -d "$OUT/backend" -s < "$HERE/enc_auth_gateway_g7_firebase.patch"
got=$(SHA "$OUT/backend/app/services/otp_rate_limiter.py")
[ "$got" = "$LIMITER_SHA256" ] || { echo "otp_rate_limiter.py changed by the G7 patch; refusing" >&2; exit 1; }

# ---- the cryptography pin: appended, nothing else in requirements.txt is touched ----
REQ="$OUT/backend/scripts/requirements.txt"
[ -f "$REQ" ] || { echo "requirements.txt not found; refusing" >&2; exit 1; }
if tr -d '\r' < "$REQ" | grep -qi 'cryptography'; then echo "requirements.txt already names cryptography; refusing" >&2; exit 1; fi
cp "$REQ" "$REQ.before"
if [ -n "$(tr -cd '\r' < "$REQ" | head -c 1)" ]; then CRLF=1; else CRLF=0; fi          # keep the file's own line-ending style
if [ -n "$(tail -c 1 "$REQ")" ]; then                                                   # the last line has no line break yet: add one
  if [ "$CRLF" = 1 ]; then printf '\r\n' >> "$REQ"; else printf '\n' >> "$REQ"; fi
fi
if [ "$CRLF" = 1 ]; then printf '%s\r\n' "$CRYPTOGRAPHY_PIN" >> "$REQ"; else printf '%s\n' "$CRYPTOGRAPHY_PIN" >> "$REQ"; fi
cmp -s -n "$(wc -c < "$REQ.before")" "$REQ.before" "$REQ" || { echo "requirements.txt: an earlier byte changed; refusing" >&2; exit 1; }
[ "$(tr -d '\r' < "$REQ" | grep -c '^cryptography==50\.0\.2$')" = "1" ] || { echo "requirements.txt: the cryptography pin is not present exactly once; refusing" >&2; exit 1; }
rm -f "$REQ.before"
echo "built: $OUT/backend  (d733776 + four production patches + G6a + G6b + G7 firebase adapter + the cryptography pin)"
