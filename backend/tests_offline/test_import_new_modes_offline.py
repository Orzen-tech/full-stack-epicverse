"""Offline tests for scripts/import_new_modes.py.

NO DATABASE: the connection is a fake that records the transaction sequence; asyncpg is never imported and no SQL
reaches a server. The spreadsheets are small fixtures written to a temporary folder (fake game data).
These tests live outside tests/ on purpose: tests/conftest.py starts a PostgreSQL server when it is loaded.
Run from backend/:  python -m pytest -p no:cacheprovider tests_offline
"""
import importlib.util
import pathlib
import sys

import openpyxl
import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "import_new_modes.py"
HEAD = ["Character", "Character Card No.", "Attribute", "Attribute Card No.", "App Message (Crisp)", "Final Segment"]
FAKE_URL = "postgresql://fake-user:fake-secret-pw@db.fake.invalid:5432/fakedb"


@pytest.fixture
def im(monkeypatch):
    spec = importlib.util.spec_from_file_location("import_new_modes_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_load_env", lambda: None)  # never read a real .env
    return mod


def write_mode(folder, i, rows, header=HEAD):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(folder / f"mode {i}.xlsx")


@pytest.fixture
def data(tmp_path, monkeypatch):
    for i in range(1, 8):
        write_mode(tmp_path, i, [[f"Char{i}", 1, "Attr", 2, f"message {i}a", "Valid"],
                                 [f"Char{i}", 3, "Attr", 4, f"message {i}b", "Invalid"]])
    monkeypatch.setenv("EPICVERSE_DATA_DIR", str(tmp_path))
    return tmp_path


class FakeTx:
    def __init__(self, conn):
        self.conn = conn

    async def start(self):
        self.conn.events.append("begin")

    async def commit(self):
        self.conn.events.append("commit")

    async def rollback(self):
        self.conn.events.append("rollback")


class FakeConn:
    def __init__(self, fail_on_insert=None, count_override=None):
        self.fail_on_insert, self.count_override = fail_on_insert, count_override
        self.events, self.rows = [], []

    def transaction(self):
        return FakeTx(self)

    async def execute(self, sql, *args):
        if sql.strip().upper().startswith("DELETE"):
            assert "begin" in self.events, "DELETE outside the transaction"
            self.events.append("delete")
            self.rows = []
            return "DELETE 0"
        self.events.append("insert")
        if self.fail_on_insert is not None and len(self.rows) + 1 == self.fail_on_insert:
            raise RuntimeError("simulated insert error")
        self.rows.append(args)
        return "INSERT 0 1"

    async def fetchval(self, sql):
        self.events.append("count")
        return self.count_override if self.count_override is not None else len(self.rows)

    async def fetch(self, sql):
        return [{"gameplay_mode": m} for m in sorted({r[0] for r in self.rows})]

    async def close(self):
        self.events.append("close")


def use_fake(mod, monkeypatch, conn, url=FAKE_URL):
    async def connect(u):
        conn.events.append("connect")
        return conn
    monkeypatch.setattr(mod, "_connect", connect)
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)


def assert_no_secret(captured):
    text = captured.out + captured.err
    assert "fake-secret-pw" not in text and FAKE_URL not in text and "fake-user" not in text


# ---------------------------------------------------------------- safeguards: nothing connects
def test_validate_only_without_apply_never_connects(im, data, monkeypatch, capsys):
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    assert im.main([]) == 0
    assert conn.events == []
    out = capsys.readouterr()
    assert "Validated 14 rows" in out.out and "VALIDATION ONLY" in out.out
    assert_no_secret(out)


def test_apply_without_confirmation_refuses_before_connecting(im, data, monkeypatch, capsys):
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    with pytest.raises(SystemExit) as e:
        im.main(["--apply"])
    assert "--confirm-host" in str(e.value) and "db.fake.invalid" in str(e.value) and conn.events == []
    assert "fake-secret-pw" not in str(e.value)
    assert_no_secret(capsys.readouterr())


def test_apply_with_wrong_host_refuses_before_connecting(im, data, monkeypatch):
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    with pytest.raises(SystemExit, match="does not match"):
        im.main(["--apply", "--confirm-host", "prod.example.invalid"])
    assert conn.events == []


