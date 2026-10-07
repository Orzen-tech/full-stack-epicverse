"""R1-R6 remediation: password-reset limiter, OTP at rest, WebSocket log
hygiene, Redis/health disclosure, and feedback-email injection.

Runs against the throwaway local PostgreSQL from conftest.py. (R3, the Sentry
privacy finding, is Flutter-only: frontend/EpicVerseApp/test/sentry_privacy_test.dart.)
"""
import asyncio
import json
import re
from types import SimpleNamespace

import firebase_admin.auth as fb_auth
import pytest

from app.api import routes
from app.core.config import settings
from app.services import email_service, memory_store, otp_rate_limiter, user_db
from f09_helpers import API, bearer, execute, fetch, fetchrow, fetchval

EMAIL = "victim@example.com"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ===================================================================== R1
async def _reset(client, email, **headers):
    return await client.post(f"{API}/auth/send-password-reset", data={"identifier": email}, headers=headers)


@pytest.fixture
def reset_ok(monkeypatch):
    sent = []

    async def _send(email, link):
        sent.append((email, link))
        return True

    monkeypatch.setattr(fb_auth, "generate_password_reset_link", lambda e: f"https://reset.example/{e}")
    monkeypatch.setattr(email_service, "send_password_reset_email", _send)
    return sent


async def test_r1_normal_reset_still_sends(client, reset_ok):
    r = await _reset(client, EMAIL)
    assert r.status_code == 200 and r.json() == {"status": "sent"}
    assert len(reset_ok) == 1


async def test_r1_limit_is_per_identifier_case_insensitive_and_has_retry_after(client, reset_ok):
    for e in (EMAIL, EMAIL.upper(), f" {EMAIL} "):
        assert (await _reset(client, e)).status_code == 200
    r = await _reset(client, "VICTIM@example.com")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert len(reset_ok) == 3
    # a different address from the same IP is unaffected
    assert (await _reset(client, "other@example.com")).status_code == 200


async def test_r1_limit_lives_in_postgres_so_it_survives_restart_and_other_instances(client, reset_ok, monkeypatch):
    for _ in range(3):
        assert (await _reset(client, EMAIL)).status_code == 200
    # "restart" / another instance: no in-process state exists to lose
    monkeypatch.setattr(otp_rate_limiter, "_last_cleanup_attempt", None)
    assert not hasattr(routes, "_otp_rate") and not hasattr(routes, "_otp_allowed")
    assert (await _reset(client, EMAIL)).status_code == 429
    assert await fetchval("SELECT count(*) FROM otp_send_rate_limits") >= 1


async def test_r1_limit_is_per_ip(client, reset_ok):
    xff = {"X-Forwarded-For": "198.51.100.7"}
    codes = [(await _reset(client, f"user{i}@example.com", **xff)).status_code for i in range(22)]
    assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]
    # another IP is still served
    assert (await _reset(client, "fresh@example.com", **{"X-Forwarded-For": "198.51.100.8"})).status_code == 200


async def test_r1_namespace_is_separate_from_otp_sending(client, reset_ok, mailbox):
    for _ in range(3):
        assert (await _reset(client, EMAIL)).status_code == 200
    assert (await _reset(client, EMAIL)).status_code == 429
    r = await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    assert r.status_code == 200 and mailbox.to(EMAIL)


async def test_r1_unknown_address_looks_identical_and_is_limited_identically(client, monkeypatch):
    def no_user(email):
        raise fb_auth.UserNotFoundError("none")

    monkeypatch.setattr(fb_auth, "generate_password_reset_link", no_user)
    bodies = [await _reset(client, "ghost@example.com") for _ in range(4)]
    assert [b.status_code for b in bodies] == [200, 200, 200, 429]
    assert bodies[0].json() == {"status": "sent"}


