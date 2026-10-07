"""init_db startup lock hardening.

Startup schema work must give up quickly (INIT_DB_LOCK_TIMEOUT_MS) rather than
queue live traffic behind a waiting ALTER/CREATE INDEX, without changing any
runtime query's timeout behaviour. Everything here runs against a throwaway
database inside the disposable local PostgreSQL started by conftest.py.
"""
import asyncio
import pathlib
import re
import time
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from conftest import TEST_DB_URI
from app.services import user_db

T = user_db.INIT_DB_LOCK_TIMEOUT_MS / 1000
OLD_CHECK = "CHECK (purpose IN ('login_mfa', 'enable_mfa'))"
APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


@pytest_asyncio.fixture
async def lockdb(monkeypatch):
    assert "/postgres?" in TEST_DB_URI
    uri = TEST_DB_URI.replace("/postgres?", "/lock_h1?", 1)
    admin = await asyncpg.connect(TEST_DB_URI)
    await admin.execute("DROP DATABASE IF EXISTS lock_h1 WITH (FORCE)")
    await admin.execute("CREATE DATABASE lock_h1")
    pools, conns = [], []

    async def make_pool(size=1):
        pool = await asyncpg.create_pool(uri, min_size=1, max_size=size, command_timeout=15)
        pools.append(pool)

        async def _get_pool():
            return pool

        monkeypatch.setattr(user_db, "get_pool", _get_pool)
        return pool

    async def connect():
        c = await asyncpg.connect(uri)
        conns.append(c)
        return c

    try:
        yield SimpleNamespace(uri=uri, make_pool=make_pool, connect=connect)
    finally:
        for c in conns:
            if not c.is_closed():
                await c.close()
        for p in pools:
            await p.close()
        await admin.execute("DROP DATABASE IF EXISTS lock_h1 WITH (FORCE)")
        await admin.close()


async def _purpose_constraints(conn):
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'mfa_login_challenges'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE '%purpose%'")
    return [(r["conname"], r["def"]) for r in rows]


async def _make_pre_h1(pool):
    """Schema as it was before H1: narrow purpose CHECK, no v2 proof table."""
    await user_db.init_db()
    async with pool.acquire() as conn:
        for name, _ in await _purpose_constraints(conn):
            await conn.execute(f'ALTER TABLE mfa_login_challenges DROP CONSTRAINT "{name}"')
        await conn.execute(f"ALTER TABLE mfa_login_challenges ADD {OLD_CHECK}")
        await conn.execute("DROP TABLE email_verification_proofs_v2")


async def _hold(conn, sql):
    """Open a transaction on `conn` that keeps a lock until ROLLBACK."""
    await conn.execute("BEGIN")
    await conn.execute(sql)


async def _expect_give_up(label_fragment):
    t0 = time.perf_counter()
    with pytest.raises(user_db.SchemaLockTimeout) as err:
        await asyncio.wait_for(user_db.init_db(), 15)
    elapsed = time.perf_counter() - t0
    assert label_fragment in str(err.value)
    # gave up near the short timeout, nowhere near the pool's 15 s command_timeout
    assert 0.8 * T <= elapsed <= T + 2.5, f"gave up after {elapsed:.2f}s (timeout {T}s)"
    return elapsed


async def _runtime_lock_timeout(pool):
    async with pool.acquire() as c:
        assert not c.is_in_transaction()
        return await c.fetchval("SHOW lock_timeout")


# ------------------------------------------------------------------- A
def test_selected_timeout_is_short_but_not_trivial():
    assert 500 <= user_db.INIT_DB_LOCK_TIMEOUT_MS <= 1000


async def test_uncontended_init_db_succeeds_and_is_repeatable(lockdb):
    await lockdb.make_pool()
    t0 = time.perf_counter()
    await user_db.init_db()
    await user_db.init_db()
    assert time.perf_counter() - t0 < 10


