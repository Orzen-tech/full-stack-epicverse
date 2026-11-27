"""Generate EpicVerse invite codes safely.

Format: EPIC-XXXXXX (the app shows the "EPIC-" prefix, so users type only the 6 characters).
Codes come from the `secrets` module (cryptographically secure), use an alphabet without the
look-alike characters 0/O/1/I, and are one-use by default.

SAFE BY DEFAULT
  * Dry run: with no flags it only writes an Excel file labelled "DRY RUN — NOT ACTIVE" and touches NO database.
  * It NEVER deletes or changes existing invite codes. With --apply it only INSERTs new rows
    (ON CONFLICT DO NOTHING) and aborts if the number inserted differs from the number requested.
  * Everything that can fail locally (openpyxl, the output folder, the file names, DATABASE_URL) is checked
    BEFORE any database work.
  * With --apply the spreadsheet is written INSIDE the database transaction, first as <out>.pending.xlsx, so a
    spreadsheet failure rolls the insert back. It is renamed to <out> only after the commit succeeded. If the
    commit itself fails (its outcome is then unknown) the file is renamed to
    <out>.COMMIT-FAILED-VERIFY-BEFORE-USE.xlsx: its codes may be INACTIVE, so check the database before using any.
  * Exclusions (--exclude) are compared after normalisation: whitespace removed, upper-case, "EPIC-" added when
    missing, so " a1b0c1 " and "EPIC-A1B0C1" are the same code (an example that can never be a real code: real codes
    have no 0 or 1). Existing database codes are normalised the same way.

SECURITY: invite codes are credentials. Generated codes are never printed. Never commit the output file (*.xlsx is
git-ignored) and keep it outside the repository.

Usage (from the backend folder):
  python scripts/generate_invites.py --count 50 --out D:/private/Invites.xlsx            # dry run, no DB
  python scripts/generate_invites.py --count 50 --out D:/private/Invites.xlsx --apply    # also insert into DB
  Optional: --exclude old_codes.txt   (one code per line, or an .xlsx) to avoid ever re-issuing known/leaked codes
  DATABASE_URL must be set in the environment for --apply.
"""
import argparse
import asyncio
import os
import secrets
import sys
import tempfile

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # letters + numbers, no 0/O/1/I
PREFIX = "EPIC-"
SUFFIX_LEN = 6
DRY_RUN_LABEL = "DRY RUN — NOT ACTIVE"
PENDING_SUFFIX = ".pending.xlsx"
FAILED_SUFFIX = ".COMMIT-FAILED-VERIFY-BEFORE-USE.xlsx"
INSERT_SQL = "INSERT INTO invite_codes (code, current_uses, max_uses) VALUES ($1, 0, $2) ON CONFLICT (code) DO NOTHING"


def generate_code(rng=secrets) -> str:
    return PREFIX + "".join(rng.choice(ALPHABET) for _ in range(SUFFIX_LEN))


def normalise_code(value) -> str | None:
    """Canonical form for every comparison: no whitespace, upper-case, with the EPIC- prefix. None if empty."""
    if value is None:
        return None
    code = "".join(str(value).split()).upper()
    if not code:
        return None
    return code if code.startswith(PREFIX) else PREFIX + code


def generate_unique_codes(count: int, taken=frozenset()) -> list[str]:
    """`count` distinct codes, none of them equal (after normalisation) to anything in `taken`."""
    taken_norm = {n for n in (normalise_code(c) for c in taken) if n}
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < count:
        c = generate_code()
        if c in seen or c in taken_norm:
            continue
        seen.add(c)
        out.append(c)
    return out


def load_exclusions(path: str | None) -> set[str]:
    if not path:
        return set()
    if path.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        raw = [c for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for c in row if isinstance(c, str)]
    else:
        with open(path, encoding="utf-8") as fh:
            raw = list(fh)
    return {n for n in (normalise_code(c) for c in raw) if n}


def output_paths(out: str) -> dict:
    base = out[:-5] if out.lower().endswith(".xlsx") else out
    return {"final": out, "pending": base + PENDING_SUFFIX, "failed": base + FAILED_SUFFIX}


def preflight(out: str, apply: bool) -> dict:
    """Everything that can fail locally, checked before any code is generated or any database is contacted."""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise SystemExit("openpyxl is not installed; nothing was generated or inserted.")
    paths = output_paths(out)
    for p in paths.values():
        if os.path.exists(p):
            raise SystemExit(f"{p} already exists; refusing to overwrite")
    folder = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(folder):
        raise SystemExit(f"output folder does not exist: {folder}")
    try:  # prove the folder accepts a new file, then remove the empty probe
        fd, probe = tempfile.mkstemp(dir=folder, prefix=".invite-write-check-", suffix=".tmp")
        os.close(fd)
        os.remove(probe)
    except OSError as e:
        raise SystemExit(f"cannot write to the output folder {folder}: {type(e).__name__}")
    if apply and not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set; refusing to touch a database.")
    return paths


