"""Shared support for the G8 offline tests: paths, the G7 network-denial layer and fake Firebase (reused, not copied), and the G8 fakes that
CHARGE the real `Egress` for every operation, so a stage script's request count is measured against the real quotas. No network, ever."""
import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
G8 = HERE.parent
POC = G8.parent
G7 = POC / "g7"
for _p in (G7 / "harness", G7 / "tests", POC / "g3", G8):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _tree  # noqa: E402  (installs the network-denial layer; honours G7_TREE)

BACKEND = _tree.use_backend_tree()

import fake_firebase as ff  # noqa: E402
import g3_guard as g3  # noqa: E402
import g8_guard as guard  # noqa: E402
import g8_protect as protect  # noqa: E402
import g8_stages as stages  # noqa: E402
from app.services import enc_auth_identity as base  # noqa: E402,F401
from app.services import enc_auth_identity_firebase as real  # noqa: E402

PROJECT = guard.ALLOWED_PROJECT_ID
SIGNER = "g3-signer@%s.iam.gserviceaccount.com" % PROJECT
API_KEY = "AIza" + "B" * 35
BASE = "https://identitytoolkit.googleapis.com/v1"
PROJ = BASE + "/projects/" + PROJECT
URLS = {
    "sign_in": ("POST", BASE + "/accounts:signInWithPassword?key=" + API_KEY),
    "custom_exchange": ("POST", BASE + "/accounts:signInWithCustomToken?key=" + API_KEY),
    "refresh": ("POST", "https://securetoken.googleapis.com/v1/token?key=" + API_KEY),
    "lookup": ("POST", PROJ + "/accounts:lookup"),
    "create": ("POST", PROJ + "/accounts"),
    "update": ("POST", PROJ + "/accounts:update"),
    "delete": ("POST", PROJ + "/accounts:delete"),
    "certs": ("GET", "https://www.googleapis.com" + guard.CERTS_PATH),
    "sign_blob": ("POST", "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/%s:signBlob" % SIGNER),
}


def charge(egress, kind, uid=None):
    method, url = URLS[kind]
    body = json.dumps({"localId": uid}).encode() if uid is not None else b"{}"
    return egress.charge(method, url, body, 5.0)


class FakeClock:
    def __init__(self, start=1_700_000_000.0, step=0.0):
        self.now, self.step = start, step

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.step

    def advance(self, seconds):
        self.now += seconds


class ChargingRest(ff.FakeRest):
    def __init__(self, world, egress, clock):
        super().__init__(world)
        self.egress, self.clock = egress, clock

    async def sign_in(self, email, password):
        charge(self.egress, "sign_in")
        self.clock.tick()
        return await super().sign_in(email, password)


class ChargingAdmin(ff.FakeAdmin):
    """Charges what the REAL SDK sends (measured in test_sdk_paths): create and update read the record back with one extra lookup."""

    def __init__(self, world, egress, clock, ignore_revoke=False):
        super().__init__(world)
        self.egress, self.clock, self.ignore_revoke = egress, clock, ignore_revoke
        self.mutated = []

    async def get_user(self, uid):
        charge(self.egress, "lookup")
        self.clock.tick()
        return await super().get_user(uid)

    async def create_user(self, email, password):
        charge(self.egress, "create")
        self.clock.tick()
        uid = await super().create_user(email, password)
        charge(self.egress, "lookup")
        self.mutated.append(("create", uid))
        return uid

    async def set_password(self, uid, password):
        charge(self.egress, "update", uid)
        self.clock.tick()
        await super().set_password(uid, password)
        charge(self.egress, "lookup")
        self.mutated.append(("password", uid))

    async def revoke(self, uid):
        charge(self.egress, "update", uid)
        self.clock.tick()
        if not self.ignore_revoke:
            await super().revoke(uid)
        self.mutated.append(("revoke", uid))

    async def verify_token(self, token):
        charge(self.egress, "certs")
        self.clock.tick()
        record = self.world.id_tokens.get(token)
        if record is not None and record["uid"] in self.world.accounts:
            charge(self.egress, "lookup")
        return await super().verify_token(token)


class ChargingSigner(ff.FakeSigner):
    def __init__(self, world, egress, clock):
        super().__init__(world)
        self.egress, self.clock = egress, clock

    async def sign_blob(self, data):
        charge(self.egress, "sign_blob")
        self.clock.tick()
        return await super().sign_blob(data)


class FakeOps:
    """Admin disable / enable / delete / find by e-mail, charging like the SDK does (update_user reads the record back)."""

    def __init__(self, world, egress, clock):
        self.world, self.egress, self.clock = world, egress, clock
        self.mutated = []

    async def set_disabled(self, uid, flag):
        charge(self.egress, "update", uid)
        self.clock.tick()
        self.world.accounts[uid]["disabled"] = bool(flag)
        charge(self.egress, "lookup")
        self.mutated.append(("disable" if flag else "enable", uid))

    async def delete_user(self, uid):
        charge(self.egress, "delete", uid)
        self.clock.tick()
        self.world.accounts.pop(uid, None)
        self.mutated.append(("delete", uid))

    async def find_uid(self, email):
        charge(self.egress, "lookup")
        self.clock.tick()
        return self.world.find_by_email(email)