# ------------------------------------------------------------------- B
async def test_gives_up_quickly_when_users_is_locked(lockdb, capsys):
    pool = await lockdb.make_pool()
    await user_db.init_db()
    holder = await lockdb.connect()
    await _hold(holder, "SELECT count(*) FROM users")           # ACCESS SHARE, as any open reader
    try:
        await _expect_give_up("ALTER TABLE users")
        out = capsys.readouterr().out
        assert "init_db gave up" in out and f"{user_db.INIT_DB_LOCK_TIMEOUT_MS} ms" in out
        assert "postgres" not in out.lower() and "host=" not in out and "password" not in out.lower()
    finally:
        await holder.execute("ROLLBACK")
    await user_db.init_db()                                       # D: retry succeeds once it is released
    assert await _runtime_lock_timeout(pool) == "0"


async def test_gives_up_quickly_on_a_create_index_share_lock(lockdb):
    await lockdb.make_pool()
    await user_db.init_db()
    holder = await lockdb.connect()
    await _hold(holder, "LOCK TABLE mfa_sessions IN ROW EXCLUSIVE MODE")   # conflicts with SHARE
    try:
        await _expect_give_up("idx_mfa_sessions")
    finally:
        await holder.execute("ROLLBACK")
    await user_db.init_db()


# ---------------------------------------------------------------- B + F + D
async def test_constraint_swap_is_atomic_when_its_lock_times_out_then_retry_completes(lockdb):
    pool = await lockdb.make_pool()
    await _make_pre_h1(pool)
    async with pool.acquire() as c:
        before = await _purpose_constraints(c)
    assert len(before) == 1 and "verify_email" not in before[0][1]

    holder = await lockdb.connect()
    await _hold(holder, "LOCK TABLE mfa_login_challenges IN ACCESS SHARE MODE")
    try:
        await _expect_give_up("mfa_login_challenges purpose constraint")
        # rolled back as a whole: the constraint is neither missing nor half-changed
        async with pool.acquire() as c:
            assert await _purpose_constraints(c) == before
            assert await c.fetchval("SELECT to_regclass('email_verification_proofs_v2')") is None
    finally:
        await holder.execute("ROLLBACK")

    await user_db.init_db()                                       # retry completes idempotently
    async with pool.acquire() as c:
        after = await _purpose_constraints(c)
        assert len(after) == 1 and "verify_email" in after[0][1]
        assert await c.fetchval("SELECT to_regclass('email_verification_proofs_v2')") is not None
    await user_db.init_db()                                       # and again, still a no-op
    async with pool.acquire() as c:
        assert await _purpose_constraints(c) == after


# ------------------------------------------------------------------- C
async def test_a_failed_attempt_does_not_leave_normal_queries_blocked(lockdb):
    pool = await lockdb.make_pool()
    await user_db.init_db()
    holder, reader = await lockdb.connect(), await lockdb.connect()
    await _hold(holder, "SELECT count(*) FROM users")             # stays open for the whole test
    try:
        attempt = asyncio.create_task(user_db.init_db())
        await asyncio.sleep(0.15)                                 # the startup ALTER is now waiting
        t0 = time.perf_counter()
        # An ordinary query queues behind the waiting ALTER only until it gives up.
        assert await asyncio.wait_for(reader.fetchval("SELECT count(*) FROM users"), 6) == 0
        waited = time.perf_counter() - t0
        assert waited < T + 2.0, f"reader was blocked {waited:.2f}s"
        with pytest.raises(user_db.SchemaLockTimeout):
            await attempt
        # nothing is left waiting behind the failed attempt
        assert await reader.fetchval("SELECT count(*) FROM pg_locks WHERE NOT granted") == 0
        assert await asyncio.wait_for(reader.fetchval("SELECT 1"), 2) == 1
    finally:
        await holder.execute("ROLLBACK")


# ------------------------------------------------------------------- E
async def test_lock_timeout_never_remains_on_a_reused_pooled_connection(lockdb):
    pool = await lockdb.make_pool(size=1)                         # one connection: guaranteed reuse
    await user_db.init_db()
    assert await _runtime_lock_timeout(pool) == "0"               # after success

    holder = await lockdb.connect()
    await _hold(holder, "SELECT count(*) FROM users")
    try:
        await _expect_give_up("ALTER TABLE users")
    finally:
        await holder.execute("ROLLBACK")
    assert await _runtime_lock_timeout(pool) == "0"               # after a timed-out attempt

    # ...and runtime queries still wait as long as they always did: a lock held
    # for ~1.2 s (longer than the startup timeout) delays get_user, it does not fail it.
    blocker = await lockdb.connect()
    await _hold(blocker, "LOCK TABLE users IN ACCESS EXCLUSIVE MODE")

    async def release():
        await asyncio.sleep(1.2)
        await blocker.execute("ROLLBACK")

    releasing = asyncio.create_task(release())
    t0 = time.perf_counter()
    assert await user_db.get_user("nobody") is None
    assert time.perf_counter() - t0 >= 1.0
    await releasing


