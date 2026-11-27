"""Reload the card_combos table from the seven mode spreadsheets.

DESTRUCTIVE with --apply: it DELETEs every row of card_combos (and, through ON DELETE CASCADE, card_embeddings,
which must be regenerated afterwards) and inserts the spreadsheet rows, all in ONE transaction.

SAFE BY DEFAULT
  * Without --apply it only reads and validates the spreadsheets and prints per-file row counts. No database is
    contacted.
  * --apply also requires --confirm-host HOST, which must equal the host of DATABASE_URL. Only the host is printed,
    never the credentials or the connection string. A missing or unidentifiable host, a missing confirmation or a
    mismatch refuses to run before any connection is attempted.
  * All seven files must exist; each needs an "App Message (Crisp)" or "App Message" column; fully empty rows are
    skipped; any other row with blank message text (or an unreadable card number) stops the run before connecting.
  * In the database: the transaction begins before the DELETE; every insert and the final row-count check run inside
    it; any failure rolls everything back, so the table is never left empty or half-loaded.

Usage (from the backend folder; the data folder defaults to backend/data, override with EPICVERSE_DATA_DIR):
  python scripts/import_new_modes.py                                   # validate only, no database
  python scripts/import_new_modes.py --apply --confirm-host <db-host>  # replace card_combos
"""
import argparse
import asyncio
import os
import sys
from urllib.parse import parse_qs, urlsplit

# Mapping of File Index -> Correct Mode Name
KANDA_MAP = {
    1: "OriginArc (Balakanda)",
    2: "CrownShift (AyodhyaKanda)",
    3: "WildRun (AranyaKanda)",
    4: "GlowLine (KishkindhaKanda)",
    5: "lankaLeap (SundaraKanda)",
    6: "WarRoom (YuddhaKanda)",
    7: "AfterLight (UttaraKanda)"
}
# The message column is "App Message (Crisp)" in most sheets but was "App Message" in the Mode 6 sheet; reading only
# the first name once loaded Mode 6 with blank text.
MSG_HEADERS = ("App Message (Crisp)", "App Message")
INSERT_SQL = '''
    INSERT INTO card_combos (
        gameplay_mode, character, character_card_number,
        attribute, attribute_card_no,
        final_segment, final_status, revised_scholar_reason
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
'''


def default_data_dir() -> str:
    return os.environ.get("EPICVERSE_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def db_host(url: str | None) -> str | None:
    """The database host of a postgres URL (also the ?host= form used for Unix sockets); None if not identifiable."""
    if not url:
        return None
    try:
        parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        host = parts.hostname or (parse_qs(parts.query).get("host") or [None])[0]
    except ValueError:
        return None
    return host or None


def load_rows(data_dir: str):
    """Reads and validates all seven files BEFORE any database work. Returns (rows, {file: count}).
    Problems stop the run with the file and spreadsheet row number, never the cell text."""
    import pandas as pd
    missing = [f"mode {i}.xlsx" for i in KANDA_MAP if not os.path.exists(os.path.join(data_dir, f"mode {i}.xlsx"))]
    if missing:
        raise SystemExit(f"missing files in {data_dir}: {missing}; nothing done")
    rows, counts = [], {}
    for i, mode in KANDA_MAP.items():
        file = f"mode {i}.xlsx"
        df = pd.read_excel(os.path.join(data_dir, file))
        df = df.dropna(how="all").replace({pd.NA: None})  # fully empty rows are not data
        msg_col = next((h for h in MSG_HEADERS if h in df.columns), None)
        if msg_col is None:
            raise SystemExit(f"{file}: no 'App Message (Crisp)' / 'App Message' column; nothing done")
        for idx, row in df.iterrows():
            line = idx + 2  # spreadsheet row number (header is row 1)
            text = str(row.get(msg_col, '') or '').strip()
            if not text:
                raise SystemExit(f"{file}: row {line} has blank message text; nothing done")
            try:
                char_no = int(row.get('Character Card No.', 0)) if pd.notnull(row.get('Character Card No.')) else 0
                attr_no = int(row.get('Attribute Card No.', 0)) if pd.notnull(row.get('Attribute Card No.')) else 0
            except (TypeError, ValueError):
                raise SystemExit(f"{file}: row {line} has an unreadable card number; nothing done")
            status = str(row.get('Final Segment', ''))  # Current Excel maps status to 'Final Segment'
            rows.append((mode,  # FORCE THE CORRECT LABEL
                         str(row.get('Character', '')), char_no,
                         str(row.get('Attribute', '')), attr_no,
                         text, status,
                         text))  # Scholar reason
        counts[file] = sum(1 for r in rows if r[0] == mode)
    return rows, counts


async def _connect(url: str):
    import asyncpg
    return await asyncpg.connect(url)


async def replace_card_combos(url: str, rows: list) -> tuple[int, list]:
    """ONE transaction: DELETE, every insert and the row-count check; any failure rolls everything back."""
    conn = await _connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("DELETE FROM card_combos")  # NOTE: also deletes card_embeddings (ON DELETE CASCADE)
            for r in rows:
                await conn.execute(INSERT_SQL, *r)
            total = await conn.fetchval("SELECT COUNT(*) FROM card_combos")
            if total != len(rows):
                raise RuntimeError(f"the table holds {total} rows, expected {len(rows)}")
        except BaseException:
            await tx.rollback()
            raise
        await tx.commit()
        modes = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
        return total, [m["gameplay_mode"] for m in modes]
    finally:
        await conn.close()


def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="REPLACE card_combos in the database (destructive)")
    ap.add_argument("--confirm-host", help="required with --apply: the database host from DATABASE_URL")
    a = ap.parse_args(argv)

    data_dir = default_data_dir()
    rows, counts = load_rows(data_dir)  # never touches a database
    for f, n in counts.items():
        print(f"{f}: {n} rows")
    print(f"Validated {len(rows)} rows from {data_dir}")
    if not a.apply:
        print("VALIDATION ONLY (no database contacted). Re-run with --apply --confirm-host <host> to replace card_combos.")
        return 0

    _load_env()
    url = os.getenv("DATABASE_URL")
    host = db_host(url)
    if not host:
        raise SystemExit("DATABASE_URL is missing or its host cannot be identified; refusing to run. Nothing done.")
    if not a.confirm_host:
        raise SystemExit(f"--apply needs --confirm-host; the target host is {host}. Nothing done.")
    if a.confirm_host.strip().lower() != host.lower():
        raise SystemExit(f"--confirm-host does not match the target host {host}. Nothing done.")

    print(f"Replacing card_combos on host {host} (credentials not shown)...")
    total, modes = asyncio.run(replace_card_combos(url, rows))
    print("EPICVERSE FINAL TRUTH SYNC REPORT")
    print("=" * 30)
    print(f"Total Rows Synchronized: {len(rows)}")
    print(f"Database Final Total:    {total}")
    print(f"Unique Modes Present:     {len(modes)}")
    print(f"Verified Final Statuses:  {modes}")
    print("=" * 30)
    print("Remember: card_embeddings were cleared by the cascade and must be regenerated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