async def test_r1_storage_failure_fails_closed(client, reset_ok, monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(otp_rate_limiter, "get_pool", boom)
    r = await _reset(client, EMAIL)
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"
    assert reset_ok == []


# ===================================================================== R2
async def _stored(identifier):
    return await fetchval("SELECT otp FROM user_otps WHERE identifier = $1", identifier)


async def test_r2_raw_otp_is_never_stored(client, mailbox):
    r = await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    assert r.status_code == 200
    otp = mailbox.last_otp(EMAIL)
    stored = await _stored(EMAIL)
    assert HEX64.match(stored) and otp not in stored
    assert not any(otp in str(v) for row in await fetch("SELECT * FROM user_otps") for v in row.values())


async def test_r2_hash_is_bound_to_identifier_and_code(monkeypatch):
    a = user_db._otp_at_rest_hash("a@example.com", "123456")
    assert a == user_db._otp_at_rest_hash("a@example.com", "123456")
    assert a != user_db._otp_at_rest_hash("b@example.com", "123456")
    assert a != user_db._otp_at_rest_hash("a@example.com", "123457")
    # length-prefixed: shifting a character between identifier and code changes the digest
    assert user_db._otp_at_rest_hash("ab", "1") != user_db._otp_at_rest_hash("a", "b1")
    monkeypatch.setattr(settings, "MFA_OTP_HASH_SECRET", "a-different-secret-value")
    assert user_db._otp_at_rest_hash("a@example.com", "123456") != a


async def test_r2_correct_and_incorrect_codes(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    otp = mailbox.last_otp(EMAIL)
    wrong = "000000" if otp != "000000" else "111111"
    assert await user_db.verify_otp(EMAIL, wrong) == "invalid"
    assert await fetchval("SELECT attempts FROM user_otps WHERE identifier = $1", EMAIL) == 1
    assert await user_db.verify_otp(EMAIL, otp) == "success"
    assert await _stored(EMAIL) is None                      # single use
    assert await user_db.verify_otp(EMAIL, otp) == "invalid"


async def test_r2_identifier_case_does_not_matter(client, mailbox):
    assert await user_db.save_otp("Mixed@Example.com", "424242")
    assert await user_db.verify_otp("MIXED@example.COM", "424242") == "success"


async def test_r2_five_wrong_attempts_lock_out_even_the_right_code(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    otp = mailbox.last_otp(EMAIL)
    wrong = "000000" if otp != "000000" else "111111"
    results = [await user_db.verify_otp(EMAIL, wrong) for _ in range(5)]
    assert results == ["invalid"] * 4 + ["too_many_attempts"]
    assert await user_db.verify_otp(EMAIL, otp) == "invalid"


async def test_r2_expired_code_is_refused(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    otp = mailbox.last_otp(EMAIL)
    await execute("UPDATE user_otps SET created_at = NOW() - INTERVAL '2 minutes'")
    assert await user_db.verify_otp(EMAIL, otp) == "invalid"


async def test_r2_concurrent_guesses_cannot_exceed_the_attempt_budget(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    otp = mailbox.last_otp(EMAIL)
    wrong = "000000" if otp != "000000" else "111111"
    results = await asyncio.gather(*[user_db.verify_otp(EMAIL, wrong) for _ in range(12)])
    assert "success" not in results
    # serialized by the row lock: exactly one request trips the lockout; the
    # code is then deleted, so every later guess (and the right code) is refused
    assert results.count("too_many_attempts") == 1
    assert await user_db.verify_otp(EMAIL, otp) == "invalid"


@pytest.mark.parametrize("secret", ["", "short"])
async def test_r2_missing_or_invalid_secret_fails_closed(client, mailbox, monkeypatch, secret):
    monkeypatch.setattr(settings, "MFA_OTP_HASH_SECRET", secret)
    assert await user_db.save_otp(EMAIL, "123456") is False
    assert await fetchval("SELECT count(*) FROM user_otps") == 0
    r = await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    assert r.status_code == 500 and mailbox.sent == []
    r = await client.post(f"{API}/auth/send-otp", headers=bearer("x"), data={"identifier": EMAIL})
    assert mailbox.sent == []
    # a pre-existing legacy row is also refused without the secret
    await execute("INSERT INTO user_otps (identifier, otp, attempts) VALUES ($1, '123456', 0)", EMAIL)
    assert await user_db.verify_otp(EMAIL, "123456") == "invalid"


async def test_r2_no_email_when_persistence_fails(client, mailbox, monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(user_db, "get_pool", boom)
    r = await client.post(f"{API}/auth/send-email-otp", data={"identifier": EMAIL})
    assert r.status_code == 500 and "OTP" in r.json()["detail"]
    assert mailbox.sent == []


async def test_r2_stale_rows_are_swept_on_save(client):
    await execute("INSERT INTO user_otps (identifier, otp, created_at, attempts) "
                  "VALUES ('old@example.com', 'x', NOW() - INTERVAL '2 hours', 0), "
                  "('recent@example.com', 'y', NOW() - INTERVAL '10 minutes', 0)")
    assert await user_db.save_otp(EMAIL, "123456")
    ids = {r["identifier"] for r in await fetch("SELECT identifier FROM user_otps")}
    assert ids == {"recent@example.com", EMAIL}


async def test_r2_legacy_plaintext_row_works_once_and_only_as_six_digits(client):
    await execute("INSERT INTO user_otps (identifier, otp, attempts) VALUES ($1, '654321', 0)", EMAIL)
    assert await user_db.verify_otp(EMAIL, "654320") == "invalid"
    assert await user_db.verify_otp(EMAIL, "654321") == "success"
    assert await _stored(EMAIL) is None
    # anything else that is not a 6-digit or 64-hex value never matches
    for junk in ("abc", "65432", "6543210", "   654321"):
        await execute("DELETE FROM user_otps")
        await execute("INSERT INTO user_otps (identifier, otp, attempts) VALUES ($1, $2, 0)", EMAIL, junk)
        assert await user_db.verify_otp(EMAIL, junk) == "invalid"
    # a stored hash is not accepted as a plaintext code
    await execute("DELETE FROM user_otps")
    assert await user_db.save_otp(EMAIL, "111111")
    assert await user_db.verify_otp(EMAIL, await _stored(EMAIL)) == "invalid"


async def test_r2_new_rows_are_never_plaintext(client, mailbox):
    for i in range(5):
        assert await user_db.save_otp(f"u{i}@example.com", f"{100000 + i}")
    for row in await fetch("SELECT otp FROM user_otps"):
        assert HEX64.match(row["otp"])


# ===================================================================== R4
class _WS:
    def __init__(self, token="tok:uidA", mfa=None):
        self.headers = {"authorization": f"Bearer {token}"}
        if mfa:
            self.headers["x-mfa-session"] = mfa
        self.sent, self.code = [], None

    async def accept(self):
        pass

    async def send_text(self, t):
        self.sent.append(json.loads(t))

    async def close(self, code=1000):
        self.code = code


def test_r4_ws_safe_escapes_bounds_and_truncates():
    forged = routes._ws_safe("x\n[WS] Realtime connection uid=admin\r\n")
    assert "\n" not in forged and "\r" not in forged
    assert len(routes._ws_safe("A" * 5000)) < 20
    assert len(routes._ws_safe("m" * 5000, 24)) < 40
    full = "firebaseUid1234567890ABCDEF"
    assert full not in routes._ws_safe(full) and full[-8:] in routes._ws_safe(full)
    assert routes._ws_safe(None) == "''"


async def test_r4_newlines_and_long_values_cannot_forge_or_flood_the_log(fb, make_user, monkeypatch, capsys):
    from app.services import realtime_service

    class Stub:
        def __init__(self, **kw):
            pass

        async def run(self):
            return None

    monkeypatch.setattr(realtime_service, "RealtimeSession", Stub)
    tok = await make_user("uidOK", "ok@example.com", verified=True)
    evil_mode = "Mode 1\n[WS] FORGED admin connected " + "Z" * 4000
    evil_uid = "uid\n[WS] FORGED"
    capsys.readouterr()
    ws = _WS(tok)
    await routes.websocket_realtime(ws, uid="uidOK", mode=evil_mode, session_id="s\n[X] FORGED" * 50, token="")
    await routes.websocket_realtime(_WS(tok), uid=evil_uid, mode="Mode 1", session_id="s", token="")
    await routes.websocket_realtime(_WS("bad"), uid=evil_uid, mode="Mode 1", session_id="s", token="")
    await routes.websocket_realtime(_WS(""), uid=evil_uid, mode="m", session_id="s", token="")
    out = capsys.readouterr()
    text = out.out + out.err
    assert "FORGED admin" not in text and "ZZZZZZZZZZZZZZZZZZZZZZZZZZZ" not in text
    for line in text.splitlines():
        assert len(line) < 300
        assert not line.startswith("[X]") and "FORGED" not in line.split("[WS]")[0]


async def test_r4_identifiers_are_not_logged_in_full(fb, monkeypatch, capsys):
    uid = "AbCdEfGhIjKlMnOpQrStUvWx"
    tok = fb.add(uid, "z@example.com")
    capsys.readouterr()
    await routes.websocket_realtime(_WS(tok), uid="OtherUidOtherUidOtherUid", mode="Mode 1", session_id="s", token="")
    await routes.websocket_realtime(_WS("junk"), uid=uid, mode="Mode 1", session_id="s", token="")
    await routes.websocket_realtime(_WS(""), uid=uid, mode="Mode 1", session_id="s", token="")
    out = capsys.readouterr()
    text = out.out + out.err
    assert uid not in text and "OtherUidOtherUidOtherUid" not in text
    assert uid[-8:] in text


async def test_r4_successful_auth_and_f09_refusal_unchanged(fb, make_user, monkeypatch, capsys):
    from app.services import realtime_service
    started = []

    class Stub:
        def __init__(self, client_ws, uid, mode, session_id):
            started.append((uid, mode, session_id))

        async def run(self):
            return None

    monkeypatch.setattr(realtime_service, "RealtimeSession", Stub)
    good = await make_user("uidGood", "good@example.com", verified=True)
    ws = _WS(good)
    await routes.websocket_realtime(ws, uid="uidGood", mode="Mode 1", session_id="s1", token="")
    assert started == [("uidGood", "Mode 1", "s1")] and ws.code is None
    unverified = await make_user("uidNew", "new@example.com", verified=False)
    ws = _WS(unverified)
    await routes.websocket_realtime(ws, uid="uidNew", mode="Mode 1", session_id="s1", token="")
    assert ws.code == 1008 and ws.sent[0]["code"] == "EMAIL_VERIFICATION_REQUIRED"
    assert "[WS] Rejected: EMAIL_VERIFICATION_REQUIRED" in capsys.readouterr().out


# ===================================================================== R5
FAKE_REDIS_URL = "rediss://default:SuperSecretRedisPw@redis-host.example.internal:6379"


class _FakeRedis:
    def __init__(self, fail):
        self.fail = fail

    @classmethod
    def factory(cls, fail):
        return SimpleNamespace(from_url=lambda url, **kw: cls(fail))

    async def ping(self):
        if self.fail:
            raise ConnectionError(f"Error connecting to {FAKE_REDIS_URL}: refused")


@pytest.mark.parametrize("fail", [False, True])
async def test_r5_memory_store_never_logs_the_redis_url(monkeypatch, capsys, fail):
    monkeypatch.setattr(memory_store, "settings", SimpleNamespace(
        REDIS_URL=FAKE_REDIS_URL, REDIS_SOCKET_TIMEOUT_SECONDS=1.0))
    monkeypatch.setattr(memory_store, "Redis", _FakeRedis.factory(fail))
    await memory_store.SessionStore().connect()
    out = capsys.readouterr()
    text = out.out + out.err
    assert "SuperSecretRedisPw" not in text and "redis-host" not in text and "rediss://" not in text
    assert ("ConnectionError" in text) if fail else ("Redis connected" in text)


async def test_r5_missing_timeout_setting_path_is_silent_about_the_url(monkeypatch, capsys):
    monkeypatch.setattr(settings, "REDIS_URL", FAKE_REDIS_URL)
    await memory_store.SessionStore().connect()
    out = capsys.readouterr()
    assert "SuperSecretRedisPw" not in out.out + out.err


async def test_r5_health_has_no_exception_text(monkeypatch, capsys):
    from app import main as app_main
    from app.services import db_pool, retriever

    async def bad_pool():
        raise RuntimeError(f"could not connect to postgres://u:DbPassword@10.1.2.3/db")

    async def bad_redis():
        raise RuntimeError(f"Error connecting to {FAKE_REDIS_URL}")

    monkeypatch.setattr(db_pool, "get_pool", bad_pool)
    monkeypatch.setattr(retriever, "init_redis", bad_redis)
    result = await app_main.health()
    assert set(result) == {"status", "database", "redis", "timestamp"}
    assert result["status"] == "degraded" and result["database"] == "error" and result["redis"] == "error"
    blob = json.dumps(result) + "".join(capsys.readouterr())
    for leak in ("DbPassword", "10.1.2.3", "SuperSecretRedisPw", "redis-host", "postgres://"):
        assert leak not in blob
    assert "RuntimeError" in blob                               # type only, server side


async def test_r5_health_ok_shape_unchanged(monkeypatch):
    from app import main as app_main
    from app.services import retriever

    async def no_redis():
        return None

    monkeypatch.setattr(retriever, "init_redis", no_redis)
    result = await app_main.health()
    assert result["status"] == "ok" and result["database"].startswith("connected (")
    assert result["redis"] == "disabled or offline"


# ===================================================================== R6
class _Capture:
    def __init__(self):
        self.payload = None
        self.status = 202

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.payload = json
        return SimpleNamespace(status_code=self.status, text="")


@pytest.fixture
def sendgrid(monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "httpx", SimpleNamespace(AsyncClient=cap))
    return cap


def _html(cap):
    return cap.payload["content"][0]["value"]


def _subject(cap):
    return cap.payload["personalizations"][0]["subject"]


async def test_r6_normal_notification_sends(sendgrid, capsys):
    assert await email_service.send_feedback_notification("Asha K", "asha@example.com", "Great app!") is True
    html_ = _html(sendgrid)
    assert "Asha K" in html_ and "asha@example.com" in html_ and "Great app!" in html_
    assert _subject(sendgrid) == "New EpicVerse Feedback from Asha K"
    assert "[SENDGRID-SUCCESS] Feedback notification sent" in capsys.readouterr().out


async def test_r6_markup_is_neutralised_everywhere(sendgrid):
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)><a href="https://evil.example">click</a>"\''
    await email_service.send_feedback_notification(evil, evil, evil)
    html_ = _html(sendgrid)
    for field in ("<script", "<img", "<a href", 'href="'):
        assert field not in html_
    assert html_.count("&lt;script&gt;") == 3 and "&quot;" in html_ and "&#x27;" in html_


async def test_r6_subject_cannot_be_injected_and_is_bounded(sendgrid):
    name = "Bob\r\nBcc: attacker@example.com\nX-Evil: 1 more end\x00\x07" + "N" * 500
    await email_service.send_feedback_notification(name, "b@example.com", "hi")
    subject = _subject(sendgrid)
    assert not re.search(r"[\r\n  \x00-\x1f\x7f]", subject)
    assert len(subject) <= len("New EpicVerse Feedback from ") + 60


@pytest.mark.parametrize("name", [None, "", "   ", "\r\n\t"])
async def test_r6_empty_name_falls_back(sendgrid, name):
    assert await email_service.send_feedback_notification(name, "b@example.com", "hi") is True
    assert _subject(sendgrid) == "New EpicVerse Feedback from a user"


@pytest.mark.parametrize("addr", [None, ""])
async def test_r6_missing_email_and_message_are_handled(sendgrid, addr):
    assert await email_service.send_feedback_notification("Zed", addr, None) is True
    assert "(not provided)" in _html(sendgrid) and "None" not in _html(sendgrid)


async def test_r6_display_name_never_reaches_logs(sendgrid, capsys):
    await email_service.send_feedback_notification("Distinctive Person Name", "p@example.com", "m")
    out = capsys.readouterr()
    assert "Distinctive" not in out.out + out.err
    sendgrid.status = 500
    await email_service.send_feedback_notification("Distinctive Person Name", "p@example.com", "m")
    out = capsys.readouterr()
    assert "Distinctive" not in out.out + out.err


async def test_r6_without_api_key_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "")
    assert await email_service.send_feedback_notification("a", "b", "c") is False
