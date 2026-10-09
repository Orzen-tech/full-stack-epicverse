"""Abuse limits for non-OTP endpoints (check_rate_limit): POST /feedback per user, and the
generic limiter's per-IP branch. Runs against the throwaway PostgreSQL from conftest.py."""
import asyncio
from types import SimpleNamespace

import pytest

from app.api import routes
from app.core.config import settings
from app.services import otp_rate_limiter
from f09_helpers import API, bearer, fetchval

EMAIL_A, EMAIL_B = "fb-a@example.com", "fb-b@example.com"


def _request(ip="203.0.113.7"):
    return SimpleNamespace(headers={"x-forwarded-for": f"198.51.100.9, {ip}"}, client=SimpleNamespace(host="10.0.0.1"))


async def _post(client, tok, message="hello"):
    r = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": message})
    await asyncio.sleep(0.02)
    return r


def test_the_default_feedback_limit_is_five_per_user():
    assert routes.FEEDBACK_PER_USER_LIMIT == 5


async def test_feedback_is_limited_per_user_with_retry_after(client, make_user):
    a = await make_user("uidA", EMAIL_A, verified=True)
    for _ in range(routes.FEEDBACK_PER_USER_LIMIT):
        assert (await _post(client, a)).status_code == 200
    blocked = await _post(client, a)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too many requests. Please wait a few minutes."
    assert 0 <= int(blocked.headers["Retry-After"]) <= 600
    assert await fetchval("SELECT count(*) FROM user_feedback") == routes.FEEDBACK_PER_USER_LIMIT   # nothing stored


async def test_feedback_limit_is_per_user_not_global(client, make_user):
    a = await make_user("uidA", EMAIL_A, verified=True)
    b = await make_user("uidB", EMAIL_B, verified=True)
    for _ in range(routes.FEEDBACK_PER_USER_LIMIT + 1):
        await _post(client, a)
    assert (await _post(client, a)).status_code == 429
    assert (await _post(client, b)).status_code == 200


async def test_rejected_or_empty_feedback_does_not_consume_the_allowance(client, make_user):
    a = await make_user("uidA", EMAIL_A, verified=True)
    for _ in range(8):                                   # empty -> 422, oversized -> 422: never counted
        assert (await _post(client, a, "   ")).status_code == 422
        assert (await _post(client, a, "x" * (routes.FEEDBACK_MAX_CHARS + 1))).status_code == 422
    for _ in range(routes.FEEDBACK_PER_USER_LIMIT):
        assert (await _post(client, a)).status_code == 200


async def test_unverified_user_is_refused_before_the_limiter(client, make_user):
    a = await make_user("uidA", EMAIL_A, verified=False)
    for _ in range(routes.FEEDBACK_PER_USER_LIMIT + 3):
        r = await _post(client, a)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"


async def test_feedback_limiter_fails_closed_without_the_secret(client, make_user, monkeypatch):
    a = await make_user("uidA", EMAIL_A, verified=True)
    monkeypatch.setattr(settings, "OTP_RATE_LIMIT_HASH_SECRET", "")
    assert (await _post(client, a)).status_code == 429
    assert await fetchval("SELECT count(*) FROM user_feedback") == 0


async def test_generic_limiter_counts_per_ip_and_scope_and_resets_per_key():
    req, other = _request("203.0.113.7"), _request("203.0.113.8")
    check = otp_rate_limiter.check_rate_limit
    for _ in range(3):
        assert (await check("probe", None, req, key_limit=None, ip_limit=3))[0] is True
    allowed, retry = await check("probe", None, req, key_limit=None, ip_limit=3)
    assert allowed is False and 0 <= retry <= 600
    assert (await check("probe", None, other, key_limit=None, ip_limit=3))[0] is True        # other IP
    assert (await check("different", None, req, key_limit=None, ip_limit=3))[0] is True      # other scope
    # OTP counters are unaffected by this scope
    assert (await otp_rate_limiter.check_otp_send_allowed("someone@example.com", req))[0] is True


async def test_generic_limiter_scopes_do_not_collide_with_otp_scopes():
    rows = await fetchval("SELECT count(*) FROM otp_send_rate_limits WHERE scope IN ('identifier','ip')")
    assert rows == 0
    await otp_rate_limiter.check_rate_limit("feedback", "u1", _request(), key_limit=5, ip_limit=None)
    scopes = await fetchval("SELECT string_agg(DISTINCT scope, ',') FROM otp_send_rate_limits")
    assert scopes == "feedback_key"


# ---- /validate-invite: at most N FAILED validations per IP per window (Option B) ----
from f09_helpers import execute  # noqa: E402


