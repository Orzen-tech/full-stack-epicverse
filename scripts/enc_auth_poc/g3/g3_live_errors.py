"""G3 Phase 3 live entry point (Task A): how real Firebase answers a WRONG PASSWORD and a NONEXISTENT E-MAIL. Run it yourself, interactively:
    python -B -I g3_live_errors.py

At most TWO requests, both POST to the already approved sign-in endpoint, once each, no retries:
  1. the dummy account's e-mail with a random wrong password generated in memory;
  2. a random, clearly nonexistent address (reserved .invalid domain) with another random password.
You type only the dummy e-mail and the Web API key (hidden prompts) and then the exact project id. No real password is needed or used. It
stops after an unexpected answer, prints only the HTTP status and a sanitised error code from a fixed set, and ends the process afterwards.
"""
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # `python -I` does not add the script folder itself

import g3_guard as guard  # noqa: E402
import g3_rest_client as rc  # noqa: E402
from g3_live_run import _fixed  # noqa: E402

OPERATIONS = ("POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword   (case 1: dummy e-mail + a RANDOM wrong password)",
              "POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword   (case 2: a nonexistent .invalid e-mail + a random password)")
_RAN = []


def _case(out, name, result):
    d = result.detail()
    out("%s: outcome=%s http_status=%s error_code=%s cause=%s" % (name, _fixed(d["outcome"]), d["http_status"], _fixed(d["error_code"]), _fixed(d["cause"])))
    return d


def run(*, interactive, getpass_fn, input_fn, out, transport_factory, env, random_token=secrets.token_urlsafe):
    if _RAN:
        out("refused: a live run was already started in this process")
        return 2
    _RAN.append(True)
    if interactive is not True:
        out("refused: this needs an interactive terminal (nothing was asked, nothing was sent)")
        return 2
    try:
        guard.validate_environment(env)
    except guard.GuardError as refusal:
        out("refused: environment check failed (%s%s)" % (_fixed(refusal.reason), (", variable " + refusal.detail) if refusal.detail else ""))
        return 2
    out("G3 Phase 3 - LIVE error-behaviour test against ONE project: %s" % guard.ALLOWED_PROJECT_ID)
    out("Approved operations (exactly 2 HTTPS requests, once each, no retries):")
    for line in OPERATIONS:
        out("  - " + line)
    out("Nothing is created, changed, deleted, revoked or e-mailed. No real password is used. Values you type are hidden and never printed.")
    approval = None
    email = api_key = None
    try:
        try:
            email = getpass_fn("Dummy account e-mail (hidden): ")
            api_key = getpass_fn("Web API key of the G3 test project (hidden): ")
            config = guard.load_config(guard.ALLOWED_PROJECT_ID, api_key, env)
            if not isinstance(email, str) or not 3 <= len(email) <= 254 or email.count("@") != 1:
                raise rc.InputRejected("email_invalid")
        except (guard.GuardError, rc.InputRejected, EOFError, KeyboardInterrupt):
            out("cancelled: input missing or malformed (nothing was sent)")
            return 2
        try:
            typed = input_fn("To proceed type the exact project id (anything else cancels): ")
            approval = guard.grant_live_approval(guard.ALLOWED_PROJECT_ID, typed, interactive, guard.ERROR_TEST_BUDGET)
        except (guard.GuardError, EOFError, KeyboardInterrupt):
            out("cancelled: confirmation not given (nothing was sent)")
            return 3
        client = rc.FirebaseRestClient(config, transport_factory(approval))

        first = _case(out, "1 wrong password", client.sign_in(email, "Wp-" + random_token(12)))
        if first["outcome"] not in (rc.WRONG_CREDENTIALS, rc.UNKNOWN_EMAIL):
            out("stopped: unexpected answer to case 1; case 2 is NOT sent")
            return 1
        second = _case(out, "2 nonexistent e-mail", client.sign_in("g3-nonexistent-%s@example.invalid" % secrets.token_hex(8), "Wp-" + random_token(12)))
        if second["outcome"] not in (rc.WRONG_CREDENTIALS, rc.UNKNOWN_EMAIL):
            out("stopped: unexpected answer to case 2")
            return 1
        same = (first["http_status"], first["error_code"]) == (second["http_status"], second["error_code"])
        out("compare: identical_status_and_error_code=%s case1_error_code=%s case2_error_code=%s" % (same, _fixed(first["error_code"]), _fixed(second["error_code"])))
        out("compare: enumeration_protection_effective=%s" % (same and second["outcome"] == rc.WRONG_CREDENTIALS))
        return 0
    except Exception as exc:
        out("stopped: unexpected error (%s); no details are printed" % _fixed(type(exc).__name__.lower()))
        return 1
    finally:
        if approval is not None:
            out("requests used: %d of 2; live approval closed" % approval.used)
            approval.close()
        email = api_key = None


def main():
    import g3_live_transport as lt
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    return run(interactive=interactive, getpass_fn=getpass.getpass, input_fn=input, out=lambda text: print(text, flush=True),
               transport_factory=lambda approval: lt.LiveTransport(approval), env=os.environ)


if __name__ == "__main__":
    sys.exit(main())
