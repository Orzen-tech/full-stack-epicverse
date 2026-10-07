"""H3 (/sync-user email authority) and H4 (OTP / auth hardening).

Runs against the throwaway local PostgreSQL from conftest.py. (H2, the Flutter
networking finding, has no backend component; it is covered by
frontend/EpicVerseApp/test/network_pinning_guard_test.dart.)
"""
import asyncio
import hmac
from types import SimpleNamespace

import firebase_admin.auth as fb_auth
import pytest

from app.api import routes
from app.core.config import settings
from app.services import email_service, otp_rate_limiter, user_db
from f09_helpers import API, bearer, execute, fetch, fetchrow, fetchval

OWNER = "owner@example.com"
OTHER = "someone-else@example.com"


async def _invite(code, max_uses=1):
    await execute("INSERT INTO invite_codes (code, max_uses) VALUES ($1, $2)", code, max_uses)


async def _sync(client, token, **body):
    return await client.post(f"{API}/sync-user", headers=bearer(token), json=body)


# ===================================================================== H3
@pytest.mark.parametrize("how", ["empty-email-claim", "no-email-claim"])
async def test_h3_emailless_token_cannot_create_an_account_and_keeps_the_invite(client, fb, how):
    await _invite("EPIC-H3A")
    tok = fb.add("uidNoMail", "")
    if how == "no-email-claim":
        fb.tokens[tok].pop("email")
    # a client-supplied email must not stand in for the missing token email
    r = await _sync(client, tok, firebase_id="uidNoMail", display_name="N", email="attacker@example.com",
                    invite_code="EPIC-H3A")
    assert r.status_code == 403 and "verified email" in r.json()["detail"].lower()
    assert await fetchval("SELECT count(*) FROM users") == 0
    assert await fetchval("SELECT current_uses FROM invite_codes WHERE code = 'EPIC-H3A'") == 0
    # decided before the invite is touched: the same invite still works for a real token
    ok = await _sync(client, fb.add("uidOk", "ok@example.com"), firebase_id="uidOk", invite_code="EPIC-H3A")
    assert ok.status_code == 200
    assert await fetchval("SELECT current_uses FROM invite_codes WHERE code = 'EPIC-H3A'") == 1


async def test_h3_client_email_is_never_authoritative(client, fb):
    await _invite("EPIC-H3B", max_uses=3)
    # a different client email than the token's is refused, and nothing is created
    r = await _sync(client, fb.add("uidA", OWNER), firebase_id="uidA", email=OTHER, invite_code="EPIC-H3B")
    assert r.status_code == 403
    assert await fetchval("SELECT count(*) FROM users") == 0
    # no client email at all: the row takes the token's email
    assert (await _sync(client, fb.add("uidB", "Token.Case@Example.com"), firebase_id="uidB",
                        invite_code="EPIC-H3B")).status_code == 200
    assert await fetchval("SELECT email FROM users WHERE uid = 'uidB'") == "Token.Case@Example.com"
    # a matching client email changes nothing
    assert (await _sync(client, fb.add("uidC", "c@example.com"), firebase_id="uidC", email="C@EXAMPLE.COM",
                        invite_code="EPIC-H3B")).status_code == 200
    assert await fetchval("SELECT email FROM users WHERE uid = 'uidC'") == "c@example.com"


async def test_h3_existing_user_sync_is_unchanged_even_for_an_emailless_token(client, fb, make_user):
    tok = await make_user("uidA", OWNER, verified=True)
    fb.tokens[tok].pop("email")
    r = await _sync(client, tok, firebase_id="uidA", display_name="Renamed", email=OTHER,
                    email_verified=False, mfa_enabled=True)
    assert r.status_code == 200
    row = await fetchrow("SELECT display_name, email, email_verified, mfa_enabled FROM users WHERE uid = 'uidA'")
    assert row["display_name"] == "Renamed"
    assert row["email"] == OWNER and row["email_verified"] is True and row["mfa_enabled"] is False


# ================================================================= H4.1
async def test_h4_otp_is_compared_in_constant_time_and_behaviour_is_unchanged(monkeypatch):
    seen = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((type(a), type(b)))
        return real(a, b)

    monkeypatch.setattr(user_db.hmac, "compare_digest", spy)
    await user_db.save_otp(OWNER, "123456")
    assert await user_db.verify_otp(OWNER, "000000") == "invalid"
    assert seen == [(bytes, bytes)]
    assert await user_db.verify_otp(OWNER, "123456") == "success"
    assert len(seen) == 2
    assert await user_db.verify_otp(OWNER, "123456") == "invalid"     # consumed: single use
    assert await fetchval("SELECT count(*) FROM user_otps") == 0


