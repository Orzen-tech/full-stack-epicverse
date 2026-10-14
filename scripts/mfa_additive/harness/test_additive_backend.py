"""Backend tests for the additive MFA release (backend A) with a differential check against the deployed backend (L).
Runs INSIDE the isolated Docker network with FAKE users only: no cloud, no real email, no production data.
Exit status 1 on any failure."""
import asyncio, json, sys, time
import asyncpg
sys.path.insert(0, "/h")
from fb import token, call

A = "http://mfa-be-A:8080"
L = "http://mfa-be-L:8080"
DBS = {"A": "postgresql://postgres@mfa-pg/dba", "L": "postgresql://postgres@mfa-pg/dbl"}
VER = ("fake-verified", "verified@example.invalid")
UNV = ("fake-unverified", "unverified@example.invalid")
MFA = ("fake-mfa", "mfa@example.invalid")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:300]) if detail and not ok else ""), flush=True)


def db(sql, *args, which="A", fetch=False):
    async def go():
        c = await asyncpg.connect(DBS[which])
        try:
            return await (c.fetch(sql, *args) if fetch else c.execute(sql, *args))
        finally:
            await c.close()
    return asyncio.run(go())


def reset(which="A"):
    db("DELETE FROM otp_send_rate_limits", which=which)
    db("UPDATE users SET mfa_enabled = (uid = 'fake-mfa'), email_verified = (uid <> 'fake-unverified') WHERE uid LIKE 'fake-%' AND uid NOT LIKE 'fake-new-%'", which=which)
    db("DELETE FROM mfa_sessions", which=which); db("DELETE FROM mfa_login_challenges", which=which)


def tok(u, age_s=60):
    return token(u[0], u[1], auth_time=int(time.time()) - age_s)


def otp_for(email, name="A"):
    last = None
    for _ in range(30):
        try:
            for line in open(f"/outbox/{name}.jsonl"):
                m = json.loads(line)
                if m["email"].lower() == email.lower(): last = m["otp"]
        except FileNotFoundError:
            pass
        if last: return last
        time.sleep(0.1)
    return None


def mail_count(email, name="A"):
    try:
        return sum(1 for l in open(f"/outbox/{name}.jsonl") if json.loads(l)["email"].lower() == email.lower())
    except FileNotFoundError:
        return 0


def login(u, age=60):
    """Full modern MFA login: challenge -> verify. Returns (session_token, token_used)."""
    t = tok(u, age)
    c, b = call(A, "POST", "/auth/mfa/challenge", tok=t)
    assert c == 200, (c, b)
    code, vb = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": b["challenge_id"], "otp": otp_for(u[1])})
    assert code == 200, (code, vb)
    return vb["mfa_session_token"], t


