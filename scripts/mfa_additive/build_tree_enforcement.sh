#!/usr/bin/env bash
# Materializes the ENFORCEMENT backend:
#     deployed backend (commit d733776)  +  mfa_additive.patch (verified, unchanged)  +  mfa_enforcement.patch
# Usage: bash build_tree_enforcement.sh <empty-output-dir>      -> <output-dir>/backend is ready to test or deploy.
# Read-only on the repository: it only runs `git archive` and `patch` into the output directory.
# build_tree.sh and mfa_additive.patch are NOT modified by this script or by the enforcement work.
set -eu
OUT=${1:?usage: build_tree_enforcement.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)

# The verified additive patch must be byte-identical to the one that was reviewed and deployed as
# mfa-additive-2 (the image tag carries the first 8 characters of this hash). Hashed with line
# endings normalised so a Windows checkout cannot cause a false alarm.
ADDITIVE_SHA256=edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2
if command -v sha256sum >/dev/null 2>&1; then SHA() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
else SHA() { tr -d '\r' < "$1" | shasum -a 256 | cut -d' ' -f1; }; fi
actual=$(SHA "$HERE/mfa_additive.patch")
[ "$actual" = "$ADDITIVE_SHA256" ] || { echo "mfa_additive.patch hash mismatch (expected $ADDITIVE_SHA256, got $actual); refusing to build" >&2; exit 1; }
[ -f "$HERE/mfa_enforcement.patch" ] || { echo "mfa_enforcement.patch not found" >&2; exit 1; }

[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }
mkdir -p "$OUT"
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive d733776 backend | tar -x -C "$OUT"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_additive.patch"
# Check the second patch applies cleanly BEFORE touching the tree, then apply it.
patch -p2 -d "$OUT/backend" --dry-run -s < "$HERE/mfa_enforcement.patch"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_enforcement.patch"
echo "built: $OUT/backend  (d733776 + mfa_additive.patch + mfa_enforcement.patch)"
