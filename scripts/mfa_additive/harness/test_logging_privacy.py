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
SRC_BASE = "/srcE/backend"      # the tree WITHOUT the privacy patch (additive + enforcement), for the equivalence run
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
# Step 8K: provider ids and model-generated tool values. Every id starts with MARK, so even the old "first 12/16 characters"
# style of partial logging would put MARK in a log line and be caught.
MARK = "zq9cn8k"
CID = "5e1f4a7b"
TOOL_WORDS = "canary say my name is zed " + CID
MODE_TEXT = "canary-mode-text-" + CID
ATTR_TEXT = "canary-attr-text-" + CID
LANG_TEXT = "canary-lang-text-" + CID
TOOL_NAME_TEXT = "canary_unknown_tool_" + CID
SINGLE_WORD = "canarysingleword" + CID

LAUNCHER = r'''
import os, sys, asyncio, json
sys.path.insert(0, os.environ["SRC_BACKEND"])
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
    """Replays provider events (with canary ids/values) and RECORDS everything the backend sends back, for the equivalence run."""
    def __init__(self):
        M, C = "zq9cn8k", os.environ["PRIV_CID"]
        self.wire = os.environ.get("PRIV_WIRE")
        ids = {k: M + k + C for k in ("sess", "commit", "created", "resp", "fc", "msg", "call1", "call2", "call3", "call5")}
        self.events = [
            {"type": "session.created", "session": {"id": ids["sess"]}},
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
            # ---- 8K: provider ids -------------------------------------------------------------------------------
            {"type": "input_audio_buffer.committed", "item_id": ids["commit"]},
            {"type": "conversation.item.created", "item": {"id": ids["msg"], "type": "message", "role": "assistant"}},
            {"type": "response.created", "response": {"id": ids["resp"]}},
            {"type": "response.output_item.added", "item": {"id": ids["fc"], "type": "function_call", "name": "canary_tool_" + C}},
            {"type": "response.output_item.added", "item": {"id": ids["msg"], "type": "message"}},
            # ---- 8K: language path (single word + LLM guess that differs from the locked language) ----------------
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "canarysingleword" + C},
            # ---- 8K: tool calls ---------------------------------------------------------------------------------
            {"type": "response.function_call_arguments.done", "call_id": ids["call1"], "name": "query_database_for_combo",
             "arguments": json.dumps({"mode": "OriginArc (Balakanda)", "character": "3", "attribute": "45", "detected_language": "English"})},
            {"type": "response.function_call_arguments.done", "call_id": ids["call2"], "name": "query_database_for_combo",
             "arguments": json.dumps({"mode": "canary-mode-text-" + C, "character": "canary say my name is zed " + C,
                                      "attribute": "canary-attr-text-" + C, "detected_language": "canary-lang-text-" + C})},
            {"type": "response.function_call_arguments.done", "call_id": ids["call3"], "name": "canary_unknown_tool_" + C,
             "arguments": json.dumps({"x": "canary say my name is zed " + C})},
            {"type": "response.function_call_arguments.done", "call_id": ids["call5"] + "failsend", "name": "query_database_for_combo",
             "arguments": json.dumps({"mode": "OriginArc (Balakanda)", "character": "5", "attribute": "50", "detected_language": "English"})},
        ]
        if os.environ.get("PRIV_MALFORMED_CALL") == "1":
            ok_args = json.dumps({"mode": "OriginArc (Balakanda)", "character": "3", "attribute": "45", "detected_language": "English"})
            self.events += [
                {"type": "response.function_call_arguments.done", "call_id": ["zq9cn8kcalllist" + C], "name": "query_database_for_combo", "arguments": ok_args},
                {"type": "response.function_call_arguments.done", "call_id": 7777123456, "name": "query_database_for_combo", "arguments": ok_args},
                {"type": "response.function_call_arguments.done", "call_id": None, "name": "query_database_for_combo", "arguments": ok_args},
                {"type": "response.text.done", "text": "malformed-call-final-marker"},
            ]
        if os.environ.get("PRIV_MALFORMED_CALL") != "1" and os.environ.get("PRIV_MALFORMED") == "1":
            # Missing / non-string provider ids. The unpatched code would raise on `sid[:16]` for these; the patched code must not.
            self.events += [
                {"type": "session.created", "session": {"id": 12345}},
                {"type": "response.created", "response": {"id": {"x": 1}}},
                {"type": "conversation.item.created", "item": {"id": ["a"], "type": "message", "role": "user"}},
                {"type": "input_audio_buffer.committed"},
                {"type": "response.output_item.added", "item": {"type": "function_call", "name": 12345}},
                {"type": "response.output_item.added", "item": {"id": None, "type": "message"}},
                {"type": "response.text.done", "text": "malformed-run-final-marker"},
            ]
    async def close(self): return None
    async def send(self, data=None, *a, **k):
        if self.wire and data is not None:
            with open(self.wire, "a") as f: f.write(data + "\n")
        if isinstance(data, str) and "function_call_output" in data and "failsend" in data:
            raise RuntimeError("send failed " + CAN_EXC)
        return None
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
class _Msg:
    def __init__(self, c): self.content = c
class _Choice:
    def __init__(self, c): self.message = _Msg(c)
class _LLMResp:
    def __init__(self, c): self.choices = [_Choice(c)]
class _LLMCompletions:
    async def create(self, *a, **k):
        msgs = k.get('messages') or []
        system = (msgs[0].get('content') if msgs else '') or ''
        user = (msgs[-1].get('content') if msgs else '') or ''
        if system.startswith('Identify the language'):
            return _LLMResp('Hindi' if 'canarysingleword' in user else 'Tamil')
        return _LLMResp('NONE')     # number extractor: not a number
class _LLMClient:
    chat = type('c', (), {'completions': _LLMCompletions()})()
    audio = type('a', (), {'transcriptions': _Boom()})()
def _llm_client(): return _LLMClient()
rs.get_openai_client = _llm_client
# The app picks a random 'flavor' phrase for card-combo answers; make it deterministic so two trees can be compared byte for byte.
rs._pick_combo_message = lambda *a, **k: 'Fixed test flavor.'
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
    def __init__(self, name, port, env=None, src=SRC):
        self.name, self.port, self.env, self.src = name, port, env or {}, src
        self.wire = '/tmp/priv_wire_%s.jsonl' % name
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
            "PRIV_CID": CID, "PRIV_WIRE": self.wire, "SRC_BACKEND": self.src,
        }
        e.update(self.env)
        if os.path.exists(self.wire):
            os.remove(self.wire)
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", LAUNCHER], env=e, cwd=self.src,
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
    B = Server("base", 18203, dict(base, MFA_ENFORCEMENT_MODE="log"), src=SRC_BASE)      # no privacy patch
    M = Server("mal", 18204, dict(base, MFA_ENFORCEMENT_MODE="log", PRIV_MALFORMED="1"))   # privacy tree, malformed provider ids
    MCP = Server("mcallp", 18205, dict(base, MFA_ENFORCEMENT_MODE="log", PRIV_MALFORMED_CALL="1"))                    # privacy tree
    MCB = Server("mcallb", 18206, dict(base, MFA_ENFORCEMENT_MODE="log", PRIV_MALFORMED_CALL="1"), src=SRC_BASE)      # unpatched tree
    S = (L, E, B, M, MCP, MCB)
    try:
        for s in S:
            s.start()
        for s in S:
            s.wait_ready()
        run(L, E)
        run_8k(L, E, B, M)
        run_8kd(MCP, MCB)
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



def wire(server):
    """Everything the backend sent to the (fake) OpenAI socket, as canonical JSON strings, order-insensitive."""
    try:
        return sorted(json.dumps(json.loads(l), sort_keys=True) for l in open(server.wire) if l.strip())
    except FileNotFoundError:
        return []


def frames(r):
    out = []
    for f in r["frames"]:
        try:
            out.append(json.dumps(json.loads(f), sort_keys=True))
        except Exception:
            out.append(f if isinstance(f, str) else repr(f))
    return sorted(out)


NEW = (MARK, CID, TOOL_WORDS, MODE_TEXT, ATTR_TEXT, LANG_TEXT, TOOL_NAME_TEXT, SINGLE_WORD)


def run_8k(L, E, B, M):
    """Step 8K: provider ids, raw tool arguments, model-generated strings; and proof that behavior is unchanged."""
    for srv in (B, M):
        user(srv.db, UID, EMAIL, mfa=False)
    T = tok(UID, EMAIL)
    sid = "8k-session-" + CID
    # Full-length runs against the patched server (L), the unpatched baseline (B) and the malformed-ids server (M).
    # L already served earlier test connections that replayed (parts of) the same events: compare only THIS run's traffic.
    time.sleep(1.0)
    for srv in (L, B, M):
        if os.path.exists(srv.wire):
            os.remove(srv.wire)
    rL = asyncio.run(watch(L, UID, T, seconds=9.0, session_id=sid))
    rB = asyncio.run(watch(B, UID, T, seconds=9.0, session_id=sid))
    rM = asyncio.run(watch(M, UID, T, seconds=9.0, session_id=sid))
    txt = L.text()
    lines = list(L.log)

    # ---- provider ids: no raw or partial id anywhere ---------------------------------------------------------------
    check("K1 provider ids (session/item/response/call): neither the id nor its first 12-16 characters appear in any log line",
          MARK not in txt and CID not in txt, [l[:90] for l in lines if MARK in l or CID in l][:2])
    check("K1b masked provider session id is logged (OpenAI session created | session=o-<10hex>)",
          any(re.search(r"OpenAI session created \| session=o-[0-9a-f]{10}$", l) for l in lines), [l for l in lines if "session created" in l][:2])
    check("K1c masked item id on committed / created / composing lines",
          any("audio buffer committed | item=o-" in l for l in lines) and any("CONV ITEM CREATED" in l and re.search(r"item=o-[0-9a-f]{10}", l) for l in lines)
          and any("LLM COMPOSING" in l and re.search(r"item=o-[0-9a-f]{10}", l) for l in lines))
    check("K1d masked response id and masked call id",
          any(re.search(r"response=o-[0-9a-f]{10}", l) for l in lines) and any(re.search(r"fn=query_database_for_combo \| call=o-[0-9a-f]{10}", l) for l in lines))
    created = re.findall(r"CONV ITEM CREATED.*item=(o-[0-9a-f]{10})", txt)
    composing = re.findall(r"LLM COMPOSING.*item=(o-[0-9a-f]{10})", txt)
    check("K1e the same provider id gives the same masked id on different lines (correlation still works)",
          bool(created) and bool(composing) and created[0] == composing[0], (created[:1], composing[:1]))
    check("K1f an id that differs gives a different masked id", len(set(re.findall(r"item=(o-[0-9a-f]{10})", txt))) >= 3,
          set(re.findall(r"item=(o-[0-9a-f]{10})", txt)))

    # ---- raw function-call JSON / tool arguments -------------------------------------------------------------------
    check("K2 raw function-call JSON and argument values never appear (canary words, mode, attribute, language, tool name)",
          not any(x in txt for x in (TOOL_WORDS, "canary say my name", MODE_TEXT, ATTR_TEXT, LANG_TEXT, TOOL_NAME_TEXT, SINGLE_WORD, "canary-mode-text", "canary-attr-text")),
          [l[:100] for l in lines if "canary say" in l or "canary-" in l][:2])
    check("K2b TOOL ARGS READY keeps the allow-listed function name and only the length of the raw arguments",
          any(re.search(r"TOOL ARGS READY.*fn=query_database_for_combo \| args_len=\d+$", l) for l in lines)
          and any(re.search(r"TOOL ARGS READY.*fn=other \| args_len=\d+$", l) for l in lines), [l for l in lines if "TOOL ARGS READY" in l][:3])
    check("K2c TOOL ARGS RAW: known mode shown, digits flagged; unexpected mode is 'other'; free text flagged 'text' with length only",
          any(re.search(r"TOOL ARGS RAW.*mode=OriginArc \(Balakanda\) \| char_len=1 char_kind=digits \| attribute_len=2 attribute_kind=digits", l) for l in lines)
          and any(re.search(r"TOOL ARGS RAW.*mode=other \| char_len=\d+ char_kind=text \| attribute_len=\d+ attribute_kind=text", l) for l in lines),
          [l for l in lines if "TOOL ARGS RAW" in l][:3])
    check("K2d NUMBER PARSE FAIL logs lengths and kinds only",
          any(re.search(r"NUMBER PARSE FAIL.*could not extract card numbers \| char_len=\d+ char_kind=text \| attribute_len=\d+ attribute_kind=text", l) for l in lines),
          [l for l in lines if "NUMBER PARSE" in l][:2])
    check("K2e DB QUERY START keeps the (digit-only) card numbers and a safe mode",
          any(re.search(r"DB QUERY START.*mode=OriginArc \(Balakanda\) AND char='3' AND attribute='45'", l) for l in lines), [l for l in lines if "DB QUERY" in l][:2])
    check("K2f unknown tool name is reported as 'other'", any("TOOL UNKNOWN" in l and "unknown tool other" in l for l in lines))
    check("K2g language values: allow-listed name for 'English', 'other' for arbitrary text",
          any("LANG FROM LLM" in l and "audio-detected → English" in l for l in lines) and any("LANG FROM LLM" in l and "audio-detected → other" in l for l in lines),
          [l for l in lines if "LANG FROM" in l][:3])
    check("K2h LANG SWITCH BLOCKED no longer prints the transcript (length and allow-listed languages only)",
          any(re.search(r"LANG SWITCH BLOCKED.*utterance \(len=\d+\) guessed Hindi — keeping locked language Tamil", l) for l in lines) and SINGLE_WORD not in txt,
          [l for l in lines if "LANG SWITCH" in l][:2])
    check("K2i send failures and error paths log the exception class only (no exception text)",
          any("TOOL SEND ERROR" in l and "RuntimeError" in l for l in lines) and SUBJECT_EXC not in txt and "send failed" not in txt,
          [l[:100] for l in lines if "TOOL SEND ERROR" in l][:2])

    # ---- malformed / missing identifiers ---------------------------------------------------------------------------
    mt = M.text()
    check("K3 malformed ids (int, dict, list, None, missing) do not break the relay: the replay ran to its final event",
          any("malformed-run-final-marker" in f for f in rM["frames"]) and "OPENAI RELAY ERR" not in mt, [l[:100] for l in M.log if "RELAY ERR" in l][:2])
    check("K3b malformed ids are logged as 'invalid' or 'none', never as their content",
          "session=invalid" in mt and "item=invalid" in mt and "response=invalid" in mt and "item=none" in mt, [l[:100] for l in M.log if "session created" in l][:2])

    # ---- behavior equivalence: unpatched baseline (B) vs patched (L) -----------------------------------------------
    wl, wb = wire(L), wire(B)
    check("K4 messages sent to OpenAI (function_call_output, response.create, session.update, ...) are IDENTICAL: %d vs %d" % (len(wl), len(wb)),
          wl == wb and len(wl) > 0, (len(wl), len(wb), [x for x in wl if x not in wb][:1], [x for x in wb if x not in wl][:1]))
    check("K4b the tool call outputs (the 'output' of each function_call_output) are identical",
          sorted(x for x in wl if "function_call_output" in x) == sorted(x for x in wb if "function_call_output" in x) and any("function_call_output" in x for x in wl))
    check("K4c response.create is sent the same number of times",
          sum('"response.create"' in x for x in wl) == sum('"response.create"' in x for x in wb) and sum('"response.create"' in x for x in wl) >= 1,
          (sum('"response.create"' in x for x in wl), sum('"response.create"' in x for x in wb)))
    if wl != wb:      # diagnostics for a failed comparison (synthetic data only)
        import difflib
        for x in wl:
            if x not in wb:
                close = difflib.get_close_matches(x, wb, n=1, cutoff=0.5)
                print("  [diag] patched-only message vs closest baseline message (differing pieces):")
                if close:
                    sm = difflib.SequenceMatcher(None, x, close[0])
                    for op, i1, i2, j1, j2 in sm.get_opcodes():
                        if op != "equal":
                            print("    patched[%r]  baseline[%r]" % (x[max(0, i1 - 15):i2 + 15][:160], close[0][max(0, j1 - 15):j2 + 15][:160]))
        for nm, srv in (("baseline", B), ("patched", L)):
            print("  [diag] %s server, lifecycle/error lines:" % nm)
            for l in srv.log:
                if any(t in l for t in ("ERR", "ERROR", "RELAY", "CLOSED", "TOOL", "DB ", "SESSION")):
                    print("    " + l[:170])
    fl, fb = frames(rL), frames(rB)
    check("K4d messages delivered to the app over the WebSocket are IDENTICAL: %d vs %d" % (len(fl), len(fb)), fl == fb and len(fl) > 0,
          (len(fl), len(fb), [x[:80] for x in fl if x not in fb][:1], [x[:80] for x in fb if x not in fl][:1]))
    check("K4e the unpatched baseline really does log the raw values (the comparison is sensitive)",
          MARK in B.text() and TOOL_WORDS in B.text(), "baseline log missing canaries")
    check("K4f close behaviour is the same (both connected, same close outcome)",
          any("connection_success" in f for f in rL["frames"]) and any("connection_success" in f for f in rB["frames"]) and rL["closed"] == rB["closed"], (rL["closed"], rB["closed"]))
    for name, srv in (("patched", L), ("malformed-run", M)):
        check("K5 %s server: no raw canary (anywhere) in any captured line" % name, not leaks(srv, extra=NEW), leaks(srv, extra=NEW))


def db_state(server):
    """Row counts and the user's session columns: what a request could have changed in the database."""
    out = {}
    for tbl in ("mfa_sessions", "mfa_login_challenges", "user_otps", "otp_send_rate_limits", "user_feedback"):
        out[tbl] = sql(server.db, "select count(*) from %s" % tbl, fetch=True)[0][0]
    row = sql(server.db, "select count(*), max(session_id), bool_or(mfa_enabled), bool_or(email_verified) from users", fetch=True)[0]
    out["users"] = tuple(row)
    return out


def wire_outputs(server):
    """The call_id of every function_call_output the backend sent to the (fake) OpenAI socket, with its original JSON type."""
    res, texts = [], []          # texts[i] belongs to res[i]
    if os.path.exists(server.wire):
        for l in open(server.wire):
            try:
                m = json.loads(l)
            except Exception:
                continue
            if m.get("item", {}).get("type") == "function_call_output":
                res.append(m["item"]["call_id"])
                texts.append(m["item"]["output"])
    return res, texts


def run_8kd(P, Bm):
    """Step 8K-D: tool calls whose OpenAI call_id is NOT a string (list, integer, null), on the patched and the unpatched tree.

    Documents the one behavior difference found in the 8K-C review: the old log line did call_id[:12], which raised for an integer
    or null and ended the relay; the new log line cannot raise. Observed, not assumed."""
    for srv in (P, Bm):
        user(srv.db, UID, EMAIL, mfa=False)
    T = tok(UID, EMAIL)
    sid = "8kd-session-" + CID
    time.sleep(1.0)
    for srv in (P, Bm):
        if os.path.exists(srv.wire):
            os.remove(srv.wire)
    rP = asyncio.run(watch(P, UID, T, seconds=9.0, session_id=sid))
    rB = asyncio.run(watch(Bm, UID, T, seconds=9.0, session_id=sid))
    lp, lb = list(P.log), list(Bm.log)
    tp, tb = P.text(), Bm.text()
    op, otp = wire_outputs(P)
    ob, otb = wire_outputs(Bm)
    kinds = lambda xs: sorted(type(x).__name__ for x in xs)
    # Every server also plays the standard replay events (well-formed calls with string call_ids); look only at the malformed ones.
    bad_p = [(c, t) for c, t in zip(op, otp) if not isinstance(c, str)]
    bad_b = [(c, t) for c, t in zip(ob, otb) if not isinstance(c, str)]
    good_p = [t for c, t in zip(op, otp) if c == "zq9cn8kcall1" + CID]        # the standard well-formed lookup of cards 3 / 45
    print("  [observed] patched: function_call_output call_id types sent to OpenAI (all): %s" % kinds(op))
    print("  [observed] unpatched: function_call_output call_id types sent to OpenAI (all): %s" % kinds(ob))
    print("  [observed] patched: DB QUERY START=%d, NUMBER PARSE FAIL=%d, relay errors=%d" %
          (sum("DB QUERY START" in l for l in lp), sum("NUMBER PARSE FAIL" in l for l in lp), sum("RELAY ERR" in l for l in lp)))
    print("  [observed] unpatched: DB QUERY START=%d, relay errors=%d" % (sum("DB QUERY START" in l for l in lb), sum("RELAY ERR" in l for l in lb)))

    # ---- logging: classified safely, nothing raw, no exception from the logging operation -------------------------------
    calls = [l for l in lp if "TOOL CALL RECEIVED" in l]
    check("K6 non-string call_id values are logged only as 'invalid' (list, integer) and 'none' (null); well-formed ones as masked ids",
          sum("call=invalid" in l for l in calls) == 2 and sum("call=none" in l for l in calls) == 1
          and sum(bool(re.search(r"call=o-[0-9a-f]{10}$", l)) for l in calls) == len(calls) - 3, [c[-40:] for c in calls])
    check("K6b the raw call_id (list content, integer, partial values) appears in no log line of the patched server",
          "7777123456" not in tp and MARK not in tp and "zq9cn8kcalllist" not in tp, [l[:100] for l in lp if "7777123456" in l or MARK in l][:2])
    check("K6c no exception escaped from the logging code: no relay error, and the replay ran to its final event",
          "RELAY ERR" not in tp and "RELAY ERROR" not in tp and any("malformed-call-final-marker" in f for f in rP["frames"]),
          [l[:100] for l in lp if "ERR" in l][:2])

    # ---- what the relay does with them (explicitly observed) -------------------------------------------------------------
    std_lookups = sum("DB QUERY START" in l for l in lb) - 1              # unpatched tree: standard lookups + the one list-id call it processed
    check("K7 the game lookup is attempted once per malformed tool call (%d standard + 3 malformed = %d DB QUERY START) and no other tool is run"
          % (std_lookups, std_lookups + 3),
          sum("DB QUERY START" in l for l in lp) == std_lookups + 3 and sum("TOOL UNKNOWN" in l for l in lp) == sum("TOOL UNKNOWN" in l for l in lb) == 1,
          (sum("DB QUERY START" in l for l in lp), std_lookups, sum("TOOL UNKNOWN" in l for l in lp)))
    check("K7b a function_call_output is produced for each malformed call and echoes the malformed id back to OpenAI (list, int, null): documented behavior",
          sorted(type(c).__name__ for c, _ in bad_p) == ["NoneType", "int", "list"], kinds([c for c, _ in bad_p]))
    check("K7c the tool result inside those 3 outputs is exactly the result of the well-formed lookup of the same cards (identical output text)",
          len(bad_p) == 3 and len(good_p) == 1 and all(t == good_p[0] for _, t in bad_p), (len(bad_p), len(good_p)))

    # ---- no state change beyond the normal connection ------------------------------------------------------------------------
    sp, sb = db_state(P), db_state(Bm)
    check("K8 database state after the malformed tool calls: no MFA, OTP or feedback rows; the user row only has the normal session_id",
          sp["mfa_sessions"] == sp["mfa_login_challenges"] == sp["user_otps"] == sp["otp_send_rate_limits"] == sp["user_feedback"] == 0
          and sp["users"][0] == 1 and sp["users"][1] == sid and sp["users"][2] is False, sp)
    check("K8b the patched and the unpatched trees leave identical database state", sp == sb, (sp, sb))

    # ---- the unpatched tree's old behavior, for the record ---------------------------------------------------------------------
    check("K9 unpatched tree: a log line raised for the integer call_id and ended the relay (the old behavior this test documents)",
          any("RELAY ERR" in l for l in lb) and not any("malformed-call-final-marker" in f for f in rB["frames"]), [l[:100] for l in lb if "ERR" in l][:2])
    check("K9b unpatched tree: only the list-id call (slicing a list does not raise) reached OpenAI, and its raw id was logged",
          sorted(type(c).__name__ for c, _ in bad_b) == ["list"] and "zq9cn8kcalllist" in tb, kinds([c for c, _ in bad_b]))
    check("K10 no raw canary anywhere in the patched server's output", not leaks(P, extra=NEW + ("7777123456",)), leaks(P, extra=NEW + ("7777123456",)))


if __name__ == "__main__":
    main()
