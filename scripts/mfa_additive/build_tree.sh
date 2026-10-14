#!/usr/bin/env bash
# Materializes the ADDITIVE MFA backend: the deployed backend (commit d733776) + mfa_additive.patch.
# Usage: bash build_tree.sh <empty-output-dir>      -> <output-dir>/backend is ready to test or deploy.
# Read-only on the repository: it only runs `git archive` and `patch` into the output directory.
set -eu
OUT=${1:?usage: build_tree.sh <empty-output-dir>}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "output directory must be empty: $OUT" >&2; exit 1; }
mkdir -p "$OUT"
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive d733776 backend | tar -x -C "$OUT"
patch -p2 -d "$OUT/backend" -s < "$HERE/mfa_additive.patch"
echo "built: $OUT/backend  (d733776 + mfa_additive.patch)"
