"""G8 stage scripts: Stage A (existing dummy account, READ-ONLY), Stage B (one disposable account created by the run), Stage C (cleanup of that
disposable account). Standard library only; every collaborator is injected, so offline tests drive these functions with the G7 fake Firebase and
a live run (later, separately approved) drives them with the real ports. Nothing here opens a connection.

Outcome vocabulary of a check: "pass" | "fail" | "ambiguous" | "not_run". `ambiguous` is NEVER a pass: it means the answer could not prove
the point (a throttle, an outage, a same-second revocation timestamp). A run is clean only if every check passed and nothing stopped it.
The report contains check ids, outcomes, request counts per kind and fixed codes - no e-mail, uid, token, password or key.
"""
import asyncio
from dataclasses import dataclass, field

import g8_revocation as rev
from g8_guard import LiveSafetyStop

WRONG_PASSWORD_CODES = frozenset({"INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD", "EMAIL_NOT_FOUND"})
SETTLE_SECONDS = 5.0                               # > revocation.SKEW_SECONDS + 1: the revoke window is closed before the next token is issued
assert SETTLE_SECONDS > rev.SKEW_SECONDS + 1


@dataclass
class SessionOutcome:
    """What the session port (the G3 REST client in a live run) tells the stage: no free text, no values except the Secrets themselves."""
    ok: bool
    code: str = "-"                  # a Firebase error code from G3's fixed set, "-" if none
    id_token: object = None          # Secret
    refresh_token: object = None     # Secret
    is_new_user: object = None


@dataclass
class Claims:
    uid: str
    issued_at: int
    provider: str


class Report:
    def __init__(self, stage):
        self.stage, self.checks, self.stop_code = stage, [], None
        self.notes, self.requests, self.cleanup_handle = [], ({}, 0), None
        self.created_account = False

    def add(self, check_id, outcome):
        assert outcome in ("pass", "fail", "ambiguous", "not_run")
        self.checks.append((check_id, outcome))

    def outcome(self, check_id):
        return next((o for c, o in self.checks if c == check_id), "not_run")

    def clean(self):
        return self.stop_code is None and bool(self.checks) and all(o == "pass" for _, o in self.checks)

    def counts(self):
        return {o: sum(1 for _, x in self.checks if x == o) for o in ("pass", "fail", "ambiguous", "not_run")}

    def render(self):
        lines = ["stage=%s clean=%s stop=%s" % (self.stage, self.clean(), self.stop_code or "-")]
        lines += ["%s %s" % item for item in self.checks]
        used, total = self.requests
        lines.append("requests total=%d %s" % (total, " ".join("%s=%d" % kv for kv in sorted(used.items()))))
        if self.cleanup_handle:
            lines.append("cleanup_pending handle=%s" % self.cleanup_handle)
        return "\n".join(lines)


@dataclass
class Context:
    identity: object                      # GuardedIdentity
    session: object                       # SessionPort (async): load_certs, sign_in, refresh, exchange, claims
    state: object                         # RunState
    egress: object
    clock: object                         # callable -> float seconds
    sleep: object                         # async callable(seconds)
    secrets: dict = field(default_factory=dict)      # Stage A: dummy_email, dummy_password (str); Stage B: generated
    ops: object = None                    # GuardedAccountOps (B, C)
    settle_seconds: float = SETTLE_SECONDS
    pre_revoke_wait: float = SETTLE_SECONDS       # real latency between requests is small: wait so the old token is provably older than the revoke window


async def _call(awaitable):
    """(error_name, value). Library errors become a class NAME only (never a message); safety stops and cancellation are not caught."""
    try:
        return None, await awaitable
    except Exception as exc:
        return type(exc).__name__, None


def _grade(report, check_id, condition, error=None):
    report.add(check_id, "ambiguous" if error in ("IdentityUnavailable",) else ("pass" if condition else "fail"))


def _answer(error, claims_or_none):
    if error is not None:
        return "error"
    return "accepted" if claims_or_none else "rejected"


async def _run(report, ctx, body):
    try:
        await body(report, ctx)
    except LiveSafetyStop as stop:
        report.stop_code = stop.code
    except (asyncio.CancelledError, KeyboardInterrupt):
        report.stop_code = "cancelled"
        raise
    finally:
        report.requests = ctx.egress.used()
        state = ctx.state
        report.created_account = state.created_uid is not None
        if state.stage == "B" and state.created_email:             # also when the outcome of the create call is unknown: the account may exist
            report.cleanup_handle = state.created_email.split("@")[0]
        if state.account_created_unexpectedly:
            report.notes.append("account_created_unexpectedly")
    return report


# ---------------------------------------------------------------------------------------------------------------------------
# Stage A - the existing dummy account, read-only
# ---------------------------------------------------------------------------------------------------------------------------

