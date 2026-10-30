"""Test-of-the-tests: breaks the SCRATCH G6b tree on purpose (one deliberate defect at a time, after the official hash-gated build) and checks
that the selected G6b tests FAIL. A mutant that is not caught means the tests are too weak. Nothing in the repository is modified: the
mutation is applied to a temporary build output (lab tests: via G6B_MUTANT inside the orchestrator; static tests: a temporary tree).

    python -B -I run_mutants.py [name ...]        # default: all
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
G6B = HERE.parent
TESTS = G6B / "tests"
sys.path.insert(0, str(TESTS))
from _tree import BUILD, _posix, bash  # noqa: E402

GATEWAY = "app/api/enc_gateway.py"
IDENT = "app/services/enc_auth_identity.py"
STORE = "app/services/enc_auth_store.py"
LIMITS = "app/services/enc_auth_limits_eval.py"
ROUTES = "app/api/routes.py"
PROOF = "test_integration_proof_email_uid.py"
RELIAB = "test_integration_reliability.py"
RATE = "test_integration_ratelimit.py"
STATIC = "test_static_g6b.py"

# name: (file, old, new, test module, -k filter, what it breaks)
MUTANTS = {
    "proof-max-age-removed": (GATEWAY, "AND created_at > NOW() - make_interval(secs => $2::double precision)", "AND $2::double precision > 0",
                              PROOF, "test_a_proof_older_than_the_gateway_maximum_age", "gateway-side 10-minute proof age"),
    "claim-never-refuses": (STORE, "return owner is None or owner == uid", "return True",
                            PROOF, "test_a_different_uid_with_the_same_email_cannot_mark_verified", "UID association of the proof"),
    "claim-not-exclusive": (STORE, "    return owner == uid\n", "    return True\n",
                            PROOF, "test_concurrent_claims_for_different_uids_leave_exactly_one_owner", "atomic single owner"),
    "uid-mismatch-check-removed": (GATEWAY, 'if uid != caller["uid"]:', "if False:",
                                   PROOF, "test_reauth_is_rejected_mints_nothing", "reauth/change_password UID check"),
    "email-route-uses-claim-only": (ROUTES, '    token_email = email_verify.norm_email(await resolve_token_email(current_user))\n    if not uid or "@" not in token_email:\n        raise HTTPException(status_code=400, detail="No email on token")\n    row = await get_user(uid)',
                                    '    token_email = email_verify.norm_email(current_user.get("email"))\n    if not uid or "@" not in token_email:\n        raise HTTPException(status_code=400, detail="No email on token")\n    row = await get_user(uid)',
                                    PROOF, "test_the_same_flow_with_an_unsigned_token_that_has_no_email_claim", "verified-UID helper on verify-request"),
    "retry-everything": (IDENT, "return isinstance(exc, IdentityUnavailable) and exc.cause in TRANSIENT_CAUSES", "return isinstance(exc, IdentityUnavailable)",
                         STATIC, "test_is_transient_requires_both", "retry only transient causes (static; the path is not reachable by an integration fault)"),
    "transport-failure-counted": (GATEWAY, "        if not keep:\n", "        if False:\n",
                                  RELIAB, "test_without_retries_the_same_fault", "transport failure is not a wrong password"),
    "unknown-4xx-is-a-credential-error": (IDENT, 'return "service"', 'return "credential"',
                                          RELIAB, "test_identity_400_or_401_that_is_not_about_credentials", "service errors are not credential errors"),
    "unavailable-reported-as-credential": (GATEWAY, "        return _unavailable(op, exc.stage, exc.cause, extra)", '        return _reply(400, {"error": "invalid-credential"})',
                                           RELIAB, "test_without_retries_the_same_fault", "safe unavailable answer"),
    "pair-limit-removed": (LIMITS, 'if hits["pair"] > limits["pair"]:', "if False:",
                           RATE, "test_five_wrong_passwords_from_one_ip", "per e-mail+IP limit"),
    "email-logged": (GATEWAY, "    uid, early = await _verified_uid(creds.email, creds.password, ip)\n    if early is not None:\n        return early\n    return _reply(200, await _mint(uid))",
                     "    print(creds.email, flush=True)\n    uid, early = await _verified_uid(creds.email, creds.password, ip)\n    if early is not None:\n        return early\n    return _reply(200, await _mint(uid))",
                     RATE, "test_five_wrong_passwords_from_one_ip", "no credentials/e-mails in logs (leak scan)"),
    "create-user-retried": (IDENT, '_timed("create_user", lambda: asyncio.to_thread(auth.create_user,', '_timed("create_user", lambda: _retry("create_user", None) or asyncio.to_thread(auth.create_user,',
                            STATIC, "test_retries_wrap_only_the_idempotent_reads", "no automatic retry of account creation (static)"),
}
STATIC_ONLY = {"create-user-retried", "retry-everything"}


def build_tree(target):
    res = subprocess.run([bash(), _posix(BUILD), _posix(target)], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("build failed: " + res.stderr[-300:])


def apply(tree, rel, old, new):
    path = tree / "backend" / rel
    text = path.read_bytes().decode("utf-8")
    if text.count(old) != 1:
        raise SystemExit("mutation pattern for %s not found exactly once" % rel)
    path.write_bytes(text.replace(old, new).encode("utf-8"))


def run(name):
    rel, old, new, module, selector, target = MUTANTS[name]
    env = dict(os.environ, DART_SUPPRESS_ANALYTICS="true")
    tmp = None
    if name in STATIC_ONLY:
        tmp = Path(tempfile.mkdtemp(prefix="g6b_mutant_"))
        build_tree(tmp)
        apply(tmp, rel, old, new)
        env["G6B_TREE"] = str(tmp)
    else:
        env["G6B_MUTANT"] = json.dumps({"file": rel, "old": old, "new": new})
    try:
        started = time.time()
        res = subprocess.run([sys.executable, "-B", "-I", "-m", "unittest", "discover", "-s", str(TESTS), "-p", module, "-k", selector],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
        output = res.stdout + res.stderr
        failed = res.returncode != 0
        return failed, round(time.time() - started), output
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def main(names):
    names = names or list(MUTANTS)
    caught = 0
    for name in names:
        failed, seconds, output = run(name)
        caught += failed
        summary = [l for l in output.splitlines() if l.startswith(("FAIL:", "ERROR:", "Ran ", "OK", "FAILED"))][:4]
        print("%-38s %-8s %3ds  breaks: %s\n      %s" % (name, "CAUGHT" if failed else "MISSED", seconds, MUTANTS[name][5], " | ".join(summary)), flush=True)
    print("\nmutants caught: %d / %d" % (caught, len(names)))
    return 0 if caught == len(names) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
