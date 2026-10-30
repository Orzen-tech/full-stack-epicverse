"""Developer tool: writes enc_auth_gateway.patch as the difference between two backend trees.

    python -B -I make_patch.py <base-backend-dir> <work-backend-dir> <output.patch>

<base-backend-dir> is the tree built by scripts/mfa_additive/build_tree_proof.sh (d733776 + the four production patches), and
<work-backend-dir> is a copy of it with the gateway changes. The output is deterministic (no timestamps, sorted, LF) and uses
`a/backend/...` and `b/backend/...` headers, so it applies with `patch -p2 -d <tree>/backend`. Deletions are refused: this patch is
strictly additive or edits existing files in place.
"""
import difflib
import sys
from pathlib import Path

SKIP_PARTS = {"__pycache__"}


def files(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file() and not (SKIP_PARTS & set(p.parts))}


def read_lines(path):
    data = path.read_bytes()
    if b"\r" in data:
        raise SystemExit("CR found in %s: trees must use LF" % path)
    return data.decode("utf-8").splitlines(keepends=True)


def main(base, work, out):
    base_files, work_files = files(Path(base)), files(Path(work))
    removed = sorted(set(base_files) - set(work_files))
    if removed:
        raise SystemExit("refusing: files deleted in the work tree: %s" % removed[:3])
    chunks, added, changed = [], 0, 0
    for rel in sorted(work_files):
        if rel in base_files and base_files[rel].read_bytes() == work_files[rel].read_bytes():
            continue  # identical (this also skips binary files such as the .xlsx data, which the patch never touches)
        new = read_lines(work_files[rel])
        old = read_lines(base_files[rel]) if rel in base_files else None
        label_old = "a/backend/" + rel if old is not None else "/dev/null"
        diff = list(difflib.unified_diff(old or [], new, fromfile=label_old, tofile="b/backend/" + rel, n=3))
        for i, line in enumerate(diff):  # keep the patch valid when a file lacks a final newline
            if not line.endswith("\n"):
                diff[i] = line + "\n\\ No newline at end of file\n"
        chunks.append("".join(diff))
        added += old is None
        changed += old is not None
    Path(out).write_bytes("".join(chunks).encode("utf-8"))
    print("wrote %s: %d new files, %d changed files, %d bytes" % (out, added, changed, Path(out).stat().st_size))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
