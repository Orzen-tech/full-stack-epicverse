"""Isolated test harness for the F-09 backend.

SAFETY: every test runs against a throwaway PostgreSQL started by `pgserver`
on a Unix socket inside a fresh temp directory. The environment is pointed at
it BEFORE any app module is imported, and the suite refuses to start unless
the database URL is provably that local socket. Firebase, SendGrid and
OpenAI are never contacted: Firebase token verification and the email sender
are replaced with in-memory fakes.
"""
import os
import pathlib
import re
import shutil
import tempfile
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

# --- 1. disposable database + environment (must precede any `app` import) ---
import pgserver

# A short temp path keeps the Unix-socket path under macOS's 104-byte limit.
_PG_DIR = tempfile.mkdtemp(prefix="pgt_")
_SERVER = pgserver.get_server(pathlib.Path(_PG_DIR), cleanup_mode="delete")
_URI = _SERVER.get_uri()

_host = re.search(r"[?&]host=([^&]+)", _URI)
assert _host and os.path.realpath(_host.group(1)).startswith(os.path.realpath(_PG_DIR)), \
    "test database is not a local temp-dir Unix socket; refusing to run"
assert "@/" in _URI, "test database URL must not name a TCP host; refusing to run"

os.environ["DATABASE_URL"] = _URI
os.environ["MFA_OTP_HASH_SECRET"] = "test-only-mfa-otp-hash-secret"
os.environ["OTP_RATE_LIMIT_HASH_SECRET"] = "test-only-otp-rate-limit-secret"
for _var in ("EMAIL_VERIFY_LEGACY_COMPAT", "GOOGLE_APPLICATION_CREDENTIALS",
             "SENDGRID_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_var, None)

import firebase_admin.auth as fb_auth  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import db_pool  # noqa: E402

assert settings.DATABASE_URL == _URI and db_pool.DATABASE_URL == _URI, \
    "app is not using the disposable test database; refusing to run"

TEST_DB_URI = _URI

_TRUNCATE_TABLES = (
    "users", "user_otps", "email_verification_proofs", "email_verification_proofs_v2",
    "otp_send_rate_limits", "invite_codes", "user_feedback",
)


def pytest_sessionfinish(session, exitstatus):
    try:
        _SERVER.cleanup()
    finally:
        shutil.rmtree(_PG_DIR, ignore_errors=True)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _database():
    from app.services import user_db
    await user_db.init_db()
    yield
    await db_pool.close_pool()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(_database):
    pool = await db_pool.get_pool()
    async with pool.acquire() as conn:
        for table in _TRUNCATE_TABLES:
            if await conn.fetchval("SELECT to_regclass($1)", table):
                await conn.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
    yield


# --- 2. fakes for everything outside the database --------------------------
class FakeFirebase:
    """Stands in for firebase_admin.auth. Tokens look like 'tok:<uid>'."""

    def __init__(self):
        self.tokens: dict[str, dict] = {}
        self.accounts_by_email: dict[str, SimpleNamespace] = {}
        self.lookup_calls = 0

    def add(self, uid: str, email: str, auth_age_seconds: int = 60) -> str:
        token = f"tok:{uid}"
        self.tokens[token] = {
            "uid": uid, "user_id": uid, "sub": uid, "email": email,
            "auth_time": int(time.time()) - auth_age_seconds,
        }
        return token

    def verify_id_token(self, token, *args, **kwargs):
        if token in self.tokens:
            return dict(self.tokens[token])
        raise ValueError("invalid test token")

    def get_user_by_email(self, email, *args, **kwargs):
        self.lookup_calls += 1
        account = self.accounts_by_email.get(email.strip().lower())
        if account is None:
            raise fb_auth.UserNotFoundError("no such test user")
        return account


class Mailbox:
    """Captures OTP emails instead of sending them."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_otp_email(self, email, otp, valid_minutes=1):
        self.sent.append((email, otp))
        return True

    def to(self, email: str) -> list[str]:
        return [otp for to, otp in self.sent if to.strip().lower() == email.strip().lower()]

    def last_otp(self, email: str) -> str:
        otps = self.to(email)
        assert otps, "no OTP email was sent to that address"
        return otps[-1]


@pytest.fixture
def fb(monkeypatch):
    fake = FakeFirebase()
    monkeypatch.setattr(fb_auth, "verify_id_token", fake.verify_id_token)
    monkeypatch.setattr(fb_auth, "get_user_by_email", fake.get_user_by_email)
    return fake


@pytest.fixture
def mailbox(monkeypatch):
    from app.services import email_service
    box = Mailbox()

    async def _no_notification(*args, **kwargs):
        return True

    monkeypatch.setattr(email_service, "send_otp_email", box.send_otp_email)
    monkeypatch.setattr(email_service, "send_feedback_notification", _no_notification)
    return box


@pytest_asyncio.fixture
async def client(fb, mailbox):
    from app.api import routes
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def make_user(fb):
    """Inserts a users row (as /sync-user would) and returns its auth token."""
    from app.services.db_pool import get_pool

    async def _make(uid: str, email: str, verified: bool = False, mfa: bool = False,
                    firebase_account: bool = True, auth_age_seconds: int = 60) -> str:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled) "
                "VALUES ($1, $2, $3, $4, $5)", uid, uid, email, verified, mfa)
        if firebase_account:
            fb.accounts_by_email[email.strip().lower()] = SimpleNamespace(uid=uid)
        return fb.add(uid, email, auth_age_seconds)

    return _make
