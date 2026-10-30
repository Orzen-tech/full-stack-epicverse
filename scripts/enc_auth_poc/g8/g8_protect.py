"""G8 account protection. Standard library only; makes no network call.

Two wrappers sit between the stage scripts and the real G7 adapter / Admin SDK:

* `GuardedIdentity` - the same method names as `FirebaseIdentity`. The EXISTING DUMMY ACCOUNT (Stage A) is read-only: every mutating method
  refuses, whatever was confirmed. In Stage B the only account that may be touched is the one THIS run created (the "disposable" account);
  the dummy account's uid and e-mail are never even supplied to Stage B.
* `GuardedAccountOps` - disable / enable / delete of the disposable account, nothing else.

Mutations are individually confirmed by a human (`Mutations.confirm`): create, password, revoke, disable, delete. An unconfirmed class refuses.
Refusals raise LiveSafetyStop (a BaseException) with a fixed code; no uid, e-mail or password ever appears in a message.
"""
import hashlib
import re

from g8_guard import MUTATING_KINDS, LiveSafetyStop

CLASSES = ("create", "password", "revoke", "disable", "delete")
KIND_OF = {"create": "create", "password": "update", "revoke": "update", "disable": "update", "delete": "delete"}
DISPOSABLE_EMAIL_RE = re.compile(r"g8-[0-9a-f]{16}@example\.com")
MAX_CREATE_CALLS = 2                                  # the account itself and one duplicate-create probe with the SAME e-mail


def _digest(value):
    return hashlib.sha256(("g8|" + str(value).strip().lower()).encode("utf-8")).hexdigest()


class Mutations:
    """Which mutation classes a human has separately confirmed in this run. Stage A can never confirm any."""

    def __init__(self, stage, egress):
        self._stage, self._egress, self._confirmed = stage, egress, set()

    def confirm(self, cls):
        if self._stage not in ("B", "C") or cls not in CLASSES or (self._stage == "C" and cls != "delete"):
            raise LiveSafetyStop("mutation_not_allowed")
        self._egress.enable_mutation(KIND_OF[cls])
        self._confirmed.add(cls)

    def require(self, cls):
        if self._stage == "A":
            raise LiveSafetyStop("mutation_forbidden_stage_a")
        if cls not in self._confirmed:
            raise LiveSafetyStop("mutation_not_confirmed")

    def confirmed(self):
        return frozenset(self._confirmed)


class RunState:
    """The identity facts of one run. Everything is compared by digest or equality; nothing is printed.
    Stage B never receives the dummy identity at all; the cleanup stage C may receive its uid ONLY as a digest to refuse against."""

    def __init__(self, stage, egress, dummy_uid=None, dummy_email=None):
        if stage not in ("A", "B", "C"):
            raise LiveSafetyStop("stage_unknown")
        if stage == "A" and (not dummy_uid or not dummy_email):
            raise LiveSafetyStop("dummy_identity_missing")
        if stage == "B" and (dummy_uid or dummy_email):
            raise LiveSafetyStop("dummy_identity_not_allowed_in_stage_b")
        self.stage, self.egress = stage, egress
        self._dummy_uid = _digest(dummy_uid) if dummy_uid else None
        self._dummy_email = _digest(dummy_email) if dummy_email else None
        self.created_uid = None
        self.created_email = None
        self.create_calls = 0
        self.verified_uids = set()
        self.mutations = Mutations(stage, egress)
        self.account_created_unexpectedly = False

    def is_dummy_uid(self, uid):
        return self._dummy_uid is not None and _digest(uid) == self._dummy_uid

    def is_dummy_email(self, email):
        return self._dummy_email is not None and _digest(email) == self._dummy_email

    def check_read_uid(self, uid):
        if self.stage == "A":
            if not self.is_dummy_uid(uid):
                raise LiveSafetyStop("uid_not_the_dummy")
        elif uid is None or uid != self.created_uid or self.is_dummy_uid(uid):
            raise LiveSafetyStop("uid_not_disposable")

    def check_email(self, email):
        if self.stage == "A":
            if not self.is_dummy_email(email):
                raise LiveSafetyStop("email_not_the_dummy")
        elif email is None or email != self.created_email:
            raise LiveSafetyStop("email_not_disposable")

    def adopt_for_cleanup(self, uid, email):
        """Cleanup run only: the account found by its generated e-mail becomes the (only) deletable target - never the dummy."""
        if self.stage != "C" or self.created_uid is not None:
            raise LiveSafetyStop("adopt_refused")
        if not isinstance(email, str) or not DISPOSABLE_EMAIL_RE.fullmatch(email) or self.is_dummy_uid(uid) or self.is_dummy_email(email):
            raise LiveSafetyStop("adopt_not_disposable")
        self.created_uid, self.created_email = uid, email
        self.egress.bind_disposable_uid(uid)
        self.verified_uids.add(uid)

    def require_disposable(self, uid, cls):
        self.mutations.require(cls)
        if self.created_uid is None or uid != self.created_uid or self.is_dummy_uid(uid):
            raise LiveSafetyStop("mutation_target_not_disposable")

    def assert_exchange_did_not_create(self, tokens):
        """signInWithCustomToken CREATES an account for an unknown uid. isNewUser must be exactly False."""
        if getattr(tokens, "is_new_user", None) is not False:
            self.account_created_unexpectedly = True
            raise LiveSafetyStop("custom_exchange_created_or_unknown_account")