class FakeSession:
    """The G3 session facade over the fake world: refresh tokens, custom-token exchange (creates unknown uids, like Firebase), claims."""

    def __init__(self, world, egress, clock):
        self.world, self.egress, self.clock = world, egress, clock
        self.refresh_tokens, self.providers, self.created_by_exchange = {}, {}, []
        self._ids = itertools.count(1)

    async def load_certs(self):
        charge(self.egress, "certs")

    def _issue(self, uid, provider, new=None):
        iat = int(self.clock())
        token = self.world.id_token_for(uid, issued_at=iat)
        refresh = "fake-refresh-%d" % next(self._ids)
        self.refresh_tokens[refresh] = (uid, iat)
        self.providers[token] = provider
        return stages.SessionOutcome(True, "-", g3.Secret(token), g3.Secret(refresh), new)

    async def sign_in(self, email, password):
        charge(self.egress, "sign_in")
        self.clock.tick()
        uid = self.world.find_by_email(email)
        account = self.world.accounts.get(uid)
        if account is None or account["password"] != password:
            return stages.SessionOutcome(False, "INVALID_LOGIN_CREDENTIALS")
        if account["disabled"]:
            return stages.SessionOutcome(False, "USER_DISABLED")
        return self._issue(uid, "password")

    async def refresh(self, secret):
        charge(self.egress, "refresh")
        self.clock.tick()
        record = self.refresh_tokens.get(secret.reveal())
        account = self.world.accounts.get(record[0]) if record else None
        if account is None:
            return stages.SessionOutcome(False, "USER_NOT_FOUND")
        if account["disabled"]:
            return stages.SessionOutcome(False, "USER_DISABLED")
        if record[1] < account["valid_since"]:
            return stages.SessionOutcome(False, "TOKEN_EXPIRED")
        return self._issue(record[0], "password")

    async def exchange(self, custom_token):
        charge(self.egress, "custom_exchange")
        self.clock.tick()
        uid = self.world.verify_custom_token(custom_token)["uid"]
        new = uid not in self.world.accounts
        if new:
            self.world.add_account("created.by.exchange.%s@example.com" % uid, "x" * 12, uid=uid)
            self.created_by_exchange.append(uid)
        if self.world.accounts[uid]["disabled"]:
            return stages.SessionOutcome(False, "USER_DISABLED")
        return self._issue(uid, "custom", new)

    async def claims(self, id_token):
        record = self.world.id_tokens.get(id_token)
        if record is None:
            return None
        return stages.Claims(record["uid"], record["iat"], self.providers.get(id_token, "other"))


class World(ff.World):
    """The G7 fake world with Firebase-shaped (alphanumeric) uids."""

    def add_account(self, email, password, disabled=False, uid=None):
        return super().add_account(email, password, disabled, uid or "fakeuid%08d" % next(self._ids))


class Lab:
    """One offline run: the G7 fake world, the real G7 adapter on charging fake ports, the real G8 protection and egress."""

    def __init__(self, stage, step=1.5, with_dummy=False, ignore_revoke=False, world=None, protect_dummy=False, session_cls=None, clock=None):
        self.clock = clock or FakeClock(step=step)
        self.world = world or World(project=PROJECT, signer="g3-signer", clock=self.clock)
        self.dummy = ("dummyuid00000001", "dummy.person@example.net", "Dummy-Passw0rd!")
        if (with_dummy or stage == "A") and self.dummy[0] not in self.world.accounts:
            self.world.add_account(self.dummy[1], self.dummy[2], uid=self.dummy[0])
        self.egress = guard.Egress(stage, SIGNER)
        self.admin = ChargingAdmin(self.world, self.egress, self.clock, ignore_revoke)
        inner = real.FirebaseIdentity(ChargingRest(self.world, self.egress, self.clock), self.admin,
                                      ChargingSigner(self.world, self.egress, self.clock), self.world.service_account, self.clock)
        if stage == "A":
            self.state = protect.RunState("A", self.egress, self.dummy[0], self.dummy[1])
        elif stage == "C" and protect_dummy:
            self.state = protect.RunState("C", self.egress, self.dummy[0], None)
        else:
            self.state = protect.RunState(stage, self.egress)
        self.ops = FakeOps(self.world, self.egress, self.clock)
        self.session = (session_cls or FakeSession)(self.world, self.egress, self.clock)
        self.slept = []

        async def sleep(seconds):
            self.slept.append(seconds)
            self.clock.advance(seconds)

        secrets = {}
        if stage == "A":
            secrets = {"dummy_email": self.dummy[1], "dummy_password": self.dummy[2]}
        if stage == "B":
            secrets = {"email": "g8-0123456789abcdef@example.com", "password": "Start-Passw0rd-1", "new_password": "Second-Passw0rd-2"}
        if stage == "C":
            secrets = {"email": "g8-0123456789abcdef@example.com"}
        self.identity = protect.GuardedIdentity(inner, self.state)
        self.ctx = stages.Context(self.identity, self.session, self.state, self.egress, self.clock, sleep, secrets,
                                  protect.GuardedAccountOps(self.ops, self.state))

    def confirm_all(self, *classes):
        for cls in classes:
            self.state.mutations.confirm(cls)

    def dummy_record(self):
        return dict(self.world.accounts[self.dummy[0]]) if self.dummy[0] in self.world.accounts else None


class LenientSession(FakeSession):
    """A broken Firebase that hands a custom-token session to a DISABLED account (the harness must call that a failure)."""

    async def exchange(self, custom_token):
        charge(self.egress, "custom_exchange")
        self.clock.tick()
        uid = self.world.verify_custom_token(custom_token)["uid"]
        return self._issue(uid, "custom", False)
