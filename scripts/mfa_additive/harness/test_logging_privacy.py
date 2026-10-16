"""Logging-privacy tests (mfa_logging_privacy.patch on top of the additive + enforcement backend).

Runs INSIDE the isolated Docker network (see run_local_tests.sh): FAKE users only (example.invalid), a throwaway PostgreSQL,
unsigned emulator-style Firebase tokens, NO internet, no cloud, no real email, no OpenAI, no production data.

Two real backend processes (MFA log mode and MFA enforce mode) are started with `uvicorn` at INFO level, so the real access
lines are produced. Test-only substitutions are at the OUTSIDE boundary only (Firebase credential, OpenAI connection and
client, SendGrid HTTP transport, GCS/STT upload); the code under test (routes, dependencies, email_service, speech_to_text,
ai_pipeline, realtime_service, user_db, log_privacy, uvicorn filter) is the real one.

Method: every request carries CANARY values (an e-mail address, a 28-character UID, OTP codes, ID tokens, MFA session tokens,
challenge ids, spoken transcripts, AI-generated text, exception texts, query parameters). The complete captured stdout/stderr
of each backend is then searched: no canary may appear, while the operational lines (status codes, MFA counters, error
categories, masked correlation ids, lengths) must still be there.
Exit status 1 on any failure."""
import asyncio
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
from fb import token  # noqa: E402

SRC = "/src/backend"
PG = "postgresql://postgres@mfa-pg/"
HASH_SECRET = "local-test-secret-2"
AT = int(time.time()) - 120
results = []

# ---- canaries: none of these may ever appear in a log line ----------------------------------------------------------------
EMAIL = "canary.person.7731@example.invalid"
EMAIL_B = "canary.other.5521@example.invalid"
EMAIL_RESET = "canary.reset.9904@example.invalid"
UID = "canaryuid-0123456789abcdefg"                 # 27+ chars, like a Firebase UID
UID_B = "canaryuid-bbbbbbbbbbbbbbbbbbb"
UID_M = "canaryuid-mmmmmmmmmmmmmmmmmmm"
BADTOKEN = "canary-bad-token-ZZ9876543210QQ"
QUERY_SECRET = "canary-query-secret-4411"
SESSION_ID = "canary-session-id-8c7d6e5f4a3b"
TRANSCRIPT = "canary transcript my password is hunter2zebra"
PARTIAL = "canary partial speech fragment alpha"
AI_TEXT = "canary assistant answer about the secret plan omega"
SUBJECT_EXC = "canary-exception-text-4f3e2d1c"
SQL_EXC = "canary-sql-detail-7b6a5948"
MAIL_BODY_SECRET = "canary-sendgrid-body-3c2b1a09"
MAILFILE = "/tmp/priv_mail.jsonl"
PROVIDER_TEXT = "canary-provider-message-6e5d4c3b"

