"""EV-11: a Firebase ID token must stop working once its refresh tokens are revoked (logout), on every verification path."""
import json
import threading

import pytest
from fastapi import HTTPException

from app.api import routes
from f09_helpers import API, bearer, fetchval

EMAIL = "ev11@example.com"
STATUS = f"{API}/auth/session-status"
LOGOUT = f"{API}/auth/mfa/logout"


async def _mfa_enable(client, mailbox, tok):
    r = await client.post(f"{API}/user/mfa/enable-request", headers=bearer(tok))
    assert r.status_code == 200, r.text
    r2 = await client.post(f"{API}/user/mfa/enable-confirm", headers=bearer(tok), data={
        "challenge_id": r.json()["challenge_id"], "otp": mailbox.last_otp(EMAIL)})
    assert r2.status_code == 200, r2.text
    return r2.json()["mfa_session_token"]


# ------------------------------------------------------------- main API dependency
async def test_valid_token_works_and_revocation_is_checked(client, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 200
    assert fb.verify_check_revoked and all(fb.verify_check_revoked), "get_current_user must pass check_revoked=True"


async def test_invalid_and_expired_tokens_stay_rejected(client, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    assert (await client.get(STATUS, headers=bearer("tok:nobody"))).status_code == 401
    fb.expired_tokens.add(tok)
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 401


async def test_revoked_token_is_rejected_by_the_main_api(client, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 200
    fb.revoked_uids.add("uidA")
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 401


# ------------------------------------------------------------------------- logout
async def test_normal_logout_revokes_firebase_tokens_for_a_non_mfa_user(client, make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await client.post(f"{LOGOUT}?revoke_tokens=true", headers=bearer(tok))
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert fb.revoke_calls == ["uidA"]
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 401, "the pre-logout token must now be rejected"


async def test_normal_logout_revokes_firebase_tokens_and_the_mfa_session_for_an_mfa_user(client, make_user, mailbox, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _mfa_enable(client, mailbox, tok)
    assert await fetchval("SELECT COUNT(*) FROM mfa_sessions WHERE uid = 'uidA' AND revoked_at IS NULL") == 1
    r = await client.post(f"{LOGOUT}?revoke_tokens=true", headers={**bearer(tok), "X-MFA-Session": session})
    assert r.status_code == 200
    assert fb.revoke_calls == ["uidA"]
    assert await fetchval("SELECT COUNT(*) FROM mfa_sessions WHERE uid = 'uidA' AND revoked_at IS NULL") == 0
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 401


async def test_logout_without_the_flag_never_revokes_firebase_tokens(client, make_user, mailbox, fb):
    """Kicked-session / cleanup sign-outs and older apps: the MFA session is revoked as before, the account's tokens are not."""
    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _mfa_enable(client, mailbox, tok)
    r = await client.post(LOGOUT, headers={**bearer(tok), "X-MFA-Session": session})
    assert r.status_code == 200
    assert fb.revoke_calls == []
    assert await fetchval("SELECT COUNT(*) FROM mfa_sessions WHERE uid = 'uidA' AND revoked_at IS NULL") == 0
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 200, "another device's tokens must keep working"


async def test_logout_still_succeeds_and_revokes_the_mfa_session_if_firebase_revocation_fails(client, make_user, mailbox, fb, monkeypatch):
    import firebase_admin.auth as fb_auth

    def boom(uid, *a, **k):
        raise RuntimeError("firebase down")

    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _mfa_enable(client, mailbox, tok)
    monkeypatch.setattr(fb_auth, "revoke_refresh_tokens", boom)
    r = await client.post(f"{LOGOUT}?revoke_tokens=true", headers={**bearer(tok), "X-MFA-Session": session})
    assert r.status_code == 200
    assert await fetchval("SELECT COUNT(*) FROM mfa_sessions WHERE uid = 'uidA' AND revoked_at IS NULL") == 0


async def test_logout_requires_a_valid_token(client, fb):
    assert (await client.post(f"{LOGOUT}?revoke_tokens=true", headers=bearer("tok:nobody"))).status_code == 401
    assert fb.revoke_calls == []


# ------------------------------------------------------------------- OTP bearer path
async def test_otp_bearer_path_accepts_a_valid_token_and_rejects_a_revoked_one(make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    claims = await routes._authorize_otp_request(None, f"Bearer {tok}")
    assert claims["uid"] == "uidA"
    assert all(fb.verify_check_revoked)
    fb.revoked_uids.add("uidA")
    with pytest.raises(HTTPException) as e:
        await routes._authorize_otp_request(None, f"Bearer {tok}")
    assert e.value.status_code == 403


async def test_otp_bearer_path_still_rejects_invalid_and_expired_tokens(make_user, fb):
    tok = await make_user("uidA", EMAIL, verified=True)
    fb.expired_tokens.add(tok)
    for bad in ("tok:nobody", tok):
        with pytest.raises(HTTPException) as e:
            await routes._authorize_otp_request(None, f"Bearer {bad}")
        assert e.value.status_code == 403


# ---------------------------------------------------------------------- WebSocket
class _WS:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"}
        self.sent, self.close_code = [], None

    async def accept(self):
        return None

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


async def test_websocket_accepts_a_valid_token_and_rejects_a_revoked_one(make_user, fb, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=True)
    ws = _WS(tok)
    await routes.websocket_realtime(ws, uid="uidA", mode="Mode 1", session_id="s1", token="")
    assert realtime_stub == ["uidA"] and ws.close_code != 1008
    assert all(fb.verify_check_revoked)

    fb.revoked_uids.add("uidA")
    ws2 = _WS(tok)
    await routes.websocket_realtime(ws2, uid="uidA", mode="Mode 1", session_id="s2", token="")
    assert ws2.close_code == 1008 and ws2.sent == [{"type": "error", "message": "Unauthorized"}]
    assert realtime_stub == ["uidA"], "a revoked token must not start a session"


async def test_revocation_lookups_never_run_on_the_event_loop_thread(client, make_user, fb, realtime_stub):
    """check_revoked makes the SDK do a blocking lookup, so the async OTP and WebSocket paths must run it in a worker thread."""
    loop_thread = threading.get_ident()
    tok = await make_user("uidA", EMAIL, verified=True)
    await routes._authorize_otp_request(None, f"Bearer {tok}")
    await routes.websocket_realtime(_WS(tok), uid="uidA", mode="Mode 1", session_id="s1", token="")
    assert (await client.get(STATUS, headers=bearer(tok))).status_code == 200
    assert len(fb.verify_threads) == 3 and loop_thread not in fb.verify_threads


async def test_websocket_still_rejects_invalid_and_expired_tokens(make_user, fb, realtime_stub):
    tok = await make_user("uidA", EMAIL, verified=True)
    fb.expired_tokens.add(tok)
    for bad in ("tok:nobody", tok):
        ws = _WS(bad)
        await routes.websocket_realtime(ws, uid="uidA", mode="Mode 1", session_id="s", token="")
        assert ws.close_code == 1008
    assert realtime_stub == []
