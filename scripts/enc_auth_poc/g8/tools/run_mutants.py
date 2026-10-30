"""Test-of-the-tests for the G8 harness: breaks a COPY of the harness on purpose (one defect at a time) and checks that the offline tests FAIL.
Nothing in the repository is modified. A missed mutant means the tests are too weak.

    G7_TREE=<a built G7 tree> python -B -I run_mutants.py [name ...]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
G8 = HERE.parent
POC = G8.parent

GUARD, PROTECT, STAGES, REVOCATION, RUNNER = "g8_guard.py", "g8_protect.py", "g8_stages.py", "g8_revocation.py", "g8_live_run.py"
SUITES = ["test_stage_b_wiring", "test_guard", "test_stages", "test_revocation", "test_sdk_paths", "test_runner_static.Disabled", "test_runner_static.Order",
          "test_runner_static.WiringOffline"]

MUTANTS = {
    "https-not-required": (GUARD, 'if parts.scheme != "https":', "if False:", "only HTTPS leaves the process"),
    "host-allowlist-off": (GUARD, "if host not in ALLOWED_HOSTS:", "if False:", "exact host allowlist"),
    "path-allowlist-off": (GUARD, "if kind is None:", "if False:", "exact path allowlist"),
    "kind-quota-ignored": (GUARD, "if self._used[kind] >= self._quota.get(kind, 0):", "if False:", "per-kind quota"),
    "total-cap-ignored": (GUARD, "if sum(self._used.values()) >= self.total_cap or sum(self._used.values()) >= GLOBAL_REQUEST_CAP:", "if False:", "total cap"),
    "mutation-gate-off": (GUARD, 'if self.stage not in ("B", "C") or kind not in self.enabled_mutations:', "if False:", "mutations need a confirmation and stage B or C"),
    "mutation-target-unchecked": (GUARD, 'if kind in ("update", "delete") and not self._body_names_only(body, self._bound_uid):', "if False:",
                                  "update / delete must name the disposable uid"),
    "trip-not-sticky": (GUARD, '''            if self.tripped is not None:
                self.refused += 1
                raise LiveSafetyStop("guard_tripped")''', "            pass", "after a refusal everything is refused"),
    "timeout-not-clamped": (GUARD, 'kwargs["timeout"] = bounded', "pass", "SDK timeouts are clamped"),
    "redirect-followed-urllib3": (GUARD, 'if 300 <= getattr(response, "status", 0) < 400:', "if False:", "no redirect is followed"),
    "redirect-followed-httpx": (GUARD, "if 300 <= response.status_code < 400:", "if False:", "no redirect is followed"),
    "proxy-allowed": (GUARD, "if proxied:", "if False:", "proxied pools are refused"),
    "retry-not-required-for-cap": (GUARD, "if retry_max != 0:", "if False:", "adapter retries must be off for the cap guarantee"),
    "socket-backstop-open": (GUARD, "if not loopback and ip not in resolved:", "if False:", "connections only to resolved allowed hosts"),
    "dns-open": (GUARD, "if host in ALLOWED_HOSTS or (self._test_port is not None and host == \"127.0.0.1\"):", "if True:", "only allowed names resolve"),
    "mint-for-unverified-uid": (PROTECT, "if uid not in self._state.verified_uids:", "if False:", "tokens only for uids confirmed in this run"),
    "stage-a-may-mutate": (PROTECT, 'if self._stage == "A":', "if False:", "the dummy account is read-only"),
    "create-unlimited": (PROTECT, "if state.create_calls >= MAX_CREATE_CALLS:", "if False:", "one account and one duplicate probe"),
    "disposable-shape-unchecked": (PROTECT, "not DISPOSABLE_EMAIL_RE.fullmatch(email) or state.is_dummy_email(email)", "False", "only generated e-mails are created"),
    "exchange-creation-ignored": (PROTECT, "if getattr(tokens, \"is_new_user\", None) is not False:", "if getattr(tokens, \"is_new_user\", None) is True:",
                                  "an unknown isNewUser flag is a stop"),
    "uid-read-unrestricted-stage-b": (PROTECT, 'elif uid is None or uid != self.created_uid or self.is_dummy_uid(uid):', "elif False:", "stage B reads only its own account"),
    "ambiguous-counts-as-pass": (STAGES, "rev.AMBIGUOUS: \"ambiguous\"}[classification]", "rev.AMBIGUOUS: \"pass\"}[classification]", "ambiguity is never a pass"),
    "outage-counts-as-pass": (STAGES, 'report.add(check_id, "ambiguous" if error in ("IdentityUnavailable",) else ("pass" if condition else "fail"))',
                              'report.add(check_id, "pass" if condition or error else "fail")', "an outage is never a pass"),
    "cleanup-handle-dropped": (STAGES, "if state.stage == \"B\" and state.created_email:", "if False:", "the cleanup handle is always reported"),
    "pre-revoke-wait-skipped": (STAGES, "if ctx.pre_revoke_wait > 0:", "if False:", "the old token is provably older than the revoke window"),
    "settle-delay-skipped": (STAGES, "await ctx.sleep(ctx.settle_seconds)", "pass", "time passes between revoke and the next token"),
    "skew-ignored-old": (REVOCATION, "provably_older = token_iat < start - SKEW_SECONDS", "provably_older = token_iat < start", "revocation needs a skew margin"),
    "skew-ignored-new": (REVOCATION, "provably_newer = token_iat > end + SKEW_SECONDS", "provably_newer = token_iat > end", "revocation needs a skew margin"),
    "runner-approval-ignored": (RUNNER, "if guard.LIVE_EXECUTION_APPROVED is not True or guard.STAGE_APPROVED.get(stage) is not True:", "if False:", "disabled as committed"),
    "runner-confirmations-skipped": (RUNNER, "_require(set(granted) == set(STAGE_MUTATIONS[stage]), \"mutation_not_confirmed\")", "pass", "every class is confirmed separately"),
}


RUNNER_CODE = "import sys, unittest; sys.path.insert(0, sys.argv[1]); unittest.main(module=None, argv=['unittest'] + sys.argv[2:])"


def run(name):
    filename, old, new, what = MUTANTS[name] if name != "baseline" else (GUARD, "", "", "the unmodified copy passes")
    tmp = Path(tempfile.mkdtemp(prefix="g8_mutant_"))
    try:
        root = tmp / "scripts" / "enc_auth_poc"
        shutil.copytree(G8, root / "g8", ignore=shutil.ignore_patterns("__pycache__", "tools"))
        shutil.copytree(POC / "g3", root / "g3", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        (root / "g7").mkdir()
        shutil.copytree(POC / "g7" / "tests", root / "g7" / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(POC / "g7" / "harness", root / "g7" / "harness", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(POC / "approved_integration.py", root)
        target = root / "g8" / filename
        text = target.read_bytes().decode("utf-8")
        if name != "baseline":
            if text.count(old) != 1:
                raise SystemExit("pattern for %s not found exactly once (%d)" % (name, text.count(old)))
            target.write_bytes(text.replace(old, new).encode("utf-8"))
        env = dict(os.environ)
        res = subprocess.run([sys.executable, "-B", "-I", "-c", RUNNER_CODE, str(root / "g8" / "tests"), *SUITES], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env, timeout=900)
        output = res.stdout + res.stderr
        failed = [line for line in output.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
        return res.returncode != 0, failed[:2], what
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(names):
    if not os.environ.get("G7_TREE"):
        raise SystemExit("set G7_TREE to a built G7 tree")
    names = names or list(MUTANTS)
    ok, failed, _ = run("baseline")
    if ok:
        raise SystemExit("the unmodified copy FAILS its own tests (%s); the mutant results would be meaningless" % " | ".join(failed))
    print("baseline (unmodified copy): all selected suites pass", flush=True)
    caught = 0
    for name in names:
        ok, failed, what = run(name)
        caught += ok
        print("%-34s %-7s breaks: %s\n      %s" % (name, "CAUGHT" if ok else "MISSED", what, " | ".join(f[:100] for f in failed)), flush=True)
    print("\nmutants caught: %d / %d" % (caught, len(names)))
    return 0 if caught == len(names) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
