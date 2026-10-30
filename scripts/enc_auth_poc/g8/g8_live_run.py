"""G8 interactive runner for Stage A, Stage B and the cleanup (Stage C). LIVE EXECUTION IS DISABLED: this file refuses to do anything while
`g8_guard.LIVE_EXECUTION_APPROVED` or the mode's `STAGE_APPROVED` entry is False, which is how it is committed. It is never imported by a test
into a live configuration; offline tests exercise it with injected prompts and fake collaborators only.

    python -B -I g8_live_run.py stage-a | stage-b | cleanup      (interactive terminal only)

Order of refusals, all BEFORE any secret is read or any socket is opened: approval constants -> interactive terminal -> environment (proxy, TLS
override, credentials, emulator) -> pinned adapter tree -> hooks installed and `cap_guaranteed()` -> typed project id -> typed phrase(s).
Secrets (Web API key, short-lived access tokens, the dummy account's e-mail / password / uid) are read with hidden prompts, never from argv or
the environment, and are never printed. Output is the fixed-vocabulary report only.
"""
import asyncio
import getpass
import os
import secrets as pysecrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_G3 = os.path.join(os.path.dirname(_HERE), "g3")          # sibling folder, derived from this file (python -I does not add either to sys.path)
for _path in (_G3, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import g3_guard as g3  # noqa: E402
import g8_guard as guard  # noqa: E402
import g8_protect as protect  # noqa: E402
import g8_stages as stages  # noqa: E402
import g8_wiring as wiring  # noqa: E402
from g3_rest_client import FirebaseRestClient  # noqa: E402
from g8_guard import LiveDisabled, LiveSafetyStop  # noqa: E402

MODES = {"stage-a": "A", "stage-b": "B", "cleanup": "C"}
PHRASES = {"A": "RUN STAGE A READ-ONLY", "B": "RUN STAGE B DISPOSABLE ACCOUNT", "C": "RUN CLEANUP OF THE DISPOSABLE ACCOUNT"}
MUTATION_PHRASES = {"create": "CONFIRM CREATE OF ONE DISPOSABLE ACCOUNT", "password": "CONFIRM PASSWORD UPDATE OF THE DISPOSABLE ACCOUNT",
                    "revoke": "CONFIRM REVOCATION FOR THE DISPOSABLE ACCOUNT", "disable": "CONFIRM DISABLE AND ENABLE OF THE DISPOSABLE ACCOUNT",
                    "delete": "CONFIRM DELETION OF THE DISPOSABLE ACCOUNT"}
STAGE_MUTATIONS = {"A": (), "B": ("create", "password", "revoke", "disable"), "C": ("delete",)}


class IO:
    """Injectable terminal: tests replace every member; the defaults are the real interactive ones."""

    def __init__(self, hidden=getpass.getpass, visible=input, is_tty=None, out=print):
        self.hidden, self.visible, self.out = hidden, visible, out
        self.is_tty = is_tty if is_tty is not None else (lambda: sys.stdin.isatty() and sys.stdout.isatty())


def _require(condition, code):
    if not condition:
        raise LiveSafetyStop(code)


def preflight(mode, io, tree_dir, env=None):
    """Everything that can refuse before a secret is read. Returns (stage, real_adapter, settings). Makes no network call."""
    stage = MODES.get(mode)
    _require(stage is not None, "mode_unknown")
    if guard.LIVE_EXECUTION_APPROVED is not True or guard.STAGE_APPROVED.get(stage) is not True:
        raise LiveDisabled("live_execution_not_approved")
    _require(io.is_tty(), "not_interactive")
    problem = guard.check_environment(env)
    _require(problem is None, problem or "environment")
    real, settings = wiring.verify_tree(tree_dir)
    wiring.disable_retries(settings)
    return stage, real, settings


def confirm(stage, io):
    """Typed project id, typed stage phrase, and one separate typed phrase per mutation class of that stage. Returns the confirmed classes."""
    if io.visible("Type the Firebase project id to confirm: ") != guard.ALLOWED_PROJECT_ID:
        raise LiveSafetyStop("project_not_confirmed")
    if io.visible("Type '%s': " % PHRASES[stage]) != PHRASES[stage]:
        raise LiveSafetyStop("stage_not_confirmed")
    granted = [cls for cls in STAGE_MUTATIONS[stage] if io.visible("Type '%s': " % MUTATION_PHRASES[cls]) == MUTATION_PHRASES[cls]]
    _require(set(granted) == set(STAGE_MUTATIONS[stage]), "mutation_not_confirmed")
    return granted


def generate_disposable():
    token = pysecrets.token_hex(8)
    password = lambda: pysecrets.token_urlsafe(18) + "aA1!"
    return {"email": "g8-%s@example.com" % token, "password": password(), "new_password": password()}


async def execute(mode, io, tree_dir, env=None):
    """The whole live flow. Reachable only when every approval constant was reviewed and flipped."""
    stage, real, settings = preflight(mode, io, tree_dir, env)
    granted = confirm(stage, io)
    signer = io.visible("Signer service account (<id>@%s.iam.gserviceaccount.com): " % guard.ALLOWED_PROJECT_ID)
    g3.validate_service_account(signer)
    egress = guard.Egress(stage, signer)
    guard.install_urllib3_hook(egress)
    guard.install_socket_backstop(egress)
    app, report = None, stages.Report(stage)
    try:
        ok, reasons = guard.cap_guaranteed(env, retry_max=settings.ENC_AUTH_RETRY_MAX)
        _require(ok, "cap_not_guaranteed:" + ",".join(reasons))
        api_key = g3.validate_api_key(g3.Secret(io.hidden("Web API key (hidden): ")))
        signer_token = g3.validate_bearer_token(g3.Secret(io.hidden("Signer access token, about one hour (hidden): ")))
        admin_token = g3.validate_bearer_token(g3.Secret(io.hidden("Admin access token for g8-admin, about one hour (hidden): ")))
        wiring.configure_settings(settings, signer, api_key)
        app = wiring.build_admin_app("g8-" + pysecrets.token_hex(4), admin_token)
        inner = wiring.build_identity(real, egress, api_key, signer, signer_token, app)
        client = FirebaseRestClient(g3.load_config(guard.ALLOWED_PROJECT_ID, api_key.reveal(), env), guard.g3_transport(egress))
        session, ops_port = wiring.G3Session(client), wiring.FirebaseAccountOps(app)
        if stage == "A":
            uid, email = io.hidden("Dummy account uid (hidden): "), io.hidden("Dummy account e-mail (hidden): ")
            state = protect.RunState("A", egress, uid, email)
            ctx = stages.Context(protect.GuardedIdentity(inner, state), session, state, egress, time.time, asyncio.sleep,
                                 {"dummy_email": email, "dummy_password": io.hidden("Dummy account password (hidden): ")})
            return await stages.run_stage_a(ctx, report)
        dummy_uid = io.hidden("Dummy account uid, used only to REFUSE it (hidden): ") if stage == "C" else None
        _require(stage != "C" or dummy_uid, "dummy_identity_missing")
        state = protect.RunState(stage, egress, dummy_uid)
        for cls in granted:
            state.mutations.confirm(cls)
        secrets = generate_disposable() if stage == "B" else {"email": io.visible("Cleanup handle printed by Stage B (g8-<16 hex>): ") + "@example.com"}
        ctx = stages.Context(protect.GuardedIdentity(inner, state), session, state, egress, time.time, asyncio.sleep, secrets,
                             protect.GuardedAccountOps(ops_port, state))
        return await (stages.run_stage_b(ctx, report) if stage == "B" else stages.run_stage_c(ctx, report))
    except (asyncio.CancelledError, KeyboardInterrupt):
        io.out(report.render())                                    # a cancelled Stage B still prints its cleanup handle
        raise
    finally:
        if app is not None:
            wiring.delete_app(app)
        guard.uninstall_all()


def main(argv, io=None):
    io = io or IO()
    if len(argv) != 2 or argv[1] not in MODES:
        io.out("usage: g8_live_run.py stage-a | stage-b | cleanup")
        return 2
    stage = MODES[argv[1]]
    if guard.LIVE_EXECUTION_APPROVED is not True or guard.STAGE_APPROVED.get(stage) is not True:
        io.out("G8 stopped: live_execution_not_approved")             # a disabled mode asks for nothing: no tree path, no prompt of any kind
        return 2
    try:
        tree = io.visible("Path of the hash-gated G7 tree: ")
        report = asyncio.run(execute(argv[1], io, tree))
    except LiveSafetyStop as stop:
        io.out("G8 stopped: " + stop.code.split(":")[0])
        return 2
    except g3.GuardError as refused:                           # a missing or malformed key / token / signer: fixed reason code only
        io.out("G8 stopped: " + refused.reason)
        return 2
    io.out(report.render())
    return 0 if report.clean() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