async def _stage_a(report, ctx):
    email, password = ctx.secrets["dummy_email"], ctx.secrets["dummy_password"]
    err, answer = await _call(ctx.identity.verify_password(email, password))
    uid = answer[0] if answer and answer[1] is None else None
    _grade(report, "A01", uid is not None and ctx.state.is_dummy_uid(uid), err)
    if report.outcome("A01") != "pass":                        # the password sign-in did not prove the dummy account: go no further
        for later in ("A02", "A03", "A04", "A05", "A06", "A07"):
            report.add(later, "not_run")
        return
    err, answer = await _call(ctx.identity.verify_password(email, password + "-wrong"))
    code = answer[1] if answer else None
    report.add("A02", "ambiguous" if code == "TOO_MANY_ATTEMPTS_TRY_LATER" or err else ("pass" if code in WRONG_PASSWORD_CODES else "fail"))
    err, account = await _call(ctx.identity.get_account(uid))
    _grade(report, "A03", account is not None and not account.disabled, err)
    err, custom = await _call(ctx.identity.mint_custom_token(uid))
    _grade(report, "A04", isinstance(custom, str), err)
    if not isinstance(custom, str):
        for later in ("A05", "A06", "A07"):
            report.add(later, "not_run")
        return
    outcome = await ctx.session.exchange(custom)
    if outcome.ok:
        ctx.state.assert_exchange_did_not_create(outcome)
    report.add("A05", "pass" if outcome.ok else "fail")
    if not outcome.ok:
        report.add("A06", "not_run")
        report.add("A07", "not_run")
        return
    err, admin_claims = await _call(ctx.identity.verify_id_token(outcome.id_token.reveal()))
    _grade(report, "A06", bool(admin_claims) and ctx.state.is_dummy_uid(admin_claims.get("uid")), err)
    await ctx.session.load_certs()
    claims = await ctx.session.claims(outcome.id_token.reveal())
    report.add("A07", "pass" if claims is not None and ctx.state.is_dummy_uid(claims.uid) and claims.provider == "custom" else "fail")


async def run_stage_a(ctx, report=None):
    return await _run(report or Report("A"), ctx, _stage_a)


# ---------------------------------------------------------------------------------------------------------------------------
# Stage B - one disposable account created by this run (the dummy account is not known to this stage at all)
# ---------------------------------------------------------------------------------------------------------------------------

