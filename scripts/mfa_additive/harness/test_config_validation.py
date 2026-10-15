"""Startup-validation and policy tests for the Phase 1 MFA enforcement settings (mfa_enforcement.patch).

Runs INSIDE the isolated Docker network (see run_local_tests.sh), with the enforcement backend tree mounted
read-only at /src. No database, no network, no cloud: each case starts a fresh Python process that imports the
backend configuration with a chosen environment and reports whether startup succeeded.
Exit status 1 on any failure."""
import json
import os
import subprocess
import sys

SRC = "/src/backend"
SECRET = "local-test-secret-2"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)[:300]) if detail and not ok else ""), flush=True)


def run(code, **env):
    """Runs `code` in a fresh isolated interpreter whose environment is exactly BASE + env."""
    e = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    e.update({k: v for k, v in env.items() if v is not None})
    p = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (SRC, code)],
                       env=e, cwd=SRC, capture_output=True, text=True, timeout=180)
    return p.returncode, p.stdout.strip(), p.stderr


MODE_PRINT = "from app.core.config import settings; print(settings.MFA_ENFORCEMENT_MODE)"


def starts(**env):
    rc, out, err = run(MODE_PRINT, **env)
    return rc == 0, out, err


def main():
    # ---------------------------------------------------------------- defaults and valid modes
    ok, out, err = starts()
    check("C1 default: starts and the mode is off (no secret needed)", ok and out == "off", (out, err[-200:]))
    rc, out, err = run("from app.core.config import settings as s; print(s.MFA_WS_FAILCLOSED_SECONDS, repr(s.MFA_ENFORCE_UIDS))")
    check("C2 defaults: deadline 90 s, no canary UIDs", rc == 0 and out == "90 ''", (out, err[-200:]))
    for mode in ("off", "log", "enforce"):
        ok, out, err = starts(MFA_ENFORCEMENT_MODE=mode, MFA_OTP_HASH_SECRET=SECRET)
        check("C3 valid mode %r starts" % mode, ok and out == mode, (out, err[-200:]))
    ok, out, err = starts(MFA_ENFORCEMENT_MODE="canary", MFA_ENFORCE_UIDS="enf-can-1,enf-can-2", MFA_OTP_HASH_SECRET=SECRET)
    check("C4 canary with exact UIDs starts", ok and out == "canary", (out, err[-200:]))
    ok, out, err = starts(MFA_ENFORCEMENT_MODE="  EnForce ", MFA_OTP_HASH_SECRET=SECRET)
    check("C5 case and surrounding spaces are normalised (stored as 'enforce')", ok and out == "enforce", (out, err[-200:]))

    # ---------------------------------------------------------------- invalid configuration stops startup
    for bad in ("", "   ", "on", "true", "1", "enforced", "all", "allowlist", "disabled", "off,log"):
        ok, out, err = starts(MFA_ENFORCEMENT_MODE=bad, MFA_OTP_HASH_SECRET=SECRET)
        check("C6 invalid mode %r is REJECTED at startup (never silently treated as off)" % bad,
              (not ok) and "MFA_ENFORCEMENT_MODE" in err, (out, err[-200:]))

    bad_canary = {
        "no UIDs": "", "only blanks": " , ", "empty entry": "enf-can-1,,enf-can-2", "too short": "abc",
        "space inside": "enf can 1", "short second entry": "enf-can-1,x",
    }
    for label, uids in bad_canary.items():
        ok, out, err = starts(MFA_ENFORCEMENT_MODE="canary", MFA_ENFORCE_UIDS=uids, MFA_OTP_HASH_SECRET=SECRET)
        check("C7 canary with invalid UIDs (%s) is REJECTED" % label, not ok and "MFA_ENFORCE_UIDS" in err or
              (not ok and "MFA_ENFORCEMENT_MODE=canary" in err), (out, err[-200:]))

    for mode in ("log", "canary", "enforce"):
        uids = "enf-can-1" if mode == "canary" else ""
        ok, out, err = starts(MFA_ENFORCEMENT_MODE=mode, MFA_ENFORCE_UIDS=uids)
        check("C8 mode %r without MFA_OTP_HASH_SECRET is REJECTED" % mode, not ok and "MFA_OTP_HASH_SECRET" in err, (out, err[-200:]))
        ok, out, err = starts(MFA_ENFORCEMENT_MODE=mode, MFA_ENFORCE_UIDS=uids, MFA_OTP_HASH_SECRET="   ")
        check("C8b mode %r with a blank secret is REJECTED" % mode, not ok, (out, err[-200:]))

    for secs, expect_ok in (("29", False), ("30", True), ("90", True), ("600", True), ("601", False), ("0", False), ("-5", False)):
        ok, out, err = starts(MFA_ENFORCEMENT_MODE="enforce", MFA_OTP_HASH_SECRET=SECRET, MFA_WS_FAILCLOSED_SECONDS=secs)
        check("C9 fail-closed deadline %s s -> %s" % (secs, "accepted" if expect_ok else "rejected"), ok == expect_ok, (out, err[-200:]))
    ok, out, err = starts(MFA_ENFORCEMENT_MODE="enforce", MFA_OTP_HASH_SECRET=SECRET, MFA_WS_FAILCLOSED_SECONDS="abc")
    check("C9b a non-numeric deadline is rejected", not ok, (out, err[-200:]))

    # ---------------------------------------------------------------- error messages never leak configuration
    canary_secret_uid = "SECRETUID-should-never-appear x"
    ok, out, err = starts(MFA_ENFORCEMENT_MODE="canary", MFA_ENFORCE_UIDS=canary_secret_uid, MFA_OTP_HASH_SECRET=SECRET,
                          OPENAI_API_KEY="sk-should-never-appear", SENDGRID_API_KEY="SG.should-never-appear")
    leaked = [s for s in ("SECRETUID", "sk-should-never-appear", "SG.should-never-appear", SECRET) if s in err or s in out]
    check("C10 a rejected configuration does not echo UIDs, the hash secret or other secrets", (not ok) and not leaked, (leaked, err[-300:]))
    ok, out, err = starts(MFA_ENFORCEMENT_MODE="bogus-mode", OPENAI_API_KEY="sk-should-never-appear")
    check("C10b an invalid mode does not dump the settings object", (not ok) and "sk-should-never-appear" not in err and "OPENAI_API_KEY" not in err, err[-300:])

    # ---------------------------------------------------------------- one policy object: mode x uid
    policy_code = (
        "import json, dataclasses\n"
        "from app.api.dependencies import mfa_policy\n"
        "print(json.dumps({u: dataclasses.asdict(mfa_policy(u)) for u in ['enf-can-1', 'enf-can-2', 'other-user-1', 'enf-can-1x', 'ENF-CAN-1', '', None]}))\n"
    )
    expected = {
        "off": {u: (False, False) for u in ['enf-can-1', 'enf-can-2', 'other-user-1', 'enf-can-1x', 'ENF-CAN-1', '', None]},
        "log": {u: (True, False) for u in ['enf-can-1', 'enf-can-2', 'other-user-1', 'enf-can-1x', 'ENF-CAN-1', '', None]},
        "canary": {"enf-can-1": (True, True), "enf-can-2": (True, True), "other-user-1": (True, False), "enf-can-1x": (True, False),
                   "ENF-CAN-1": (True, False), "": (True, False), None: (True, False)},
        "enforce": {u: (True, True) for u in ['enf-can-1', 'enf-can-2', 'other-user-1', 'enf-can-1x', 'ENF-CAN-1', '', None]},
    }
    for mode, table in expected.items():
        env = dict(MFA_ENFORCEMENT_MODE=mode, MFA_OTP_HASH_SECRET=SECRET)
        if mode == "canary":
            env["MFA_ENFORCE_UIDS"] = "enf-can-1,enf-can-2"
        rc, out, err = run(policy_code, **env)
        try:
            got = json.loads(out)
        except Exception:
            got = None
        want = {("None" if u is None else u): {"observe": o, "enforce": e} for u, (o, e) in table.items()}
        got = None if got is None else {("null" if k == "null" else k): v for k, v in got.items()}
        # json.dumps turns the None key into "null"
        want = {("null" if k == "None" else k): v for k, v in want.items()}
        check("C11 policy table for mode %r (exact-UID canary, no prefix/case matching, blank/None never enforced)" % mode,
              rc == 0 and got == want, (got, err[-200:]))

    # ---------------------------------------------------------------- decision counters use fixed names only
    counters_code = (
        "from app.api.dependencies import _count_decision, MfaPolicy, MfaDecision, MFA_COUNTERS\n"
        "import json\n"
        "_count_decision('feedback', MfaPolicy(True, False), MfaDecision('invalid', True, 'missing_session'))\n"
        "_count_decision('feedback', MfaPolicy(True, True), MfaDecision('invalid', True, 'invalid_session'))\n"
        "_count_decision('feedback', MfaPolicy(True, False), MfaDecision('db_error'))\n"
        "_count_decision('feedback', MfaPolicy(True, True), MfaDecision('db_error'))\n"
        "_count_decision('feedback', MfaPolicy(True, True), MfaDecision('no_row'))\n"
        "_count_decision('feedback', MfaPolicy(True, True), MfaDecision('valid', True))\n"
        "print(json.dumps(sorted(MFA_COUNTERS)))\n"
    )
    rc, out, err = run(counters_code, MFA_ENFORCEMENT_MODE="log", MFA_OTP_HASH_SECRET=SECRET)
    want_keys = sorted(["would_block endpoint=feedback reason=missing_session", "blocked endpoint=feedback reason=invalid_session",
                        "db_error_allowed endpoint=feedback", "db_error_blocked endpoint=feedback", "no_row endpoint=feedback"])
    try:
        got_keys = json.loads(out.splitlines()[-1])
    except Exception:
        got_keys = None
    check("C12 counters: log-only outcomes are 'would_block'/'db_error_allowed', enforced ones 'blocked'/'db_error_blocked', "
          "no_row is counted, 'valid' is not; names contain no identifiers", rc == 0 and got_keys == want_keys, (got_keys, err[-200:]))

    # ---------------------------------------------------------------- existing configuration is untouched
    rc, out, err = run("from app.core.config import settings as s; print(s.SESSION_LIFECYCLE_FIX, s.SESSION_RENEWAL_MODE, "
                       "s.STALE_AUDIO_PROTECTION_MODE, s.ASYNC_DATABASE_URL.startswith('postgresql://'), repr(s.REDIS_URL))")
    check("C13 existing settings and their defaults are unchanged (lifecycle fix on, renewal off, async URL derived, no Redis URL)",
          rc == 0 and out == "True off off True ''", (out, err[-200:]))

    bad = results.count(False)
    print("\nTOTAL %d checks | PASS %d | FAIL %d" % (len(results), len(results) - bad, bad))
    sys.exit(1 if bad else 0)


main()