LAUNCHER = r'''
import os, sys, asyncio, json
sys.path.insert(0, "/src/backend")
import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials
class _Fake(credentials.Base):
    def get_credential(self): return AnonymousCredentials()
firebase_admin.initialize_app(_Fake(), {"projectId": os.environ["FIREBASE_PROJECT_ID"]})
MAILFILE = os.environ["PRIV_MAILFILE"]
CAN_AI, CAN_TR, CAN_PA = os.environ["PRIV_AI"], os.environ["PRIV_TR"], os.environ["PRIV_PA"]
CAN_EXC, CAN_BODY, CAN_PROV = os.environ["PRIV_EXC"], os.environ["PRIV_BODY"], os.environ["PRIV_PROV"]

# --- OTP mail: the REAL send_otp_email runs; only the HTTP transport (httpx) is replaced and records what would be sent ---
import httpx
class _Resp:
    def __init__(self, code, text=""): self.status_code, self.text = code, text
MODE = {"mail": "ok"}
class _Client:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, headers=None, json=None, **k):
        if MODE["mail"] == "reject": return _Resp(401, "rejected for " + CAN_BODY)
        if MODE["mail"] == "raise": raise RuntimeError("transport failure " + CAN_EXC)
        try:
            body = json["content"][0]["value"] if json and json.get("content") else ""
            import re as _re
            m = _re.search(r"\b(\d{6})\b", body)
            to = json["personalizations"][0]["to"][0]["email"]
            with open(MAILFILE, "a") as f: f.write(__import__("json").dumps({"to": to, "otp": m.group(1) if m else None}) + "\n")
        except Exception as e:
            with open(MAILFILE, "a") as f: f.write(__import__("json").dumps({"err": repr(e)}) + "\n")
        return _Resp(202)
httpx.AsyncClient = lambda *a, **k: _Client()

import app.api.routes as routes
async def _upload(*a, **k): return None
async def _stt(audio): return {"text": "", "language": "en"}
routes.upload_to_gcs = _upload
routes.transcribe_audio = _stt

# --- OpenAI: a fake realtime socket replaying events that contain the canary transcript / AI text ---
import app.services.realtime_service as rs
class _FakeOpenAI:
    def __init__(self):
        self.events = [
            {"type": "session.created", "session": {}},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 900},
            {"type": "conversation.item.input_audio_transcription.delta", "delta": CAN_PA},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": CAN_TR},
            {"type": "response.audio_transcript.done", "transcript": CAN_AI},
            {"type": "response.text.done", "text": CAN_AI},
            {"type": "conversation.item.input_audio_transcription.failed",
             "error": {"code": "stt_canary_code", "type": "stt_error", "message": "stt failed " + CAN_PROV}},
            {"type": "error", "error": {"code": "invalid_request_error", "type": "canary_type", "message": "bad request " + CAN_PROV,
                                        "event_id": "evt-1"}},
            {"type": "error", "error": {"code": "free text with spaces " + CAN_PROV, "message": "x"}},
        ]
    async def close(self): return None
    async def send(self, *a, **k): return None
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for e in self.events:
            await asyncio.sleep(0.15)
            yield json.dumps(e)
        await asyncio.Event().wait()
async def _connect(self):
    self.openai_ws = _FakeOpenAI(); return True
async def _setup(self): return None
rs.RealtimeSession._connect_to_openai = _connect
rs.RealtimeSession._setup_session = _setup
class _Boom:
    def __getattr__(self, n): return self
    async def create(self, *a, **k): raise RuntimeError("openai failure " + CAN_EXC)
class _BoomClient:
    chat = type("c", (), {"completions": _Boom()})()
    audio = type("a", (), {"transcriptions": _Boom()})()
def _client(): return _BoomClient()
rs.get_openai_client = _client
import app.services.speech_to_text as stt
import app.services.ai_pipeline as aip
stt.get_openai_client = _client
aip.get_openai_client = _client

from app.main import app
from fastapi import Request

# HARNESS ONLY (isolated test processes): switches and probes. Not part of the application.
@app.get("/__test/boom")
async def _t_boom():
    # An UNHANDLED exception: nothing in the app catches it, so Starlette re-raises it and uvicorn logs the traceback.
    secret_local = CAN_EXC + "-local-variable"
    raise RuntimeError("unhandled failure containing " + CAN_EXC + " for " + os.environ["PRIV_PROBE_EMAIL"])

@app.post("/__test/mail/{mode}")
async def _t_mail(mode: str):
    MODE["mail"] = mode; return {"ok": True}

@app.post("/__test/probe/{what}")
async def _t_probe(what: str):
    out = {}
    import app.services.email_service as es
    if what == "mail":
        out["otp"] = await es.send_otp_email(os.environ["PRIV_PROBE_EMAIL"], "123456")
        out["reset"] = await es.send_password_reset_email(os.environ["PRIV_PROBE_EMAIL"], "https://x.invalid/reset?oobCode=canary-oob-code-1")
        out["fb"] = await es.send_feedback_notification("Canary Name", os.environ["PRIV_PROBE_EMAIL"], "canary feedback body")
    elif what == "stt":
        try: await stt.transcribe_audio(b"\x01\x02" * 400)
        except Exception as e: out["raised"] = type(e).__name__
    elif what == "pipeline":
        try: await aip.run_ai_pipeline("what is " + CAN_TR, "Mode 1", "probe-" + CAN_PA.replace(" ", "-"))
        except Exception as e: out["raised"] = type(e).__name__
    return out

import uvicorn
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="info")
'''


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:300]) if detail and not ok else ""), flush=True)