def _place(src: str, dst: str) -> None:
    """Moves src to dst without ever overwriting dst."""
    try:
        os.link(src, dst)  # fails with FileExistsError if dst exists
    except FileExistsError:
        raise
    except OSError:  # hard links unsupported here: fall back to a rename after re-checking
        if os.path.exists(dst):
            raise FileExistsError(dst)
        os.rename(src, dst)
        return
    try:
        os.remove(src)
    except OSError:
        print(f"WARNING: could not remove the temporary file {src}; delete it (it contains invite codes).", file=sys.stderr)


def write_excel(codes: list[str], path: str, max_uses: int, dry_run: bool = False) -> None:
    """Writes the sheet to a temporary file in the same folder, then places it at `path` (never overwriting)."""
    import openpyxl
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".invites-", suffix=".xlsx.tmp")
    os.close(fd)
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = DRY_RUN_LABEL if dry_run else "Sheet1"
        ws.append(["Invite Code", "Type", "Max Uses"])
        kind = DRY_RUN_LABEL if dry_run else "Standard"
        for c in codes:
            ws.append([c, kind, max_uses])
        ws.column_dimensions["A"].width = 22.57
        ws.column_dimensions["B"].width = 24 if dry_run else 13
        ws.column_dimensions["C"].width = 13
        wb.save(tmp)
        _place(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


async def _connect(url: str):
    import asyncpg
    return await asyncpg.connect(url)


def _mark_commit_failed(paths: dict) -> None:
    try:
        _place(paths["pending"], paths["failed"])
        where = paths["failed"]
    except OSError:
        where = paths["pending"]
    print(f"ERROR: the database COMMIT failed, so its outcome is unknown. The spreadsheet {where} may contain "
          "INACTIVE codes: verify them against the database before handing any out.", file=sys.stderr)


async def insert_codes_and_write(codes: list[str], max_uses: int, extra_taken: set[str], paths: dict):
    """Additive insert in ONE transaction; the spreadsheet is written inside it (as the pending file), so a write
    failure rolls the insert back. Returns (codes, spreadsheet path)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set; refusing to touch a database.")
    conn = await _connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    written = False
    try:
        try:
            async with conn.transaction():
                existing = {n for n in (normalise_code(r["code"]) for r in await conn.fetch("SELECT code FROM invite_codes")) if n}
                if existing & set(codes):  # astronomically unlikely, but never overwrite
                    codes = generate_unique_codes(len(codes), existing | extra_taken)
                inserted = 0
                for c in codes:
                    res = await conn.execute(INSERT_SQL, c, max_uses)
                    inserted += int(res.split()[-1])
                if inserted != len(codes):
                    raise RuntimeError(f"inserted {inserted} of {len(codes)}; rolled back")
                write_excel(codes, paths["pending"], max_uses)  # a failure here rolls the insert back
                written = True
        except BaseException:
            if written:  # the block completed, so it was the COMMIT that failed
                _mark_commit_failed(paths)
            raise
    finally:
        await conn.close()
    try:
        _place(paths["pending"], paths["final"])
        return codes, paths["final"]
    except OSError:
        print(f"WARNING: the codes ARE active, but the file could not be renamed; the record is {paths['pending']}",
              file=sys.stderr)
        return codes, paths["pending"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=50, help="how many codes (default 50)")
    ap.add_argument("--max-uses", type=int, default=1, help="uses per code (default 1)")
    ap.add_argument("--out", required=True, help="Excel file to write (keep it outside the repo)")
    ap.add_argument("--exclude", help="file of codes never to re-issue (.txt one per line, or .xlsx)")
    ap.add_argument("--apply", action="store_true", help="also INSERT the codes into the database (additive only)")
    a = ap.parse_args(argv)
    if not 1 <= a.count <= 5000:
        raise SystemExit("--count must be between 1 and 5000")
    if a.max_uses < 1:
        raise SystemExit("--max-uses must be at least 1")
    paths = preflight(a.out, a.apply)

    excluded = load_exclusions(a.exclude)
    codes = generate_unique_codes(a.count, excluded)
    if a.apply:
        codes, where = asyncio.run(insert_codes_and_write(codes, a.max_uses, excluded, paths))
        print(f"Inserted {len(codes)} codes into the database and wrote them to {where}")
    else:
        write_excel(codes, a.out, a.max_uses, dry_run=True)
        print(f"DRY RUN (no database touched): wrote {len(codes)} NOT-ACTIVE codes to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