async def _stage_b(report, ctx):
    ident, state, session = ctx.identity, ctx.state, ctx.session
    email, password, new_password = ctx.secrets["email"], ctx.secrets["password"], ctx.secrets["new_password"]
    await session.load_certs()
    # --- create, duplicate -------------------------------------------------------------------------------------------------
    err, uid = await _call(ident.create_user(email, password))
    report.add("B01", "pass" if uid and not err else "fail")
    if not uid:
        for n in range(2, 26):
            report.add("B%02d" % n, "not_run")
        return
    err, _ = await _call(ident.create_user(email, password))
    report.add("B02", "pass" if err == "EmailExists" else "fail")
    report.add("B03", "pass" if isinstance(uid, str) and 1 <= len(uid) <= 128 else "fail")        # local check: no request (the SDK already read the new record back)
    # --- password sign-in ----------------------------------------------------------------------------------------------------
    err, answer = await _call(ident.verify_password(email, password))
    _grade(report, "B04", bool(answer) and answer[0] == uid, err)
    err, answer = await _call(ident.verify_password(email, password + "-wrong"))
    report.add("B05", "ambiguous" if err or (answer and answer[1] == "TOO_MANY_ATTEMPTS_TRY_LATER") else ("pass" if answer and answer[1] in WRONG_PASSWORD_CODES else "fail"))
    t0 = await session.sign_in(email, password)
    report.add("B06", "pass" if t0.ok else "fail")
    if not t0.ok:
        for n in range(7, 26):
            report.add("B%02d" % n, "not_run")
        return
    old_id, old_refresh = t0.id_token.reveal(), t0.refresh_token
    old_claims = await session.claims(old_id)
    err, admin_claims = await _call(ident.verify_id_token(old_id))
    _grade(report, "B07", bool(admin_claims) and admin_claims.get("uid") == uid and old_claims is not None and old_claims.uid == uid, err)
    # --- password update by the Admin SDK ------------------------------------------------------------------------------------
    err, _ = await _call(ident.update_password(uid, new_password))
    report.add("B08", "pass" if err is None else ("ambiguous" if err == "IdentityUnavailable" else "fail"))
    err, answer = await _call(ident.verify_password(email, password))
    report.add("B09", "ambiguous" if err or (answer and answer[1] == "TOO_MANY_ATTEMPTS_TRY_LATER") else ("pass" if answer and answer[0] is None and answer[1] in WRONG_PASSWORD_CODES else "fail"))
    err, answer = await _call(ident.verify_password(email, new_password))
    _grade(report, "B10", bool(answer) and answer[0] == uid, err)
    # --- revocation, with the whole-second timestamp edge case handled explicitly ---------------------------------------------
    if ctx.pre_revoke_wait > 0:
        await ctx.sleep(ctx.pre_revoke_wait)
    before = ctx.clock()
    err, _ = await _call(ident.revoke_refresh_tokens(uid))
    after = ctx.clock()
    revoke_window = rev.window(before, after) if err is None else None
    report.add("B11", "pass" if err is None else ("ambiguous" if err == "IdentityUnavailable" else "fail"))
    err, admin_claims = await _call(ident.verify_id_token(old_id))
    report.add("B12", _grade_revocation(rev.classify_old_token(_answer(err, admin_claims), old_claims.issued_at if old_claims else None, revoke_window)))
    refreshed = await session.refresh(old_refresh)
    report.add("B13", _grade_revocation(rev.classify_refresh(None if refreshed.ok else refreshed.code, revoke_window, ctx.clock())))
    await ctx.sleep(ctx.settle_seconds)
    t1 = await session.sign_in(email, new_password)
    new_claims = await session.claims(t1.id_token.reveal()) if t1.ok else None
    report.add("B14", "pass" if t1.ok and new_claims is not None else "fail")
    new_answer = "error"
    if t1.ok:
        err, admin_claims = await _call(ident.verify_id_token(t1.id_token.reveal()))
        new_answer = _answer(err, admin_claims)
    report.add("B15", _grade_revocation(rev.classify_new_token(new_answer, new_claims.issued_at if new_claims else None, revoke_window)))
    # --- disable -------------------------------------------------------------------------------------------------------------
    err, _ = await _call(ctx.ops.set_disabled(uid, True))
    report.add("B16", "pass" if err is None else ("ambiguous" if err == "OpsUnavailable" else "fail"))
    err, account = await _call(ident.get_account(uid))
    _grade(report, "B17", account is not None and account.disabled is True, err)
    err, answer = await _call(ident.verify_password(email, new_password))
    report.add("B18", "ambiguous" if err else ("pass" if answer and answer[0] is None and answer[1] == "USER_DISABLED" else "fail"))
    if t1.ok:
        err, admin_claims = await _call(ident.verify_id_token(t1.id_token.reveal()))
        report.add("B19", "ambiguous" if err else ("pass" if admin_claims is None else "fail"))
        disabled_refresh = await session.refresh(t1.refresh_token)
        report.add("B20", "pass" if not disabled_refresh.ok and disabled_refresh.code == "USER_DISABLED" else ("fail" if disabled_refresh.ok else "ambiguous"))
    else:
        report.add("B19", "not_run")
        report.add("B20", "not_run")
    err, custom = await _call(ident.mint_custom_token(uid))
    report.add("B21", "pass" if isinstance(custom, str) else ("ambiguous" if err == "IdentityUnavailable" else "fail"))
    if isinstance(custom, str):
        outcome = await session.exchange(custom)
        if outcome.ok:
            state.assert_exchange_did_not_create(outcome)
        report.add("B22", "fail" if outcome.ok else ("pass" if outcome.code == "USER_DISABLED" else "ambiguous"))    # a disabled account must get no session
    else:
        report.add("B22", "not_run")
    # --- enable, custom-token session ----------------------------------------------------------------------------------------
    err, _ = await _call(ctx.ops.set_disabled(uid, False))
    report.add("B23", "pass" if err is None else ("ambiguous" if err == "OpsUnavailable" else "fail"))
    err, account = await _call(ident.get_account(uid))
    _grade(report, "B24", account is not None and account.disabled is False, err)
    err, custom = await _call(ident.mint_custom_token(uid))
    outcome = await session.exchange(custom) if isinstance(custom, str) else SessionOutcome(False)
    if outcome.ok:
        state.assert_exchange_did_not_create(outcome)
    claims = await session.claims(outcome.id_token.reveal()) if outcome.ok else None
    report.add("B25", "pass" if claims is not None and claims.uid == uid and claims.provider == "custom" else "fail")
    # cleanup is NOT part of this run: Stage C, separately approved; the report prints the handle for it


def _grade_revocation(classification):
    return {rev.REVOKED: "pass", rev.VALID: "pass", rev.FAILED: "fail", rev.AMBIGUOUS: "ambiguous"}[classification]


async def run_stage_b(ctx, report=None):
    return await _run(report or Report("B"), ctx, _stage_b)


# ---------------------------------------------------------------------------------------------------------------------------
# Stage C - cleanup of the disposable account (only after explicit approval; the dummy account is refused by digest)
# ---------------------------------------------------------------------------------------------------------------------------

async def _stage_c(report, ctx):
    email = ctx.secrets["email"]
    err, uid = await _call(ctx.ops.find_uid(email))
    if err is None and uid is None:
        report.add("C01", "pass")                               # nothing to delete: already absent
        report.notes.append("already_absent")
        return
    if err or not isinstance(uid, str):
        report.add("C01", "ambiguous")
        report.add("C02", "not_run")
        report.add("C03", "not_run")
        return
    ctx.state.adopt_for_cleanup(uid, email)
    report.add("C01", "pass")
    err, _ = await _call(ctx.ops.delete_user(uid))
    report.add("C02", "pass" if err is None else ("ambiguous" if err == "OpsUnavailable" else "fail"))
    err, account = await _call(ctx.identity.get_account(uid))
    report.add("C03", "ambiguous" if err else ("pass" if account is None else "fail"))


async def run_stage_c(ctx, report=None):
    return await _run(report or Report("C"), ctx, _stage_c)