async def asql(db, query, *args, fetch=False):
    c = await asyncpg.connect(PG + db, timeout=10)
    try:
        return await (c.fetch(query, *args) if fetch else c.execute(query, *args))
    finally:
        await c.close()


def sql(db, query, *args, fetch=False):
    return asyncio.run(asql(db, query, *args, fetch=fetch))


class Server:
    def __init__(self, name, port, env=None):
        self.name, self.port, self.env = name, port, env or {}
        self.db = "priv_" + name
        self.base = "http://127.0.0.1:%d/api/v1" % port
        self.proc, self.log = None, []

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
            "PRIV_MAILFILE": MAILFILE, "PRIV_AI": AI_TEXT, "PRIV_TR": TRANSCRIPT, "PRIV_PA": PARTIAL, "PRIV_EXC": SUBJECT_EXC,
            "PRIV_BODY": MAIL_BODY_SECRET, "PRIV_PROBE_EMAIL": EMAIL, "PRIV_PROV": PROVIDER_TEXT,
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
                if sql(self.db, "select to_regclass('public.users') is not null and to_regclass('public.mfa_sessions') is not null", fetch=True)[0][0]:
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

    def text(self):
        return "\n".join(list(self.log))

    def lines(self, needle):
        return [l for l in list(self.log) if needle in l]


def http(server, method, path, tk=None, headers=None, form=None, js=None, timeout=30, raw_path=False):
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
    url = (server.base.rsplit("/api/v1", 1)[0] + path) if raw_path else server.base + path
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
    try:
        body = json.loads(body)
    except Exception:
        body = body.decode("utf-8", "replace")[:300]
    return code, body


def tok(uid, email=None, at=AT):
    return token(uid, email or (uid + "@example.invalid"), auth_time=at)


def mails():
    try:
        return [json.loads(l) for l in open(MAILFILE) if l.strip()]
    except FileNotFoundError:
        return []


def last_otp(to):
    for m in reversed(mails()):
        if m.get("to") == to and m.get("otp"):
            return m["otp"]
    return None


async def auser(db, uid, email, mfa=False):
    await asql(db, "INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled, created_at) "
                   "VALUES ($1,'Canary Display Name',$2,true,$3, now() - interval '40 days') ON CONFLICT (uid) DO NOTHING",
               uid, email, mfa)


def user(db, uid, email, mfa=False):
    asyncio.run(auser(db, uid, email, mfa))


async def watch(server, uid, tk, sess=None, seconds=3.0, legacy_token=None, session_id=None):
    q = "uid=%s&mode=Mode%%201&session_id=%s" % (uid, session_id or SESSION_ID)
    if legacy_token:
        q += "&token=" + legacy_token
    url = "ws://127.0.0.1:%d/api/v1/ws/realtime?%s" % (server.port, q)
    hdr = {}
    if tk:
        hdr["Authorization"] = "Bearer " + tk
    if sess:
        hdr["X-MFA-Session"] = sess
    got, closed = [], None
    try:
        ws = await websockets.connect(url, extra_headers=hdr, open_timeout=25, ping_interval=None, max_size=None)
    except Exception as e:
        return {"frames": got, "closed": repr(e)}
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            try:
                got.append(await asyncio.wait_for(ws.recv(), timeout=max(0.1, seconds - (time.monotonic() - t0))))
            except asyncio.TimeoutError:
                break
    except websockets.exceptions.ConnectionClosed as e:
        closed = getattr(e, "code", None) or getattr(getattr(e, "rcvd", None), "code", None)
    finally:
        try:
            await ws.close()
        except Exception:
            pass
    return {"frames": got, "closed": closed}