async def test_h4_non_ascii_code_is_rejected_without_error_and_counts_as_an_attempt():
    await user_db.save_otp(OWNER, "123456")
    assert await user_db.verify_otp(OWNER, "１２３４５６") == "invalid"  # full-width digits
    assert await fetchval("SELECT attempts FROM user_otps WHERE identifier = $1", OWNER) == 1


async def test_h4_attempt_limit_expiry_and_locking_are_unchanged():
    await user_db.save_otp(OWNER, "123456")
    assert [await user_db.verify_otp(OWNER, "000000") for _ in range(4)] == ["invalid"] * 4
    assert await user_db.verify_otp(OWNER, "000000") == "too_many_attempts"      # fifth wrong attempt
    assert await user_db.verify_otp(OWNER, "123456") == "invalid"                # the right code is dead too

    await user_db.save_otp(OWNER, "654321")
    await execute("UPDATE user_otps SET created_at = NOW() - INTERVAL '2 minutes'")
    assert await user_db.verify_otp(OWNER, "654321") == "invalid"                # expired

    await user_db.save_otp(OWNER, "111222")                                       # row locking: concurrent guesses
    results = await asyncio.gather(*[user_db.verify_otp(OWNER, "000000") for _ in range(8)])
    assert results.count("too_many_attempts") == 1 and results.count("invalid") == 7
    assert await user_db.verify_otp(OWNER, "111222") == "invalid"


# ================================================================= H4.2
class _Req:
    headers = {}
    client = SimpleNamespace(host="203.0.113.9")


async def _db_down():
    raise RuntimeError("storage down for victim@example.com")


async def test_h4_limiter_storage_failure_rejects_and_sends_nothing(client, mailbox, fb, make_user, monkeypatch, capsys):
    tok = await make_user("uidA", OWNER)
    with monkeypatch.context() as m:
        m.setattr(otp_rate_limiter, "get_pool", _db_down)
        assert await otp_rate_limiter.check_otp_send_allowed("x@example.com", _Req()) == (False, 60)
        out = capsys.readouterr().out
        assert "rejecting request" in out and "victim@example.com" not in out
        for path, headers, data in (
                ("/auth/send-email-otp", {}, {"identifier": "new@example.com"}),
                ("/auth/send-otp", bearer(tok), {"identifier": OWNER})):
            r = await client.post(f"{API}{path}", headers=headers, data=data)
            assert r.status_code == 429 and r.headers["retry-after"] == "60", path
        assert mailbox.sent == []
        assert await fetchval("SELECT count(*) FROM user_otps") == 0          # no OTP was even generated
    # storage back: sending works again and the normal per-address limit still applies
    codes = [(await client.post(f"{API}/auth/send-email-otp", data={"identifier": "new@example.com"})).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429]


# ================================================================= H4.3
async def test_h4_authenticated_send_otp_must_target_the_token_email(client, mailbox, fb, make_user):
    tok = await make_user("uidA", OWNER)
    for field in ("identifier", "email"):
        r = await client.post(f"{API}/auth/send-otp", headers=bearer(tok), data={field: OTHER})
        assert r.status_code == 403, field
    assert mailbox.sent == [] and await fetchval("SELECT count(*) FROM user_otps") == 0
    assert await fetchval("SELECT count(*) FROM otp_send_rate_limits") == 0   # rejected before the limiter

    for variant in (OWNER, "  OWNER@Example.COM "):
        assert (await client.post(f"{API}/auth/send-otp", headers=bearer(tok),
                                  data={"identifier": variant})).status_code == 200
    assert len(mailbox.to(OWNER)) == 2 and mailbox.to(OTHER) == []

    nomail = fb.add("uidNoMail", "")
    r = await client.post(f"{API}/auth/send-otp", headers=bearer(nomail), data={"identifier": OWNER})
    assert r.status_code == 403
    assert len(mailbox.sent) == 2


async def test_h4_unauthenticated_signup_otp_paths_are_unchanged(client, mailbox):
    await _invite("EPIC-OTP1", max_uses=5)
    assert (await client.post(f"{API}/auth/send-email-otp", data={"identifier": "new@example.com"})).status_code == 200
    ok = await client.post(f"{API}/auth/send-otp", data={"identifier": "invited@example.com",
                                                          "invite_code": "EPIC-OTP1"})
    assert ok.status_code == 200 and mailbox.to("invited@example.com")
    assert (await client.post(f"{API}/auth/send-otp", data={"identifier": "x@example.com"})).status_code == 403
    bad = await client.post(f"{API}/auth/send-otp", data={"identifier": "x@example.com", "invite_code": "EPIC-NOPE"})
    assert bad.status_code == 403


