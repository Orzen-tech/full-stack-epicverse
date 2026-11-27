"""Offline tests: the invite brute-force guard on /auth/send-otp and the APP_CHECK_MODE switch.

NO DATABASE, NO NETWORK: DATABASE_URL points at an unreachable fake before anything is imported, the connection
pool raises if it is ever asked for a connection, and the limiter, invite lookup and App Check verification are
fakes. NO REAL CODES: invite values contain 0/1, which real codes never do.
The application modules are imported here and removed from sys.modules afterwards, so the other offline tests can
still check that their scripts load no database driver.
Run from backend/:  python -m pytest -p no:cacheprovider tests_offline
"""
import asyncio
import os
import sys

import pytest

FAKE_DB = "postgresql://fake-user:fake-pass@127.0.0.1:1/fake"


@pytest.fixture(scope="module")
def app_modules():
    before = set(sys.modules)
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_CHECK_MODE")}
    os.environ["DATABASE_URL"] = FAKE_DB
    os.environ.pop("APP_CHECK_MODE", None)
    try:
        import app.api.routes as routes
        import app.services.app_check_monitor as acm
        import app.services.db_pool as db_pool
        yield routes, acm, db_pool
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for name in set(sys.modules) - before:
            del sys.modules[name]


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = type("C", (), {"host": "203.0.113.7"})()


@pytest.fixture
def env(app_modules, monkeypatch):
    routes, acm, db_pool = app_modules

    async def no_pool(*a, **k):
        raise AssertionError("a database connection was requested")
    monkeypatch.setattr(db_pool, "get_pool", no_pool)
    monkeypatch.setattr(routes, "get_pool", no_pool, raising=False)

    calls = {"reserve": [], "release": [], "validate": [], "budget": (True, 0, "SLOT"), "valid": set()}

    async def reserve(scope, request, *, limit):
        calls["reserve"].append((scope, limit))
        return calls["budget"]

    async def release(handle):
        calls["release"].append(handle)

    async def validate(code):
        calls["validate"].append(code)
        return code in calls["valid"]
    monkeypatch.setattr(routes, "reserve_failure_slot", reserve)
    monkeypatch.setattr(routes, "release_failure_slot", release)
    monkeypatch.setattr(routes, "validate_invite_code", validate)
    monkeypatch.setattr(acm.settings, "APP_CHECK_MODE", "monitor")
    return routes, acm, calls


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- /auth/send-otp invite path
def test_valid_invite_uses_the_shared_budget_and_is_refunded(env):
    routes, _, calls = env
    calls["valid"].add("EPIC-FAKE01")
    assert run(routes._authorize_otp_request(" fake01 ", None, FakeRequest())) is None
    assert calls["reserve"] == [("invite_fail", routes.INVITE_FAILED_VALIDATIONS_PER_IP)]
    assert routes.INVITE_FAILED_VALIDATIONS_PER_IP == 20  # the same budget as /validate-invite
    assert calls["validate"] == ["EPIC-FAKE01"] and calls["release"] == ["SLOT"]


def test_invalid_invite_is_403_and_stays_counted(env):
    routes, _, calls = env
    with pytest.raises(routes.HTTPException) as e:
        run(routes._authorize_otp_request("EPIC-FAKE09", None, FakeRequest()))
    assert e.value.status_code == 403 and calls["release"] == []


def test_exhausted_budget_is_429_and_a_valid_code_cannot_bypass_it(env):
    routes, _, calls = env
    calls["valid"].add("EPIC-FAKE01")
    calls["budget"] = (False, 120, None)
    with pytest.raises(routes.HTTPException) as e:
        run(routes._authorize_otp_request("EPIC-FAKE01", None, FakeRequest()))
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "120"
    assert calls["validate"] == [] and calls["release"] == []


def test_limiter_failure_fails_closed(env):
    routes, _, calls = env
    calls["budget"] = (False, 60, None)  # what reserve_failure_slot returns without its secret or database
    with pytest.raises(routes.HTTPException) as e:
        run(routes._authorize_otp_request("EPIC-FAKE01", None, FakeRequest()))
    assert e.value.status_code == 429 and calls["validate"] == []


