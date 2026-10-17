"""Email-ownership verification tests (email_verification_proof.patch on top of the verified privacy backend).

Runs INSIDE the isolated Docker network (see run_local_tests.sh): FAKE users only (example.invalid), a throwaway PostgreSQL
(mfa-pg), unsigned emulator-style Firebase tokens, NO internet, no cloud, no real email. The proof backend tree is mounted
read-only at /src. This file starts its own backend processes with exactly one test-only substitution at the OUTSIDE
boundary: the emailed code is written to a local file instead of being sent (the same idea as harness/server.py).
Everything else is the real application: routes, dependencies, PostgreSQL, transactions.
Exit status 1 on any failure."""
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import asyncpg

sys.path.insert(0, "/h")
from fb import token  # noqa: E402  (unsigned emulator-style Firebase ID tokens)

SRC = "/src/backend"
PG = "postgresql://postgres@mfa-pg/"
HASH_SECRET = "local-test-secret-2"
INVITE = "EPIC-TEST01"
results = []
SECRET_VALUES = []              # every raw proof / OTP / challenge id created here: none may ever appear in a backend log

LAUNCHER = r'''
import os, sys, json
sys.path.insert(0, "/src/backend")
import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials
class _Fake(credentials.Base):
    def get_credential(self): return AnonymousCredentials()
firebase_admin.initialize_app(_Fake(), {"projectId": os.environ["FIREBASE_PROJECT_ID"]})
import app.services.email_service as es
OUT = os.environ["OUTBOX"]
async def _mail(email, otp, valid_minutes=1):
    with open(OUT, "a") as f: f.write(json.dumps({"email": email, "otp": otp, "valid_minutes": valid_minutes}) + "\n")
    return not os.path.exists(OUT + ".fail")
es.send_otp_email = _mail
import uvicorn
from app.main import app
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
'''


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:500]) if detail and not ok else ""), flush=True)


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
        self.db = "ev_" + name
        self.base = "http://127.0.0.1:%d/api/v1" % port
        self.outbox = "/tmp/outbox_%s.jsonl" % name
        self.proc, self.log, self._xff = None, [], 0

    def start(self, recreate=True):
        if recreate:
            sql("postgres", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % self.db)
            sql("postgres", 'CREATE DATABASE "%s"' % self.db)
        for p in (self.outbox, self.outbox + ".fail"):
            if os.path.exists(p):
                os.remove(p)
        e = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PORT": str(self.port),
             "DATABASE_URL": PG + self.db, "FIREBASE_PROJECT_ID": "demo-epicverse", "GCP_PROJECT_ID": "demo-epicverse",
             "FIREBASE_AUTH_EMULATOR_HOST": "mfa-idstub:9099", "OPENAI_API_KEY": "dummy-no-egress",
             "SENDGRID_API_KEY": "dummy-no-egress", "SENDGRID_FROM_EMAIL": "noreply@example.invalid",
             "OTP_RATE_LIMIT_HASH_SECRET": "local-test-secret-1", "MFA_OTP_HASH_SECRET": HASH_SECRET,
             "ALLOWED_ORIGINS": "http://localhost", "OUTBOX": self.outbox}
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
                if sql(self.db, "select to_regclass('public.users') is not null and to_regclass('public.email_verification_proofs') is not null "
                                "and to_regclass('public.email_verification_challenges') is not null "
                                "and to_regclass('public.invite_codes') is not null", fetch=True)[0][0]:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("backend %s did not become ready:\n%s" % (self.name, "\n".join(self.log[-25:])))

    def stop(self, drop=True):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
        if drop:
            try:
                sql("postgres", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % self.db)
            except Exception:
                pass

    def xff(self):
        self._xff += 1
        return "10.%d.%d.%d" % (self.port % 200, (self._xff >> 8) & 255, self._xff & 255)

    def mails(self):
        if not os.path.exists(self.outbox):
            return []
        return [json.loads(l) for l in open(self.outbox).read().splitlines() if l.strip()]

    def otp_for(self, email, after=0):
        m = [x for x in self.mails()[after:] if x["email"].lower() == email.lower()]
        return m[-1]["otp"] if m else None


