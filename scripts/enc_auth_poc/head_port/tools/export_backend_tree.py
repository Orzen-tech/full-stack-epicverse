"""Exports the backend that Git would see (tracked files plus new, not-ignored files) into an EMPTY directory, as <dir>/backend. The head_port lab mounts
this copy, so the lab always tests exactly the files of the working tree and never anything Git ignores (local data, keys, caches).

    python -B -I scripts/enc_auth_poc/head_port/tools/export_backend_tree.py <empty-dir>

Read-only on the repository (it only runs `git ls-files` and copies bytes).
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def export(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise SystemExit("destination must be empty")
    listing = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "backend"],
                             capture_output=True, check=True).stdout.decode("utf-8")
    count = 0
    for name in filter(None, listing.split("\0")):
        source = REPO / name
        if not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        count += 1
    return count


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print("exported %d files" % export(sys.argv[1]))
