"""Failure-injection tests for the WebSocket MFA watchdog (RealtimeSession._mfa_tick / _mfa_close / _watchdog_sleep).

Runs INSIDE the isolated Docker network (see run_local_tests.sh) with the enforcement backend tree mounted read-only at /src.
No database, no network, no cloud, no server process: the real RealtimeSession is built in-process with a fake WebSocket, and
only the MFA decision function (app.api.dependencies.evaluate_mfa), the counter (mfa_record) and the single-device lookup are
replaced with fakes, so every failure path can be forced deterministically and in milliseconds.
Exit status 1 on any failure."""
import asyncio
import inspect
import json
import os
import sys
import time

os.environ.update({
    "MFA_ENFORCEMENT_MODE": "enforce", "MFA_OTP_HASH_SECRET": "local-test-secret-2", "MFA_WS_FAILCLOSED_SECONDS": "30",
    "SESSION_RENEWAL_MODE": "all", "SESSION_RENEW_MINUTES": "0.001", "SESSION_RENEW_HARD_MINUTES": "0.001",
    "SESSION_RENEW_IDLE_SECONDS": "0.1", "OPENAI_API_KEY": "dummy-no-egress", "FIREBASE_PROJECT_ID": "demo-epicverse",
})
sys.path.insert(0, "/src/backend")

import app.api.dependencies as deps  # noqa: E402
import app.services.realtime_service as rs  # noqa: E402
import app.services.user_db as udb  # noqa: E402
from app.api.dependencies import MfaDecision  # noqa: E402
from app.core.config import settings  # noqa: E402

DEADLINE = settings.MFA_WS_FAILCLOSED_SECONDS
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:400]) if detail and not ok else ""), flush=True)


class FakeWS:
    def __init__(self, fail_send=False, fail_close=False):
        self.sent, self.closed, self.fail_send, self.fail_close = [], None, fail_send, fail_close

    async def send_text(self, text):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(text)

    async def close(self, code=1000, reason=None):
        if self.fail_close:
            raise RuntimeError("close failed")
        self.closed = (code, reason)


def make(ws=None, observe=True, enforce=True, required=True, ttl=None, uid="unit-user-001"):
    ws = ws or FakeWS()
    s = rs.RealtimeSession(ws, uid, "Mode 1", "sid-1", mfa_observe=observe, mfa_enforce=enforce, mfa_required=required,
                           mfa_session="s" * 40, mfa_auth_time=None, mfa_ttl_seconds=ttl)
    return s, ws


def mfa_frame(ws):
    return any('"MFA_SESSION_REQUIRED"' in t for t in ws.sent)


class Patch:
    """Temporarily replaces attributes; restores on exit."""
    def __init__(self, *triples):
        self.triples, self.saved = triples, []

    def __enter__(self):
        for obj, name, value in self.triples:
            self.saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        return self

    def __exit__(self, *a):
        for obj, name, old in reversed(self.saved):
            setattr(obj, name, old)


async def raising_eval(*a, **k):
    raise RuntimeError("unexpected failure in the MFA check")


def decision_eval(outcome, **kw):
    async def _e(*a, **k):
        return MfaDecision(outcome, **kw)
    return _e


def raising_record(event):
    raise RuntimeError("counter/log failure")


