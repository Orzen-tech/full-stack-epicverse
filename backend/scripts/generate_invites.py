"""Generate EpicVerse invite codes safely.

Format: EPIC-XXXXXX (the app shows the "EPIC-" prefix, so users type only the 6 characters).
Codes come from the `secrets` module (cryptographically secure), use an alphabet without the
look-alike characters 0/O/1/I, and are one-use by default.

SAFE BY DEFAULT
  * Dry run: with no flags it only writes the Excel file and touches NO database.
  * It NEVER deletes or changes existing invite codes. With --apply it only INSERTs new rows
    (ON CONFLICT DO NOTHING) and aborts if the number inserted differs from the number requested.
  * The Excel file uses the same layout as the earlier invite sheets (Invite Code, Type, Max Uses).

SECURITY: invite codes are credentials. Never commit the output file (it is git-ignored by name
EpicVerse_Invite*.xlsx) and keep it outside the repository.

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

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # letters + numbers, no 0/O/1/I
PREFIX = "EPIC-"
SUFFIX_LEN = 6


def generate_code(rng=secrets) -> str:
    return PREFIX + "".join(rng.choice(ALPHABET) for _ in range(SUFFIX_LEN))


def generate_unique_codes(count: int, taken=frozenset()) -> list[str]:
    """`count` distinct codes, none of them in `taken` (compared upper-case)."""
    taken_upper = {c.strip().upper() for c in taken}
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < count:
        c = generate_code()
        if c in seen or c in taken_upper:
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
        return {str(c).strip().upper() for ws in wb.worksheets for row in ws.iter_rows(values_only=True)
                for c in row if isinstance(c, str)}
    with open(path, encoding="utf-8") as fh:
        return {line.strip().upper() for line in fh if line.strip()}


def write_excel(codes: list[str], path: str, max_uses: int) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invite Code", "Type", "Max Uses"])
    for c in codes:
        ws.append([c, "Standard", max_uses])
    ws.column_dimensions["A"].width = 22.57
    ws.column_dimensions["B"].width = 13
    ws.column_dimensions["C"].width = 13
    wb.save(path)


async def insert_codes(codes: list[str], max_uses: int, extra_taken: set[str]) -> list[str]:
    """Insert only; returns the final list. Aborts (and rolls back) if the row count is not exactly len(codes)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set; refusing to touch a database.")
    import asyncpg
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            existing = {r["code"].strip().upper() for r in await conn.fetch("SELECT code FROM invite_codes")}
            if existing & set(codes):  # astronomically unlikely, but never overwrite
                codes = generate_unique_codes(len(codes), existing | extra_taken)
            inserted = 0
            for c in codes:
                res = await conn.execute(
                    "INSERT INTO invite_codes (code, current_uses, max_uses) VALUES ($1, 0, $2) ON CONFLICT (code) DO NOTHING",
                    c, max_uses)
                inserted += int(res.split()[-1])
            if inserted != len(codes):
                raise RuntimeError(f"inserted {inserted} of {len(codes)}; rolled back")
        return codes
    finally:
        await conn.close()


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
    if os.path.exists(a.out):
        raise SystemExit(f"{a.out} already exists; refusing to overwrite")

    excluded = load_exclusions(a.exclude)
    codes = generate_unique_codes(a.count, excluded)
    if a.apply:
        codes = asyncio.run(insert_codes(codes, a.max_uses, excluded))
    write_excel(codes, a.out, a.max_uses)
    print(f"{'Inserted into DB and ' if a.apply else 'DRY RUN (no database touched): '}wrote {len(codes)} codes to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
