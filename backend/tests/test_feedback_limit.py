"""POST /feedback: server-side message length limit (FEEDBACK_MAX_CHARS).

Oversized input must be refused by request validation (422) before the route
stores it or queues the notification email, without changing authentication,
email-verification, MFA, empty-message or R6/F17 escaping behaviour.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.api import routes
from app.core.config import settings
from app.services import email_service
from f09_helpers import API, bearer, fetchval

EMAIL = "fb-user@example.com"
LIMIT = routes.FEEDBACK_MAX_CHARS
_real_notify = email_service.send_feedback_notification   # captured at import, before conftest patches it


@pytest.fixture
def notifications(monkeypatch, mailbox):
    """Counts notification tasks (replaces the conftest no-op)."""
    calls = []

    async def _notify(display_name, user_email, message):
        calls.append(message)
        return True

    monkeypatch.setattr(email_service, "send_feedback_notification", _notify)
    return calls


async def _post(client, tok, message, **headers):
    r = await client.post(f"{API}/feedback", headers={**bearer(tok), **headers}, json={"message": message})
    await asyncio.sleep(0.05)  # let the fire-and-forget notification task run
    return r


async def _rows():
    return await fetchval("SELECT count(*) FROM user_feedback")


def test_the_limit_is_5000_characters():
    assert LIMIT == 5000


# ----------------------------------------------------------- accepted input
async def test_normal_message_is_stored_and_notified(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await _post(client, tok, "Love the app!")
    assert r.status_code == 200
    assert r.json() == {"status": "success", "message": "Thank you for your feedback!"}
    assert await _rows() == 1 and notifications == ["Love the app!"]


async def test_exactly_at_the_limit_is_accepted(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    msg = "a" * LIMIT
    assert (await _post(client, tok, msg)).status_code == 200
    assert await fetchval("SELECT length(message) FROM user_feedback") == LIMIT
    assert notifications == [msg]


async def test_trimming_happens_before_the_length_check(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    msg = "b" * LIMIT
    r = await _post(client, tok, "  \n\t " + msg + " \r\n  ")
    assert r.status_code == 200
    assert await fetchval("SELECT message FROM user_feedback") == msg   # stored trimmed
    assert notifications == [msg]


async def test_5000_emoji_are_accepted_because_the_limit_counts_characters(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    msg = "😀" * LIMIT                      # 20,000 UTF-8 bytes, 5,000 characters
    assert len(msg.encode()) > LIMIT
    assert (await _post(client, tok, msg)).status_code == 200
    assert await fetchval("SELECT length(message) FROM user_feedback") == LIMIT


# --------------------------------------------------------- rejected input
@pytest.mark.parametrize("message", [
    "c" * (LIMIT + 1),
    "😀" * (LIMIT + 1),
    " " + "d" * (LIMIT + 1) + " ",        # trimmed length is still over
    "e" * 2_000_000,                      # very large payload
], ids=["limit+1", "emoji limit+1", "padded limit+1", "2MB"])
async def test_oversized_messages_get_422_and_reach_neither_the_db_nor_email(client, make_user, notifications, message):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await _post(client, tok, message)
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["body", "message"] and err["type"] == "string_too_long"
    assert err["ctx"] == {"max_length": LIMIT} and err["msg"]            # useful metadata is kept
    assert "input" not in err                                             # the message is not reflected
    assert message[:200].strip() not in r.text and len(r.content) < 1000  # nothing from the message, small body
    assert await _rows() == 0
    assert notifications == []


async def test_oversized_message_never_calls_save_feedback(client, make_user, notifications, monkeypatch):
    saved = []

    async def _save(uid, message):
        saved.append(message)
        return True

    monkeypatch.setattr(routes, "save_feedback", _save)
    tok = await make_user("uidA", EMAIL, verified=True)
    assert (await _post(client, tok, "f" * (LIMIT + 1))).status_code == 422
    assert saved == []
    assert (await _post(client, tok, "ok")).status_code == 200 and saved == ["ok"]


# ------------------------------------------------ existing empty-message contract
@pytest.mark.parametrize("message", ["", "   ", "\n\t \r\n"])
async def test_empty_and_whitespace_only_keep_their_existing_422(client, make_user, notifications, message):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await _post(client, tok, message)
    assert r.status_code == 422 and r.json() == {"detail": "Feedback message cannot be empty"}
    assert await _rows() == 0 and notifications == []


# -------------------------------------------------- R6/F17 behaviour is unchanged
class _Capture:
    def __init__(self):
        self.payload = None

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.payload = json
        return SimpleNamespace(status_code=202, text="")


async def test_html_like_and_crlf_content_within_the_limit_stays_escaped_body_text(client, make_user, monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "httpx", SimpleNamespace(AsyncClient=cap))
    monkeypatch.setattr(email_service, "send_feedback_notification", _real_notify)
    tok = await make_user("uidA", EMAIL, verified=True)
    msg = '<b>X</b> <img src=x onerror=alert(1)> & "q"\r\nBcc: a@example.invalid\r\nX-Injected: 1 ' + "g" * 4000
    assert len(msg) <= LIMIT
    assert (await _post(client, tok, msg)).status_code == 200
    html_ = cap.payload["content"][0]["value"]
    assert "<b>" not in html_ and "<img" not in html_ and "&lt;b&gt;X&lt;/b&gt;" in html_
    assert "Bcc: a@example.invalid" in html_ and "X-Injected: 1" in html_    # stays body text
    subject = cap.payload["personalizations"][0]["subject"]
    assert "\r" not in subject and "\n" not in subject and "Bcc" not in subject


# ------------------------------------------------- auth / verification / MFA unchanged
async def test_unauthenticated_requests_are_refused_before_validation(client, notifications):
    for message in ("hello", "h" * (LIMIT + 1)):
        r = await client.post(f"{API}/feedback", json={"message": message})
        assert r.status_code == 403 and r.json() == {"detail": "Not authenticated"}
    r = await client.post(f"{API}/feedback", headers=bearer("not-a-real-token"), json={"message": "x" * (LIMIT + 1)})
    assert r.status_code == 401
    assert await _rows() == 0 and notifications == []


async def test_unverified_users_are_still_blocked_with_the_documented_code(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=False)
    for message in ("hello", "i" * (LIMIT + 1)):
        r = await _post(client, tok, message)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"
    assert await _rows() == 0 and notifications == []


async def _enable_mfa(client, mailbox, tok, email):
    r = await client.post(f"{API}/user/mfa/enable-request", headers=bearer(tok))
    assert r.status_code == 200, r.text
    r2 = await client.post(f"{API}/user/mfa/enable-confirm", headers=bearer(tok), data={
        "challenge_id": r.json()["challenge_id"], "otp": mailbox.last_otp(email)})
    assert r2.status_code == 200, r2.text
    return r2.json()["mfa_session_token"]


async def test_mfa_enforcement_is_unchanged(client, mailbox, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    session = await _enable_mfa(client, mailbox, tok, EMAIL)
    for message in ("hello", "j" * (LIMIT + 1)):
        # no session
        r = await _post(client, tok, message)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "MFA_SESSION_REQUIRED"
        # invalid session
        r = await _post(client, tok, message, **{"X-MFA-Session": "bogus"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "MFA_SESSION_REQUIRED"
    assert await _rows() == 0 and notifications == []
    # valid session: normal message works, oversized is the only thing refused
    assert (await _post(client, tok, "hello", **{"X-MFA-Session": session})).status_code == 200
    assert (await _post(client, tok, "k" * (LIMIT + 1), **{"X-MFA-Session": session})).status_code == 422
    assert await _rows() == 1 and notifications == ["hello"]


# ------------------------------------------------------------ no echo (feedback only)
async def test_rejected_feedback_is_never_reflected_in_any_error_shape(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    secret = "REFLECTME-" + "z" * 6000
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": secret, "extra": "REFLECTME-extra"})
    assert r.status_code == 422 and "REFLECTME" not in r.text
    # wrong type and missing field: the submitted body must not come back either
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": ["REFLECTME-list"]})
    assert r.status_code == 422 and "REFLECTME" not in r.text and all("input" not in e for e in r.json()["detail"])
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"other": "REFLECTME-other"})
    assert r.status_code == 422 and "REFLECTME" not in r.text and r.json()["detail"][0]["type"] == "missing"
    # malformed JSON
    r = await client.post(f"{API}/feedback", headers={**bearer(tok), "Content-Type": "application/json"},
                          content=b'{"message": "REFLECTME-broken')
    assert r.status_code == 422 and "REFLECTME" not in r.text
    assert await _rows() == 0 and notifications == []


async def test_a_two_megabyte_rejection_returns_a_small_body(client, make_user, notifications):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await _post(client, tok, "y" * 2_000_000)
    assert r.status_code == 422 and len(r.content) < 1000
    assert await _rows() == 0 and notifications == []


async def test_other_endpoints_keep_the_default_validation_response(client, make_user):
    """Proves the sanitising is feedback-only: an unrelated route still returns FastAPI's
    standard 422, which includes `input`."""
    tok = await make_user("uidB", "other@example.com", verified=True)
    r = await client.post(f"{API}/sync-user", headers=bearer(tok), json={"firebase_id": "uidB", "display_name": ["KEEP-DEFAULT"]})
    assert r.status_code == 422
    errs = r.json()["detail"]
    assert errs and all("input" in e for e in errs) and "KEEP-DEFAULT" in r.text
    r = await client.post(f"{API}/auth/verify-otp", data={"identifier": "someone@example.com"})   # missing form field
    assert r.status_code == 422 and all("input" in e for e in r.json()["detail"])


async def test_the_handler_logs_nothing_about_the_rejected_message(client, make_user, caplog, capsys):
    tok = await make_user("uidA", EMAIL, verified=True)
    caplog.set_level(0)
    capsys.readouterr()
    r = await _post(client, tok, "LOGLEAK-" + "x" * 6000)
    assert r.status_code == 422
    out = capsys.readouterr()
    assert "LOGLEAK" not in out.out + out.err + caplog.text
