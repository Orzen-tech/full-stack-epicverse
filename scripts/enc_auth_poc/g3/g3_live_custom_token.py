"""G3 Phase 5 live runner: the FIVE-request custom-token compatibility test. Interactive terminal only:
    python -B -I g3_live_custom_token.py

DISABLED BY DEFAULT. Nothing is asked and nothing is sent unless, at the very first prompt, you type the enabling phrase - and that enables only this
one process. Then, with hidden prompts for every value, exactly five requests happen, once each, in this fixed order, no retries:
  1. POST identitytoolkit.googleapis.com/v1/accounts:signInWithPassword   normal login of the existing dummy account
  2. GET  www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com   public certificates
        -> the login ID token is verified and its uid is compared with the uid you typed. A mismatch STOPS here: nothing is signed or exchanged.
  3. POST iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/<signer>:signBlob   Google signs a custom token for the VERIFIED uid
  4. POST identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken   exchange (is_new_user=true is a critical failure and stops here)
  5. POST securetoken.googleapis.com/v1/token   refresh of the custom-token session
The typed uid is never trusted by itself: the custom token is signed for the uid that Firebase's own signed answer proved to exist. No private key,
ADC or command-line cloud tool is used; the access token is pasted by you at a hidden prompt. Only booleans and fixed codes are printed.
"""
import getpass
import hmac
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # `python -I` does not add the script folder itself

import g3_compat as compat  # noqa: E402
import g3_custom_token as ct  # noqa: E402
import g3_guard as guard  # noqa: E402
import g3_iam_signer as signer_mod  # noqa: E402
import g3_rest_client as rc  # noqa: E402
import g3_token_inspect as ti  # noqa: E402
import g3_verify as verify  # noqa: E402
from g3_live_run import _fixed  # noqa: E402

ENABLING_PHRASE = "ENABLE ONE FIVE-REQUEST RUN"
OPERATIONS = (
    "1 POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword   (normal login of the existing dummy account)",
    "2 GET  https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com   (public certificates; verify + uid check)",
    "3 POST https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/<signer>:signBlob   (only after the uid matched)",
    "4 POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken   (exchange for the VERIFIED uid)",
    "5 POST https://securetoken.googleapis.com/v1/token   (refresh)",
)
_RAN = []


def _flags(mapping):
    return " ".join("%s=%s" % (k, _fixed(v)) for k, v in mapping.items())


def same_uid(verified, expected):
    return isinstance(expected, str) and hmac.compare_digest(verified.claims.uid.encode("utf-8"), expected.encode("utf-8"))


def run(*, interactive, getpass_fn, input_fn, out, transport_factory, clock, env):
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
    out("G3 Phase 5 - LIVE custom-token test against ONE project: %s" % guard.ALLOWED_PROJECT_ID)
    out("This run is DISABLED by default. It makes exactly 5 HTTPS requests, once each, in this order, no retries:")
    for line in OPERATIONS:
        out("  " + line)
    out("No private key is used or downloaded. Nothing is created, deleted, changed or revoked; a new account is treated as a critical failure.")
    try:
        enabled = input_fn("To enable THIS run only, type exactly: %s   (anything else cancels): " % ENABLING_PHRASE) == ENABLING_PHRASE
    except (EOFError, KeyboardInterrupt):
        enabled = False
    if not enabled and guard.CUSTOM_TOKEN_LIVE_APPROVED is not True:
        out("disabled: not enabled for this run (nothing was asked, nothing was sent)")
        return 4
    approval = None
    email = password = expected_uid = account = api_key = bearer = None
    try:
        try:
            email, password, api_key = rc.collect_runtime_credentials(getpass_fn, getpass_fn)       # e-mail, password, API key: all hidden
            expected_uid = getpass_fn("Existing dummy account uid you expect (hidden): ")
            account = getpass_fn("Signer service-account e-mail (hidden): ")
            bearer = guard.Secret(getpass_fn("One-hour access token of the signer service account (hidden): "))
            config = guard.load_config(guard.ALLOWED_PROJECT_ID, api_key, env)
            ct.build_claims(account, expected_uid, 1)                       # validates uid and signer before anything is sent
            guard.validate_bearer_token(bearer)
            rc.check_sign_in_inputs(email, password.reveal())
        except (guard.GuardError, ct.CustomTokenError, rc.InputRejected, EOFError, KeyboardInterrupt):
            out("cancelled: input missing or malformed (nothing was sent)")
            return 2
        try:
            typed = input_fn("To proceed type the exact project id (anything else cancels): ")
            approval = guard.grant_custom_token_approval(guard.ALLOWED_PROJECT_ID, typed, interactive, account, enabled_for_this_run=enabled)
        except (guard.GuardError, EOFError, KeyboardInterrupt):
            out("cancelled: confirmation not given (nothing was sent)")
            return 3
        transport = transport_factory(approval)
        client = rc.FirebaseRestClient(config, transport)
        return 0 if _execute(client, transport, email, password, account, expected_uid, bearer, out, clock) else 1
    except Exception as exc:
        out("stopped: unexpected error (%s); no details are printed" % _fixed(type(exc).__name__.lower()))
        return 1
    finally:
        if approval is not None:
            out("requests used: %d of 5; live approval closed" % approval.used)
            approval.close()
        email = password = expected_uid = account = api_key = bearer = None