CANARIES = {
    "email": EMAIL, "email_b": EMAIL_B, "email_reset": EMAIL_RESET, "uid": UID, "uid_b": UID_B, "uid_m": UID_M,
    "bad_token": BADTOKEN, "query_secret": QUERY_SECRET, "ws_session_id": SESSION_ID, "transcript": TRANSCRIPT,
    "partial": PARTIAL, "ai_text": AI_TEXT, "exception_text": SUBJECT_EXC, "mail_body": MAIL_BODY_SECRET,
    "transcript_word": "hunter2zebra", "ai_word": "omega", "display_name": "Canary Display Name", "oob": "canary-oob-code", "provider_text": PROVIDER_TEXT,
    "session_prefix": SESSION_ID[:8], "session_prefix12": SESSION_ID[:12],
}


def leaks(S, extra=()):
    """Names of canaries found in the full captured output of server S."""
    t = S.text()
    found = [n for n, v in CANARIES.items() if v in t]
    found += [("extra:" + x[:12]) for x in extra if x and x in t]
    if re.search(r"eyJ[A-Za-z0-9_\-]{8,}", t):
        found.append("jwt-like")
    if re.search(r"[\w.\-]+@[\w.\-]+\.(invalid|com)\b", t.replace("noreply@example.invalid", "")):
        found.append("any-email")
    return found


def main():
    if os.path.exists(MAILFILE):
        os.remove(MAILFILE)
    base = {"MFA_OTP_HASH_SECRET": HASH_SECRET}
    L = Server("log", 18201, dict(base, MFA_ENFORCEMENT_MODE="log"))
    E = Server("enf", 18202, dict(base, MFA_ENFORCEMENT_MODE="enforce"))
    S = (L, E)
    try:
        for s in S:
            s.start()
        for s in S:
            s.wait_ready()
        run(L, E)
    finally:
        for s in S:
            s.stop()
    n_ok, n_all = sum(results), len(results)
    print("\nlogging privacy: %d passed, %d failed, 0 skipped (of %d)" % (n_ok, n_all - n_ok, n_all), flush=True)
    sys.exit(0 if n_ok == n_all else 1)


