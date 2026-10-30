"""G2 GATEWAY STUB. Emulator-only test double, NOT the production gateway.

It shows the control flow the real gateway would need: open the G1 envelope, verify the password through the identity REST API,
refuse disabled accounts, and mint a Firebase custom token ONLY for a UID it just verified (never for a UID taken from a request).
It has no rate limiting, no persistence and no real signing; it talks only to the local emulator.
"""
import json

import firebase_admin
from firebase_admin import auth, exceptions as fb_exceptions

import emu_client as EC
import envelope as E

GENERIC = {"error": "AUTH_FAILED"}  # the one answer for EVERY envelope problem (no oracle for which check failed)

# identity-REST error -> the code the Flutter screens already understand (see lib/core/errors/error_mapper.dart)
ERROR_MAP = {
    "EMAIL_EXISTS": "email-already-in-use",
    "INVALID_LOGIN_CREDENTIALS": "invalid-credential",
    "INVALID_PASSWORD": "invalid-credential",
    "EMAIL_NOT_FOUND": "invalid-credential",  # same code as a wrong password: no account enumeration
    "USER_DISABLED": "user-disabled",
    "WEAK_PASSWORD": "weak-password",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "too-many-requests",
}


def map_error(code):
    return ERROR_MAP.get(code, "unknown")


def admin_app():
    EC.require_isolation()
    try:
        return firebase_admin.get_app("g2")
    except ValueError:
        return firebase_admin.initialize_app(options={"projectId": EC.PROJECT}, name="g2")


class GatewayStub:
    def __init__(self, keyring, nonces, app):
        self.keyring, self.nonces, self.app = keyring, nonces, app

    def _mint(self, uid):
        return auth.create_custom_token(uid, app=self.app).decode("ascii")

    def _caller_uid(self, authorization):
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        try:
            return auth.verify_id_token(authorization.split(" ", 1)[1].strip(), app=self.app, check_revoked=True)["uid"]
        except Exception:  # any verification failure is simply "not authenticated"
            return None

    def handle(self, path, body, authorization=None):
        """Returns (http_status, json_dict)."""
        if path == "/enc/nonce":
            try:
                op = json.loads(body.decode("utf-8")).get("op")
                return 200, {"nonce": self.nonces.issue(op), "ttl": self.nonces.ttl}
            except (ValueError, AttributeError, E.EnvelopeError):
                return 400, GENERIC
        if not path.startswith("/enc/auth/"):
            return 404, GENERIC
        op = path.rsplit("/", 1)[1]
        if op not in E.OPERATIONS:
            return 404, GENERIC
        try:
            creds = E.open_credentials(body, self.keyring, self.nonces, op)
        except E.EnvelopeError:
            return 400, GENERIC
        return getattr(self, "_" + op)(creds, authorization)

    # --- operations -------------------------------------------------------------------------------------------------
    def _verify(self, creds):
        status, js = EC.sign_in_password(creds.email, creds.password)
        if status != 200:
            return None, map_error(EC.error_code(js))
        uid = js["localId"]
        if auth.get_user(uid, app=self.app).disabled:
            return None, "user-disabled"
        return uid, None

    def _login(self, creds, _authorization):
        uid, err = self._verify(creds)
        return (200, {"custom_token": self._mint(uid)}) if uid else (400, {"error": err})

    def _signup(self, creds, _authorization):
        status, js = EC.sign_up(creds.email, creds.password)
        if status == 200:
            return 200, {"custom_token": self._mint(js["localId"]), "created": True}
        code = EC.error_code(js)
        if code != "EMAIL_EXISTS":
            return 400, {"error": map_error(code)}
        # signup recovery (create_profile_screen.dart): the account exists, so prove ownership with the password
        uid, _err = self._verify(creds)
        if uid:
            return 200, {"custom_token": self._mint(uid), "created": False, "recovered": True}
        return 400, {"error": "email-already-in-use"}

    def _authenticated_verify(self, creds, authorization):
        caller = self._caller_uid(authorization)
        if caller is None:
            return None, None
        uid, err = self._verify(creds)
        if uid is None:
            return caller, err
        return (caller, None) if uid == caller else (caller, "invalid-credential")  # password of ANOTHER account

    def _reauth(self, creds, authorization):
        caller, err = self._authenticated_verify(creds, authorization)
        if caller is None:
            return 401, GENERIC
        return (400, {"error": err}) if err else (200, {"custom_token": self._mint(caller)})

    def _change_password(self, creds, authorization):
        caller, err = self._authenticated_verify(creds, authorization)
        if caller is None:
            return 401, GENERIC
        if err:
            return 400, {"error": err}
        try:
            auth.update_user(caller, password=creds.new_password, app=self.app)
        except (ValueError, fb_exceptions.FirebaseError):
            return 400, {"error": "weak-password"}
        auth.revoke_refresh_tokens(caller, app=self.app)  # every other session of this account ends
        return 200, {"custom_token": self._mint(caller)}