def _verified(verifier, name, bundle, now, out):
    try:
        return verifier.verify(bundle.id_token.reveal(), guard.ALLOWED_PROJECT_ID, now)
    except (ti.TokenInspectionError, guard.GuardError) as rejected:
        out("%s: signature/claims verification FAILED (%s)" % (name, _fixed(rejected.reason)))
        return None


def _execute(client, transport, email, password, account, expected_uid, bearer, out, clock):
    now = int(clock())

    # 1. normal login -----------------------------------------------------------------------------------------------------------
    a = client.sign_in(email, password.reveal())
    d = a.detail()
    out("1 password sign-in: outcome=%s http_status=%s error_code=%s cause=%s" % (_fixed(d["outcome"]), d["http_status"], _fixed(d["error_code"]), _fixed(d["cause"])))
    if a.outcome != rc.OK:
        out("1 failed: nothing else is sent")
        return False

    # 2. certificates, then verify the normal-login token and compare the uid ------------------------------------------------------
    c = client.fetch_public_certificates()
    out("2 certificates: outcome=%s http=%s" % (_fixed(c.outcome), _fixed(c.http_class)))
    if c.outcome != rc.OK:
        out("2 failed: the login token cannot be verified, so the uid is NOT established; nothing is signed or exchanged")
        return False
    try:
        verifier = verify.FirebaseSignatureVerifier(c.data)
    except verify.CertificateRejected as rejected:
        out("2: certificate set rejected (%s); nothing is signed or exchanged" % _fixed(rejected.reason))
        return False
    login = _verified(verifier, "login token", a.tokens, now, out)
    if login is None:
        out("the uid is NOT established; nothing is signed or exchanged")
        return False
    typed_matches = same_uid(login, expected_uid)
    response_matches = a.tokens.uid == login.claims.uid
    out("uid check: verified_login_uid_matches_typed_uid=%s response_uid_matches_verified_sub=%s" % (typed_matches, response_matches))
    if not (typed_matches and response_matches):
        out("STOP: the typed uid is not the verified account; NO custom token is signed or exchanged (a wrong uid could create a new account)")
        return False
    verified_uid = login.claims.uid                       # from here on ONLY the uid that Firebase's signed answer proved to exist
    out("login token: signature_verified=True %s" % _flags(compat.observe(login, verified_uid)))
    out("login token: claim_email_matches_input=%s" % (login.claims.email is not None and login.claims.email.strip().lower() == email.strip().lower()))

    # 3. IAM signBlob for the VERIFIED uid ----------------------------------------------------------------------------------------
    try:
        signer = signer_mod.IamSignBlobSigner(account, bearer, transport)
        custom = ct.mint_with(signer, account, verified_uid, now)
    except signer_mod.SignerError as failed:
        out("3 signBlob: FAILED category=%s; the remaining requests are skipped" % _fixed(failed.category))
        return False
    out("3 signBlob: ok key_id_returned=%s" % signer.key_id_returned)

    # 4. exchange -------------------------------------------------------------------------------------------------------------------
    b = client.sign_in_with_custom_token(guard.Secret(custom))
    d = b.detail()
    out("4 custom-token sign-in: outcome=%s http_status=%s error_code=%s cause=%s" % (_fixed(d["outcome"]), d["http_status"], _fixed(d["error_code"]), _fixed(d["cause"])))
    if b.outcome != rc.OK:
        out("4 failed: refresh is skipped")
        return False
    first = b.tokens
    out("4: is_new_user=%s" % _fixed("unknown" if first.is_new_user is None else first.is_new_user))
    if first.is_new_user is True:
        out("CRITICAL TEST-DATA SAFETY FAILURE: Firebase reports that an account was CREATED. Stopping before refresh. Delete the stray user in the Console.")
        return False
    custom_login = _verified(verifier, "custom-token token", first, now, out)
    if custom_login is None:
        return False
    if not same_uid(custom_login, verified_uid):
        out("CRITICAL: the custom-token session belongs to a different uid; stopping before refresh")
        return False
    observation = compat.observe(custom_login, verified_uid)
    out("custom-token token: signature_verified=True %s" % _flags(observation))
    out("custom-token token: claim_email_matches_input=%s" % (custom_login.claims.email is not None and custom_login.claims.email.strip().lower() == email.strip().lower()))
    out("custom-token token: backend_assumptions %s" % _flags(compat.backend_assumptions(observation)))
    out("compare login-vs-custom: %s" % _flags(compat.compare_sessions(login, custom_login)))

    # 5. refresh ----------------------------------------------------------------------------------------------------------------------
    r = client.refresh(first.refresh_token)
    d = r.detail()
    out("5 refresh: outcome=%s http_status=%s error_code=%s" % (_fixed(d["outcome"]), d["http_status"], _fixed(d["error_code"])))
    if r.outcome != rc.OK:
        return False
    refreshed = _verified(verifier, "refreshed token", r.tokens, now, out)
    if refreshed is None:
        return False
    out("refreshed token: signature_verified=True uid_unchanged=%s %s" % (same_uid(refreshed, verified_uid), _flags(compat.observe(refreshed, verified_uid))))
    out("compare custom-vs-refreshed: %s" % _flags(compat.compare_sessions(custom_login, refreshed)))
    return same_uid(refreshed, verified_uid)


def main():
    import g3_live_transport as lt
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    return run(interactive=interactive, getpass_fn=getpass.getpass, input_fn=input, out=lambda text: print(text, flush=True),
               transport_factory=lambda approval: lt.LiveTransport(approval), clock=time.time, env=os.environ)


if __name__ == "__main__":
    sys.exit(main())
