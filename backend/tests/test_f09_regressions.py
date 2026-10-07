"""Regression coverage for the behaviour H1 must NOT change:
F-06 (invite-only signup / UID-bound sync-user), F-07 (OTP send rate
limiting), F-09 MFA (challenge, session, enable, disable) and the Phase 3
HTTP + WebSocket enforcement of verified email and MFA sessions."""
import json

import pytest

from f09_helpers import API, bearer, execute, fetch, fetchrow, fetchval, is_verified

EMAIL = "user@example.com"


# =============================================================== F-06
async def _invite(code="EPIC-ABC123", max_uses=1, expired=False):
    await execute(
        "INSERT INTO invite_codes (code, max_uses, expires_at) VALUES ($1, $2, "
        + ("NOW() - INTERVAL '1 day'" if expired else "NULL") + ")", code, max_uses)


async def _sync(client, token, **body):
    return await client.post(f"{API}/sync-user", headers=bearer(token), json=body)


async def test_f06_new_user_requires_an_invite(client, fb):
    tok = fb.add("uidNew", EMAIL)
    r = await _sync(client, tok, firebase_id="uidNew", display_name="N", email=EMAIL)
    assert r.status_code == 403 and "invite" in r.json()["detail"].lower()
    assert await fetchval("SELECT count(*) FROM users") == 0


async def test_f06_invalid_expired_and_exhausted_invites_are_rejected(client, fb):
    await _invite("EPIC-OLD", expired=True)
    await _invite("EPIC-ONE", max_uses=1)
    for code in ("EPIC-NOPE", "EPIC-OLD"):
        tok = fb.add("uidX", EMAIL)
        r = await _sync(client, tok, firebase_id="uidX", email=EMAIL, invite_code=code)
        assert r.status_code == 403
    tok1 = fb.add("uid1", "one@example.com")
    assert (await _sync(client, tok1, firebase_id="uid1", email="one@example.com", invite_code="EPIC-ONE")
            ).status_code == 200
    tok2 = fb.add("uid2", "two@example.com")
    r = await _sync(client, tok2, firebase_id="uid2", email="two@example.com", invite_code="EPIC-ONE")
    assert r.status_code == 403 and "limit" in r.json()["detail"].lower()
    assert await fetchval("SELECT count(*) FROM users") == 1
    assert await fetchval("SELECT current_uses FROM invite_codes WHERE code = 'EPIC-ONE'") == 1


async def test_f06_valid_invite_creates_row_bound_to_the_token_email(client, fb):
    await _invite()
    tok = fb.add("uidNew", "Token.Email@Example.com")
    # no email in the body at all: the row takes the verified token's email
    r = await _sync(client, tok, firebase_id="uidNew", display_name="N", invite_code="EPIC-ABC123")
    assert r.status_code == 200
    row = await fetchrow("SELECT email, email_verified, mfa_enabled FROM users WHERE uid = 'uidNew'")
    assert row["email"] == "Token.Email@Example.com"
    assert row["email_verified"] is False and row["mfa_enabled"] is False  # never trusted from the client
    assert await fetchval("SELECT current_uses FROM invite_codes WHERE code = 'EPIC-ABC123'") == 1


async def test_f06_body_email_must_match_the_token_and_burns_no_invite(client, fb):
    await _invite()
    tok = fb.add("uidNew", EMAIL)
    r = await _sync(client, tok, firebase_id="uidNew", email="attacker-chosen@example.com",
                    invite_code="EPIC-ABC123")
    assert r.status_code == 403
    assert await fetchval("SELECT count(*) FROM users") == 0
    assert await fetchval("SELECT current_uses FROM invite_codes") == 0


async def test_f06_body_uid_must_match_the_token(client, fb, make_user):
    await _invite()
    tok = fb.add("uidMe", EMAIL)
    r = await _sync(client, tok, firebase_id="uidSomeoneElse", email=EMAIL, invite_code="EPIC-ABC123")
    assert r.status_code == 403
    assert await fetchval("SELECT count(*) FROM users") == 0