def run(L, E):
    user(L.db, UID, EMAIL, mfa=False)
    user(E.db, UID, EMAIL, mfa=False)
    user(L.db, UID_M, EMAIL_B, mfa=True)
    user(E.db, UID_M, EMAIL_B, mfa=True)
    T = tok(UID, EMAIL)
    TM = tok(UID_M, EMAIL_B)
    extra = [T, TM, BADTOKEN]

    # ---------------- startup ----------------
    check("P0 startup: sanitizer installed and server ready lines present",
          bool(L.lines("Server ready")) or bool(L.lines("Application startup complete")), L.log[-5:])
    check("P0b startup output has no sensitive values", not leaks(L), leaks(L))

    # ---------------- OTP send / verify (success + failure) ----------------
    c, b = http(L, "POST", "/auth/send-otp", tk=T, form={"email": EMAIL})
    check("P1 send-otp returns 200 (real email_service, fake transport)", c == 200, (c, b))
    otp = last_otp(EMAIL)
    check("P1b the OTP was 'sent' (captured at the transport) and is 6 digits", bool(otp and re.fullmatch(r"\d{6}", otp)), otp)
    wrong = "000000" if otp != "000000" else "111111"
    c, _ = http(L, "POST", "/auth/verify-otp", form={"email": EMAIL, "otp": wrong})
    check("P2 wrong OTP -> 400", c == 400, c)
    c, _ = http(L, "POST", "/auth/verify-otp", form={"email": EMAIL, "otp": otp})
    check("P3 correct OTP -> 200", c == 200, c)
    check("P3b operational line kept: [SENDGRID-SUCCESS] OTP email sent", bool(L.lines("[SENDGRID-SUCCESS] OTP email sent")))
    extra += [otp, wrong]
    c, _ = http(L, "POST", "/auth/send-otp", tk=T, form={"identifier": "+15550100999"})
    check("P4 non-email identifier path logs no identifier", c == 200 and not L.lines("5550100999"), c)
    check("P4b non-email path still leaves a diagnostic line", bool(L.lines("[OTP-SMS-LOG]")))
    for mode, label in (("reject", "SendGrid rejects"), ("raise", "transport raises")):
        http(L, "POST", "/__test/mail/" + mode, raw_path=True)
        c, _ = http(L, "POST", "/auth/send-otp", tk=T, form={"email": EMAIL})
        check("P5 %s -> request fails cleanly (503/429)" % label, c in (503, 429), c)
    http(L, "POST", "/__test/mail/ok", raw_path=True)
    check("P5b mail failure diagnostics kept (status / exception class)",
          bool(L.lines("[SENDGRID-ERROR] OTP email rejected: status 401")) or bool(L.lines("[SENDGRID-FATAL] OTP email error: RuntimeError")),
          [l for l in L.log if "SENDGRID" in l][:4])
    c, _ = http(L, "POST", "/__test/probe/mail", raw_path=True)
    check("P5c email_service probe (OTP, reset, feedback) ran", c == 200, c)

    # ---------------- password reset (unknown account / Firebase error) ----------------
    c, _ = http(L, "POST", "/auth/send-password-reset", form={"email": EMAIL_RESET})
    check("P6 password reset for unknown / unavailable account answers without leaking", c in (200, 500), c)

    # ---------------- Firebase auth errors ----------------
    for path in ("/auth/session-status", "/user/" + UID):
        c, _ = http(L, "GET", path, tk=BADTOKEN)
        check("P7 bad token on %s -> 401" % path.split("/")[1], c == 401, c)
    c, _ = http(L, "GET", "/user/" + UID + "?note=" + QUERY_SECRET + "&x=1", tk=T)
    check("P7b profile read with canary query string answers", c == 200, c)
    check("P7c operational line kept: 'Firebase token error: <ExceptionClass>'", any(re.search(r"Firebase token error: \w+", l) for l in L.log))

    # ---------------- MFA challenge / verify / enforcement ----------------
    c, b = http(L, "POST", "/auth/mfa/challenge", tk=TM)
    check("P8 mfa challenge sent", c == 200 and isinstance(b, dict) and b.get("challenge_id"), (c, b))
    cid = b.get("challenge_id") if isinstance(b, dict) else None
    mo = last_otp(EMAIL_B)
    c, vb = http(L, "POST", "/auth/mfa/verify", tk=TM, form={"challenge_id": cid or "x", "otp": mo or "000000"})
    check("P9 mfa verify succeeds and returns a session token", c == 200 and isinstance(vb, dict) and vb.get("mfa_session_token"), (c, str(vb)[:100]))
    raw = (vb or {}).get("mfa_session_token") if isinstance(vb, dict) else None
    extra += [raw, cid, mo]
    c, b = http(L, "POST", "/auth/mfa/challenge", tk=TM)
    c2, _ = http(L, "POST", "/auth/mfa/verify", tk=TM, form={"challenge_id": (b or {}).get("challenge_id", "x"), "otp": "999999" if mo != "999999" else "888888"})
    check("P9b wrong MFA code -> 400", c2 == 400, c2)
    # log mode: a protected route without a session is counted, not blocked
    c, _ = http(L, "POST", "/auth/update-session", tk=TM, form={"session_id": "s-" + uuid.uuid4().hex[:8]})
    check("P10 log mode: no session -> request still served", c == 200, c)
    c, _ = http(L, "POST", "/auth/update-session", tk=TM, headers={"X-MFA-Session": raw}, form={"session_id": "s-" + uuid.uuid4().hex[:8]})
    check("P10b log mode with a valid session -> served", c == 200, c)
    check("P10c [MFA-ENFORCE] counters present in log mode", bool(L.lines("[MFA-ENFORCE]")))
    # enforce mode: blocked
    c, _ = http(E, "POST", "/auth/update-session", tk=TM, form={"session_id": "s-" + uuid.uuid4().hex[:8]})
    check("P11 enforce mode: no session -> 401 MFA_SESSION_REQUIRED", c == 401, c)
    check("P11b [MFA-ENFORCE] blocked counter kept", bool(E.lines("[MFA-ENFORCE]")) and any("blocked" in l for l in E.lines("[MFA-ENFORCE]")))
    c, _ = http(L, "POST", "/auth/mfa/logout", tk=TM, headers={"X-MFA-Session": raw})
    check("P11c mfa logout ok", c == 200, c)

    # ---------------- WebSocket connect / error ----------------
    r = asyncio.run(watch(L, UID, T, seconds=3.5))
    check("P12 WS (header token) connects and relays voice events", any("connection_success" in f for f in r["frames"]), r)
    r = asyncio.run(watch(L, UID, None, legacy_token=T, seconds=1.5))
    check("P13 WS legacy ?token= still connects", any("connection_success" in f for f in r["frames"]), r)
    check("P13b legacy marker kept without the token", any("LEGACY" in l for l in L.log))
    r = asyncio.run(watch(L, UID, BADTOKEN, seconds=1.5))
    check("P14 WS bad token rejected 1008", r["closed"] == 1008, r)
    r = asyncio.run(watch(L, UID_B, T, seconds=1.5))
    check("P14b WS uid mismatch rejected 1008", r["closed"] == 1008, r)
    check("P14c WS rejection diagnostics kept", any("[WS] Auth" in l for l in L.log))
    r = asyncio.run(watch(E, UID_M, TM, seconds=1.5))
    check("P15 WS enforce without session refused (1008)", r["closed"] == 1008, r)

    # ---------------- voice / AI companion logging ----------------
    check("P16 voice: STT COMPLETE logged with length only", any("STT COMPLETE" in l and "transcript_len=" in l for l in L.log), [l for l in L.log if "STT" in l][:3])
    check("P16b voice: AI RESPONSE TEXT logged with length only", any("AI RESPONSE TEXT" in l and "response_len=" in l for l in L.log))
    check("P16c voice: LLM TEXT DONE logged with length only", any("LLM TEXT DONE" in l and "text_len=" in l for l in L.log))
    c, _ = http(L, "POST", "/__test/probe/stt", raw_path=True)
    check("P17 STT exception path handled (probe)", c == 200, c)
    c, _ = http(L, "POST", "/__test/probe/pipeline", raw_path=True)
    check("P17c AI pipeline exception path handled (probe)", c in (200, 500), c)
    check("P17d pipeline error diagnostics kept (frames / class)", any("[TRACE]" in l or "AI PIPELINE ERROR" in l for l in L.log), [l for l in L.log if "PIPELINE" in l][:3])

    # ---------------- provider errors (OpenAI error events / Whisper failures) ----------------
    check("P17e OPENAI ERROR keeps the error code and type, with only the message LENGTH",
          any("OPENAI ERROR" in l and "code=invalid_request_error" in l and "type=canary_type" in l and re.search(r"msg_len=\d+", l) for l in L.log),
          [l for l in L.log if "OPENAI ERROR" in l][:3])
    check("P17f a free-text 'code' is never printed (shown as ?)", any("OPENAI ERROR" in l and "code=?" in l for l in L.log), [l for l in L.log if "OPENAI ERROR" in l][:3])
    check("P17g STT FAILED keeps code/type and message length only",
          any("STT FAILED" in l and "code=stt_canary_code" in l and re.search(r"msg_len=\d+", l) for l in L.log), [l for l in L.log if "STT FAILED" in l][:3])
    wsr = asyncio.run(watch(L, UID, T, seconds=4.0))
    got = " ".join(wsr["frames"])
    check("P17h provider error events are still forwarded to the client unchanged (behavior preserved)",
          "invalid_request_error" in got and "bad request" in got, got[:200])

    # ---------------- telemetry identifiers ----------------
    tel = L.lines("[TELEMETRY]")
    check("P17i telemetry lines are still emitted (event names, counters, durations)", bool(tel) and all('"telemetry"' in l and '"session_age_s"' in l for l in tel), tel[:2])
    recs = []
    for l in tel:
        try:
            recs.append(json.loads(l.split("[TELEMETRY] ", 1)[1]))
        except Exception:
            pass
    check("P17j telemetry session_id / turn_id are HMAC correlation ids (s-xxxxxxxxxx), never a raw prefix",
          bool(recs) and all(re.fullmatch(r"s-[0-9a-f]{10}", r.get("session_id", "")) and re.fullmatch(r"s-[0-9a-f]{10}-\d+", r.get("turn_id", "")) for r in recs),
          recs[:1])
    check("P17k the same session gives the same id in the WS connect line and in telemetry (still correlatable)",
          bool(recs) and any(r["session_id"] in l for r in recs[:1] for l in L.lines("[WS] Realtime connection")), (recs[:1], L.lines("[WS] Realtime connection")[:1]))

    # ---------------- unhandled exceptions (uvicorn / Starlette traceback logging) ----------------
    n_before = len(L.log)
    c, _ = http(L, "GET", "/__test/boom", raw_path=True)
    check("P17l an unhandled exception still yields HTTP 500 (response behavior unchanged)", c == 500, c)
    time.sleep(0.5)
    new = L.log[n_before:]
    blob = chr(10).join(new)
    check("P17m the unhandled-exception record is still logged (Exception in ASGI application)", "Exception in ASGI application" in blob, new[-6:])
    check("P17n diagnostics kept: exception class, file, line and function", bool(re.search(r"\[TRACE\] RuntimeError at .*\.py:\d+:_t_boom|\[TRACE\] RuntimeError at .*\.py:\d+:", blob)), new[-6:])
    check("P17o neither the exception text nor the e-mail/canaries nor source lines appear",
          SUBJECT_EXC not in blob and EMAIL not in blob and "unhandled failure containing" not in blob and "secret_local" not in blob
          and "raise RuntimeError" not in blob and "Traceback (most recent call last)" not in blob, [l for l in new if SUBJECT_EXC in l or "Traceback" in l][:3])
    check("P17p the server keeps serving after the unhandled exception", http(L, "GET", "/faq")[0] == 200)

    # ---------------- DELETE / forbidden ----------------
    c, _ = http(L, "DELETE", "/user/" + UID_B, tk=T)
    check("P18 forbidden delete of another uid -> 403", c == 403, c)
    check("P18b forbidden delete diagnostic kept", any("[DELETE] Forbidden" in l for l in L.log))

    # ---------------- access lines ----------------
    acc = L.lines("HTTP/1.1")
    check("P19 uvicorn access lines are present with status codes", len(acc) > 5 and any('" 200' in l for l in acc) and any('" 401' in l for l in acc), acc[:3])
    check("P19b access lines: user id path masked (/api/v1/user/u-xxxxxxxxxx)", any(re.search(r"/api/v1/user/u-[0-9a-f]{10}", l) for l in acc), acc[:3])
    check("P19c access lines: query values redacted (note=***)", any("note=***" in l for l in acc), [l for l in acc if "note=" in l][:2])
    wsl = [l for l in L.log if "/ws/realtime" in l]
    check("P19d WebSocket lines: query values redacted and endpoint kept", bool(wsl) and all(QUERY_SECRET not in l and UID not in l for l in wsl) and any("uid=***" in l for l in wsl), wsl[:3])

    # ---------------- the global scans ----------------
    for name, s in (("log-mode backend", L), ("enforce-mode backend", E)):
        f = leaks(s, extra)
        check("P20 %s: no canary e-mail / UID / OTP / token / transcript / AI text / exception text in ANY captured line" % name, not f, f)
    check("P21 masked correlation ids ARE present (users can still be followed)", any(re.search(r"\bu-[0-9a-f]{10}\b", l) for l in L.log))
    check("P22 'uid=' never followed by a raw 12+ char id", not any(re.search(r"uid=(?!u-|\*\*\*)[A-Za-z0-9_\-]{12,}", l) for l in L.log + E.log))


if __name__ == "__main__":
    main()