async def main():
    now = time.monotonic

    # ------------------------------------------------------------------ _mfa_close is exception-safe
    with Patch((deps, "mfa_record", raising_record)):
        s, ws = make()
        await s._mfa_close(1008, True, "ws_closed_expired")
        check("F1 a failing counter/log cannot stop the close: marked inactive, coded frame sent, closed 1008",
              s._active is False and mfa_frame(ws) and ws.closed == (1008, None), (s._active, ws.sent, ws.closed))
        s, ws = make()
        await s._mfa_close(1013, False, "ws_closed_db_unavailable")
        check("F1b ... and the bare 1013 close keeps its reason and sends NO frame", s._active is False and ws.sent == [] and ws.closed == (1013, "mfa-check-unavailable"), (ws.sent, ws.closed))
    s, ws = make(FakeWS(fail_send=True))
    await s._mfa_close(1008, True, "ws_closed_revoked")
    check("F2 a failing frame send does not prevent the close", s._active is False and ws.closed == (1008, None), (s._active, ws.closed))
    s, ws = make(FakeWS(fail_close=True))
    await s._mfa_close(1008, True, "ws_closed_revoked")
    check("F2b a failing close does not raise and the session is still marked inactive", s._active is False and mfa_frame(ws))
    s, ws = make()
    ok_order = []
    s._mfa_count = lambda ev: ok_order.append(("count", s._active))
    await s._mfa_close(1008, True, "ws_closed_expired")
    check("F3 the session is marked inactive BEFORE any counter/log call", ok_order == [("count", False)], ok_order)

    # ------------------------------------------------------------------ unexpected exceptions never bypass a known expiry
    with Patch((deps, "evaluate_mfa", raising_eval), (deps, "mfa_record", raising_record)):
        s, ws = make(ttl=3600)
        closed = [await s._mfa_tick() for _ in range(5)]
        check("F4 repeated unexpected exceptions BEFORE the expiry do not close a healthy session", closed == [False] * 5 and ws.closed is None and s._mfa_err_streak == 5, (closed, s._mfa_err_streak))
        s._mfa_expiry_mono = now() - 1
        r = await s._mfa_tick()
        check("F5 ... but the known expiry is enforced on the next tick even though the check keeps raising (frame + 1008)",
              r is True and mfa_frame(ws) and ws.closed == (1008, None) and s._active is False, (r, ws.sent, ws.closed))
        s, ws = make(ttl=3600)
        s._mfa_expiry_mono = now() - 1
        r = await s._mfa_tick()
        check("F5b expiry + exception + a failing counter on the very first tick still closes", r is True and ws.closed == (1008, None))

    # ------------------------------------------------------------------ unexpected exceptions never bypass the fail-closed deadline
    with Patch((deps, "evaluate_mfa", raising_eval), (deps, "mfa_record", raising_record)):
        s, ws = make(ttl=None)
        s._mfa_last_ok_mono = now() - (DEADLINE - 5)
        a = await s._mfa_tick()
        s._mfa_last_ok_mono = now() - (DEADLINE + 1)
        b = await s._mfa_tick()
        check("F6 repeated exceptions: still open before the %d s deadline, closed after it with a bare 1013 (no frame)" % DEADLINE,
              a is False and b is True and ws.sent == [] and ws.closed == (1013, "mfa-check-unavailable") and s._active is False, (a, b, ws.sent, ws.closed))
        s, ws = make(ttl=3600)
        s._mfa_expiry_mono = now() + 3600
        s._mfa_last_ok_mono = now() - (DEADLINE + 1)
        check("F6b the deadline applies even when an expiry is tracked but far away", await s._mfa_tick() is True and ws.closed == (1013, "mfa-check-unavailable"))

    # ------------------------------------------------------------------ the tick itself never raises
    s, ws = make()
    async def boom(*a, **k):
        raise RuntimeError("close path failure")
    s._mfa_close = boom
    s._mfa_expiry_mono = now() - 1
    with Patch((deps, "evaluate_mfa", raising_eval)):
        try:
            r = await s._mfa_tick()
            check("F7 even if the close path itself fails, the tick returns instead of raising (the watchdog survives)", r is False)
        except Exception as e:
            check("F7 even if the close path itself fails, the tick returns instead of raising (the watchdog survives)", False, repr(e))

    # ------------------------------------------------------------------ polling frequency during persistent failures
    with Patch((deps, "evaluate_mfa", raising_eval)):
        s, ws = make(ttl=3600)
        s._mfa_expiry_mono = now() + 0.05           # expiry almost due: would normally wake in 0.2 s
        sleeps = []
        for _ in range(3):
            sleeps.append(s._watchdog_sleep(30.0))
            await s._mfa_tick()
        healthy = make(ttl=3600)[0]
        healthy._mfa_expiry_mono = now() + 0.05
        check("F8 while the MFA check keeps failing, the watchdog never polls faster than every 5 s (no hot loop)",
              all(x >= 5.0 for x in sleeps[1:]) and healthy._watchdog_sleep(30.0) <= 0.21, (sleeps, healthy._watchdog_sleep(30.0)))
        s, ws = make(ttl=3600)
        s._mfa_last_ok_mono = now()                  # persistent failures, nothing due yet: 50 simulated ticks
        sleeps, closes = [], 0
        for _ in range(50):
            closes += 1 if await s._mfa_tick() else 0
            sleeps.append(s._watchdog_sleep(30.0))
        check("F8b 50 failing ticks with nothing due: no close, every computed sleep is the normal wake-up (>= 5 s)", closes == 0 and min(sleeps) >= 5.0 and ws.closed is None, (closes, min(sleeps)))
        s._mfa_last_ok_mono = now() - DEADLINE - 1
        after = [await s._mfa_tick() for _ in range(2)]
        check("F8c ... and the next failing tick after the deadline ends the loop by closing (so polling stops)", after[0] is True and s._active is False)
        s, ws = make(ttl=3600)
        s._mfa_err_streak = 0
        check("F9 normal scheduling is unchanged: about 30 s for a healthy enforced session with distant expiry and deadline",
              29.0 <= s._watchdog_sleep(30.0) <= 30.0, s._watchdog_sleep(30.0))
        s, ws = make(ttl=3600)
        s._mfa_last_ok_mono = "not-a-number"
        check("F9b _watchdog_sleep never raises: on anything unexpected it returns the normal poll interval", s._watchdog_sleep(30.0) == 30.0)

    # ------------------------------------------------------------------ no regression for sessions that are not enforced
    with Patch((deps, "evaluate_mfa", raising_eval), (deps, "mfa_record", raising_record)):
        for label, kw in (("log", dict(observe=True, enforce=False, required=True)),
                          ("canary-unlisted", dict(observe=True, enforce=False, required=True)),
                          ("MFA-disabled enforced", dict(observe=True, enforce=True, required=False))):
            s, ws = make(ttl=None, **kw)
            s._mfa_expiry_mono = now() - 1
            s._mfa_last_ok_mono = now() - (DEADLINE + 100)
            r = [await s._mfa_tick() for _ in range(3)]
            check("N1 %s: persistent exceptions never close the session, even with a stale last check" % label, r == [False] * 3 and ws.closed is None and s._active is True, (r, ws.closed))
            check("N1b %s: scheduling is exactly the normal poll interval" % label, s._watchdog_sleep(30.0) == 30.0, s._watchdog_sleep(30.0))
    for label, kw in (("log", dict(observe=True, enforce=False, required=True)), ("canary-unlisted", dict(observe=True, enforce=False, required=True)),
                      ("MFA-disabled enforced", dict(observe=True, enforce=True, required=False))):
        with Patch((deps, "evaluate_mfa", decision_eval("db_error"))):
            s, ws = make(ttl=None, **kw)
            s._mfa_last_ok_mono = now() - (DEADLINE + 100)
            check("N2 %s: a database outage past the deadline does not close it" % label, await s._mfa_tick() is False and ws.closed is None)
    with Patch((deps, "evaluate_mfa", decision_eval("invalid", mfa_required=True, reason="invalid_session"))):
        for label in ("log", "canary-unlisted"):
            s, ws = make(ttl=None, observe=True, enforce=False, required=True)
            r = await s._mfa_tick()
            check("N3 %s: a revoked/missing MFA session is only counted, never closed" % label, r is False and ws.closed is None and s._active is True)
        s, ws = make(observe=False, enforce=False, required=False)
        check("N4 off: scheduling is exactly the normal poll interval and the watchdog never calls the MFA step", s._watchdog_sleep(30.0) == 30.0 and s._mfa_observe is False)
    with Patch((deps, "evaluate_mfa", decision_eval("not_enabled"))):
        s, ws = make(ttl=60)
        s._mfa_expiry_mono = now() - 1
        r = await s._mfa_tick()
        check("N5 MFA switched off before the expiry (database healthy): the connection stays open and tracking is cleared",
              r is False and ws.closed is None and s._mfa_required is False and s._mfa_expiry_mono is None)
        check("N5b ... and scheduling returns to the normal poll interval", s._watchdog_sleep(30.0) == 30.0)

    # ------------------------------------------------------------------ MFA closes still work through the real watchdog loop
    async def same_session(uid):
        return "sid-1"
    with Patch((udb, "get_session_id", same_session), (deps, "evaluate_mfa", decision_eval("invalid", mfa_required=True, reason="invalid_session"))):
        s, ws = make(ttl=None)
        await asyncio.wait_for(s._session_watchdog(poll_interval=0.05), timeout=10)
        check("W1 real watchdog loop: a revoked session closes with the coded frame and 1008", mfa_frame(ws) and ws.closed == (1008, None), (ws.sent, ws.closed))
    with Patch((udb, "get_session_id", same_session), (deps, "evaluate_mfa", decision_eval("db_error"))):
        s, ws = make(ttl=0.6)
        t0 = now()
        await asyncio.wait_for(s._session_watchdog(poll_interval=30.0), timeout=10)
        el = now() - t0
        check("W2 real watchdog loop: expiry with the database down closes at the expiry (about 0.6 s), not at the 30 s tick",
              mfa_frame(ws) and ws.closed == (1008, None) and 0.3 <= el <= 3.0, (el, ws.sent, ws.closed))

    # ------------------------------------------------------------------ single-device kick and renewal are untouched
    async def other_device(uid):
        return "another-device"
    with Patch((udb, "get_session_id", other_device), (deps, "evaluate_mfa", decision_eval("invalid", mfa_required=True, reason="invalid_session"))):
        s, ws = make(ttl=None)
        await asyncio.wait_for(s._session_watchdog(poll_interval=0.05), timeout=10)
        types = [t for t in ws.sent]
        check("K1 single-device kick takes precedence over the MFA step: only SESSION_KICKED is sent, then close 1008",
              len(types) == 1 and "SESSION_KICKED" in types[0] and ws.closed == (1008, None) and not mfa_frame(ws), (ws.sent, ws.closed))
    with Patch((udb, "get_session_id", other_device), (deps, "evaluate_mfa", raising_eval)):
        s, ws = make(observe=False, enforce=False, required=False)
        await asyncio.wait_for(s._session_watchdog(poll_interval=0.05), timeout=10)
        check("K2 kick still works for an unenforced (off) session", len(ws.sent) == 1 and "SESSION_KICKED" in ws.sent[0] and ws.closed == (1008, None))
    s, ws = make(ttl=3600)
    s._tts_streaming = False
    await asyncio.wait_for(s._renewal_watch(), timeout=10)
    check("R1 session renewal is untouched: session_renew frame, then a normal close 1000, no MFA frame",
          len(ws.sent) == 1 and "session_renew" in ws.sent[0] and ws.closed == (1000, None) and not mfa_frame(ws), (ws.sent, ws.closed))

    # ------------------------------------------------------------------ a deactivated session forwards nothing more (client relay)
    class FakeClient(FakeWS):
        """Client socket whose receive() the test controls; a close that fails leaves it 'open', as in the failure case."""
        def __init__(self, **kw):
            super().__init__(**kw)
            self.q = asyncio.Queue()

        async def receive(self):
            return await self.q.get()

    class FakeOpenAI:
        def __init__(self, on_send=None):
            self.sent, self.on_send = [], on_send

        async def send(self, data):
            self.sent.append(data)
            if self.on_send:
                self.on_send(len(self.sent))

    def audio(b=b"\x01\x02" * 8):
        return {"type": "websocket.receive", "bytes": b}

    def text(d):
        return {"type": "websocket.receive", "text": json.dumps(d)}

    DISCONNECT = {"type": "websocket.disconnect"}

    async def relay(client_msgs_after_start=(), *, fail_close=False, deactivate=False, pre_buffer=(), on_send=None, appended=False):
        """Starts the real _relay_from_client, lets it block in receive(), optionally runs the real _mfa_close (the way the
        watchdog does), feeds the given client frames and returns (session, client, openai, finished_in_time)."""
        ws = FakeClient(fail_close=fail_close)
        s, _ = make(ws)
        oa = FakeOpenAI(on_send)
        s.openai_ws = oa
        s._cooldown_audio_buffer = list(pre_buffer)
        s._audio_appended_since_commit = appended
        task = asyncio.ensure_future(s._relay_from_client())
        await asyncio.sleep(0.05)                     # the relay is now waiting in receive()
        if deactivate:
            await s._mfa_close(1008, True, "ws_closed_revoked")
        for m in client_msgs_after_start:
            ws.q.put_nowait(m)
        try:
            await asyncio.wait_for(task, timeout=2)
            done = True
        except asyncio.TimeoutError:
            done = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return s, ws, oa, done

    def is_append(raw):
        return json.loads(raw).get("type") == "input_audio_buffer.append"

    # --- after deactivation: nothing that was already waiting in receive() is forwarded
    s, ws, oa, done = await relay([audio()], deactivate=True, fail_close=True)
    check("X1 audio received after deactivation (close failed, socket still open) is NOT forwarded and the relay ends",
          oa.sent == [] and done and s._active is False, (oa.sent, done))
    s, ws, oa, done = await relay([text({"type": "response.create"})], deactivate=True, fail_close=True)
    check("X2 a text frame received after deactivation (response.create) is NOT forwarded and the relay ends",
          oa.sent == [] and done, (oa.sent, done))
    s, ws, oa, done = await relay([text({"type": "conversation.item.create", "item": {"type": "message"}})], deactivate=True, fail_close=True)
    check("X2b ... nor a conversation.item.create", oa.sent == [] and done, (oa.sent, done))
    s, ws, oa, done = await relay([audio()], deactivate=True)
    check("X1b the same with a close that succeeds: nothing forwarded", oa.sent == [] and done, (oa.sent, done))
    s, ws, oa, done = await relay([text({"type": "end"})], deactivate=True, fail_close=True, appended=True)
    check("X5b a control message ('end') after deactivation is not processed either: no silence padding sent, state untouched",
          oa.sent == [] and done and s._audio_appended_since_commit is True, (oa.sent, done))
    s, ws, oa, done = await relay([DISCONNECT], deactivate=True, fail_close=True)
    check("X6 a client disconnect after deactivation still ends the relay cleanly", done and oa.sent == [] and s._active is False)

    # --- deactivation in the middle of the cooldown-buffer flush
    def deactivate_after_second(n):
        if n == 2:
            holder["s"]._active = False
    holder = {}
    ws_ = FakeClient()
    s, _ = make(ws_)
    holder["s"] = s
    oa = FakeOpenAI(deactivate_after_second)
    s.openai_ws = oa
    s._cooldown_audio_buffer = [b"c1" * 8, b"c2" * 8, b"c3" * 8, b"c4" * 8, b"c5" * 8]
    ws_.q.put_nowait(audio(b"live" * 4))
    await asyncio.wait_for(s._relay_from_client(), timeout=2)
    check("X3 deactivated during the buffer flush: chunks already sent stay sent, the remaining buffered chunks AND the live chunk are not",
          len(oa.sent) == 2 and all(is_append(x) for x in oa.sent) and s._active is False, (len(oa.sent), s._active))

    # --- unchanged behaviour while the session is active
    s, ws, oa, done = await relay([audio(), text({"type": "response.create"}), DISCONNECT])
    check("X4 active: an audio chunk is forwarded as input_audio_buffer.append and a text frame is forwarded verbatim, in order",
          done and len(oa.sent) == 2 and is_append(oa.sent[0]) and json.loads(oa.sent[1]) == {"type": "response.create"}, oa.sent)
    s, ws, oa, done = await relay([audio(b"live" * 4), DISCONNECT], pre_buffer=[b"a1" * 8, b"a2" * 8, b"a3" * 8])
    check("X4b active: a non-empty cooldown buffer is flushed completely (3 chunks) before the live chunk (4 sends in total)",
          done and len(oa.sent) == 4 and all(is_append(x) for x in oa.sent) and s._cooldown_audio_buffer == [], (len(oa.sent), done))
    s, ws, oa, done = await relay([text({"type": "ping"}), text({"type": "start_wakeword"}), text({"type": "stop_wakeword"}), DISCONNECT])
    check("X5 active: ping / start_wakeword / stop_wakeword are consumed locally and nothing is sent to OpenAI", done and oa.sent == [], oa.sent)
    s, ws, oa, done = await relay([text({"type": "end"}), DISCONNECT], appended=True)
    check("X5c active: 'end' with audio pending still appends the 500 ms silence padding (one send) and clears the flag",
          done and len(oa.sent) == 1 and is_append(oa.sent[0]) and s._audio_appended_since_commit is False, (oa.sent, done))
    s, ws, oa, done = await relay([text({"type": "end"}), DISCONNECT], appended=False)
    check("X5d active: 'end' with nothing pending sends nothing", done and oa.sent == [], oa.sent)
    s, ws, oa, done = await relay([DISCONNECT])
    check("X6b active: a client disconnect ends the relay and marks the session inactive", done and s._active is False and oa.sent == [])

    # --- every caller of _append_audio_to_openai is one of the two guarded call sites
    src = inspect.getsource(rs.RealtimeSession)
    guarded = inspect.getsource(rs.RealtimeSession._relay_from_client)
    check("X7 _append_audio_to_openai has exactly two callers and both are inside _relay_from_client (so both are guarded)",
          src.count("self._append_audio_to_openai(") == 2 and guarded.count("self._append_audio_to_openai(") == 2,
          (src.count("self._append_audio_to_openai("), guarded.count("self._append_audio_to_openai(")))
    check("X8 the guard sits right after receive() and the disconnect handling in _relay_from_client",
          guarded.index("await self.client_ws.receive()") < guarded.index("websocket.disconnect") < guarded.index("if not self._active:\n                    break"))

    bad = results.count(False)
    print("\nTOTAL %d checks | PASS %d | FAIL %d" % (len(results), len(results) - bad, bad))
    sys.exit(1 if bad else 0)


asyncio.run(main())