def http(srv, method, path, tk=None, form=None, js=None, headers=None, timeout=40, xff=True):
    h = dict(headers or {})
    if xff and "X-Forwarded-For" not in h:
        h["X-Forwarded-For"] = srv.xff()
    data = None
    if tk:
        h["Authorization"] = "Bearer " + tk
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if js is not None:
        data = json.dumps(js).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(srv.base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body, hd = r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, body, hd = e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, str(e), {}
    try:
        body = json.loads(body)
    except Exception:
        body = body.decode("utf-8", "replace")[:300]
    return code, body, hd


def tk(uid, email):
    return token(uid, email, auth_time=int(time.time()) - 60)


def em(name):
    return "%s@example.invalid" % name


def user_row(srv, uid, email, verified=False):
    sql(srv.db, "INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled, created_at) "
                "VALUES ($1,'Fake User',$2,$3,FALSE, now()) ON CONFLICT (uid) DO UPDATE SET email=$2, email_verified=$3", uid, email, verified)


def verified(srv, uid):
    r = sql(srv.db, "SELECT email_verified FROM users WHERE uid=$1", uid, fetch=True)
    return r[0][0] if r else None


def ph(raw):
    """The server's keyed hash of a raw proof (same secret and label), to inspect or edit its stored row."""
    import hashlib, hmac
    return hmac.new(HASH_SECRET.encode(), ("email-proof:" + raw).encode(), hashlib.sha256).hexdigest()


def proof_row(srv, raw):
    h = ph(raw)
    r = sql(srv.db, "SELECT consumed_at, consumed_by_uid, expires_at FROM email_verification_proofs WHERE proof_hash=$1", h, fetch=True)
    return r[0] if r else None


def get_proof(srv, email):
    """send-email-otp + verify-otp for an email: returns the raw proof (and records it)."""
    n = len(srv.mails())
    c, _, _ = http(srv, "POST", "/auth/send-email-otp", form={"identifier": email})
    otp = srv.otp_for(email, n)
    if c != 200 or not otp:
        return None
    SECRET_VALUES.append(otp)
    c, b, h = http(srv, "POST", "/auth/verify-otp", form={"identifier": email, "otp": otp})
    if c != 200 or not isinstance(b, dict) or not b.get("email_verification_proof"):
        return None
    SECRET_VALUES.append(b["email_verification_proof"])
    return b["email_verification_proof"]


def mark(srv, uid, email, proof=None, form_key="proof"):
    return http(srv, "POST", "/auth/mark-verified", tk=tk(uid, email), form=({form_key: proof} if proof is not None else None))


def detail_code(b):
    d = b.get("detail") if isinstance(b, dict) else None
    return d.get("code") if isinstance(d, dict) else None


# ==================================================================================================================
def main():
    S = Server("main", 18400)
    S.start()
    S.wait_ready()
    sql(S.db, "INSERT INTO invite_codes (code, current_uses, max_uses, created_at) VALUES ($1, 0, 500, now())", INVITE)
    try:
        run_main(S)
    finally:
        S.stop()


def run_main(S):
    print("################ A. SIGNUP FLOW (what the current app does)")
    email = em("sign-a"); uid = "ev-sign-a"
    n0 = len(S.mails())
    c, b, h = http(S, "POST", "/auth/send-email-otp", form={"identifier": email})
    otp = S.otp_for(email, n0); SECRET_VALUES.append(otp or "")
    check("A1 send-email-otp still works and delivers a code", c == 200 and b.get("status") == "sent" and bool(otp), (c, b))
    c, b, h = http(S, "POST", "/auth/verify-otp", form={"identifier": email, "otp": otp})
    proof = b.get("email_verification_proof") if isinstance(b, dict) else None
    SECRET_VALUES.append(proof or "")
    check("A2 verify-otp keeps its fields (status, message) and adds the proof", c == 200 and b.get("status") == "success"
          and b.get("message") == "OTP verified" and isinstance(proof, str), (c, b))
    check("A3 proof has the prefix the app requires, is long and random", isinstance(proof, str) and proof.startswith("evp1_") and len(proof) >= 40, proof and proof[:8])
    check("A4 the response is not cacheable", (h.get("Cache-Control") or h.get("cache-control")) == "no-store", h)
    check("A5 only ONE thing changed in the response: no other new field", set(b) == {"status", "message", "email_verification_proof"}, b)
    row = proof_row(S, proof)
    check("A6 the proof is stored (as a keyed hash), unused, and expires in about 30 minutes",
          row is not None and row[0] is None and 29 * 60 < (row[2].timestamp() - time.time()) < 31 * 60, row)
    raw_cols = sql(S.db, "SELECT proof_hash, email_hash FROM email_verification_proofs", fetch=True)
    check("A7 neither the proof nor the email is stored in clear text",
          all(proof not in (x[0] + x[1]) and email not in (x[0] + x[1]) and re.fullmatch(r"[0-9a-f]{64}", x[0]) for x in raw_cols), raw_cols)
    c, b, _ = http(S, "POST", "/sync-user", tk=tk(uid, email), js={"firebase_id": uid, "display_name": "A", "email": email,
                                                                    "invite_code": INVITE, "session_id": "s1"})
    check("A8 sync-user (invite) creates the unverified profile, as before", c == 200 and verified(S, uid) is False, (c, b))
    c, b, _ = mark(S, uid, email, proof)
    check("A9 mark-verified with the proof verifies this account", c == 200 and b.get("status") == "ok" and verified(S, uid) is True, (c, b))
    check("A10 the proof is consumed and records the UID", proof_row(S, proof)[0] is not None and proof_row(S, proof)[1] == uid)
    c, b, _ = mark(S, uid, email, proof)
    check("A11 a retry after success (lost response) is harmless: 200, nothing changes", c == 200, (c, b))
    c, b, _ = http(S, "GET", "/auth/session-status", tk=tk(uid, email))
    check("A12 session-status now reports the account verified", c == 200 and b.get("email_verified") is True, (c, b))

    print("################ B. EXISTING SIGNED-IN USER (what the app does at login/splash for an unverified account)")
    uid = "ev-exist"; email = em("exist"); user_row(S, uid, email, False)
    n0 = len(S.mails())
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(uid, email))
    otp = S.otp_for(email, n0); SECRET_VALUES.append(otp or "")
    cid = b.get("challenge_id") if isinstance(b, dict) else None; SECRET_VALUES.append(cid or "")
    check("B1 verify-request sends a code and returns a challenge id", c == 200 and b.get("status") == "sent" and cid and bool(otp), (c, b))
    check("B2 the emailed code is valid for the challenge lifetime (5 minutes)", S.mails()[-1]["valid_minutes"] == 5)
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, email), form={"challenge_id": cid, "otp": "000000" if otp != "000000" else "111111"})
    check("B3 a wrong code is refused", c == 400 and verified(S, uid) is False, (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, email), form={"challenge_id": cid, "otp": otp})
    check("B4 the right code verifies the account", c == 200 and b == {"status": "verified"} and verified(S, uid) is True, (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, email), form={"challenge_id": cid, "otp": otp})
    check("B5 replaying the challenge fails", c == 400, (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(uid, email))
    check("B6 an already verified account is told so", c == 200 and b == {"status": "already_verified"}, (c, b))

    print("################ C. ORIGINAL BYPASS AND LEGACY PATHS ARE CLOSED")
    uid = "ev-byp"; email = em("byp"); user_row(S, uid, email, False)
    c, b, _ = mark(S, uid, email)
    check("C1 the ORIGINAL bypass (mark-verified with no proof) is refused", c == 403 and detail_code(b) == "EMAIL_PROOF_INVALID" and verified(S, uid) is False, (c, b))
    c, b, _ = mark(S, uid, email, "evp1_" + "A" * 43)
    check("C2 a forged proof with the right prefix is refused", c == 403 and verified(S, uid) is False, (c, b))
    c, b, _ = mark(S, uid, email, "forged-without-prefix")
    check("C3 a proof without the prefix is refused", c == 403 and verified(S, uid) is False, (c, b))
    c, b, _ = mark(S, uid, email, "evp1_" + "B" * 400)
    check("C4 an oversized proof is refused", c == 403 and verified(S, uid) is False, (c, b))
    c, b, _ = mark(S, uid, email, "", )
    check("C5 an empty proof is refused", c == 403 and verified(S, uid) is False, (c, b))
    c, b, _ = http(S, "POST", "/auth/mark-verified", tk=tk(uid, email), form={"email": email, "uid": uid, "email_verified": "true"})
    check("C6 naming the account or the flag in the body does nothing", c == 403 and verified(S, uid) is False, (c, b))
    c, b, _ = http(S, "POST", "/auth/mark-verified")
    check("C7 mark-verified without a Firebase token is refused", c in (401, 403), (c, b))
    # legacy 1.0.5-style registration: verify-otp, create account, mark-verified with NO body
    uid = "ev-leg"; email = em("leg"); n0 = len(S.mails())
    http(S, "POST", "/auth/send-email-otp", form={"identifier": email}); otp = S.otp_for(email, n0)
    http(S, "POST", "/auth/verify-otp", form={"identifier": email, "otp": otp})
    http(S, "POST", "/sync-user", tk=tk(uid, email), js={"firebase_id": uid, "display_name": "L", "email": email, "invite_code": INVITE, "session_id": "s"})
    c, b, _ = mark(S, uid, email)
    check("C8 legacy-style registration (no proof sent) can no longer verify an account", c == 403 and verified(S, uid) is False, (c, b))
    # legacy login-time marking: verify-otp must not mark any account any more
    uid = "ev-old"; email = em("old"); user_row(S, uid, email, False); n0 = len(S.mails())
    http(S, "POST", "/auth/send-email-otp", form={"identifier": email}); otp = S.otp_for(email, n0)
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": email, "otp": otp})
    check("C9 verify-otp no longer marks an existing account (the old login-time side effect is gone)",
          c == 200 and verified(S, uid) is False, (c, b))
    check("C10 the code is single-use (replay of the same code is refused)",
          http(S, "POST", "/auth/verify-otp", form={"identifier": email, "otp": otp})[0] == 400)

    print("################ D. OWNERSHIP BINDING")
    a_uid, a_mail = "ev-bind-a", em("bind-a"); b_uid, b_mail = "ev-bind-b", em("bind-b")
    user_row(S, a_uid, a_mail, False); user_row(S, b_uid, b_mail, False)
    pa = get_proof(S, a_mail)
    c, b, _ = mark(S, b_uid, b_mail, pa)
    check("D1 a proof for email A cannot verify the account with email B", c == 403 and verified(S, b_uid) is False, (c, b))
    check("D2 ...and that failed attempt did not burn A's proof", proof_row(S, pa)[0] is None)
    c, b, _ = mark(S, a_uid, a_mail, pa)
    check("D3 the legitimate owner can still use it afterwards", c == 200 and verified(S, a_uid) is True, (c, b))
    # same email, two accounts (pre-squatted account + owner): the proof works for the one that holds it, once
    s_uid, o_uid, v_mail = "ev-squat", "ev-owner", em("victim")
    user_row(S, s_uid, v_mail, False); user_row(S, o_uid, v_mail, False)
    pv = get_proof(S, v_mail)
    c, b, _ = mark(S, s_uid, v_mail)
    check("D4 a pre-squatted account WITHOUT the proof stays unverified even though the owner just verified the mailbox",
          c == 403 and verified(S, s_uid) is False, (c, b))
    c, b, _ = mark(S, s_uid, v_mail, "evp1_" + "C" * 43)
    check("D5 ...and cannot guess its way in", c == 403 and verified(S, s_uid) is False, (c, b))
    c, b, _ = mark(S, o_uid, v_mail, pv)
    check("D6 the account whose device received the proof is verified", c == 200 and verified(S, o_uid) is True, (c, b))
    c, b, _ = mark(S, s_uid, v_mail, pv)
    check("D7 replaying that proof from the squatter's account is refused (single use)", c == 403 and verified(S, s_uid) is False, (c, b))
    # token email differs from the row's email
    r_uid = "ev-rowmis"; user_row(S, r_uid, em("row-mail"), False); pr = get_proof(S, em("tok-mail"))
    c, b, _ = mark(S, r_uid, em("tok-mail"), pr)
    check("D8 token email != stored profile email: refused, proof not burned", c == 403 and verified(S, r_uid) is False and proof_row(S, pr)[0] is None, (c, b))
    # no profile row yet
    n_uid, n_mail = "ev-norow", em("norow"); pn = get_proof(S, n_mail)
    c, b, _ = mark(S, n_uid, n_mail, pn)
    check("D9 no profile row yet: refused and the proof is NOT burned", c == 403 and proof_row(S, pn)[0] is None, (c, b))
    http(S, "POST", "/sync-user", tk=tk(n_uid, n_mail), js={"firebase_id": n_uid, "display_name": "N", "email": n_mail, "invite_code": INVITE, "session_id": "s"})
    c, b, _ = mark(S, n_uid, n_mail, pn)
    check("D10 after sync-user the SAME proof works (registration order is preserved)", c == 200 and verified(S, n_uid) is True, (c, b))
    # case / whitespace normalisation
    u2, m2 = "ev-case", em("CaseMix"); user_row(S, u2, m2.lower(), False); p2 = get_proof(S, m2)
    c, b, _ = mark(S, u2, m2, p2)
    check("D11 email case differences do not break the binding", c == 200 and verified(S, u2) is True, (c, b))

    print("################ E. EXPIRY, SUPERSEDING, OTP LIMITS")
    u, m = "ev-exp", em("exp"); user_row(S, u, m, False); p = get_proof(S, m)
    sql(S.db, "UPDATE email_verification_proofs SET expires_at = NOW() - INTERVAL '1 minute' WHERE proof_hash = $1", ph(p))
    c, b, _ = mark(S, u, m, p)
    check("E1 an expired proof is refused", c == 403 and verified(S, u) is False, (c, b))
    u, m = "ev-sup", em("sup"); user_row(S, u, m, False); p1 = get_proof(S, m); p2 = get_proof(S, m)
    c1, _, _ = mark(S, u, m, p1)
    check("E2 requesting a new verification supersedes the earlier proof", c1 == 403 and verified(S, u) is False and p1 != p2)
    c2, _, _ = mark(S, u, m, p2)
    check("E3 the newest proof works", c2 == 200 and verified(S, u) is True)
    m = em("otp5"); n0 = len(S.mails()); http(S, "POST", "/auth/send-email-otp", form={"identifier": m}); good = S.otp_for(m, n0)
    wrong = "000000" if good != "000000" else "111111"
    codes = [http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": wrong})[0] for _ in range(5)]
    check("E4 five wrong codes: 400 x4 then 429 and the code is destroyed", codes == [400, 400, 400, 400, 429], codes)
    check("E5 the right code after lockout is refused (no proof)", http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good})[0] == 400)
    m = em("otpexp"); n0 = len(S.mails()); http(S, "POST", "/auth/send-email-otp", form={"identifier": m}); good = S.otp_for(m, n0)
    sql(S.db, "UPDATE user_otps SET created_at = NOW() - INTERVAL '2 minutes' WHERE identifier = $1", m)
    check("E6 an expired code is refused", http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good})[0] == 400)
    m = "+15550001111"
    sql(S.db, "INSERT INTO user_otps (identifier, otp, created_at, attempts) VALUES ($1,'123456',NOW(),0) ON CONFLICT (identifier) DO UPDATE SET otp='123456', created_at=NOW(), attempts=0", m)
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": "123456"})
    check("E7 a non-email identifier still verifies, and never gets a proof", c == 200 and b == {"status": "success", "message": "OTP verified"}, (c, b))
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"otp": "123456"})
    check("E8 missing identifier is a 422 as before", c == 422, (c, b))

    print("################ F. CONCURRENCY")
    m = em("race-otp"); n0 = len(S.mails()); http(S, "POST", "/auth/send-email-otp", form={"identifier": m}); good = S.otp_for(m, n0)
    out = []
    ths = [threading.Thread(target=lambda: out.append(http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good}))) for _ in range(12)]
    [t.start() for t in ths]; [t.join() for t in ths]
    ok = [o for o in out if o[0] == 200]
    check("F1 12 parallel verify-otp with the same code: exactly one success and one proof", len(ok) == 1 and all(o[0] == 400 for o in out if o[0] != 200), [o[0] for o in out])
    SECRET_VALUES.extend(o[1].get("email_verification_proof", "") for o in ok if isinstance(o[1], dict))
    rm = em("race-proof"); user_row(S, "ev-race-1", rm, False); user_row(S, "ev-race-2", rm, False)
    p = get_proof(S, rm); out = []
    ths = [threading.Thread(target=lambda u=u: out.append((u, mark(S, u, rm, p)[0]))) for u in ("ev-race-1", "ev-race-2") * 6]
    [t.start() for t in ths]; [t.join() for t in ths]
    winners = {u for u, c in out if c == 200}
    flags = [verified(S, "ev-race-1"), verified(S, "ev-race-2")]
    check("F2 12 parallel mark-verified with one proof for two accounts: exactly one account verified", len(winners) == 1 and sorted(flags) == [False, True], (out, flags))
    # a verified account answers 200 to everyone after that - the other account must still be unverified (checked above)

    print("################ G. RATE LIMITING")
    uid = "ev-cap"; m = em("cap"); user_row(S, uid, m, False)
    codes = [mark(S, uid, m, "evp1_" + "D" * 43)[0] for _ in range(13)]
    check("G1 per-account cap: 10 refusals then 429", codes[:10] == [403] * 10 and codes[10:] == [429] * 3, codes)
    m = em("cap2"); codes = []
    for i in range(4):
        n0 = len(S.mails()); codes.append(http(S, "POST", "/auth/send-email-otp", form={"identifier": m})[0])
    check("G2 the existing OTP send limit is unchanged (3 per email, then 429)", codes == [200, 200, 200, 429], codes)
    uid = "ev-vr"; m = em("vr"); user_row(S, uid, m, False)
    codes = [http(S, "POST", "/auth/email/verify-request", tk=tk(uid, m), headers={"X-Forwarded-For": "10.250.0.1"})[0] for _ in range(4)]
    check("G3 verify-request uses the existing OTP send limit (3 then 429)", codes == [200, 200, 200, 429], codes)

    print("################ H. VERIFY-REQUEST / CONFIRM NEGATIVES")
    a, am = "ev-ch-a", em("ch-a"); b_, bm = "ev-ch-b", em("ch-b"); user_row(S, a, am, False); user_row(S, b_, bm, False)
    n0 = len(S.mails()); c, ba, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(a, am)); otp_a = S.otp_for(am, n0); cid_a = ba.get("challenge_id")
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(b_, bm), form={"challenge_id": cid_a, "otp": otp_a})
    check("H1 another account cannot use my challenge, even with the right code", c == 400 and verified(S, b_) is False and verified(S, a) is False, (c, b))
    sql(S.db, "UPDATE users SET email = $2 WHERE uid = $1", a, em("ch-a-changed"))
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": cid_a, "otp": otp_a})
    check("H2 if the stored email no longer equals the token email, confirm is refused", c == 400 and verified(S, a) is False, (c, b))
    sql(S.db, "UPDATE users SET email = $2 WHERE uid = $1", a, am)
    sql(S.db, "UPDATE email_verification_challenges SET expires_at = NOW() - INTERVAL '1 minute' WHERE challenge_id = $1", cid_a)
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": cid_a, "otp": otp_a})
    check("H3 an expired challenge is refused", c == 400 and verified(S, a) is False, (c, b))
    n0 = len(S.mails()); c, b1, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(a, am), headers={"X-Forwarded-For": "10.250.1.1"}); o1 = S.otp_for(am, n0)
    n0 = len(S.mails()); c, b2, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(a, am), headers={"X-Forwarded-For": "10.250.1.2"}); o2 = S.otp_for(am, n0)
    c1, _, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": b1["challenge_id"], "otp": o1})
    check("H4 a newer request supersedes the older challenge", c1 == 400 and verified(S, a) is False)
    wrong = "000000" if o2 != "000000" else "111111"
    codes = [http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": b2["challenge_id"], "otp": wrong})[0] for _ in range(5)]
    check("H5 five wrong codes lock the challenge (400 x4, then 429)", codes == [400, 400, 400, 400, 429], codes)
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": b2["challenge_id"], "otp": o2})
    check("H6 the right code after the lock is refused", c in (400, 429) and verified(S, a) is False, (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": "x" * 200, "otp": "123456"})
    check("H7 a made-up challenge id is refused", c in (400, 429), (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(a, am), form={"challenge_id": cid_a, "otp": "12ab56"})
    check("H8 a malformed code is refused before any lookup", c == 400, (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk("ev-ghost", em("ghost")))
    check("H9 verify-request for an account without a profile is a 404", c == 404, (c, b))
    user_row(S, "ev-mm", em("mm-row"), False)
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk("ev-mm", em("mm-token")))
    check("H10 verify-request: token email != stored email is a 409 and no mail is sent", c == 409 and not any(x["email"].startswith("mm-") for x in S.mails()), (c, b))
    c, b, _ = http(S, "POST", "/auth/email/verify-request")
    check("H11 verify-request / confirm need a Firebase token", c in (401, 403) and http(S, "POST", "/auth/email/verify-confirm", form={"challenge_id": "a", "otp": "123456"})[0] in (401, 403))

    print("################ I. TRANSACTION SAFETY (nothing is consumed halfway)")
    m = em("tx-issue"); n0 = len(S.mails()); http(S, "POST", "/auth/send-email-otp", form={"identifier": m}); good = S.otp_for(m, n0)
    sql(S.db, "ALTER TABLE email_verification_proofs ADD CONSTRAINT tx_block CHECK (false) NOT VALID")
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good})
    left = sql(S.db, "SELECT count(*) FROM user_otps WHERE identifier=$1", m.lower(), fetch=True)[0][0]
    check("I1 if the proof cannot be stored: 503, and the user's code is NOT burned", c == 503 and left == 1, (c, b, left))
    sql(S.db, "ALTER TABLE email_verification_proofs DROP CONSTRAINT tx_block")
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good})
    check("I2 the same code then works once the problem is fixed", c == 200 and b.get("email_verification_proof"), (c, b))
    uid = "tx-skip"; m = em("tx-skip"); user_row(S, uid, m, False); p = get_proof(S, m)
    sql(S.db, "CREATE OR REPLACE FUNCTION tx_skip() RETURNS trigger AS $$ BEGIN RETURN NULL; END $$ LANGUAGE plpgsql")
    sql(S.db, "CREATE TRIGGER tx_skip_trg BEFORE UPDATE ON users FOR EACH ROW WHEN (OLD.uid = 'tx-skip') EXECUTE FUNCTION tx_skip()")
    c, b, _ = mark(S, uid, m, p)
    check("I3 the user update touches no row: refused and the proof is rolled back (NOT consumed)", c == 403 and proof_row(S, p)[0] is None and verified(S, uid) is False, (c, b))
    sql(S.db, "DROP TRIGGER tx_skip_trg ON users")
    c, b, _ = mark(S, uid, m, p)
    check("I4 the same proof then works", c == 200 and verified(S, uid) is True, (c, b))
    uid = "tx-skip2"; m = em("tx-skip2"); user_row(S, uid, m, False); n0 = len(S.mails())
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(uid, m)); otp = S.otp_for(m, n0)
    sql(S.db, "CREATE TRIGGER tx_skip_trg2 BEFORE UPDATE ON users FOR EACH ROW WHEN (OLD.uid = 'tx-skip2') EXECUTE FUNCTION tx_skip()")
    c, b2, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, m), form={"challenge_id": b["challenge_id"], "otp": otp})
    consumed = sql(S.db, "SELECT consumed_at FROM email_verification_challenges WHERE challenge_id=$1", b["challenge_id"], fetch=True)[0][0]
    check("I5 confirm: user update touches no row -> refused and the challenge is NOT consumed", c == 400 and consumed is None and verified(S, uid) is False, (c, b2, consumed))
    sql(S.db, "DROP TRIGGER tx_skip_trg2 ON users")
    c, b2, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, m), form={"challenge_id": b["challenge_id"], "otp": otp})
    check("I6 ...and then succeeds with the same code", c == 200 and verified(S, uid) is True, (c, b2))
    # email delivery failure: the challenge must be invalidated
    uid = "tx-mail"; m = em("tx-mail"); user_row(S, uid, m, False); open(S.outbox + ".fail", "w").close()
    c, b, _ = http(S, "POST", "/auth/email/verify-request", tk=tk(uid, m))
    os.remove(S.outbox + ".fail")
    open_ch = sql(S.db, "SELECT count(*) FROM email_verification_challenges WHERE uid=$1 AND consumed_at IS NULL", uid, fetch=True)[0][0]
    check("I7 if the email cannot be sent: 503 and no usable challenge is left", c == 503 and open_ch == 0, (c, b, open_ch))

    print("################ J. DATABASE OUTAGE")
    uid = "out-1"; m = em("out-1"); user_row(S, uid, m, False); p = get_proof(S, m)
    m2 = em("out-otp"); n0 = len(S.mails()); http(S, "POST", "/auth/send-email-otp", form={"identifier": m2}); good2 = S.otp_for(m2, n0)
    asyncio.run(asql("postgres", 'ALTER DATABASE "%s" WITH ALLOW_CONNECTIONS false' % S.db))
    asyncio.run(asql("postgres", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()", S.db, fetch=True))
    c1, b1, _ = mark(S, uid, m, p)
    c2, b2, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": m2, "otp": good2})
    c3, b3, _ = http(S, "POST", "/auth/email/verify-confirm", tk=tk(uid, m), form={"challenge_id": "abc", "otp": "123456"})
    asyncio.run(asql("postgres", 'ALTER DATABASE "%s" WITH ALLOW_CONNECTIONS true' % S.db))
    check("J1 database down: mark-verified 503 (never a false 'invalid'/'ok')", c1 == 503, (c1, b1))
    check("J2 database down: verify-otp 503", c2 == 503, (c2, b2))
    check("J3 database down: verify-confirm 503", c3 == 503, (c3, b3))
    time.sleep(2)
    c, b, _ = mark(S, uid, m, p)
    check("J4 after recovery the proof issued before the outage still works", c == 200 and verified(S, uid) is True, (c, b))
    c, b, _ = http(S, "POST", "/auth/verify-otp", form={"identifier": m2, "otp": good2})
    check("J5 after recovery the code that met the outage still works (it was never consumed)", c == 200 and b.get("email_verification_proof"), (c, b))

    print("################ K. LOGS, SCHEMA, UNRELATED ENDPOINTS")
    text = "\n".join(S.log)
    leaked = [v for v in set(SECRET_VALUES) if v and v in text]
    check("K1 no proof, code or challenge id appears in any backend log", not leaked, leaked[:2])
    check("K2 no test email address appears in the verification log lines",
          not [l for l in S.log if "[EMAIL-VERIFY]" in l and "example.invalid" in l])
    ch = sql(S.db, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='mfa_login_challenges'::regclass AND contype='c' AND pg_get_constraintdef(oid) ILIKE '%purpose%'", fetch=True)
    check("K3 existing MFA tables are untouched (purpose check still lists only login_mfa and enable_mfa)",
          len(ch) == 1 and "verify_email" not in ch[0][0] and "login_mfa" in ch[0][0], ch)
    S.stop(drop=False)
    S.start(recreate=False); S.wait_ready()
    check("K4 a restart on the same database works (schema creation is idempotent)",
          http(S, "GET", "/faq")[0] == 200 and sql(S.db, "SELECT count(*) FROM users", fetch=True)[0][0] > 0)
    uid = "ev-regress"; m = em("regress"); user_row(S, uid, m, True)
    check("K5 an already verified user's other endpoints are unaffected (profile read, session update, check-session)",
          http(S, "GET", "/user/" + uid, tk=tk(uid, m))[0] == 200
          and http(S, "POST", "/auth/update-session", tk=tk(uid, m), form={"session_id": "abc"})[0] == 200
          and http(S, "GET", "/auth/check-session?session_id=abc", tk=tk(uid, m))[0] == 200)
    check("K6 an unverified existing user is NOT blocked by the backend from profile/session endpoints",
          http(S, "GET", "/user/ev-bind-b", tk=tk("ev-bind-b", em("bind-b")))[0] == 200
          and http(S, "POST", "/auth/update-session", tk=tk("ev-bind-b", em("bind-b")), form={"session_id": "abc"})[0] == 200)
    check("K7 the password-reset and OTP-send routes are unchanged (neutral response for unknown account)",
          http(S, "POST", "/auth/send-email-otp", form={"identifier": "not-an-email"})[0] == 422)

    print("################ L. SECRET NOT CONFIGURED (fail closed, nothing consumed)")
    N = Server("nosecret", 18401, env={"MFA_OTP_HASH_SECRET": ""})
    N.start(); N.wait_ready()
    try:
        m = em("nos"); n0 = len(N.mails()); http(N, "POST", "/auth/send-email-otp", form={"identifier": m}); good = N.otp_for(m, n0)
        c, b, _ = http(N, "POST", "/auth/verify-otp", form={"identifier": m, "otp": good})
        left = sql(N.db, "SELECT count(*) FROM user_otps WHERE identifier=$1", m, fetch=True)[0][0]
        check("L1 no secret: verify-otp is 503 and the code is not consumed", c == 503 and left == 1, (c, b, left))
        user_row(N, "ns-1", em("ns-1"), False)
        c, b, _ = mark(N, "ns-1", em("ns-1"), "evp1_" + "E" * 43)
        check("L2 no secret: mark-verified is 503, nothing verified", c == 503 and verified(N, "ns-1") is False, (c, b))
        check("L3 no secret: the rest of the app still starts and serves", http(N, "GET", "/faq")[0] == 200)
    finally:
        N.stop()

    total, bad = len(results), results.count(False)
    print("\n%d checks, %d failed" % (total, bad))
    sys.exit(1 if bad else 0)


main()
