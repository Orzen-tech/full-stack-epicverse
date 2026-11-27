"""Offline tests for scripts/generate_invites.py.

NO DATABASE: every connection is a fake; asyncpg is never imported. NO REAL CODES: code generation is replaced by
scripted values containing 0/1, which the real alphabet excludes, so they can never be valid invite codes.
These tests live outside tests/ on purpose: tests/conftest.py starts a PostgreSQL server when it is loaded.
Run from backend/:  python -m pytest -p no:cacheprovider tests_offline
"""
import asyncio
import importlib.util
import os
import pathlib
import sys

import openpyxl
import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "generate_invites.py"


@pytest.fixture
def gi():
    spec = importlib.util.spec_from_file_location("generate_invites_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scripted(mod, monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(mod, "generate_code", lambda rng=None: next(it))


def no_connect(mod, monkeypatch):
    async def boom(url):
        raise AssertionError("a database connection was attempted")
    monkeypatch.setattr(mod, "_connect", boom)


class FakeTx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.events.append("begin")
        return self

    async def __aexit__(self, et, e, tb):
        if et is None:
            if self.conn.fail_commit:
                self.conn.events.append("commit-failed")
                raise RuntimeError("simulated commit failure")
            self.conn.events.append("commit")
        else:
            self.conn.events.append("rollback")
        return False


class FakeConn:
    def __init__(self, existing=(), insert_result="INSERT 0 1", fail_commit=False):
        self.existing, self.insert_result, self.fail_commit = list(existing), insert_result, fail_commit
        self.events, self.inserted = [], []

    def transaction(self):
        return FakeTx(self)

    async def fetch(self, sql):
        self.events.append("select")
        return [{"code": c} for c in self.existing]

    async def execute(self, sql, *args):
        assert sql.lstrip().upper().startswith("INSERT") and "ON CONFLICT (CODE) DO NOTHING" in sql.upper()
        self.events.append("insert")
        self.inserted.append(args[0])
        return self.insert_result

    async def close(self):
        self.events.append("close")


def use_fake(mod, monkeypatch, conn):
    async def connect(url):
        conn.events.append("connect")
        return conn
    monkeypatch.setattr(mod, "_connect", connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-user:fake-pass@db.invalid/fake")


FAKE = [f"EPIC-FAKE0{i}" for i in range(1, 10)] + [f"EPIC-FAK10{i}" for i in range(10)]


# ---------------------------------------------------------------- generation and normalisation
def test_default_rng_is_secrets_and_format_uses_the_reduced_alphabet(gi):
    import inspect
    import secrets
    assert inspect.signature(gi.generate_code).parameters["rng"].default is secrets

    class SeqRng:  # deterministic, not random: proves only the format
        def __init__(self):
            self.i = 0

        def choice(self, seq):
            self.i += 1
            return seq[self.i % len(seq)]
    code = gi.generate_code(rng=SeqRng())
    assert code.startswith("EPIC-") and len(code) == 11 and set(code[5:]) <= set(gi.ALPHABET)
    assert not set("0O1I") & set(gi.ALPHABET)


@pytest.mark.parametrize("raw", ["A1B0C1", "a1b0c1", " a1b0c1 ", "EPIC-A1B0C1", "epic-a1b0c1", "  EPIC- A1B0C1\t"])
def test_prefix_whitespace_and_case_normalise_to_one_code(gi, raw):
    assert gi.normalise_code(raw) == "EPIC-A1B0C1"


def test_empty_values_normalise_to_none(gi):
    assert gi.normalise_code("") is None and gi.normalise_code("   ") is None and gi.normalise_code(None) is None


def test_exclusions_from_text_and_xlsx_are_normalised(gi, tmp_path):
    txt = tmp_path / "ex.txt"
    txt.write_text(" a1b0c1 \nEPIC-B1C0D1\n\nepic-c1d0e1\n", encoding="utf-8")
    assert gi.load_exclusions(str(txt)) == {"EPIC-A1B0C1", "EPIC-B1C0D1", "EPIC-C1D0E1"}
    wb = openpyxl.Workbook()
    wb.active.append(["a1b0c1"])
    wb.active.append([" EPIC-B1C0D1 "])
    wb.save(tmp_path / "ex.xlsx")
    assert {"EPIC-A1B0C1", "EPIC-B1C0D1"} <= gi.load_exclusions(str(tmp_path / "ex.xlsx"))


def test_excluded_and_duplicate_values_are_never_issued(gi, monkeypatch):
    scripted(gi, monkeypatch, ["EPIC-A1B0C1", "EPIC-FAKE01", "EPIC-FAKE01", "EPIC-FAKE02", "EPIC-FAKE03"])
    out = gi.generate_unique_codes(3, taken={"a1b0c1"})  # excluded without the prefix, generated with it
    assert out == ["EPIC-FAKE01", "EPIC-FAKE02", "EPIC-FAKE03"]


# ---------------------------------------------------------------- dry run and local safety
def test_dry_run_never_connects_and_labels_the_sheet_not_active(gi, monkeypatch, tmp_path, capsys):
    no_connect(gi, monkeypatch)
    scripted(gi, monkeypatch, FAKE)
    out = tmp_path / "dry.xlsx"
    assert gi.main(["--count", "3", "--out", str(out)]) == 0
    ws = openpyxl.load_workbook(out).active
    assert ws.title == "DRY RUN — NOT ACTIVE"
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("Invite Code", "Type", "Max Uses")
    assert [r[1] for r in rows[1:]] == ["DRY RUN — NOT ACTIVE"] * 3
    printed = capsys.readouterr()
    assert "DRY RUN" in printed.out and not any(c in printed.out + printed.err for c in FAKE)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dry.xlsx"], "no temp or probe files left behind"


@pytest.mark.parametrize("existing", ["out.xlsx", "out.pending.xlsx", "out.COMMIT-FAILED-VERIFY-BEFORE-USE.xlsx"])
def test_refuses_to_overwrite_any_output_name(gi, monkeypatch, tmp_path, existing):
    no_connect(gi, monkeypatch)
    (tmp_path / existing).write_bytes(b"keep me")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        gi.main(["--count", "2", "--out", str(tmp_path / "out.xlsx"), "--apply"])
    assert (tmp_path / existing).read_bytes() == b"keep me"


def test_missing_output_folder_or_database_url_stops_before_generation(gi, monkeypatch, tmp_path):
    no_connect(gi, monkeypatch)
    monkeypatch.setattr(gi, "generate_code", lambda rng=None: pytest.fail("codes generated before the checks"))
    with pytest.raises(SystemExit, match="output folder does not exist"):
        gi.main(["--out", str(tmp_path / "nope" / "o.xlsx")])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL is not set"):
        gi.main(["--out", str(tmp_path / "o.xlsx"), "--apply"])


# ---------------------------------------------------------------- --apply with a FAKE connection
def run_apply(gi, monkeypatch, tmp_path, conn, n=3):
    use_fake(gi, monkeypatch, conn)
    scripted(gi, monkeypatch, FAKE)
    return gi.main(["--count", str(n), "--out", str(tmp_path / "out.xlsx"), "--apply"])


def test_apply_success_commits_then_publishes_the_file(gi, monkeypatch, tmp_path, capsys):
    conn = FakeConn()
    assert run_apply(gi, monkeypatch, tmp_path, conn) == 0
    assert conn.events == ["connect", "begin", "select", "insert", "insert", "insert", "commit", "close"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.xlsx"]
    rows = list(openpyxl.load_workbook(tmp_path / "out.xlsx").active.iter_rows(values_only=True))[1:]
    assert [r[0] for r in rows] == conn.inserted and all(r[1] == "Standard" for r in rows)
    printed = capsys.readouterr()
    assert not any(c in printed.out + printed.err for c in FAKE) and "fake-pass" not in printed.out + printed.err


def test_spreadsheet_write_failure_rolls_the_insert_back(gi, monkeypatch, tmp_path):
    conn = FakeConn()

    def broken(*a, **k):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(gi, "write_excel", broken)
    with pytest.raises(OSError):
        run_apply(gi, monkeypatch, tmp_path, conn)
    assert "rollback" in conn.events and "commit" not in conn.events and conn.events[-1] == "close"
    assert list(tmp_path.iterdir()) == []


def test_commit_failure_marks_the_file_and_warns(gi, monkeypatch, tmp_path, capsys):
    conn = FakeConn(fail_commit=True)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        run_apply(gi, monkeypatch, tmp_path, conn)
    assert "commit-failed" in conn.events and "commit" not in conn.events
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.COMMIT-FAILED-VERIFY-BEFORE-USE.xlsx"]
    err = capsys.readouterr().err
    assert "COMMIT failed" in err and "INACTIVE" in err and not any(c in err for c in FAKE)


def test_count_mismatch_rolls_back_and_writes_nothing(gi, monkeypatch, tmp_path):
    conn = FakeConn(insert_result="INSERT 0 0")
    with pytest.raises(RuntimeError, match="rolled back"):
        run_apply(gi, monkeypatch, tmp_path, conn)
    assert "rollback" in conn.events and "commit" not in conn.events and list(tmp_path.iterdir()) == []


def test_collision_with_existing_codes_regenerates_after_normalising(gi, monkeypatch, tmp_path):
    conn = FakeConn(existing=["fake01"])  # stored without prefix / lower-case: still the same code as EPIC-FAKE01
    use_fake(gi, monkeypatch, conn)
    scripted(gi, monkeypatch, ["EPIC-FAKE01", "EPIC-FAKE02", "EPIC-FAKE01", "EPIC-FAKE03", "EPIC-FAKE04"])
    assert gi.main(["--count", "2", "--out", str(tmp_path / "out.xlsx"), "--apply"]) == 0
    assert "EPIC-FAKE01" not in conn.inserted and len(conn.inserted) == 2 and conn.events[-2:] == ["commit", "close"]


def test_no_database_driver_or_fixture_was_loaded():
    assert "asyncpg" not in sys.modules and "pgserver" not in sys.modules