# ------------------------------------------------------------------- G
async def test_concurrent_startups_converge_without_leftover_state(lockdb):
    pool = await lockdb.make_pool(size=6)
    await _make_pre_h1(pool)
    results = await asyncio.gather(*[user_db.init_db() for _ in range(5)], return_exceptions=True)
    # each instance either completes or gives up cleanly; nothing else may happen
    assert all(r is None or isinstance(r, user_db.SchemaLockTimeout) for r in results), results
    await user_db.init_db()                                       # a later start always completes
    async with pool.acquire() as c:
        found = await _purpose_constraints(c)
        assert len(found) == 1 and "verify_email" in found[0][1]
        assert await c.fetchval("SELECT count(*) FROM pg_locks WHERE NOT granted") == 0
    # no pooled connection kept a timeout
    conns = await asyncio.gather(*[pool.acquire() for _ in range(6)])
    try:
        assert {await c.fetchval("SHOW lock_timeout") for c in conns} == {"0"}
    finally:
        for c in conns:
            await pool.release(c)


async def test_concurrent_first_start_on_an_empty_database_converges(lockdb):
    pool = await lockdb.make_pool(size=5)
    results = await asyncio.gather(*[user_db.init_db() for _ in range(4)], return_exceptions=True)
    # Creating brand-new tables concurrently can, in PostgreSQL itself, raise a
    # duplicate-name error; that is pre-existing behaviour and is non-fatal in
    # the app (main.py catches it). What matters is that a retry converges.
    allowed = (user_db.SchemaLockTimeout, asyncpg.exceptions.UniqueViolationError,
               asyncpg.exceptions.DuplicateTableError, asyncpg.exceptions.DuplicateObjectError)
    assert all(r is None or isinstance(r, allowed) for r in results), results
    await user_db.init_db()
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT to_regclass('email_verification_proofs_v2')") is not None
        assert len(await _purpose_constraints(c)) == 1


# --------------------------------------------- the runtime is untouched
def test_lock_timeout_is_only_ever_set_locally_inside_startup_schema_code():
    """No runtime code path, pool setting or session default may carry a lock timeout."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"lock_timeout", code, re.I) and path.name != "user_db.py":
                offenders.append(f"{path.name}:{n}")
            if re.search(r"SET\s+lock_timeout", code, re.I) or "server_settings" in code:
                offenders.append(f"{path.name}:{n} (session-level or pool default)")
    assert offenders == []
    src = (APP_DIR / "services" / "user_db.py").read_text()
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert len(re.findall(r"SET\s+LOCAL\s+lock_timeout", code)) == 1       # the one helper
    assert re.findall(r"SET\s+lock_timeout", code) == []                    # never session-level


def test_pool_and_runtime_timeouts_are_unchanged():
    def code_only(name):
        # comments are stripped: db_pool.py's explanatory comment also mentions the value
        text = (APP_DIR / "services" / name).read_text()
        return "\n".join(l.split("#", 1)[0] for l in text.splitlines())

    pool_code = code_only("db_pool.py")
    assert re.search(r"^\s*command_timeout=15,\s*$", pool_code, re.M)
    assert re.search(r"^\s*min_size=2,\s*$", pool_code, re.M) and re.search(r"^\s*max_size=8,\s*$", pool_code, re.M)
    assert "lock_timeout" not in pool_code and "server_settings" not in pool_code
    retriever_code = code_only("retriever.py")
    assert "command_timeout=settings.DB_COMMAND_TIMEOUT_SECONDS" in retriever_code
    assert "lock_timeout" not in retriever_code and "server_settings" not in retriever_code


def test_startup_treats_a_lock_timeout_as_non_fatal():
    assert issubclass(user_db.SchemaLockTimeout, Exception)
    main_src = (APP_DIR / "main.py").read_text()
    assert re.search(r"try:\s*await init_db\(\)\s*except Exception as e:", main_src)