def main():
    # ---------------------------------------------------------------- session-status
    reset()
    c, b = call(A, "GET", "/auth/session-status"); check("B1 session-status without a token is refused", c in (401, 403), c)
    c, b = call(A, "GET", "/auth/session-status", tok=tok(VER))
    check("B2 session-status for a verified non-MFA user", c == 200 and b == {"email_verified": True, "mfa_enabled": False, "mfa_session_valid": False}, (c, b))
    c, b = call(A, "GET", "/auth/session-status", tok=tok(MFA)); check("B3 MFA user without a session is not valid", c == 200 and b["mfa_enabled"] is True and b["mfa_session_valid"] is False, (c, b))
    c, b = call(A, "GET", "/auth/session-status", tok=tok(("ghost", "ghost@example.invalid"))); check("B4 unknown profile -> 404", c == 404, (c, b))

    # ---------------------------------------------------------------- login challenge / verify
    reset(); t = tok(MFA)
    c, b = call(A, "POST", "/auth/mfa/challenge", tok=t); check("L1 login challenge sent", c == 200 and b.get("status") == "sent" and b.get("challenge_id") and b.get("expires_in") == 300, (c, b))
    cid = b["challenge_id"]; code = otp_for(MFA[1])
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": "000000" if code != "000000" else "111111"}); check("L2 wrong code -> 400", c == 400, (c, b2))
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": "12ab56"}); check("L3 malformed code -> 400", c == 400, (c, b2))
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=tok(VER), form={"challenge_id": cid, "otp": code}); check("L4 challenge cannot be used by another user", c in (400, 409) , (c, b2))
    c, vb = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": code})
    check("L5 correct code -> session token + expiry", c == 200 and len(vb.get("mfa_session_token", "")) >= 30 and vb.get("expires_in") == 43200, (c, {k: v for k, v in vb.items() if k != "mfa_session_token"}))
    sess = vb["mfa_session_token"]
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": code}); check("L6 a used code cannot be reused", c == 400, (c, b2))
    h = {"X-MFA-Session": sess}
    c, b2 = call(A, "GET", "/auth/session-status", tok=t, headers=h); check("L7 server validates the real token (valid)", c == 200 and b2["mfa_session_valid"] is True, (c, b2))
    c, b2 = call(A, "GET", "/auth/session-status", tok=t); check("L8 Firebase sign-in alone is NOT MFA proof (no token -> invalid)", b2["mfa_session_valid"] is False, b2)
    c, b2 = call(A, "GET", "/auth/session-status", tok=t, headers={"X-MFA-Session": sess[:-2] + "xx"}); check("L9 a tampered token is invalid", b2["mfa_session_valid"] is False, b2)
    c, b2 = call(A, "GET", "/auth/session-status", tok=tok(MFA, 3000), headers=h); check("L10 token is bound to the Firebase sign-in (different auth_time -> invalid)", b2["mfa_session_valid"] is False, b2)
    c, b2 = call(A, "GET", "/auth/session-status", tok=tok(VER), headers=h); check("L11 token is bound to the user", b2["mfa_session_valid"] is False, b2)
    db("UPDATE mfa_sessions SET expires_at = NOW() - interval '1 minute'")
    c, b2 = call(A, "GET", "/auth/session-status", tok=t, headers=h); check("L12 expired session is invalid (server expiry)", b2["mfa_session_valid"] is False, b2)
    db("UPDATE mfa_sessions SET expires_at = NOW() + interval '1 hour'")
    c, b2 = call(A, "GET", "/auth/session-status", tok=t, headers=h); check("L12b (control) restoring expiry makes it valid again", b2["mfa_session_valid"] is True, b2)
    c, b2 = call(A, "POST", "/auth/mfa/logout", tok=t, headers=h); check("L13 logout revokes the session", c == 200, (c, b2))
    c, b2 = call(A, "GET", "/auth/session-status", tok=t, headers=h); check("L14 revoked session is invalid", b2["mfa_session_valid"] is False, b2)

    # expired code / attempts lockout
    reset(); t = tok(MFA)
    c, b = call(A, "POST", "/auth/mfa/challenge", tok=t); cid = b["challenge_id"]; code = otp_for(MFA[1])
    db("UPDATE mfa_login_challenges SET expires_at = NOW() - interval '1 second' WHERE challenge_id = $1", cid)
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": code}); check("L15 expired code (5 min) -> 400", c == 400, (c, b2))
    reset(); c, b = call(A, "POST", "/auth/mfa/challenge", tok=t); cid = b["challenge_id"]; code = otp_for(MFA[1]); bad = "999999" if code != "999999" else "888888"
    statuses = [call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": bad})[0] for _ in range(5)]
    c, b2 = call(A, "POST", "/auth/mfa/verify", tok=t, form={"challenge_id": cid, "otp": code})
    check("L16 five wrong attempts lock the challenge (statuses 400x4 then 429; even the right code fails)", statuses == [400, 400, 400, 400, 429] and c in (400, 429), (statuses, c))
    reset(); c, b = call(A, "POST", "/auth/mfa/challenge", tok=tok(VER)); check("L17 login challenge for a non-MFA user -> 409", c == 409, (c, b))
    reset(); db("UPDATE users SET mfa_enabled = TRUE WHERE uid = 'fake-unverified'"); c, b = call(A, "POST", "/auth/mfa/challenge", tok=tok(UNV)); check("L18 login challenge for an unverified email -> 403", c == 403, (c, b))
    c, b = call(A, "POST", "/auth/mfa/challenge"); check("L19 challenge without a token is refused", c in (401, 403), c)

    # ---------------------------------------------------------------- enable
    reset(); before = mail_count(VER[1])
    c, b = call(A, "POST", "/user/mfa/enable-request", tok=tok(VER, 1800)); check("E1 enable-request with a stale sign-in -> 401 RECENT_SIGN_IN_REQUIRED, no email sent",
        c == 401 and (b.get("detail") or {}).get("code") == "RECENT_SIGN_IN_REQUIRED" and mail_count(VER[1]) == before, (c, b))
    reset(); c, b = call(A, "POST", "/user/mfa/enable-request", tok=tok(UNV)); check("E2 enable-request for an unverified email -> 403", c == 403, (c, b))
    t = tok(VER); c, b = call(A, "POST", "/user/mfa/enable-request", tok=t); check("E3 enable-request with a recent sign-in sends a code", c == 200 and b.get("status") == "sent", (c, b))
    cid = b["challenge_id"]; code = otp_for(VER[1])
    flag = db("SELECT mfa_enabled FROM users WHERE uid='fake-verified'", fetch=True)[0][0]; check("E4 MFA is NOT enabled before confirmation", flag is False, flag)
    c, b2 = call(A, "POST", "/user/mfa/enable-confirm", tok=t, form={"challenge_id": cid, "otp": "000000" if code != "000000" else "111111"}); check("E5 wrong code -> 400 and still not enabled",
        c == 400 and db("SELECT mfa_enabled FROM users WHERE uid='fake-verified'", fetch=True)[0][0] is False, (c, b2))
    c, b2 = call(A, "POST", "/user/mfa/enable-confirm", tok=tok(VER, 1800), form={"challenge_id": cid, "otp": code}); check("E6 confirm with a stale sign-in -> 401, still not enabled",
        c == 401 and db("SELECT mfa_enabled FROM users WHERE uid='fake-verified'", fetch=True)[0][0] is False, (c, b2))
    c, b2 = call(A, "POST", "/user/mfa/enable-confirm", tok=t, form={"challenge_id": cid, "otp": code})
    check("E7 correct code enables MFA and returns a session", c == 200 and b2.get("mfa_enabled") is True and b2.get("mfa_session_token"), (c, {k: v for k, v in b2.items() if k != "mfa_session_token"}))
    check("E8 enabled flag persisted in the database", db("SELECT mfa_enabled FROM users WHERE uid='fake-verified'", fetch=True)[0][0] is True)
    c, b3 = call(A, "POST", "/user/mfa/enable-confirm", tok=t, form={"challenge_id": cid, "otp": code}); check("E9 the enable code cannot be reused", c == 400, (c, b3))
    reset(); t = tok(MFA); c, b = call(A, "POST", "/user/mfa/enable-request", tok=t); check("E10 enable-request when already enabled -> already_enabled", c == 200 and b.get("status") == "already_enabled", (c, b))

    # ---------------------------------------------------------------- disable + legacy endpoint
    reset(); sess, t = login(MFA)
    c, b = call(A, "POST", "/user/mfa/disable", tok=t); check("D1 disable without an MFA session -> 401 MFA_SESSION_REQUIRED, flag unchanged",
        c == 401 and (b.get("detail") or {}).get("code") == "MFA_SESSION_REQUIRED" and db("SELECT mfa_enabled FROM users WHERE uid='fake-mfa'", fetch=True)[0][0] is True, (c, b))
    c, b = call(A, "POST", "/user/mfa/disable", tok=tok(MFA, 3000), headers={"X-MFA-Session": sess}); check("D2 session from another sign-in is not accepted", c == 401, (c, b))
    old = tok(MFA, 1800); s2, _ = login(MFA, 1800)
    c, b = call(A, "POST", "/user/mfa/disable", tok=old, headers={"X-MFA-Session": s2}); check("D3 valid session but stale sign-in -> 401 RECENT_SIGN_IN_REQUIRED",
        c == 401 and (b.get("detail") or {}).get("code") == "RECENT_SIGN_IN_REQUIRED", (c, b))
    # legacy endpoint
    c, b = call(A, "POST", "/user/update-mfa", tok=t, form={"mfa_enabled": "false"}); check("D4 LEGACY disable with only a Firebase token -> refused (bypass closed), flag unchanged",
        c == 401 and db("SELECT mfa_enabled FROM users WHERE uid='fake-mfa'", fetch=True)[0][0] is True, (c, b))
    c, b = call(L, "POST", "/user/update-mfa", tok=token("fake-mfa", MFA[1]), form={"mfa_enabled": "false"}); check("D4b (control) the DEPLOYED backend allowed exactly that bypass", c == 200, (c, b))
    db("UPDATE users SET mfa_enabled = TRUE WHERE uid='fake-mfa'", which="L")
    c, b = call(A, "POST", "/user/update-mfa", tok=t, form={"mfa_enabled": "true"}); check("D5 legacy request matching the current state is a harmless no-op (200)", c == 200, (c, b))
    reset(); c, b = call(A, "POST", "/user/update-mfa", tok=tok(VER), form={"mfa_enabled": "true"}); check("D6 LEGACY enable -> 403 MFA_ENABLE_FLOW_REQUIRED, flag unchanged",
        c == 403 and (b.get("detail") or {}).get("code") == "MFA_ENABLE_FLOW_REQUIRED" and db("SELECT mfa_enabled FROM users WHERE uid='fake-verified'", fetch=True)[0][0] is False, (c, b))
    c, b = call(A, "POST", "/user/update-mfa", tok=tok(("ghost", "g@example.invalid")), form={"mfa_enabled": "true"}); check("D7 legacy call for an unknown profile -> 404", c == 404, (c, b))
    reset(); sess, t = login(MFA); sess2, _ = login(MFA)
    c, b = call(A, "POST", "/user/mfa/disable", tok=t, headers={"X-MFA-Session": sess2}); check("D8 valid session + recent sign-in disables MFA", c == 200 and b.get("mfa_enabled") is False, (c, b))
    check("D9 disabled flag persisted", db("SELECT mfa_enabled FROM users WHERE uid='fake-mfa'", fetch=True)[0][0] is False)
    c, b = call(A, "GET", "/auth/session-status", tok=t, headers={"X-MFA-Session": sess}); check("D10 disabling revokes every session", b["mfa_session_valid"] is False, b)
    c, b = call(A, "POST", "/user/mfa/disable", tok=t); check("D11 disabling when already off is a no-op (200)", c == 200, (c, b))

    # ---------------------------------------------------------------- cross-user: A's valid session can never disable B's MFA
    reset(); db("UPDATE users SET mfa_enabled = TRUE WHERE uid = 'fake-verified'")
    sessA, tA = login(MFA); sessB, tB = login(VER)          # two users, both MFA-enabled, each with their OWN valid session
    flag = lambda uid: db("SELECT mfa_enabled FROM users WHERE uid = $1", uid, fetch=True)[0][0]
    c, b = call(A, "POST", "/user/mfa/disable", tok=tB, headers={"X-MFA-Session": sessA})
    check("X1 user A's valid session cannot disable user B's MFA (401 MFA_SESSION_REQUIRED); B stays enabled",
          c == 401 and (b.get("detail") or {}).get("code") == "MFA_SESSION_REQUIRED" and flag("fake-verified") is True, (c, b))
    c, b = call(A, "POST", "/user/mfa/disable", tok=tA, headers={"X-MFA-Session": sessB})
    check("X2 user B's valid session cannot disable user A's MFA (401); A stays enabled",
          c == 401 and (b.get("detail") or {}).get("code") == "MFA_SESSION_REQUIRED" and flag("fake-mfa") is True, (c, b))
    c, b = call(A, "POST", "/user/update-mfa", tok=tB, headers={"X-MFA-Session": sessA}, form={"mfa_enabled": "false"})
    check("X3 the legacy endpoint with A's session cannot disable B either", c == 401 and flag("fake-verified") is True, (c, b))
    s1 = call(A, "GET", "/auth/session-status", tok=tA, headers={"X-MFA-Session": sessA})[1]["mfa_session_valid"]
    s2 = call(A, "GET", "/auth/session-status", tok=tB, headers={"X-MFA-Session": sessB})[1]["mfa_session_valid"]
    check("X4 the refused attempts have no side effects (both users and both sessions are untouched)",
          s1 is True and s2 is True and flag("fake-mfa") is True and flag("fake-verified") is True, (s1, s2))
    c, b = call(A, "POST", "/user/mfa/disable", tok=tB, headers={"X-MFA-Session": sessB})
    check("X5 control: B's own session does disable B, and A is unaffected",
          c == 200 and flag("fake-verified") is False and flag("fake-mfa") is True, (c, b))

    # ---------------------------------------------------------------- /sync-user: mfa_enabled and email are server-controlled
    def row(uid, which="A"):
        r = db("SELECT mfa_enabled, email, display_name, primary_language, profile_picture FROM users WHERE uid = $1", uid, which=which, fetch=True)
        return tuple(r[0]) if r else None
    def sync(base, u, body, headers=None):
        return call(base, "POST", "/sync-user", tok=tok(u), js=dict(body, uid=u[0]), headers=headers)

    # S1-S3: an existing account with MFA ON keeps it, whatever the client sends for mfa_enabled (omitted / false / true)
    for n, extra in (("S1 omitted", {}), ("S2 explicit false", {"mfa_enabled": False}), ("S3 explicit true", {"mfa_enabled": True})):
        reset(); sess, _ = login(MFA)
        c, b = sync(A, MFA, dict(extra, display_name="Renamed"), headers={"X-MFA-Session": sess})
        r = row("fake-mfa")
        check(n + " mfa_enabled -> 200, MFA stays ON, profile still updated", c == 200 and r[0] is True and r[2] == "Renamed", (c, b, r))
    # S4-S5: an existing account with MFA OFF cannot be switched ON by a client value either
    for n, extra in (("S4 omitted", {}), ("S5 explicit true", {"mfa_enabled": True})):
        reset(); c, b = sync(A, VER, dict(extra, display_name="Plain"))
        r = row("fake-verified")
        check(n + " mfa_enabled on an MFA-off account -> 200, stays OFF", c == 200 and r[0] is False, (c, b, r))
    # S6: attempted email change (server-controlled) is ignored; the MFA code destination stays the stored address
    reset(); sess, _ = login(MFA)
    c, b = sync(A, MFA, {"email": "attacker@example.invalid", "display_name": "Mail Try"}, headers={"X-MFA-Session": sess})
    r = row("fake-mfa")
    check("S6 attempted email change is ignored (200); the stored email is unchanged, other fields still apply", c == 200 and r[1] == MFA[1] and r[2] == "Mail Try", (c, b, r))
    # S7: permitted profile updates (display name, language, picture) all still work
    reset(); c, b = sync(A, VER, {"display_name": "New Name", "primary_language": "Hindi", "profile_picture": "https://example.invalid/p.png"})
    r = row("fake-verified")
    check("S7 display name, language and picture are updated", c == 200 and r[2:] == ("New Name", "Hindi", "https://example.invalid/p.png"), (c, b, r))
    # S8: a blank/absent display name or picture does not wipe the stored value (COALESCE semantics unchanged)
    c, b = sync(A, VER, {"primary_language": "Tamil"}); r = row("fake-verified")
    check("S8 omitted display name / picture keep their stored values", c == 200 and r[2] == "New Name" and r[4] == "https://example.invalid/p.png" and r[3] == "Tamil", (c, b, r))
    # S9: sync for another user's uid is still refused and changes nothing
    reset(); before = row("fake-mfa")
    c, b = call(A, "POST", "/sync-user", tok=tok(VER), js={"uid": "fake-mfa", "mfa_enabled": False, "email": "x@example.invalid"})
    check("S9 syncing someone else's uid -> 403, nothing changed", c == 403 and row("fake-mfa") == before, (c, b))
    # S10: /sync-user is not session-gated (the app calls it during login, before the MFA step) - so it must be harmless without a session
    reset(); c, b = sync(A, MFA, {"mfa_enabled": False, "email": "attacker@example.invalid"})
    r = row("fake-mfa")
    check("S10 sync without an MFA session (as during login) -> 200, but MFA stays ON and the email is unchanged", c == 200 and r[0] is True and r[1] == MFA[1], (c, b, r))
    # S11-S12: new accounts always start with MFA disabled, even when the client sends true; invite consumed, email from the token
    reset(); db("DELETE FROM users WHERE uid LIKE 'fake-new-%'"); NEW1 = ("fake-new-1", "fake-new-1@example.invalid")
    uses = db("SELECT current_uses FROM invite_codes WHERE code='EPIC-TEST01'", fetch=True)[0][0]
    c, b = sync(A, NEW1, {"invite_code": "EPIC-TEST01", "display_name": "Fresh", "mfa_enabled": True})
    r = row("fake-new-1")
    check("S11 new account with a valid invite -> 200, created with MFA OFF even though the client sent true", c == 200 and r is not None and r[0] is False and r[2] == "Fresh", (c, b, r))
    check("S12 new account email is taken from the verified token (none sent); invite use consumed once", r is not None and r[1] == NEW1[1] and
          db("SELECT current_uses FROM invite_codes WHERE code='EPIC-TEST01'", fetch=True)[0][0] == uses + 1, r)
    c, b = sync(A, ("fake-new-2", "fake-new-2@example.invalid"), {"display_name": "NoInvite"})
    check("S13 new account without an invite is still rejected (403), nothing created", c == 403 and row("fake-new-2") is None, (c, b))
    # S14: legacy client (1.0.x never sends mfa_enabled) gets the same status/body on deployed L and additive A
    reset("L"); reset(); body = {"display_name": "Legacy", "primary_language": "English"}
    cl, bl = sync(L, VER, body); ca, ba = sync(A, VER, body)
    check("S14 legacy-client request: identical status and response body on deployed L and additive A", (cl, bl) == (ca, ba) == (200, {"status": "success", "message": "User synchronized"}), ((cl, bl), (ca, ba)))
    # S15: differential control, proving the defect existed in the deployed backend L (so S1-S5 are meaningful)
    reset("L"); db("UPDATE users SET mfa_enabled = TRUE WHERE uid = 'fake-verified'", which="L")
    sync(L, VER, {"display_name": "Control"})
    check("S15 CONTROL: deployed L resets MFA to OFF on an omitted value (the defect the fix removes)", row("fake-verified", "L")[0] is False)
    reset("L"); sync(L, VER, {"email": "attacker@example.invalid"})
    check("S16 CONTROL: deployed L rewrites the stored email (the defect the fix removes)", row("fake-verified", "L")[1] == "attacker@example.invalid")
    reset("L")

    # ---------------------------------------------------------------- rate limits (unchanged limiter)
    reset(); t = tok(MFA); codes = [call(A, "POST", "/auth/mfa/challenge", tok=t)[0] for _ in range(5)]
    check("R1 existing limit applies to MFA sends: 3 allowed, then 429", codes == [200, 200, 200, 429, 429], codes)
    c, b = call(A, "POST", "/auth/mfa/challenge", tok=t); check("R2 a further request in the same window is still refused (429)", c == 429, (c, b))
    reset(); codes = [call(A, "POST", "/auth/send-otp", tok=tok(MFA), form={"identifier": MFA[1]})[0] for _ in range(4)]
    check("R3 legacy send-otp keeps its 3-per-window limit and shares the budget", codes == [200, 200, 200, 429], codes)

    # ---------------------------------------------------------------- old (1.0.5) client differential: deployed L vs additive A
    def run_old_client(base, name, which):
        reset(which); seq = []
        t = token("fake-mfa", MFA[1]); tv = token("fake-verified", VER[1]); tu = token("fake-unverified", UNV[1])
        seq.append(("GET user", call(base, "GET", "/user/fake-mfa", tok=t)[0]))
        seq.append(("GET user other", call(base, "GET", "/user/fake-verified", tok=t)[0]))
        seq.append(("send-otp bearer", call(base, "POST", "/auth/send-otp", tok=t, form={"identifier": MFA[1]})[0]))
        o = otp_for(MFA[1], name)
        seq.append(("verify-otp wrong", call(base, "POST", "/auth/verify-otp", form={"identifier": MFA[1], "otp": "000000" if o != "000000" else "111111"})[0]))
        seq.append(("verify-otp right", call(base, "POST", "/auth/verify-otp", form={"identifier": MFA[1], "otp": o})[0]))
        seq.append(("send-otp no auth", call(base, "POST", "/auth/send-otp", form={"identifier": MFA[1]})[0]))
        seq.append(("send-email-otp", call(base, "POST", "/auth/send-email-otp", form={"identifier": "fake-new-6@example.invalid"})[0]))
        seq.append(("update-session", call(base, "POST", "/auth/update-session", tok=tv, form={"session_id": "s1"})[0]))
        seq.append(("check-session", call(base, "GET", "/auth/check-session?session_id=s1", tok=tv)[0]))
        seq.append(("mark-verified", call(base, "POST", "/auth/mark-verified", tok=tu)[0]))
        seq.append(("validate-invite", call(base, "GET", "/validate-invite/EPIC-TEST01")[0]))
        seq.append(("faq", call(base, "GET", "/faq")[0]))
        seq.append(("feedback", call(base, "POST", "/feedback", tok=tv, js={"message": "hello", "rating": 5})[0]))
        seq.append(("no token on /user", call(base, "GET", "/user/fake-mfa")[0]))
        return seq
    seqL = run_old_client(L, "L", "L"); seqA = run_old_client(A, "A", "A")
    diffs = [(a[0], a[1], b[1]) for a, b in zip(seqL, seqA) if a[1] != b[1]]
    check("O1 old-client call sequence: identical status codes on the deployed and additive backends", not diffs, diffs)
    print("      old-client statuses:", [s for _, s in seqA])
    bl = call(L, "GET", "/user/fake-mfa", tok=token("fake-mfa", MFA[1]))[1]; ba = call(A, "GET", "/user/fake-mfa", tok=token("fake-mfa", MFA[1]))[1]
    check("O2 GET /user returns the same fields (mfa_enabled, email_verified preserved)", set(bl) == set(ba) and ba["mfa_enabled"] is True and ba["email_verified"] is True, (sorted(set(bl) ^ set(ba))))
    # existing MFA preferences survive: nothing in the new code touches them except the verified flows
    reset(); check("O3 existing MFA preference untouched by read-only calls",
                   db("SELECT mfa_enabled FROM users WHERE uid='fake-mfa'", fetch=True)[0][0] is True)

    bad = results.count(False); print(f"\nTOTAL {len(results)} checks | PASS {len(results)-bad} | FAIL {bad}"); sys.exit(1 if bad else 0)


main()