def test_an_invite_is_never_checked_without_the_request(env):
    routes, _, calls = env
    with pytest.raises(routes.HTTPException) as e:
        run(routes._authorize_otp_request("EPIC-FAKE01", None))
    assert e.value.status_code == 403 and calls["reserve"] == [] and calls["validate"] == []


def test_the_token_path_does_not_touch_the_invite_budget(env):
    routes, _, calls = env
    with pytest.raises(routes.HTTPException) as e:
        run(routes._authorize_otp_request(None, None, FakeRequest()))
    assert e.value.status_code == 403 and calls["reserve"] == []


def test_send_otp_route_passes_the_request_to_the_guard(env):
    routes, _, calls = env
    with pytest.raises(routes.HTTPException) as e:
        run(routes.send_otp(FakeRequest(), identifier="person@example.invalid", email=None,
                            invite_code="EPIC-FAKE09", authorization=None))
    assert e.value.status_code == 403 and calls["reserve"] == [("invite_fail", 20)]


# ---------------------------------------------------------------- APP_CHECK_MODE
def _verify(acm, monkeypatch, outcome):
    def fake_verify(token):
        if outcome == "invalid":
            raise ValueError("bad token")
        if outcome == "error":
            raise RuntimeError("jwks unavailable")
        return {"app_id": "fake"}
    monkeypatch.setattr(acm.app_check, "verify_token", fake_verify)


@pytest.mark.parametrize("token, outcome", [(None, None), ("tok-x", "invalid"), ("tok-x", "error"), ("tok-x", "valid")])
def test_monitor_mode_never_rejects(env, monkeypatch, capsys, token, outcome):
    _, acm, _ = env
    _verify(acm, monkeypatch, outcome)
    acm.check_app_check(FakeRequest({"X-Firebase-AppCheck": token} if token else {}), "send-otp")
    assert "tok-x" not in capsys.readouterr().out


@pytest.mark.parametrize("token, outcome, rejected", [
    (None, None, True), ("tok-x", "invalid", True), ("tok-x", "valid", False), ("tok-x", "error", False)])
def test_enforce_mode_rejects_missing_or_invalid_only(env, monkeypatch, capsys, token, outcome, rejected):
    _, acm, _ = env
    monkeypatch.setattr(acm.settings, "APP_CHECK_MODE", " Enforce ")
    _verify(acm, monkeypatch, outcome)
    req = FakeRequest({"X-Firebase-AppCheck": token} if token else {})
    if rejected:
        with pytest.raises(acm.HTTPException) as e:
            acm.check_app_check(req, "send-otp")
        assert e.value.status_code == 403
    else:
        acm.check_app_check(req, "send-otp")
    assert "tok-x" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["", "monitor", "enforcing", "on", "true"])
def test_any_other_mode_value_behaves_as_monitor(env, mode, monkeypatch):
    _, acm, _ = env
    monkeypatch.setattr(acm.settings, "APP_CHECK_MODE", mode)
    assert acm.app_check_mode() == "monitor"
    acm.check_app_check(FakeRequest(), "send-otp")  # missing token, not rejected


def test_the_default_setting_is_monitor(env):
    _, acm, _ = env
    assert type(acm.settings).model_fields["APP_CHECK_MODE"].default == "monitor"


@pytest.mark.parametrize("call", ["send", "verify"])
def test_both_otp_routes_reject_before_any_other_work_in_enforce_mode(env, monkeypatch, call):
    routes, acm, calls = env
    monkeypatch.setattr(acm.settings, "APP_CHECK_MODE", "enforce")
    with pytest.raises(routes.HTTPException) as e:
        if call == "send":
            run(routes.send_otp(FakeRequest(), identifier="person@example.invalid", email=None,
                                invite_code="EPIC-FAKE09", authorization=None))
        else:
            run(routes.verify_otp_route(FakeRequest(), identifier="person@example.invalid", email=None, otp="000000"))
    assert e.value.status_code == 403 and e.value.detail == acm.APP_CHECK_REJECTED_DETAIL
    assert calls["reserve"] == [] and calls["validate"] == []