async def test_f06_existing_user_sync_cannot_change_email_or_security_flags(client, fb, make_user):
    tok = await make_user("uidA", EMAIL)
    r = await _sync(client, tok, firebase_id="uidA", display_name="Renamed", email="new@example.com",
                    email_verified=True, mfa_enabled=True, invite_code="EPIC-WHATEVER",
                    primary_language="Hindi")
    assert r.status_code == 200
    row = await fetchrow("SELECT display_name, primary_language, email, email_verified, mfa_enabled "
                         "FROM users WHERE uid = 'uidA'")
    assert row["display_name"] == "Renamed" and row["primary_language"] == "Hindi"  # profile fields sync
    assert row["email"] == EMAIL and row["email_verified"] is False and row["mfa_enabled"] is False


async def test_f06_validate_invite_route(client):
    await _invite("EPIC-GOOD")
    assert (await client.get(f"{API}/validate-invite/epic-good")).json()["valid"] is True
    assert (await client.get(f"{API}/validate-invite/EPIC-BAD")).json()["valid"] is False


# =============================================================== F-07
async def _send(client, identifier):
    return await client.post(f"{API}/auth/send-email-otp", data={"identifier": identifier})


async def test_f07_three_sends_per_email_per_window(client, mailbox):
    codes = [(await _send(client, EMAIL)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = await _send(client, EMAIL)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert (await _send(client, "different@example.com")).status_code == 200
    assert len(mailbox.to(EMAIL)) == 3


async def test_f07_case_and_whitespace_variants_share_one_limit(client, mailbox):
    variants = ["user@example.com", "  USER@example.com", "User@Example.COM ", " user@example.com "]
    codes = [(await _send(client, v)).status_code for v in variants]
    assert codes == [200, 200, 200, 429]
    assert await fetchval("SELECT count(*) FROM user_otps") == 1
    assert await fetchval("SELECT identifier FROM user_otps") == EMAIL


async def test_f07_per_ip_limit(client, mailbox):
    codes = [(await _send(client, f"person{i}@example.com")).status_code for i in range(22)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]


async def test_f07_send_otp_endpoint_requires_authorisation(client, mailbox, fb, make_user):
    r = await client.post(f"{API}/auth/send-otp", data={"identifier": EMAIL})
    assert r.status_code == 403 and mailbox.sent == []
    tok = await make_user("uidA", EMAIL)
    r = await client.post(f"{API}/auth/send-otp", headers=bearer(tok), data={"identifier": EMAIL})
    assert r.status_code == 200 and len(mailbox.to(EMAIL)) == 1


# ============================================================ F-09 MFA
async def _mfa_enable(client, mailbox, tok, email=EMAIL):
    r = await client.post(f"{API}/user/mfa/enable-request", headers=bearer(tok))
    assert r.status_code == 200, r.text
    r2 = await client.post(f"{API}/user/mfa/enable-confirm", headers=bearer(tok), data={
        "challenge_id": r.json()["challenge_id"], "otp": mailbox.last_otp(email)})
    assert r2.status_code == 200, r2.text
    return r2.json()["mfa_session_token"]


async def test_mfa_enable_requires_a_verified_email(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL, verified=False)
    r = await client.post(f"{API}/user/mfa/enable-request", headers=bearer(tok))
    assert r.status_code == 403 and mailbox.sent == []


async def test_mfa_enable_login_protected_route_and_disable(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _mfa_enable(client, mailbox, tok)
    assert await fetchval("SELECT mfa_enabled FROM users WHERE uid = 'uidA'") is True

    # protected route: needs the MFA session once MFA is on
    body = {"message": "hello"}
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json=body)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "MFA_SESSION_REQUIRED"
    r = await client.post(f"{API}/feedback", headers={**bearer(tok), "X-MFA-Session": session}, json=body)
    assert r.status_code == 200
    r = await client.post(f"{API}/feedback", headers={**bearer(tok), "X-MFA-Session": "bogus"}, json=body)
    assert r.status_code == 401

    # a fresh login challenge issues a new session
    r = await client.post(f"{API}/auth/mfa/challenge", headers=bearer(tok))
    assert r.status_code == 200
    r = await client.post(f"{API}/auth/mfa/verify", headers=bearer(tok), data={
        "challenge_id": r.json()["challenge_id"], "otp": mailbox.last_otp(EMAIL)})
    assert r.status_code == 200 and r.json()["mfa_session_token"] != session

    # session-status reflects server-authoritative state
    st = await client.get(f"{API}/auth/session-status", headers={**bearer(tok), "X-MFA-Session": session})
    assert st.json() == {"email_verified": True, "mfa_enabled": True, "mfa_session_valid": True}

    # disable needs the session; then everything is revoked
    assert (await client.post(f"{API}/user/mfa/disable", headers=bearer(tok))).status_code == 401
    r = await client.post(f"{API}/user/mfa/disable", headers={**bearer(tok), "X-MFA-Session": session})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is False
    assert await fetchval("SELECT mfa_enabled FROM users WHERE uid = 'uidA'") is False
    assert await fetchval("SELECT count(*) FROM mfa_sessions WHERE revoked_at IS NULL") == 0


async def test_mfa_wrong_otp_exhaustion(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL, verified=True, mfa=True)
    r = await client.post(f"{API}/auth/mfa/challenge", headers=bearer(tok))
    cid, good = r.json()["challenge_id"], mailbox.last_otp(EMAIL)
    wrong = "000000" if good != "000000" else "111111"
    codes = [(await client.post(f"{API}/auth/mfa/verify", headers=bearer(tok),
                                data={"challenge_id": cid, "otp": wrong})).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 400, 429]
    assert (await client.post(f"{API}/auth/mfa/verify", headers=bearer(tok),
                              data={"challenge_id": cid, "otp": good})).status_code == 400


async def test_mfa_disable_needs_a_recent_sign_in(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL, verified=True, mfa=True, auth_age_seconds=20 * 60)
    r = await client.post(f"{API}/auth/mfa/challenge", headers=bearer(tok))
    r = await client.post(f"{API}/auth/mfa/verify", headers=bearer(tok), data={
        "challenge_id": r.json()["challenge_id"], "otp": mailbox.last_otp(EMAIL)})
    session = r.json()["mfa_session_token"]
    r = await client.post(f"{API}/user/mfa/disable", headers={**bearer(tok), "X-MFA-Session": session})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "RECENT_SIGN_IN_REQUIRED"
    assert await fetchval("SELECT mfa_enabled FROM users WHERE uid = 'uidA'") is True


async def test_mfa_session_is_bound_to_its_user(client, mailbox, make_user):
    tok_a = await make_user("uidA", EMAIL, verified=True)
    tok_b = await make_user("uidB", "b@example.com", verified=True, mfa=True)
    session_a = await _mfa_enable(client, mailbox, tok_a)
    r = await client.post(f"{API}/feedback", headers={**bearer(tok_b), "X-MFA-Session": session_a},
                          json={"message": "x"})
    assert r.status_code == 401


# ============================================ Phase 3 HTTP enforcement
async def test_phase3_http_unverified_users_are_blocked_with_the_documented_code(client, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=False)
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": "hi"})
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "EMAIL_VERIFICATION_REQUIRED", "message": "Email verification required"}
    # a token with no profile row is also "not verified"
    r = await client.post(f"{API}/feedback", headers=bearer(fb.add("uidGhost", EMAIL)), json={"message": "hi"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"
    # /process-audio is gated by the same dependency, before any processing
    r = await client.post(f"{API}/process-audio", headers=bearer(tok),
                          files={"audio_file": ("a.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"


async def test_phase3_http_verified_users_pass_and_gated_routes_stay_gated_for_mfa(client, make_user):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": "hi"})
    assert r.status_code == 200
    assert await fetchval("SELECT count(*) FROM user_feedback") == 1


async def test_phase3_http_routes_intentionally_open_to_unverified_users(client, mailbox, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=False)
    # An unverified user must still be able to log in, read their own profile
    # and run the verification flow itself: none of these may demand verification.
    status = await client.get(f"{API}/auth/session-status", headers=bearer(tok))
    assert status.status_code == 200 and status.json()["email_verified"] is False
    assert (await client.get(f"{API}/user/uidA", headers=bearer(tok))).status_code == 200
    for method, path in (("get", "/auth/check-session?session_id=x"),
                         ("post", "/auth/email/verify-request"),
                         ("post", "/auth/mark-verified")):
        r = await getattr(client, method)(f"{API}{path}", headers=bearer(tok))
        assert "EMAIL_VERIFICATION_REQUIRED" not in r.text, path


# ====================================== Phase 3 WebSocket enforcement
class FakeWebSocket:
    def __init__(self, token, mfa_session=None):
        self.headers = {"authorization": f"Bearer {token}"}
        if mfa_session:
            self.headers["x-mfa-session"] = mfa_session
        self.sent, self.close_code, self.accepted = [], None, False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.close_code = code


@pytest.fixture
def realtime_stub(monkeypatch):
    from app.services import realtime_service
    started = []

    class StubSession:
        def __init__(self, client_ws, uid, mode, session_id):
            started.append(uid)

        async def run(self):
            return None

    monkeypatch.setattr(realtime_service, "RealtimeSession", StubSession)
    return started


async def _connect(token, uid, mfa_session=None):
    from app.api import routes
    ws = FakeWebSocket(token, mfa_session)
    await routes.websocket_realtime(ws, uid=uid, mode="Mode 1", session_id="s1", token="")
    return ws


async def test_phase3_ws_unverified_user_is_refused_with_the_code(make_user, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=False)
    ws = await _connect(tok, "uidA")
    assert ws.sent == [{"type": "error", "code": "EMAIL_VERIFICATION_REQUIRED", "message": "Unauthorized"}]
    assert ws.close_code == 1008 and realtime_stub == []


async def test_phase3_ws_user_without_a_profile_is_refused(fb, realtime_stub):
    ws = await _connect(fb.add("uidGhost", EMAIL), "uidGhost")
    assert ws.sent[0]["code"] == "EMAIL_VERIFICATION_REQUIRED" and ws.close_code == 1008


async def test_phase3_ws_verified_user_connects(make_user, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=True)
    ws = await _connect(tok, "uidA")
    assert ws.sent == [] and ws.close_code is None and realtime_stub == ["uidA"]


async def test_phase3_ws_mfa_user_needs_a_valid_session_header(client, mailbox, make_user, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _mfa_enable(client, mailbox, tok)
    refused = await _connect(tok, "uidA")
    assert refused.sent[0]["code"] == "MFA_SESSION_REQUIRED" and refused.close_code == 1008
    bad = await _connect(tok, "uidA", mfa_session="bogus")
    assert bad.sent[0]["code"] == "MFA_SESSION_REQUIRED"
    ok = await _connect(tok, "uidA", mfa_session=session)
    assert ok.sent == [] and ok.close_code is None
    assert realtime_stub == ["uidA"]


async def test_phase3_ws_rejects_uid_mismatch_and_bad_tokens(make_user, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=True)
    mismatch = await _connect(tok, "uidSomeoneElse")
    assert mismatch.close_code == 1008 and realtime_stub == []
    forged = await _connect("tok:forged", "uidA")
    assert forged.close_code == 1008 and realtime_stub == []
