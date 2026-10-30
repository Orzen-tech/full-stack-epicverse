"""G3 Phase 2 live-test entry point. Run it YOURSELF, in an interactive terminal:   python -B -I g3_live_run.py

Scope (and nothing else): ONE e-mail/password sign-in, ONE refresh-token exchange and ONE download of Firebase's PUBLIC signing
certificates for the single test project, then signature verification and a safe claim comparison. At most 3 HTTPS requests, no retries.
It refuses to start without an interactive terminal, collects the e-mail, password and Web API key through HIDDEN prompts, and asks you to
type the exact project id immediately before the first request; anything else cancels. Approval is held in memory for this one process,
and the process ends when the run does. It prints only fixed codes and booleans - never an e-mail, UID, key, password or token.
"""
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # `python -I` does not add the script folder itself

import g3_guard as guard  # noqa: E402
import g3_rest_client as rc  # noqa: E402
import g3_token_inspect as ti  # noqa: E402
import g3_verify as verify  # noqa: E402

APPROVED_OPERATIONS = (
    "POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword   (1 sign-in with the dummy account)",
    "POST https://securetoken.googleapis.com/v1/token                              (1 refresh-token exchange)",
    "GET  https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com   (public certificates)",
)
_RAN = []


def _fixed(value):
    """Only booleans, small integers, short fixed lower-case codes and the KNOWN Firebase error-code names may be printed."""
    if isinstance(value, bool) or isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value in rc.KNOWN_ERROR_CODES:      # exact membership in a fixed set: free text can never match
        return value
    if isinstance(value, str) and len(value) <= 40 and all(c.islower() or c in "_-0123456789" for c in value):
        return value
    return "other"


def run(*, interactive, getpass_fn, input_fn, out, transport_factory, clock, env):
    """Everything is injected so the whole flow can be tested offline. Returns a process exit code."""
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

    out("G3 Phase 2 - LIVE test against ONE project: %s" % guard.ALLOWED_PROJECT_ID)
    out("Approved operations (at most 3 HTTPS requests, no retries):")
    for line in APPROVED_OPERATIONS:
        out("  - " + line)
    out("Nothing is created, changed, deleted, revoked or e-mailed. Values you type are hidden and never printed.")
    approval = None
    email = password = api_key = None
    try:
        try:
            email, password, api_key = rc.collect_runtime_credentials(getpass_fn, getpass_fn)        # all three hidden
            config = guard.load_config(guard.ALLOWED_PROJECT_ID, api_key, env)
        except (guard.GuardError, rc.InputRejected, EOFError, KeyboardInterrupt):
            out("cancelled: input missing or malformed (nothing was sent)")
            return 2
        try:
            typed = input_fn("To proceed type the exact project id (anything else cancels): ")
            approval = guard.grant_live_approval(guard.ALLOWED_PROJECT_ID, typed, interactive)
        except (guard.GuardError, EOFError, KeyboardInterrupt):
            out("cancelled: confirmation not given (nothing was sent)")
            return 3
        client = rc.FirebaseRestClient(config, transport_factory(approval))
        ok = _execute(client, email, password, out, clock)
        return 0 if ok else 1
    except Exception as exc:                                                                       # fail closed, category only
        out("stopped: unexpected error (%s); no details are printed" % _fixed(type(exc).__name__.lower()))
        return 1
    finally:
        if approval is not None:
            out("requests used: %d of 3; live approval closed" % approval.used)
            approval.close()
        email = password = api_key = None


def _report(out, name, result):
    d = result.diagnostic()
    out("%s: outcome=%s http=%s cause=%s" % (name, _fixed(d["outcome"]), _fixed(d["http_class"]), _fixed(d["cause"])))


def _execute(client, email, password, out, clock):
    a = client.sign_in(email, password.reveal())
    _report(out, "A sign-in", a)
    if a.outcome != rc.OK:
        out("A failed: refresh (B) and certificate download (C) are skipped")
        return False
    tokens = a.tokens
    out("A: id_token_returned=True refresh_token_returned=True uid_returned=True expires_in_positive=%s" % (tokens.expires_in > 0))
    out("A: response_email_matches_input=%s" % (tokens.email is not None and tokens.email.reveal().strip().lower() == email.strip().lower()))

    b = client.refresh(tokens.refresh_token)
    _report(out, "B refresh", b)
    refreshed = b.tokens if b.outcome == rc.OK else None
    if refreshed is not None:
        out("B: new_id_token_returned=True uid_unchanged=%s id_token_differs=%s" % (
            refreshed.uid == tokens.uid, refreshed.id_token.reveal() != tokens.id_token.reveal()))

    c = client.fetch_public_certificates()
    _report(out, "C certificates", c)
    if c.outcome != rc.OK:
        out("C failed: signature verification is NOT possible; no unverified token is trusted")
        return False
    try:
        verifier = verify.FirebaseSignatureVerifier(c.data)
    except verify.CertificateRejected as rejected:
        out("C: certificate set rejected (%s)" % _fixed(rejected.reason))
        return False
    out("C: certificate_count=%d" % len(c.data))
    now = int(clock())
    verified = {}
    for name, bundle in (("A", tokens), ("B", refreshed)):
        if bundle is None:
            out("%s: signature verification skipped (no token)" % name)
            continue
        try:
            verified[name] = verifier.verify(bundle.id_token.reveal(), guard.ALLOWED_PROJECT_ID, now)
        except (ti.TokenInspectionError, guard.GuardError) as rejected:
            out("%s: signature/claims verification FAILED (%s)" % (name, _fixed(rejected.reason)))
            continue
        summary = verify.safe_summary(verified[name])
        out("%s: signature_verified=True aud_and_iss_exact=True %s" % (name, " ".join("%s=%s" % (k, _fixed(v)) for k, v in summary.items())))
        out("%s: sub_matches_response_uid=%s claim_email_matches_input=%s" % (
            name, verified[name].claims.uid == bundle.uid,
            verified[name].claims.email is not None and verified[name].claims.email.strip().lower() == email.strip().lower()))
    if "A" in verified and "B" in verified:
        ca, cb = verified["A"].claims, verified["B"].claims
        out("compare: uid_unchanged=%s project_unchanged=True auth_time_preserved=%s iat_not_earlier=%s provider_unchanged=%s" % (
            ca.uid == cb.uid, ca.auth_time == cb.auth_time, cb.issued_at >= ca.issued_at, ca.sign_in_provider == cb.sign_in_provider))
    return "A" in verified and refreshed is not None and "B" in verified


def main():
    import g3_live_transport as lt
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    return run(interactive=interactive, getpass_fn=getpass.getpass, input_fn=input, out=lambda text: print(text, flush=True),
               transport_factory=lambda approval: lt.LiveTransport(approval), clock=time.time, env=os.environ)


if __name__ == "__main__":
    sys.exit(main())
