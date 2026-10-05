"""F-09: server-side MFA — email OTP challenges and short-lived MFA sessions.

OTPs are stored only as an HMAC (keyed with MFA_OTP_HASH_SECRET and bound to
the challenge id); session tokens only as SHA-256. Neither, nor any email
address, is ever logged. Every challenge is single-use, UID-bound, expires
after 5 minutes and allows at most 5 attempts. A session is bound to the
Firebase UID *and* the Firebase sign-in (auth_time) and expires after 12 hours.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.services.db_pool import get_pool

CHALLENGE_LIFETIME = timedelta(minutes=5)
MAX_ATTEMPTS = 5
SESSION_LIFETIME = timedelta(hours=12)
RECENT_SIGN_IN = timedelta(minutes=15)

PURPOSE_LOGIN_MFA = "login_mfa"
PURPOSE_ENABLE_MFA = "enable_mfa"

RESULT_SUCCESS = "success"
RESULT_WRONG_OTP = "wrong_otp"
RESULT_TOO_MANY_ATTEMPTS = "too_many_attempts"
RESULT_INVALID = "invalid"  # missing, other UID/purpose, expired or already used


class MfaConfigError(Exception):
    """MFA_OTP_HASH_SECRET is not configured — MFA fails closed."""


def _secret() -> str:
    secret = settings.MFA_OTP_HASH_SECRET
    if not secret:
        raise MfaConfigError()
    return secret


def _hmac(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _session_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def auth_time_from_claims(claims: dict) -> datetime | None:
    value = claims.get("auth_time")
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def signed_in_recently(auth_time: datetime | None) -> bool:
    if auth_time is None:
        return False
    age = datetime.now(timezone.utc) - auth_time
    return timedelta(minutes=-5) <= age <= RECENT_SIGN_IN


async def create_mfa_challenge(uid: str, email: str, purpose: str) -> tuple[str, str]:
    """Creates a challenge and returns (challenge_id, otp). Any outstanding
    challenge for the same UID and purpose is superseded."""
    secret = _secret()
    otp = str(secrets.randbelow(900000) + 100000)
    challenge_id = secrets.token_urlsafe(32)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM mfa_login_challenges WHERE uid = $1 AND expires_at < NOW() - INTERVAL '1 hour'", uid)
            await conn.execute(
                "UPDATE mfa_login_challenges SET consumed_at = NOW() "
                "WHERE uid = $1 AND purpose = $2 AND consumed_at IS NULL", uid, purpose)
            await conn.execute('''
                INSERT INTO mfa_login_challenges
                    (challenge_id, uid, identifier, purpose, otp_hash, attempts, created_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, 0, NOW(), NOW() + $6::interval)
            ''', challenge_id, uid, _hmac(secret, f"mfa-identifier:{email.strip().lower()}"), purpose,
                _hmac(secret, f"{challenge_id}:{otp}"), CHALLENGE_LIFETIME)
    return challenge_id, otp


async def invalidate_mfa_challenge(challenge_id: str, uid: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mfa_login_challenges SET consumed_at = NOW() "
            "WHERE challenge_id = $1 AND uid = $2 AND consumed_at IS NULL", challenge_id, uid)


async def _consume_challenge(conn, uid: str, challenge_id: str, purpose: str, otp: str, secret: str) -> str:
    """Must run inside a transaction. Wrong attempts are recorded and committed
    (the function returns normally), so they are never rolled back."""
    row = await conn.fetchrow('''
        SELECT uid, purpose, otp_hash, attempts, consumed_at, (expires_at <= NOW()) AS expired
        FROM mfa_login_challenges WHERE challenge_id = $1 FOR UPDATE
    ''', challenge_id)
    if row is None or row["uid"] != uid or row["purpose"] != purpose:
        return RESULT_INVALID
    if row["consumed_at"] is not None or row["expired"]:
        return RESULT_INVALID
    if row["attempts"] >= MAX_ATTEMPTS:
        return RESULT_TOO_MANY_ATTEMPTS
    if not hmac.compare_digest(_hmac(secret, f"{challenge_id}:{otp}"), row["otp_hash"]):
        attempts = row["attempts"] + 1
        locked = attempts >= MAX_ATTEMPTS
        await conn.execute(
            "UPDATE mfa_login_challenges SET attempts = $2, "
            "consumed_at = CASE WHEN $3 THEN NOW() ELSE consumed_at END "
            "WHERE challenge_id = $1", challenge_id, attempts, locked)
        return RESULT_TOO_MANY_ATTEMPTS if locked else RESULT_WRONG_OTP
    await conn.execute("UPDATE mfa_login_challenges SET consumed_at = NOW() WHERE challenge_id = $1", challenge_id)
    return RESULT_SUCCESS


async def _create_session(conn, uid: str, auth_time: datetime) -> str:
    raw_token = secrets.token_urlsafe(32)
    await conn.execute(
        "DELETE FROM mfa_sessions WHERE uid = $1 AND (expires_at < NOW() - INTERVAL '1 day' "
        "OR revoked_at < NOW() - INTERVAL '1 day')", uid)
    await conn.execute('''
        INSERT INTO mfa_sessions (session_hash, uid, created_at, expires_at, auth_time)
        VALUES ($1, $2, NOW(), NOW() + $3::interval, $4)
    ''', _session_hash(raw_token), uid, SESSION_LIFETIME, auth_time)
    return raw_token


async def verify_login_challenge(uid: str, challenge_id: str, otp: str,
                                 auth_time: datetime | None) -> tuple[str, str | None]:
    """Verifies a login challenge; on success returns a new raw session token."""
    if auth_time is None:
        return RESULT_INVALID, None
    secret = _secret()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await _consume_challenge(conn, uid, challenge_id, PURPOSE_LOGIN_MFA, otp, secret)
            if result != RESULT_SUCCESS:
                return result, None
            return result, await _create_session(conn, uid, auth_time)


async def confirm_enable_mfa(uid: str, challenge_id: str, otp: str,
                             auth_time: datetime | None) -> tuple[str, str | None]:
    """Enables MFA only after a correct enable-purpose OTP, atomically with
    consuming the challenge and creating the first session."""
    if auth_time is None:
        return RESULT_INVALID, None
    secret = _secret()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT 1 FROM users WHERE uid = $1 FOR UPDATE", uid)
            result = await _consume_challenge(conn, uid, challenge_id, PURPOSE_ENABLE_MFA, otp, secret)
            if result != RESULT_SUCCESS:
                return result, None
            await conn.execute("UPDATE users SET mfa_enabled = TRUE WHERE uid = $1", uid)
            return result, await _create_session(conn, uid, auth_time)


async def validate_mfa_session(raw_token: str | None, uid: str, auth_time: datetime | None) -> bool:
    if not raw_token or not uid or auth_time is None:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT 1 FROM mfa_sessions
            WHERE session_hash = $1 AND uid = $2 AND auth_time = $3
              AND expires_at > NOW() AND revoked_at IS NULL
        ''', _session_hash(raw_token), uid, auth_time)
    return row is not None


async def revoke_mfa_session(raw_token: str | None, uid: str) -> None:
    if not raw_token or not uid:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mfa_sessions SET revoked_at = NOW() "
            "WHERE session_hash = $1 AND uid = $2 AND revoked_at IS NULL", _session_hash(raw_token), uid)


async def disable_mfa_and_revoke_state(uid: str) -> None:
    """Disables MFA, revokes every session and invalidates every outstanding
    challenge for this UID, atomically."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET mfa_enabled = FALSE WHERE uid = $1", uid)
            await conn.execute(
                "UPDATE mfa_sessions SET revoked_at = NOW() WHERE uid = $1 AND revoked_at IS NULL", uid)
            await conn.execute(
                "UPDATE mfa_login_challenges SET consumed_at = NOW() WHERE uid = $1 AND consumed_at IS NULL", uid)
