"""A FAKE Firebase for testing the real-Firebase adapter offline: an in-memory account store plus fake REST / Admin / signBlob ports.
TEST HARNESS ONLY (never part of a patch or a deployment). It makes no network call. It is NOT Firebase: it only implements the behaviours the
adapter's contract relies on, so a passing test proves the adapter's logic and error handling, not real Firebase behaviour.
"""
import base64
import itertools
import json
import secrets
import time
from collections import Counter

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class World:
    def __init__(self, project="epicverse-fake-project", signer="g7-signer", clock=time.time):
        self.clock = clock
        self.project = project
        self.service_account = "%s@%s.iam.gserviceaccount.com" % (signer, project)
        self.accounts = {}                    # uid -> {"email", "password", "disabled", "valid_since", "email_verified"}
        self.id_tokens = {}                   # fake ID token -> {"uid", "iat", "exp"}
        self.events = []                      # ordered operation names, for ordering assertions
        self.calls = Counter()
        self.faults = {}                      # operation -> {"kind", "cause", "times"} | {"throttle": True, "times": n}
        self.min_password_length = 8
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._ids = itertools.count(1)
        self.bearers_seen = []

    # ---- setup helpers -------------------------------------------------------------------------------------------------------
    def add_account(self, email, password, disabled=False, uid=None):
        uid = uid or "fake-uid-%d" % next(self._ids)
        self.accounts[uid] = {"email": email, "password": password, "disabled": disabled, "valid_since": 0, "email_verified": False}
        return uid

    def find_by_email(self, email):
        wanted = (email or "").strip().lower()
        return next((uid for uid, a in self.accounts.items() if a["email"].lower() == wanted), None)

    def id_token_for(self, uid, ttl=3600, issued_at=None):
        token = "fake-id-" + secrets.token_urlsafe(24)
        iat = int(issued_at if issued_at is not None else self.clock())
        self.id_tokens[token] = {"uid": uid, "iat": iat, "exp": iat + ttl}
        return token

    def fault(self, operation, kind="unavailable", cause="http_503", times=1, throttle=False):
        self.faults[operation] = {"kind": kind, "cause": cause, "times": times, "throttle": throttle}

    def clear_faults(self):
        self.faults.clear()

    def reset_counters(self):
        self.calls.clear()
        self.events.clear()

    def _enter(self, operation):
        self.calls[operation] += 1
        self.events.append(operation)
        fault = self.faults.get(operation)
        if fault and fault["times"] > 0:
            fault["times"] -= 1
            return fault
        return None

    # ---- inspection ----------------------------------------------------------------------------------------------------------
    def verify_custom_token(self, token):
        """Checks the signature with the fake signer's public key and returns the claims (raises ValueError otherwise)."""
        header_b64, payload_b64, signature_b64 = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        self.private_key.public_key().verify(base64.urlsafe_b64decode(pad(signature_b64)), (header_b64 + "." + payload_b64).encode(),
                                             padding.PKCS1v15(), hashes.SHA256())
        header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
        claims = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
        if header != {"alg": "RS256", "typ": "JWT"}:
            raise ValueError("header")
        return claims


def _port_error(kind, cause):
    from app.services.enc_auth_identity_firebase import PortError
    return PortError(kind, cause)


class FakeRest:
    def __init__(self, world):
        self.world = world

    async def sign_in(self, email, password):
        fault = self.world._enter("rest.sign_in")
        if fault:
            if fault.get("throttle"):
                return None, "TOO_MANY_ATTEMPTS_TRY_LATER"
            raise _port_error(fault["kind"], fault["cause"])
        uid = self.world.find_by_email(email)
        account = self.world.accounts.get(uid)
        if account is None or account["password"] != password:
            return None, "INVALID_LOGIN_CREDENTIALS"          # unknown e-mail and wrong password: the same answer (enumeration protection ON)
        if account["disabled"]:
            return None, "USER_DISABLED"
        return uid, None


class FakeAdmin:
    def __init__(self, world):
        self.world = world

    async def get_user(self, uid):
        fault = self.world._enter("admin.get_user")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        account = self.world.accounts.get(uid)
        return None if account is None else {"uid": uid, "email": account["email"], "disabled": account["disabled"]}

    async def create_user(self, email, password):
        fault = self.world._enter("admin.create_user")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        if self.world.find_by_email(email):
            raise _port_error("email_exists", "sdk_error")
        if len(password) < self.world.min_password_length:
            raise _port_error("invalid_argument", "sdk_error")
        return self.world.add_account(email, password)

    async def set_password(self, uid, password):
        fault = self.world._enter("admin.set_password")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        if uid not in self.world.accounts:
            raise _port_error("not_found", "sdk_error")
        if len(password) < self.world.min_password_length:
            raise _port_error("invalid_argument", "sdk_error")
        self.world.accounts[uid]["password"] = password

    async def revoke(self, uid):
        fault = self.world._enter("admin.revoke")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        self.world.accounts[uid]["valid_since"] = int(self.world.clock())

    async def verify_token(self, token):
        fault = self.world._enter("admin.verify_token")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        record = self.world.id_tokens.get(token)
        if record is None:
            return None
        account = self.world.accounts.get(record["uid"])
        if account is None or account["disabled"]:
            return None
        if record["exp"] <= self.world.clock() or record["iat"] < account["valid_since"]:
            return None                                          # expired or revoked
        return {"uid": record["uid"], "email": account["email"], "iat": record["iat"]}


class FakeSigner:
    def __init__(self, world):
        self.world = world

    async def sign_blob(self, data):
        fault = self.world._enter("iam.sign_blob")
        if fault:
            raise _port_error(fault["kind"], fault["cause"])
        return self.world.private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def build_identity(world, clock=None):
    """The real adapter wired to the fake ports (lazy import: needs the built backend tree on sys.path)."""
    from app.services.enc_auth_identity_firebase import FirebaseIdentity
    return FirebaseIdentity(FakeRest(world), FakeAdmin(world), FakeSigner(world), world.service_account, clock or world.clock)