class GuardedIdentity:
    def __init__(self, inner, state):
        self._inner, self._state = inner, state

    async def verify_password(self, email, password):
        self._state.check_email(email)
        return await self._inner.verify_password(email, password)

    async def get_account(self, uid):
        self._state.check_read_uid(uid)
        account = await self._inner.get_account(uid)
        if account is not None:
            self._state.verified_uids.add(uid)
        return account

    async def create_user(self, email, password):
        state = self._state
        state.mutations.require("create")
        if not isinstance(email, str) or not DISPOSABLE_EMAIL_RE.fullmatch(email) or state.is_dummy_email(email):
            raise LiveSafetyStop("email_not_disposable_shape")
        if state.create_calls >= MAX_CREATE_CALLS:
            raise LiveSafetyStop("create_calls_exhausted")
        if state.created_email is not None and email != state.created_email:
            raise LiveSafetyStop("second_create_with_other_email")
        state.create_calls += 1
        state.created_email = state.created_email or email
        uid = await self._inner.create_user(email, password)
        if state.created_uid is not None:
            raise LiveSafetyStop("duplicate_create_succeeded")
        state.created_uid = uid
        state.egress.bind_disposable_uid(uid)
        state.verified_uids.add(uid)
        return uid

    async def update_password(self, uid, new_password):
        self._state.require_disposable(uid, "password")
        return await self._inner.update_password(uid, new_password)

    async def revoke_refresh_tokens(self, uid):
        self._state.require_disposable(uid, "revoke")
        return await self._inner.revoke_refresh_tokens(uid)

    async def mint_custom_token(self, uid):
        if uid not in self._state.verified_uids:
            raise LiveSafetyStop("mint_for_unverified_uid")
        return await self._inner.mint_custom_token(uid)

    async def verify_id_token(self, token):
        return await self._inner.verify_id_token(token)


class GuardedAccountOps:
    """disable / enable / delete of the disposable account, through an injected port with `set_disabled(uid, flag)`, `delete_user(uid)` and
    `find_uid(email)` (cleanup only: the generated disposable e-mail -> uid, a read)."""

    def __init__(self, port, state):
        self._port, self._state = port, state

    async def find_uid(self, email):
        if self._state.stage != "C" or not isinstance(email, str) or not DISPOSABLE_EMAIL_RE.fullmatch(email) or self._state.is_dummy_email(email):
            raise LiveSafetyStop("find_refused")
        return await self._port.find_uid(email)

    async def set_disabled(self, uid, flag):
        self._state.require_disposable(uid, "disable")
        return await self._port.set_disabled(uid, bool(flag))

    async def delete_user(self, uid):
        self._state.require_disposable(uid, "delete")
        return await self._port.delete_user(uid)


assert set(KIND_OF.values()) <= MUTATING_KINDS
