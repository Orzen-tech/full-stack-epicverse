"""F-09 H1 — the additive schema changes in init_db().

A SECOND disposable database inside the same throwaway Postgres stands in for
a pre-H1 deployment, so nothing here touches the main test database and the
migration is exercised exactly as it would run at Cloud Run start-up.
"""
import asyncio

import asyncpg
import pytest
import pytest_asyncio

from conftest import TEST_DB_URI
from app.services import user_db

OLD_CHECK = "CHECK (purpose IN ('login_mfa', 'enable_mfa'))"


def _uri_for(dbname: str) -> str:
    assert "/postgres?" in TEST_DB_URI
    return TEST_DB_URI.replace("/postgres?", f"/{dbname}?", 1)


async def _purpose_constraints(conn):
    return await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'mfa_login_challenges'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE '%purpose%'")


@pytest_asyncio.fixture
async def legacy_db(monkeypatch):
    admin = await asyncpg.connect(TEST_DB_URI)
    await admin.execute("DROP DATABASE IF EXISTS legacy_h1")
    await admin.execute("CREATE DATABASE legacy_h1")
    pool = await asyncpg.create_pool(_uri_for("legacy_h1"), min_size=1, max_size=6)

    async def _get_pool():
        return pool

    monkeypatch.setattr(user_db, "get_pool", _get_pool)
    try:
        yield pool
    finally:
        await pool.close()
        await admin.execute("DROP DATABASE IF EXISTS legacy_h1")
        await admin.close()


async def _make_pre_h1(pool):
    """Build the schema as it existed before H1: the narrow purpose CHECK
    (under whatever name Postgres generated) and no v2 proof table."""
    await user_db.init_db()
    async with pool.acquire() as conn:
        for c in await _purpose_constraints(conn):
            await conn.execute(f'ALTER TABLE mfa_login_challenges DROP CONSTRAINT "{c["conname"]}"')
        await conn.execute(f"ALTER TABLE mfa_login_challenges ADD {OLD_CHECK}")
        await conn.execute("DROP TABLE email_verification_proofs_v2")
        await conn.execute("INSERT INTO users (uid, email) VALUES ('uidA', 'a@example.com')")
        await conn.execute(
            "INSERT INTO mfa_login_challenges (challenge_id, uid, identifier, purpose, otp_hash, expires_at) "
            "VALUES ('c-login', 'uidA', 'h', 'login_mfa', 'h', NOW() + INTERVAL '5 minutes')")
        await conn.execute(
            "INSERT INTO email_verification_proofs (email_hash, expires_at) "
            "VALUES ('legacy-hash', NOW() + INTERVAL '5 minutes')")


async def _insert_challenge(conn, cid, purpose):
    await conn.execute(
        "INSERT INTO mfa_login_challenges (challenge_id, uid, identifier, purpose, otp_hash, expires_at) "
        "VALUES ($1, 'uidA', 'h', $2, 'h', NOW() + INTERVAL '5 minutes')", cid, purpose)


async def test_pre_h1_database_rejects_verify_email_until_migrated(legacy_db):
    await _make_pre_h1(legacy_db)
    async with legacy_db.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_challenge(conn, "c-before", "verify_email")


async def test_init_db_widens_the_constraint_and_adds_the_v2_table(legacy_db):
    await _make_pre_h1(legacy_db)
    await user_db.init_db()
    async with legacy_db.acquire() as conn:
        found = await _purpose_constraints(conn)
        assert len(found) == 1 and "verify_email" in found[0]["def"]
        for cid, purpose in (("c1", "login_mfa"), ("c2", "enable_mfa"), ("c3", "verify_email")):
            await _insert_challenge(conn, cid, purpose)
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_challenge(conn, "c-bad", "something_else")
        # existing rows survive, and the v1 table is deliberately left in place
        assert await conn.fetchval("SELECT count(*) FROM mfa_login_challenges WHERE challenge_id='c-login'") == 1
        assert await conn.fetchval("SELECT count(*) FROM email_verification_proofs") == 1
        # v2 table exists with its constraints
        assert await conn.fetchval("SELECT to_regclass('email_verification_proofs_v2')") is not None
        with pytest.raises(asyncpg.CheckViolationError):  # wrong purpose
            await conn.execute("INSERT INTO email_verification_proofs_v2 (proof_hash, email_hash, purpose, expires_at) "
                               "VALUES ('p', 'e', 'other', NOW() + INTERVAL '1 minute')")
        with pytest.raises(asyncpg.CheckViolationError):  # expiry must be after creation
            await conn.execute("INSERT INTO email_verification_proofs_v2 (proof_hash, email_hash, purpose, expires_at) "
                               "VALUES ('p', 'e', 'signup_email_verification', NOW() - INTERVAL '1 minute')")
        with pytest.raises(asyncpg.CheckViolationError):  # a UID without a consumption time
            await conn.execute("INSERT INTO email_verification_proofs_v2 "
                               "(proof_hash, email_hash, purpose, expires_at, consumed_by_uid) "
                               "VALUES ('p', 'e', 'signup_email_verification', NOW() + INTERVAL '1 minute', 'u')")


async def test_init_db_is_idempotent_and_safe_to_run_concurrently(legacy_db):
    await _make_pre_h1(legacy_db)
    # four instances starting at once on a pre-H1 database
    await asyncio.gather(*[user_db.init_db() for _ in range(4)])
    await user_db.init_db()
    async with legacy_db.acquire() as conn:
        found = await _purpose_constraints(conn)
        assert len(found) == 1 and "verify_email" in found[0]["def"]
        await _insert_challenge(conn, "c-after", "verify_email")


async def test_old_ddl_is_harmless_against_the_new_schema(legacy_db):
    """A rolled-back (pre-H1) backend re-runs its own CREATE TABLE IF NOT
    EXISTS at start-up; that must not narrow the constraint again or break."""
    await user_db.init_db()
    async with legacy_db.acquire() as conn:
        await conn.execute(f'''
            CREATE TABLE IF NOT EXISTS mfa_login_challenges (
                challenge_id TEXT PRIMARY KEY,
                uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                identifier TEXT NOT NULL,
                purpose TEXT NOT NULL {OLD_CHECK},
                otp_hash TEXT NOT NULL,
                attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL
            )''')
        await conn.execute("INSERT INTO users (uid, email) VALUES ('uidA', 'a@example.com')")
        await _insert_challenge(conn, "c-new", "verify_email")
        # and the old v1 proof table still behaves as the old code expects
        await conn.execute("INSERT INTO email_verification_proofs (email_hash, expires_at) "
                           "VALUES ('h', NOW() + INTERVAL '1 minute') ON CONFLICT (email_hash) DO NOTHING")
