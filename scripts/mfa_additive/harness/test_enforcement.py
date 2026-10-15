"""Phase 1 server-side MFA enforcement tests (mfa_enforcement.patch on top of the verified additive backend).

Runs INSIDE the isolated Docker network (see run_local_tests.sh): FAKE users only (example.invalid), a throwaway
PostgreSQL (mfa-pg), unsigned emulator-style Firebase tokens, NO internet, no cloud, no real email, no OpenAI.
The enforcement backend tree is mounted read-only at /src. This file starts its own backend processes (one per
configuration) with exactly three test-only substitutions at the OUTSIDE boundary, never in the code under test:
  * Firebase Admin uses the anonymous fake credential + emulator mode (same as harness/server.py);
  * the OpenAI realtime connection is a stub, so a voice session can stay open without any network;
  * Google Cloud Storage upload and speech-to-text are stubs, so /process-audio fails fast after the MFA gate.
Everything else is the real application: routes, dependencies, MFA validator, PostgreSQL, RealtimeSession watchdog.

Database outages are simulated per backend with ALTER DATABASE ... ALLOW_CONNECTIONS false + terminating its
connections, and recovery by switching it back on, so each backend can be broken independently.
Exit status 1 on any failure."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import asyncpg
import websockets

sys.path.insert(0, "/h")
from fb import token  # noqa: E402  (unsigned emulator-style Firebase ID tokens)

SRC = "/src/backend"
PG = "postgresql://postgres@mfa-pg/"
HASH_SECRET = "local-test-secret-2"
FAILCLOSED = 30                 # smallest allowed deadline, to keep the outage tests short
AT = int(time.time()) - 120     # Firebase sign-in time used by every token/session in the HTTP tests
results = []
ALL_RAW = []                    # every raw MFA session token created here (must never appear in any log)

LAUNCHER = r'''
import os, sys, asyncio
sys.path.insert(0, "/src/backend")
import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials
class _Fake(credentials.Base):
    def get_credential(self): return AnonymousCredentials()
firebase_admin.initialize_app(_Fake(), {"projectId": os.environ["FIREBASE_PROJECT_ID"]})
import app.services.email_service as es
async def _mail(email, otp, valid_minutes=1): return True
async def _notify(*a, **k): return True
es.send_otp_email = _mail
es.send_feedback_notification = _notify
import app.api.routes as routes
async def _upload(*a, **k): return None
async def _stt(audio): return {"text": "", "language": "en"}
routes.upload_to_gcs = _upload
routes.transcribe_audio = _stt
import app.services.realtime_service as rs
class _FakeOpenAI:
    async def close(self): return None
    async def send(self, *a, **k): return None
async def _connect(self):
    self.openai_ws = _FakeOpenAI(); return True
async def _setup(self): return None
async def _relay_openai(self): await asyncio.Event().wait()
rs.RealtimeSession._connect_to_openai = _connect
rs.RealtimeSession._setup_session = _setup
rs.RealtimeSession._relay_from_openai = _relay_openai
import uvicorn
from app.main import app
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
'''


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:400]) if detail and not ok else ""), flush=True)


# ------------------------------------------------------------------------------------------------ database helpers
async def asql(db, query, *args, fetch=False):
    c = await asyncpg.connect(PG + db, timeout=10)
    try:
        return await (c.fetch(query, *args) if fetch else c.execute(query, *args))
    finally:
        await c.close()


def sql(db, query, *args, fetch=False):
    return asyncio.run(asql(db, query, *args, fetch=fetch))


async def aoutage(db, on):
    await asql("postgres", 'ALTER DATABASE "%s" WITH ALLOW_CONNECTIONS %s' % (db, "false" if on else "true"))
    if on:
        await asql("postgres", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                   db, fetch=True)


def outage(db, on):
    asyncio.run(aoutage(db, on))


def sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


async def auser(db, uid, mfa=False, verified=True):
    await asql(db, "INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled, created_at) "
                   "VALUES ($1,'Fake User',$2,$3,$4, now() - interval '40 days') ON CONFLICT (uid) DO NOTHING",
               uid, uid + "@example.invalid", verified, mfa)


async def asession(db, uid, auth_time=AT, ttl=3600):
    raw = secrets.token_urlsafe(32)
    ALL_RAW.append(raw)
    await asql(db, "INSERT INTO mfa_sessions (session_hash, uid, created_at, expires_at, auth_time) "
                   "VALUES ($1,$2,NOW(), NOW() + make_interval(secs => $3::float8), to_timestamp($4::float8))",
               sha(raw), uid, float(ttl), float(auth_time))
    return raw


def user(db, uid, mfa=False):
    asyncio.run(auser(db, uid, mfa))


def session(db, uid, auth_time=AT, ttl=3600):
    return asyncio.run(asession(db, uid, auth_time, ttl))


def tok(uid, at=AT):
    return token(uid, uid + "@example.invalid", auth_time=at)


# ------------------------------------------------------------------------------------------------ backend processes
class Server:
    def __init__(self, name, port, env=None):
        self.name, self.port, self.env = name, port, env or {}
        self.db = "enf_" + name
        self.base = "http://127.0.0.1:%d/api/v1" % port
        self.proc = None
        self.log = []

    def start(self):
        sql("postgres", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % self.db)
        sql("postgres", 'CREATE DATABASE "%s"' % self.db)
        e = {
            "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PORT": str(self.port),
            "DATABASE_URL": PG + self.db, "FIREBASE_PROJECT_ID": "demo-epicverse", "GCP_PROJECT_ID": "demo-epicverse",
            "FIREBASE_AUTH_EMULATOR_HOST": "mfa-idstub:9099", "OPENAI_API_KEY": "dummy-no-egress",
            "SENDGRID_API_KEY": "dummy-no-egress", "SENDGRID_FROM_EMAIL": "noreply@example.invalid",
            "OTP_RATE_LIMIT_HASH_SECRET": "local-test-secret-1", "MFA_OTP_HASH_SECRET": HASH_SECRET,
            "ALLOWED_ORIGINS": "http://localhost",
        }
        e.update(self.env)
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", LAUNCHER], env=e, cwd=SRC,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stdout:
            self.log.append(line.rstrip("\n"))

    def wait_ready(self, timeout=240):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError("backend %s exited early:\n%s" % (self.name, "\n".join(self.log[-25:])))
            try:
                urllib.request.urlopen(self.base + "/faq", timeout=2).read()
                ok = sql(self.db, "select to_regclass('public.users') is not null and to_regclass('public.mfa_sessions') is not null "
                                  "and to_regclass('public.invite_codes') is not null", fetch=True)[0][0]
                if ok:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("backend %s did not become ready:\n%s" % (self.name, "\n".join(self.log[-25:])))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
        try:
            sql("postgres", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % self.db)
        except Exception:
            pass

    def lines(self, needle):
        return [l for l in list(self.log) if needle in l]


# ------------------------------------------------------------------------------------------------ HTTP helpers
def hget(headers, name):
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def multipart(files):
    b = "----enf" + uuid.uuid4().hex
    parts = []
    for k, (fn, ct, content) in files.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (b, k, fn, ct)).encode()
                     + content + b"\r\n")
    parts.append(("--%s--\r\n" % b).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + b


def http(server, method, path, tk=None, headers=None, form=None, js=None, files=None, timeout=40):
    h = dict(headers or {})
    data = None
    if tk:
        h["Authorization"] = "Bearer " + tk
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if js is not None:
        data = json.dumps(js).encode()
        h["Content-Type"] = "application/json"
    if files is not None:
        data, h["Content-Type"] = multipart(files)
    req = urllib.request.Request(server.base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body, hd = r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, body, hd = e.code, e.read(), dict(e.headers)
    try:
        body = json.loads(body)
    except Exception:
        body = body.decode("utf-8", "replace")[:200]
    return code, body, hd


def detail_code(r):
    b = r[1]
    return ((b.get("detail") or {}).get("code") if isinstance(b, dict) and isinstance(b.get("detail"), dict) else None)


def is_blocked(r):
    return r[0] == 401 and detail_code(r) == "MFA_SESSION_REQUIRED"


def is_unavail(r):
    return r[0] == 503 and detail_code(r) == "MFA_CHECK_UNAVAILABLE"


def passed(r):
    return (not is_blocked(r)) and (not is_unavail(r)) and r[0] not in (401, 403)


GATED = ("update_session", "check_session", "feedback", "process_audio")


def gated_calls(server, tk, sess=None, extra=None):
    h = dict(extra or {})
    if sess is not None:
        h["X-MFA-Session"] = sess
    return {
        "update_session": http(server, "POST", "/auth/update-session", tk, h, form={"session_id": "s-" + uuid.uuid4().hex[:8]}),
        "check_session": http(server, "GET", "/auth/check-session?session_id=x", tk, h),
        "feedback": http(server, "POST", "/feedback", tk, h, js={"message": "hello"}),
        "process_audio": http(server, "POST", "/process-audio", tk, h, files={"audio_file": ("a.wav", "audio/wav", b"RIFF" + b"\0" * 64)}),
    }


def all_pass(rs):
    return all(passed(v) for v in rs.values())


def all_blocked(rs):
    return all(is_blocked(v) for v in rs.values())


def contract_ok(rs):
    return (rs["update_session"][:2] == (200, {"status": "ok"}) and rs["check_session"][0] == 200 and "valid" in rs["check_session"][1]
            and rs["feedback"][0] == 200 and rs["feedback"][1].get("status") == "success" and rs["process_audio"][0] in (400, 500))


def tamper(raw):
    return raw[:-2] + ("xx" if raw[-2:] != "xx" else "yy")


# ------------------------------------------------------------------------------------------------ HTTP suite
def http_suite(S):
    E, L, O, C = S["enf"], S["log"], S["off"], S["can"]
    for s in (E, L, O, C):
        for uid, mfa in (("enf-plain-01", False), ("enf-mfa-0001", True), ("enf-mfa-0002", True), ("enf-can-listed", True)):
            user(s.db, uid, mfa)
        sql(s.db, "INSERT INTO invite_codes (code, current_uses, max_uses, created_at) VALUES ('EPIC-TEST01', 0, 50, now()) ON CONFLICT DO NOTHING")
    plain, m1, m2 = "enf-plain-01", "enf-mfa-0001", "enf-mfa-0002"
    tp, t1, t2 = tok(plain), tok(m1), tok(m2)

    # ---- enforce: the five approved surfaces (HTTP part)
    s1, s2 = session(E.db, m1), session(E.db, m2)
    r = gated_calls(E, tp)
    check("E1 enforce: MFA-off user passes all four gated HTTP endpoints with no session", all_pass(r), r)
    check("E1b enforce: response contracts for the MFA-off user are unchanged", contract_ok(r), r)
    r = gated_calls(E, t1, s1)
    check("E2 enforce: MFA user with a valid session passes all four, same contracts", all_pass(r) and contract_ok(r), r)
    r = gated_calls(E, t1)
    check("E3 enforce: MFA user with the header REMOVED is refused on all four (401 MFA_SESSION_REQUIRED)", all_blocked(r), r)
    r = gated_calls(E, t1, "")
    check("E3b enforce: an EMPTY session header is refused", all_blocked(r), r)
    r = gated_calls(E, t1, tamper(s1))
    check("E4 enforce: a tampered session is refused", all_blocked(r), r)
    r = gated_calls(E, t1, s2)
    check("E5 enforce: another user's valid session is refused (cross-user)", all_blocked(r), r)
    r = gated_calls(E, tok(m1, AT + 30), s1)
    check("E6 enforce: a session from a different Firebase sign-in (auth_time) is refused", all_blocked(r), r)
    s_exp = session(E.db, m1)
    sql(E.db, "UPDATE mfa_sessions SET expires_at = NOW() - interval '1 minute' WHERE session_hash = $1", sha(s_exp))
    check("E7 enforce: an expired session is refused", all_blocked(gated_calls(E, t1, s_exp)))
    s_rev = session(E.db, m1)
    sql(E.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(s_rev))
    check("E8 enforce: a revoked session is refused", all_blocked(gated_calls(E, t1, s_rev)))
    r = gated_calls(E, t1, None, {"X-App-Version": "999.0", "User-Agent": "EpicVerse/9.9 (new build)", "X-Client": "mfa-ok"})
    check("E9 enforce: claiming a newer app version / client via headers does not lower the requirement", all_blocked(r), r)
    r = gated_calls(E, t1, None, {"X-Forwarded-For": "127.0.0.1"})
    check("E9b enforce: spoofed forwarding headers do not lower the requirement", all_blocked(r), r)
    check("E10 enforce: the 401 body keeps the contract the apps already parse (detail.code + message)",
          detail_code(gated_calls(E, t1)["feedback"]) == "MFA_SESSION_REQUIRED"
          and "message" in gated_calls(E, t1)["feedback"][1]["detail"])
    c = http(E, "POST", "/feedback", None, js={"message": "x"})
    check("E11 enforce: no Firebase token is still refused by the existing authentication (401/403)", c[0] in (401, 403), c)
    c = http(E, "POST", "/feedback", "garbage", js={"message": "x"})
    check("E11b enforce: an invalid Firebase token is still refused by the existing authentication (401)", c[0] == 401 and not is_blocked(c), c)

    # ---- missing row stays log-only; signup keeps working
    norow = "enf-norow-01"
    r = gated_calls(E, tok(norow))
    check("E12 enforce: an account with NO database row is NOT blocked in Phase 1 (log-only)", all_pass(r), r)
    check("E12b enforce: ... and the missing row is counted", len(E.lines("no_row endpoint=feedback")) >= 1)
    newuid = "enf-new-0001"
    before = gated_calls(E, tok(newuid))
    c = http(E, "POST", "/sync-user", tok(newuid), js={"uid": newuid, "invite_code": "EPIC-TEST01", "display_name": "Fresh"})
    row = sql(E.db, "SELECT mfa_enabled FROM users WHERE uid = $1", newuid, fetch=True)
    after = gated_calls(E, tok(newuid))
    check("E13 signup under enforce: before the row exists, /sync-user with an invite creates it (MFA off) and gated calls work before and after",
          all_pass(before) and c[0] == 200 and row and row[0][0] is False and all_pass(after), (before, c, row, after))

    # ---- exempt endpoints are unchanged for an MFA user WITHOUT a session
    c = http(E, "POST", "/sync-user", t1, js={"uid": m1, "display_name": "Exempt Rename", "mfa_enabled": False, "email": "attacker@example.invalid"})
    row = sql(E.db, "SELECT mfa_enabled, email, display_name FROM users WHERE uid = $1", m1, fetch=True)[0]
    check("E14 sync-user stays available before MFA, and still cannot change MFA or the email",
          c[0] == 200 and row[0] is True and row[1] == m1 + "@example.invalid" and row[2] == "Exempt Rename", (c, tuple(row)))
    g = http(E, "GET", "/user/" + m1, t1)
    check("E14b GET /user/{own uid} stays available before MFA", g[0] == 200 and g[1].get("mfa_enabled") is True, g)
    g = http(E, "GET", "/user/" + m2, t1)
    check("E14c GET /user/{other uid} is still 403 (ownership)", g[0] == 403, g)
    g = http(E, "GET", "/auth/session-status", t1)
    check("E14d session-status stays available and reports the truth", g[0] == 200 and g[1] == {"email_verified": True, "mfa_enabled": True, "mfa_session_valid": False}, g)
    g = http(E, "POST", "/auth/mark-verified", t1)
    check("E14e mark-verified stays available", g[0] == 200, g)
    before_lines = len(E.lines("endpoint=delete_account"))
    g = http(E, "DELETE", "/user/" + m1, t1)
    pend = sql(E.db, "SELECT deletion_requested_at IS NOT NULL FROM users WHERE uid = $1", m1, fetch=True)[0][0]
    check("E15 account deletion is LOG-ONLY in Phase 1: an MFA user with no session is not blocked", g[0] == 200 and pend is True, (g, pend))
    check("E15b ... and the would-be block is counted", len(E.lines("would_block endpoint=delete_account")) > before_lines - 1 and len(E.lines("would_block endpoint=delete_account")) >= 1)
    g = http(E, "POST", "/user/%s/cancel-deletion" % m1, t1)
    pend = sql(E.db, "SELECT deletion_requested_at IS NOT NULL FROM users WHERE uid = $1", m1, fetch=True)[0][0]
    check("E15c cancel-deletion (exempt) still restores the account", g[0] == 200 and pend is False, (g, pend))
    g = http(E, "DELETE", "/user/" + m2, t1, headers={"X-MFA-Session": s2})
    check("E15d deletion ownership is unchanged (403 for another uid)", g[0] == 403, g)

    # ---- log mode never blocks, counts
    r = gated_calls(L, t1)
    check("G1 log: an MFA user with NO session is not blocked on any endpoint", all_pass(r), r)
    r2 = gated_calls(L, t1, tamper("x" * 40))
    check("G1b log: a garbage session is not blocked either", all_pass(r2), r2)
    check("G2 log: what would have been blocked is counted", len(L.lines("would_block endpoint=feedback reason=missing_session")) >= 1
          and len(L.lines("would_block endpoint=update_session reason=invalid_session")) >= 1)
    check("G2b log: counters never contain identifiers or tokens",
          not any(re.search(r"enf-|ws-|Bearer|eyJ", l) for l in L.lines("[MFA-ENFORCE]")))
    # ---- off mode: existing behaviour, no MFA machinery at all
    r = gated_calls(O, t1)
    check("O1 off: an MFA user with no session passes (existing behaviour)", all_pass(r) and contract_ok(r), r)
    # ---- canary: only the exact listed UID is enforced
    tl = tok("enf-can-listed")
    check("N1 canary: the LISTED MFA user without a session is refused", all_blocked(gated_calls(C, tl)))
    check("N2 canary: an UNLISTED MFA user without a session is not blocked", all_pass(gated_calls(C, t1)))
    check("N3 canary: the listed user with a valid session passes", all_pass(gated_calls(C, tl, session(C.db, "enf-can-listed"))))
    check("N4 canary: the unlisted user's would-be block is only counted", len(C.lines("would_block endpoint=feedback")) >= 1)
    user(C.db, "enf-can-listed-extra", True)          # an MFA user whose UID merely STARTS WITH a listed UID
    check("N5 canary: a UID that only starts with a listed UID is NOT enforced (exact match only)",
          all_pass(gated_calls(C, tok("enf-can-listed-extra"))))

    # ---- the same plain-user call sequence is identical in off, log and enforce (no behaviour change for MFA-off users)
    def sequence(srv):
        uid = plain
        t = tok(uid)
        out = []
        out.append(("faq", http(srv, "GET", "/faq")[0]))
        out.append(("user", http(srv, "GET", "/user/" + uid, t)[0]))
        out.append(("user-other", http(srv, "GET", "/user/someone-else", t)[0]))
        out.append(("update-session", http(srv, "POST", "/auth/update-session", t, form={"session_id": "snap"})[:2]))
        out.append(("check-session", http(srv, "GET", "/auth/check-session?session_id=snap", t)[:2]))
        out.append(("feedback", http(srv, "POST", "/feedback", t, js={"message": "snap"})[:2]))
        out.append(("session-status", http(srv, "GET", "/auth/session-status", t)[:2]))
        out.append(("sync-user", http(srv, "POST", "/sync-user", t, js={"uid": uid, "display_name": "Snap"})[:2]))
        out.append(("validate-invite", http(srv, "GET", "/validate-invite/EPIC-TEST01")[:2]))
        out.append(("mark-verified", http(srv, "POST", "/auth/mark-verified", t)[:2]))
        return out
    so, sl, se = sequence(O), sequence(L), sequence(E)
    check("R1 an MFA-off user's call sequence is identical (status and body) in off, log and enforce", so == sl == se, (so, sl, se))
    check("R1b ... and the sequence itself is healthy (no 4xx/5xx except the intended 403)",
          all(((x[1] if isinstance(x[1], int) else x[1][0]) in (200, 403)) for x in so), so)


def http_outage_suite(S):
    E, L, O = S["enf"], S["log"], S["off"]
    m1, plain = "enf-mfa-0001", "enf-plain-01"
    s1 = session(E.db, m1)
    outage(E.db, True)
    try:
        t0 = time.monotonic()
        r_m = gated_calls(E, tok(m1), s1)
        r_p = gated_calls(E, tok(plain))
        el = time.monotonic() - t0
        check("D1 enforce + database down: an MFA user gets 503 MFA_CHECK_UNAVAILABLE (fail closed), never an allow",
              all(is_unavail(v) for v in r_m.values()), r_m)
        check("D1b enforce + database down: with no way to know the account's MFA state, an MFA-off user is refused the same way",
              all(is_unavail(v) for v in r_p.values()), r_p)
        check("D1c the 503 carries Retry-After and the check is bounded in time", hget(r_m["feedback"][2], "Retry-After") == "5" and el < 90, (hget(r_m["feedback"][2], "Retry-After"), el))
        check("D1d database errors are counted as blocked", len(E.lines("db_error_blocked endpoint=feedback")) >= 1)
    finally:
        outage(E.db, False)
    ok = False
    for _ in range(40):
        r = http(E, "POST", "/feedback", tok(plain), js={"message": "recovered"})
        if r[0] == 200:
            ok = True
            break
        time.sleep(1)
    check("D2 enforce: after the database returns, requests succeed again without any restart", ok)
    outage(L.db, True)
    try:
        r = http(L, "POST", "/feedback", tok(m1), js={"message": "x"})
        check("D3 log + database down: never blocked by the MFA check (the request is handled as before)", not is_unavail(r) and not is_blocked(r), r)
        check("D3b ... and the database error is counted as allowed", len(L.lines("db_error_allowed endpoint=feedback")) >= 1)
    finally:
        outage(L.db, False)
    outage(O.db, True)
    try:
        before = len(O.lines("[MFA-ENFORCE]"))
        r = http(O, "POST", "/feedback", tok(m1), js={"message": "x"})
        check("D4 off + database down: no MFA check at all (existing behaviour)", not is_unavail(r) and not is_blocked(r) and len(O.lines("[MFA-ENFORCE]")) == before, r)
    finally:
        outage(O.db, False)


# ------------------------------------------------------------------------------------------------ WebSocket helpers
def parse_frame(m):
    if isinstance(m, bytes):
        return {"_bytes": len(m)}
    try:
        return json.loads(m)
    except Exception:
        return {"_text": m}


def close_code(e):
    c = getattr(e, "code", None)
    if c is None and getattr(e, "rcvd", None) is not None:
        c = e.rcvd.code
    return c


def close_reason(e):
    r = getattr(e, "reason", None)
    if r is None and getattr(e, "rcvd", None) is not None:
        r = e.rcvd.reason
    return r


async def watch(server, uid, tk, sess=None, seconds=5.0, session_id=None):
    """Opens the voice WebSocket and records every frame and how/when it closed, for at most `seconds`."""
    url = "ws://127.0.0.1:%d/api/v1/ws/realtime?uid=%s&mode=Mode%%201&session_id=%s" % (server.port, uid, session_id or uuid.uuid4().hex)
    hdr = {"Authorization": "Bearer " + tk}
    if sess:
        hdr["X-MFA-Session"] = sess
    out = {"frames": [], "times": [], "closed": False, "code": None, "reason": None, "t_close": None, "error": None, "connected": False}
    t0 = time.monotonic()
    try:
        ws = await websockets.connect(url, extra_headers=hdr, open_timeout=25, ping_interval=None, max_size=None)
    except Exception as e:
        out["error"] = repr(e)
        return out
    try:
        while True:
            left = seconds - (time.monotonic() - t0)
            if left <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=left)
            except asyncio.TimeoutError:
                break
            f = parse_frame(msg)
            out["frames"].append(f)
            out["times"].append(time.monotonic() - t0)
            if isinstance(f, dict) and f.get("type") == "connection_success":
                out["connected"] = True
    except websockets.exceptions.ConnectionClosed as e:
        out["closed"] = True
        out["code"], out["reason"] = close_code(e), close_reason(e)
        out["t_close"] = time.monotonic() - t0
    finally:
        if not out["closed"]:
            try:
                await ws.close()
            except Exception:
                pass
    return out


def codes(frames):
    return [f.get("code") for f in frames if isinstance(f, dict)]


def types(frames):
    return [f.get("type") for f in frames if isinstance(f, dict)]


def refused_mfa(r):
    return (not r["connected"]) and r["closed"] and r["code"] == 1008 and "MFA_SESSION_REQUIRED" in codes(r["frames"])


def stays_open(r):
    return r["connected"] and not r["closed"] and not r["error"]


def mfa_closed(r, max_t):
    return r["connected"] and r["closed"] and r["code"] == 1008 and "MFA_SESSION_REQUIRED" in codes(r["frames"]) \
        and r["t_close"] is not None and r["t_close"] <= max_t


async def later(delay, fn):
    await asyncio.sleep(delay)
    return await fn()


# ------------------------------------------------------------------------------------------------ WebSocket suite
async def ws_suite(S):
    E, L, O, C, U = S["enf"], S["log"], S["off"], S["can"], S["out"]

    async def mk(server, uid, mfa, ttl=3600, with_session=True, auth_time=AT):
        await auser(server.db, uid, mfa)
        sess = await asession(server.db, uid, auth_time, ttl) if (mfa and with_session) else None
        return sess

    # ---------- handshake matrix (enforce)
    async def handshake_enforce():
        P, M, M_ns, M_t, M_x, M_at, M_ex, M_rv, NR = ("ws-h-plain", "ws-h-mfa-ok", "ws-h-mfa-none", "ws-h-mfa-tamper", "ws-h-mfa-cross",
                                                      "ws-h-mfa-authtime", "ws-h-mfa-expired", "ws-h-mfa-revoked", "ws-h-norow")
        await auser(E.db, P, False)
        sM = await mk(E, M, True); await mk(E, M_ns, True)
        sT = await mk(E, M_t, True); sX_other = await mk(E, "ws-h-mfa-other", True); await mk(E, M_x, True)
        sAT = await mk(E, M_at, True); sEX = await mk(E, M_ex, True); sRV = await mk(E, M_rv, True)
        await asql(E.db, "UPDATE mfa_sessions SET expires_at = NOW() - interval '1 minute' WHERE session_hash = $1", sha(sEX))
        await asql(E.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(sRV))
        rs = await asyncio.gather(
            watch(E, P, tok(P), None, 3), watch(E, M, tok(M), sM, 3), watch(E, M_ns, tok(M_ns), None, 3),
            watch(E, M_t, tok(M_t), tamper(sT), 3), watch(E, M_x, tok(M_x), sX_other, 3),
            watch(E, M_at, tok(M_at, AT + 30), sAT, 3), watch(E, M_ex, tok(M_ex), sEX, 3), watch(E, M_rv, tok(M_rv), sRV, 3),
            watch(E, NR, tok(NR), None, 3))
        rP, rM, rNS, rT, rX, rAT, rEX, rRV, rNR = rs
        check("W1 enforce: an MFA-off user connects with no session and stays connected", stays_open(rP), rP)
        check("W2 enforce: an MFA user with a valid session connects and stays connected", stays_open(rM), rM)
        check("W3 enforce: an MFA user with NO session is refused: coded frame, then close 1008, never 'connected'", refused_mfa(rNS), rNS)
        check("W3b enforce: tampered session refused", refused_mfa(rT), rT)
        check("W3c enforce: another user's session refused", refused_mfa(rX), rX)
        check("W3d enforce: a session from another sign-in (auth_time) refused", refused_mfa(rAT), rAT)
        check("W3e enforce: an expired session refused", refused_mfa(rEX), rEX)
        check("W3f enforce: a revoked session refused", refused_mfa(rRV), rRV)
        check("W4 enforce: an account with no database row connects (log-only in Phase 1)", stays_open(rNR), rNR)
        bad = await watch(E, P, tok("someone-else"), None, 3)
        check("W5 existing authentication is unchanged: a token for another UID is refused with the original 'Unauthorized' frame",
              bad["closed"] and bad["code"] == 1008 and any(isinstance(f, dict) and f.get("message") == "Unauthorized" for f in bad["frames"]), bad)

    # ---------- handshake in the other modes
    async def handshake_modes():
        mO = "ws-o-mfa"; await mk(O, mO, True, with_session=False)
        mL = "ws-l-mfa"; await mk(L, mL, True, with_session=False)
        mC = "ws-c-unlisted"; await mk(C, mC, True, with_session=False)
        listed = "enf-can-listed"; await mk(C, listed, True, with_session=False)
        sL = await asession(C.db, listed)
        rs = await asyncio.gather(watch(O, mO, tok(mO), None, 3), watch(L, mL, tok(mL), None, 3), watch(C, mC, tok(mC), None, 3),
                                  watch(C, listed, tok(listed), None, 3))
        check("W6 off: an MFA user with no session connects (existing behaviour)", stays_open(rs[0]), rs[0])
        check("W7 log: an MFA user with no session connects (observe only)", stays_open(rs[1]), rs[1])
        check("W8 canary: an unlisted MFA user with no session connects", stays_open(rs[2]), rs[2])
        check("W8b canary: the listed MFA user with no session is refused", refused_mfa(rs[3]), rs[3])
        r = await watch(C, listed, tok(listed), sL, 3)
        check("W8c canary: the listed user with a valid session connects", stays_open(r), r)

    # ---------- mid-session: enforce
    async def m_revocation():
        u = "ws-m1-revoke"; s = await mk(E, u, True)
        r, _ = await asyncio.gather(watch(E, u, tok(u), s, 42),
                                    later(2, lambda: asql(E.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(s))))
        check("M1 enforce: revoking the session closes the live voice connection (coded frame, 1008) within one watchdog tick", mfa_closed(r, 37), r)

    async def m_expiry():
        u = "ws-m2-expiry"; s = await mk(E, u, True, ttl=10)
        r = await watch(E, u, tok(u), s, 26)
        ok = mfa_closed(r, 16) and r["t_close"] >= 6
        check("M2 enforce: the connection closes AT the session's expiry (about 10 s), not at the next 30 s tick", ok, r)
        ra = await watch(E, u, tok(u), None, 6)
        rb = await watch(E, u, tok(u), s, 6)
        check("M2b after an expiry close, a reconnect without the session is refused once, cleanly (no loop, no hang)",
              refused_mfa(ra) and ra["t_close"] < 5 and len(ra["frames"]) == 1, ra)
        check("M2c ... and with the stale session header it is refused once, cleanly", refused_mfa(rb) and rb["t_close"] < 5 and len(rb["frames"]) == 1, rb)

    async def m_enabled_elsewhere():
        u = "ws-m3-enable"; await auser(E.db, u, False)
        r, _ = await asyncio.gather(watch(E, u, tok(u), None, 42), later(2, lambda: asql(E.db, "UPDATE users SET mfa_enabled = TRUE WHERE uid = $1", u)))
        check("M3 enforce: MFA enabled on another device closes a connection that holds no MFA session", mfa_closed(r, 37), r)

    async def m_disabled_elsewhere():
        u = "ws-m4-disable"; s = await mk(E, u, True, ttl=14)
        r, _ = await asyncio.gather(watch(E, u, tok(u), s, 26), later(2, lambda: asql(E.db, "UPDATE users SET mfa_enabled = FALSE WHERE uid = $1", u)))
        check("M4 enforce: if the user switches MFA off, the connection is NOT dropped at the old expiry", stays_open(r), r)

    async def m_kick_db_one(label, mfa):
        u = "ws-m9-kick-" + label; s = await mk(E, u, mfa)
        r, _ = await asyncio.gather(watch(E, u, tok(u), s, 42, session_id="device-a"),
                                    later(2, lambda: asql(E.db, "UPDATE users SET session_id = 'other-device' WHERE uid = $1", u)))
        check("M9 single-device kick still works (%s user): SESSION_KICKED then close 1008 within one tick, not an MFA error" % label,
              r["connected"] and r["closed"] and r["code"] == 1008 and "SESSION_KICKED" in types(r["frames"])
              and "MFA_SESSION_REQUIRED" not in codes(r["frames"]) and r["t_close"] <= 37, r)

    async def m_kick_db():
        await asyncio.gather(m_kick_db_one("plain", False), m_kick_db_one("mfa", True))

    async def m_kick_dup():
        u = "ws-m10-dup"; await auser(E.db, u, False)
        first = asyncio.ensure_future(watch(E, u, tok(u), None, 14, session_id="dev-1"))
        await asyncio.sleep(3)
        second = await watch(E, u, tok(u), None, 5, session_id="dev-2")
        r1 = await first
        check("M10 duplicate connection: the first connection receives SESSION_KICKED, the second stays open",
              "SESSION_KICKED" in types(r1["frames"]) and stays_open(second), (r1, second))

    async def m_renewal_one(label, mfa):
        u = "enf-renew-" + label; s = await mk(E, u, mfa)
        r = await watch(E, u, tok(u), s, 40)
        check("M11 session renewal still works (%s user): session_renew frame then a normal close 1000, no MFA error" % label,
              r["connected"] and "session_renew" in types(r["frames"]) and r["closed"] and r["code"] == 1000
              and "MFA_SESSION_REQUIRED" not in codes(r["frames"]), r)

    async def m_renewal():
        await asyncio.gather(m_renewal_one("plain", False), m_renewal_one("mfa", True))

    # ---------- mid-session: canary / log / off
    async def m_canary():
        a = "enf-can-listed"; sa = await asession(C.db, a)
        b = "ws-c-listed-plain"; await mk(C, b, False)
        c_ = "ws-c-unl-plain"; await mk(C, c_, False)
        d = "ws-c-unl-mfa"; sd = await mk(C, d, True)
        ra, rb, rc, rd, *_ = await asyncio.gather(
            watch(C, a, tok(a), sa, 42), watch(C, b, tok(b), None, 42), watch(C, c_, tok(c_), None, 40), watch(C, d, tok(d), sd, 40),
            later(2, lambda: asql(C.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(sa))),
            later(2, lambda: asql(C.db, "UPDATE users SET mfa_enabled = TRUE WHERE uid = ANY($1::text[])", [b, c_])),
            later(2, lambda: asql(C.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(sd))))
        check("M12 canary: a LISTED user's revoked session is closed by the watchdog", mfa_closed(ra, 37), ra)
        check("M12b canary: a LISTED user who gets MFA enabled elsewhere is closed", mfa_closed(rb, 37), rb)
        check("M12c canary: an UNLISTED user is NEVER closed by the MFA watchdog (MFA enabled elsewhere)", stays_open(rc), rc)
        check("M12d canary: an UNLISTED user's revoked session is NEVER closed by the MFA watchdog", stays_open(rd), rd)

    async def m_log_off():
        pl = "ws-l-plain"; await mk(L, pl, False)
        ml = "ws-l-mfa-s"; sl = await mk(L, ml, True)
        mo = "ws-o-mfa-s"; so = await mk(O, mo, True)
        rpl, rml, rmo, *_ = await asyncio.gather(
            watch(L, pl, tok(pl), None, 40), watch(L, ml, tok(ml), sl, 40), watch(O, mo, tok(mo), so, 40),
            later(2, lambda: asql(L.db, "UPDATE users SET mfa_enabled = TRUE WHERE uid = $1", pl)),
            later(2, lambda: asql(L.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(sl))),
            later(2, lambda: asql(O.db, "UPDATE mfa_sessions SET revoked_at = NOW() WHERE session_hash = $1", sha(so))))
        check("M13 log: MFA enabled elsewhere does not close the connection (observe only)", stays_open(rpl), rpl)
        check("M13b log: a revoked session does not close the connection (observe only)", stays_open(rml), rml)
        check("M13c log: what the watchdog would have closed is counted", len(L.lines("would_close endpoint=websocket_watchdog")) >= 1)
        check("M14 off: a revoked session does not close the connection and nothing is checked", stays_open(rmo), rmo)

    # ---------- database outage (dedicated backend, enforce)
    async def outage_sustained():
        mA = "ws-u-mfa-deadline"; sA = await mk(U, mA, True)
        mB = "ws-u-mfa-expiry"; sB = await mk(U, mB, True, ttl=14)
        pC = "ws-u-plain"; await mk(U, pC, False)
        ra, rb, rc, *_ = await asyncio.gather(
            watch(U, mA, tok(mA), sA, 75), watch(U, mB, tok(mB), sB, 40), watch(U, pC, tok(pC), None, 75),
            later(4, lambda: aoutage(U.db, True)))
        check("U1 outage: an MFA-protected connection is closed with a BARE 1013 (no frame after 'connected', explanatory reason) once "
              "%d s pass without a successful MFA check" % FAILCLOSED,
              ra["connected"] and ra["closed"] and ra["code"] == 1013 and types(ra["frames"]) == ["connection_success"]
              and "mfa" in (ra["reason"] or "") and FAILCLOSED - 3 <= ra["t_close"] <= FAILCLOSED + 35, ra)
        check("U2 outage: session EXPIRY is still honoured with PostgreSQL down: coded frame + 1008 at about the TTL, before the deadline",
              rb["connected"] and rb["closed"] and rb["code"] == 1008 and "MFA_SESSION_REQUIRED" in codes(rb["frames"]) and 9 <= rb["t_close"] <= 20, rb)
        check("U3 outage: a connection of an MFA-OFF user is NOT closed (database errors ignored exactly as before)", stays_open(rc), rc)
        # reconnect attempts during the outage: bare 1013 every time, quickly, never a frame that would reset the app's back-off
        attempts = []
        for _ in range(3):
            attempts.append(await watch(U, mA, tok(mA), sA, 20))
        check("U4 outage: every reconnect attempt is refused with a bare 1013 and NO frame (so the app keeps its exponential back-off), each within 15 s",
              all(a["closed"] and a["code"] == 1013 and a["frames"] == [] and a["t_close"] < 15 for a in attempts), attempts)
        await aoutage(U.db, False)
        ok = None
        for _ in range(40):
            ok = await watch(U, mA, tok(mA), sA, 2)
            if stays_open(ok):
                break
            await asyncio.sleep(1)
        check("U5 recovery: once PostgreSQL returns, a reconnect with the SAME session is accepted (no new code needed)", ok and stays_open(ok), ok)

    async def outage_recovery_before_deadline():
        u = "ws-u2-mfa"; s = await mk(U, u, True)
        r, *_ = await asyncio.gather(watch(U, u, tok(u), s, 48), later(2, lambda: aoutage(U.db, True)), later(9, lambda: aoutage(U.db, False)))
        check("U6 outage shorter than the deadline: the connection survives and is revalidated afterwards", stays_open(r), r)

    # ---------- handshake with the database down in non-enforcing modes (run after the groups above)
    async def handshake_outage_modes():
        mO, mL, mC, listed = "ws-o-mfa", "ws-l-mfa", "ws-c-unlisted", "enf-can-listed"
        sess_listed = await asession(C.db, listed)
        await aoutage(O.db, True)
        try:
            r = await watch(O, mO, tok(mO), None, 3)
            check("D5 off + database down: the handshake behaves as before (no MFA check, no database access)", stays_open(r), r)
        finally:
            await aoutage(O.db, False)
        await aoutage(L.db, True)
        try:
            r = await watch(L, mL, tok(mL), None, 3)
            check("D6 log + database down: the handshake is not blocked", stays_open(r), r)
        finally:
            await aoutage(L.db, False)
        await aoutage(C.db, True)
        try:
            r1 = await watch(C, mC, tok(mC), None, 3)
            r2 = await watch(C, listed, tok(listed), sess_listed, 3)
            check("D7 canary + database down: an UNLISTED user is not blocked", stays_open(r1), r1)
            check("D7b canary + database down: a LISTED user gets a bare 1013 and no frame", r2["closed"] and r2["code"] == 1013 and r2["frames"] == [], r2)
        finally:
            await aoutage(C.db, False)

    await handshake_enforce()
    await handshake_modes()
    await asyncio.gather(m_revocation(), m_expiry(), m_enabled_elsewhere(), m_disabled_elsewhere(), m_kick_db(), m_kick_dup(),
                         m_renewal(), m_canary(), m_log_off(), outage_sustained())
    await outage_recovery_before_deadline()
    await handshake_outage_modes()


# ------------------------------------------------------------------------------------------------ main
def main():
    base_mfa = {"MFA_OTP_HASH_SECRET": HASH_SECRET}
    S = {
        "off": Server("off", 18101, dict(base_mfa)),
        "log": Server("log", 18102, dict(base_mfa, MFA_ENFORCEMENT_MODE="log")),
        "can": Server("can", 18103, dict(base_mfa, MFA_ENFORCEMENT_MODE="canary", MFA_ENFORCE_UIDS="enf-can-listed,ws-c-listed-plain")),
        "enf": Server("enf", 18104, dict(base_mfa, MFA_ENFORCEMENT_MODE="enforce", MFA_WS_FAILCLOSED_SECONDS=str(FAILCLOSED),
                                         SESSION_RENEWAL_MODE="allowlist", SESSION_RENEWAL_UIDS="enf-renew",
                                         SESSION_RENEW_MINUTES="0.3", SESSION_RENEW_HARD_MINUTES="0.3", SESSION_RENEW_IDLE_SECONDS="1")),
        "out": Server("out", 18105, dict(base_mfa, MFA_ENFORCEMENT_MODE="enforce", MFA_WS_FAILCLOSED_SECONDS=str(FAILCLOSED))),
    }
    try:
        for s in S.values():
            s.start()
        for s in S.values():
            s.wait_ready()
        print("backends ready: " + ", ".join(sorted(S)), flush=True)
        http_suite(S)
        asyncio.run(ws_suite(S))
        http_outage_suite(S)

        time.sleep(1)
        check("L1 off: the MFA machinery printed nothing at all for the whole run", len(S["off"].lines("[MFA-ENFORCE]")) == 0, S["off"].lines("[MFA-ENFORCE]")[:3])
        mfa_lines = [l for s in S.values() for l in s.lines("[MFA-ENFORCE]")]
        check("L2 enforcement log lines are fixed-format counters: no UID, token, e-mail or header value anywhere in them",
              len(mfa_lines) > 0 and not any(re.search(r"enf-|ws-|@|Bearer|eyJ|X-MFA", l) for l in mfa_lines), mfa_lines[:3])
        all_logs = "\n".join(l for s in S.values() for l in s.log)
        leaked = [r[:6] for r in ALL_RAW if r in all_logs]
        check("L3 no raw MFA session token appears in any backend log", not leaked, leaked[:3])
        for needle, srv in (("blocked endpoint=update_session", "enf"), ("ws_closed_revoked", "enf"), ("ws_closed_expired", "enf"),
                            ("ws_closed_db_unavailable", "out"), ("ws_closed_expired", "out"), ("would_block endpoint=feedback", "log")):
            check("L4 expected counter '%s' was recorded on the %s backend" % (needle, srv), len(S[srv].lines(needle)) >= 1)
    finally:
        for s in S.values():
            try:
                s.stop()
            except Exception:
                pass
    bad = results.count(False)
    print("\nTOTAL %d checks | PASS %d | FAIL %d" % (len(results), len(results) - bad, bad))
    sys.exit(1 if bad else 0)


main()