# ================================================================= H4.4
TOKEN_TEXT = "SECRETTOKENTEXT.part2.part3"


class _WS:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"}
        self.sent, self.code = [], None

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        self.code = code


async def test_h4_token_verification_errors_never_log_the_token(client, fb, monkeypatch, capsys):
    def verify(token, *a, **k):  # mimics google-auth, whose message for a malformed token embeds it
        raise ValueError(f"Wrong number of segments in token: {token}")

    monkeypatch.setattr(fb_auth, "verify_id_token", verify)
    r = await client.get(f"{API}/auth/session-status", headers=bearer(TOKEN_TEXT))
    assert r.status_code == 401
    r = await client.post(f"{API}/auth/send-otp", headers=bearer(TOKEN_TEXT), data={"identifier": OWNER})
    assert r.status_code == 403
    ws = _WS(TOKEN_TEXT)
    await routes.websocket_realtime(ws, uid="uidX", mode="Mode 1", session_id="s", token="")
    assert ws.code == 1008
    out = capsys.readouterr()
    text = out.out + out.err
    assert "SECRETTOKENTEXT" not in text and "Wrong number of segments" not in text
    assert text.count("ValueError") == 3                                      # still useful: the error type


class _FakeSendGrid:
    def __init__(self, status=202, text="", exc=None):
        self.status, self.text, self.exc = status, text, exc

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if self.exc:
            raise self.exc
        return SimpleNamespace(status_code=self.status, text=self.text)


async def _reset(client, email):
    return await client.post(f"{API}/auth/send-password-reset", data={"identifier": email})


async def test_h4_password_reset_logs_carry_no_email_link_or_provider_body(client, monkeypatch, capsys):
    link = "https://reset.example/oob?code=RESETSECRET123"

    def no_user(email):
        raise fb_auth.UserNotFoundError("none")

    def broken(email):
        raise RuntimeError(f"boom for {email} {link}")

    # unknown account: same 200 as a known one, and the address is not logged
    monkeypatch.setattr(fb_auth, "generate_password_reset_link", no_user)
    assert (await _reset(client, "victim1@example.com")).status_code == 200
    # Firebase failure: only the exception type is logged
    monkeypatch.setattr(fb_auth, "generate_password_reset_link", broken)
    assert (await _reset(client, "victim2@example.com")).status_code == 500
    # provider rejects: status only, never its body (which can echo the recipient and link)
    monkeypatch.setattr(fb_auth, "generate_password_reset_link", lambda email: link)
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "httpx", SimpleNamespace(AsyncClient=_FakeSendGrid(
        status=400, text=f"invalid recipient victim3@example.com in {link}")))
    assert (await _reset(client, "victim3@example.com")).status_code == 503
    # transport error: only the exception type
    monkeypatch.setattr(email_service, "httpx", SimpleNamespace(AsyncClient=_FakeSendGrid(
        exc=RuntimeError(f"net error victim4@example.com {link}"))))
    assert (await _reset(client, "victim4@example.com")).status_code == 503
    # success: a generic line
    monkeypatch.setattr(email_service, "httpx", SimpleNamespace(AsyncClient=_FakeSendGrid(status=202)))
    assert (await _reset(client, "victim5@example.com")).status_code == 200

    out = capsys.readouterr()
    text = out.out + out.err
    for secret in ("victim1@", "victim2@", "victim3@", "victim4@", "victim5@", "RESETSECRET123",
                   "reset.example", "invalid recipient", "boom for", "net error"):
        assert secret not in text, secret
    assert "unknown account" in text and "RuntimeError" in text and "status=400" in text
    assert "Password reset email sent" in text


async def test_h4_otp_database_errors_do_not_log_the_values(monkeypatch, capsys):
    async def failing_pool():
        raise Exception("could not write row (victim@example.com, 123456)")

    monkeypatch.setattr(user_db, "get_pool", failing_pool)
    assert await user_db.save_otp("victim@example.com", "123456") is False
    assert await user_db.verify_otp("victim@example.com", "123456") == "invalid"
    out = capsys.readouterr()
    text = out.out + out.err
    assert "victim@example.com" not in text and "123456" not in text
    assert "OTP Save Error: Exception" in text and "OTP Verification Error: Exception" in text