@pytest.mark.parametrize("url", [None, "", "not a url", "postgresql:///nohost"])
def test_missing_or_unidentifiable_host_refuses_before_connecting(im, data, monkeypatch, url):
    conn = FakeConn()
    use_fake(im, monkeypatch, conn, url=url)
    with pytest.raises(SystemExit, match="host cannot be identified"):
        im.main(["--apply", "--confirm-host", "anything"])
    assert conn.events == []


def test_unix_socket_host_is_identified(im):
    assert im.db_host("postgresql://u:p@/db?host=/cloudsql/proj:region:inst") == "/cloudsql/proj:region:inst"
    assert im.db_host("postgresql+asyncpg://u:p@Db.Example.Invalid:5432/x") == "db.example.invalid"


# ---------------------------------------------------------------- validation before any connection
def test_missing_file_stops_before_connecting(im, data, monkeypatch):
    (data / "mode 4.xlsx").unlink()
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    with pytest.raises(SystemExit, match="missing files"):
        im.main(["--apply", "--confirm-host", "db.fake.invalid"])
    assert conn.events == []


def test_missing_message_column_stops_before_connecting(im, data, monkeypatch):
    write_mode(data, 3, [["C", 1, "A", 2, "x", "Valid"]], header=HEAD[:4] + ["Message"] + HEAD[5:])
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    with pytest.raises(SystemExit, match="mode 3.xlsx: no 'App Message"):
        im.main(["--apply", "--confirm-host", "db.fake.invalid"])
    assert conn.events == []


@pytest.mark.parametrize("blank", [None, "   "])
def test_blank_message_stops_before_connecting_without_printing_cell_text(im, data, monkeypatch, blank):
    write_mode(data, 2, [["C", 1, "A", 2, "fine text", "Valid"], ["C", 2, "A", 3, blank, "Valid"]])
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    with pytest.raises(SystemExit) as e:
        im.main(["--apply", "--confirm-host", "db.fake.invalid"])
    assert "mode 2.xlsx: row 3 has blank message text" in str(e.value) and "fine text" not in str(e.value)
    assert conn.events == []


def test_fully_empty_rows_are_skipped_before_counting(im, data, monkeypatch):
    write_mode(data, 5, [["C", 1, "A", 2, "first", "Valid"], [None] * 6, ["C", 3, "A", 4, "second", "Valid"]])
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    assert im.main(["--apply", "--confirm-host", "db.fake.invalid"]) == 0
    assert len(conn.rows) == 14 and conn.events[-2:] == ["commit", "close"]


def test_both_message_headers_are_accepted(im, data, monkeypatch):
    write_mode(data, 6, [["C", 1, "A", 2, "from the old header", "Valid"]], header=HEAD[:4] + ["App Message"] + HEAD[5:])
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    assert im.main(["--apply", "--confirm-host", "DB.FAKE.INVALID"]) == 0
    assert any(r[5] == "from the old header" and r[0] == "WarRoom (YuddhaKanda)" for r in conn.rows)


# ---------------------------------------------------------------- the transaction (fake connection)
def test_valid_run_is_one_transaction_delete_inserts_count_commit(im, data, monkeypatch, capsys):
    conn = FakeConn()
    use_fake(im, monkeypatch, conn)
    assert im.main(["--apply", "--confirm-host", "db.fake.invalid"]) == 0
    assert conn.events == ["connect", "begin", "delete"] + ["insert"] * 14 + ["count", "commit", "close"]
    assert {r[0] for r in conn.rows} == set(im.KANDA_MAP.values())
    out = capsys.readouterr()
    assert "db.fake.invalid" in out.out and "card_embeddings" in out.out
    assert_no_secret(out)


def test_database_insert_error_rolls_everything_back(im, data, monkeypatch):
    conn = FakeConn(fail_on_insert=5)
    use_fake(im, monkeypatch, conn)
    with pytest.raises(RuntimeError, match="simulated insert error"):
        im.main(["--apply", "--confirm-host", "db.fake.invalid"])
    assert conn.events[-2:] == ["rollback", "close"] and "commit" not in conn.events


def test_row_count_mismatch_rolls_everything_back(im, data, monkeypatch):
    conn = FakeConn(count_override=13)
    use_fake(im, monkeypatch, conn)
    with pytest.raises(RuntimeError, match="expected 14"):
        im.main(["--apply", "--confirm-host", "db.fake.invalid"])
    assert conn.events[-2:] == ["rollback", "close"] and "commit" not in conn.events


def test_no_database_driver_or_fixture_was_loaded():
    assert "asyncpg" not in sys.modules and "pgserver" not in sys.modules
