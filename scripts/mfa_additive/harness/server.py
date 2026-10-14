"""Runs one backend source tree under test with: fake Firebase (emulator mode), captured (not sent) OTP mail, no egress."""
import os, json, sys
import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials
class _Fake(credentials.Base):
    def get_credential(self): return AnonymousCredentials()
firebase_admin.initialize_app(_Fake(), {"projectId": os.environ["FIREBASE_PROJECT_ID"]})
import app.services.email_service as es
OUT = "/outbox/%s.jsonl" % os.environ["BACKEND_NAME"]
async def _capture(email, otp, valid_minutes=1):
    with open(OUT, "a") as f: f.write(json.dumps({"email": email, "otp": otp}) + "\n")
    return True
es.send_otp_email = _capture
import uvicorn
from app.main import app

# HARNESS ONLY (isolated test containers): reset fake-user state between tests. Not part of the application.
@app.post("/__test/reset")
async def _test_reset():
    from app.services.db_pool import get_pool
    pool = await get_pool()
    async with pool.acquire() as c:
        for sql in ("DELETE FROM otp_send_rate_limits", "DELETE FROM mfa_sessions", "DELETE FROM mfa_login_challenges",
                    "UPDATE users SET mfa_enabled = (uid = 'fake-mfa'), email_verified = (uid <> 'fake-unverified') "
                    "WHERE uid LIKE 'fake-%' AND uid NOT LIKE 'fake-new-%'"):
            await c.execute(sql)
    return {"ok": True}
uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