def _xff(ip):
    return {"x-forwarded-for": f"198.51.100.9, {ip}"}


async def _validate(client, code, ip="203.0.113.50"):
    return await client.get(f"{API}/validate-invite/{code}", headers=_xff(ip))


async def _mk_invite(code="EPIC-GOOD01", max_uses=1000):
    await execute("INSERT INTO invite_codes (code, max_uses) VALUES ($1, $2)", code, max_uses)


def test_the_invite_failure_limit_is_twenty_per_ip():
    assert routes.INVITE_FAILED_VALIDATIONS_PER_IP == 20


async def test_twenty_failures_are_answered_normally_then_429_with_retry_after(client):
    for i in range(20):
        r = await _validate(client, f"EPIC-BAD{i:03d}")
        assert r.status_code == 200 and r.json()["valid"] is False
    r = await _validate(client, "EPIC-BAD999")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


async def test_successful_validations_are_never_counted(client):
    await _mk_invite()
    for _ in range(60):
        r = await _validate(client, "epic-good01")
        assert r.status_code == 200 and r.json()["valid"] is True
    for i in range(20):                       # the full failure budget is still available
        assert (await _validate(client, f"EPIC-BAD{i:03d}")).status_code == 200


async def test_a_valid_code_cannot_bypass_an_existing_lockout(client):
    await _mk_invite()
    for i in range(20):
        await _validate(client, f"EPIC-BAD{i:03d}")
    assert (await _validate(client, "EPIC-GOOD01")).status_code == 429
    assert (await _validate(client, "EPIC-GOOD01")).status_code == 429
    # another IP is unaffected
    assert (await _validate(client, "EPIC-GOOD01", ip="203.0.113.77")).json()["valid"] is True


async def test_a_success_between_failures_does_not_refund_failures(client):
    await _mk_invite()
    for i in range(10):
        await _validate(client, f"EPIC-BAD{i:03d}")
    assert (await _validate(client, "EPIC-GOOD01")).json()["valid"] is True
    for i in range(10, 20):
        assert (await _validate(client, f"EPIC-BAD{i:03d}")).status_code == 200
    assert (await _validate(client, "EPIC-BAD999")).status_code == 429


async def test_concurrent_failures_never_exceed_the_budget(client):
    rs = await asyncio.gather(*[_validate(client, f"EPIC-BAD{i:03d}", ip="203.0.113.60") for i in range(60)])
    assert sorted(r.status_code for r in rs).count(200) == 20
    assert sum(r.status_code == 429 for r in rs) == 40


async def test_concurrent_valid_requests_do_not_lock_out_a_clean_ip(client):
    await _mk_invite()
    rs = await asyncio.gather(*[_validate(client, "EPIC-GOOD01", ip="203.0.113.61") for _ in range(15)])
    assert all(r.status_code == 200 and r.json()["valid"] for r in rs)
    assert (await _validate(client, "EPIC-BAD000", ip="203.0.113.61")).status_code == 200


async def test_invite_limit_is_per_ip_using_the_last_forwarded_entry(client):
    for i in range(20):
        await client.get(f"{API}/validate-invite/EPIC-BAD{i:03d}",
                         headers={"x-forwarded-for": f"1.1.1.{i}, 203.0.113.62"})
    r = await client.get(f"{API}/validate-invite/EPIC-BAD999", headers={"x-forwarded-for": "9.9.9.9, 203.0.113.62"})
    assert r.status_code == 429


async def test_invite_limit_fails_closed_without_the_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "OTP_RATE_LIMIT_HASH_SECRET", "")
    assert (await _validate(client, "EPIC-BAD000", ip="203.0.113.63")).status_code == 429


async def test_invite_limit_fails_closed_when_the_database_is_unavailable(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(otp_rate_limiter, "get_pool", boom)
    await _mk_invite()
    r = await _validate(client, "EPIC-GOOD01", ip="203.0.113.64")
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"


async def test_a_failed_release_leaves_the_slot_counted(client, monkeypatch):
    await _mk_invite()
    real = otp_rate_limiter.get_pool
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 2:                   # the release call
            raise RuntimeError("db blip")
        return await real()
    monkeypatch.setattr(otp_rate_limiter, "get_pool", flaky)
    assert (await _validate(client, "EPIC-GOOD01", ip="203.0.113.65")).json()["valid"] is True
    monkeypatch.setattr(otp_rate_limiter, "get_pool", real)
    n = await fetchval("SELECT request_count FROM otp_send_rate_limits WHERE scope = 'invite_fail_ip'")
    assert n == 1
