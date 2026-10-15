#!/usr/bin/env bash
# LOCAL-ONLY end-to-end tests of the additive MFA backend and the app's MFA flows.
#   * isolated Docker network with NO internet; throwaway Postgres; a fake identity service; captured (never sent) email;
#   * FAKE users (example.invalid) only; no cloud, Firebase, OpenAI, SendGrid or production data is ever contacted;
#   * everything it creates is removed on exit (success, failure or Ctrl-C).
#
# Usage:  bash scripts/mfa_additive/run_local_tests.sh [--no-flutter] [--with-newer] [--no-enforcement]
# Needs:  Docker, Git Bash, a backend test image (MFA_TEST_IMAGE, default epicverse-backend:tests-c185263) and,
#         unless --no-flutter, Flutter.   --with-newer also runs the app's older suite against commit e121583.
#         The Phase 1 ENFORCEMENT tests (build_tree_enforcement.sh = d733776 + mfa_additive.patch + mfa_enforcement.patch;
#         harness/test_config_validation.py and harness/test_enforcement.py) run last, in the same isolated network, and are
#         skipped with --no-enforcement. test_enforcement.py takes several minutes (database-outage and watchdog timing).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
IMAGE=${MFA_TEST_IMAGE:-epicverse-backend:tests-c185263}
FLUTTER=1; NEWER=0; ENFORCE=1
for a in "$@"; do case "$a" in --no-flutter) FLUTTER=0;; --with-newer) NEWER=1;; --no-enforcement) ENFORCE=0;; *) echo "unknown option $a" >&2; exit 2;; esac; done

W=$(mktemp -d); WW=$(cygpath -w "$W"); BS='\'; HW=$(cygpath -w "$HERE/harness")
NET=mfa-net; CONTAINERS="mfa-pg mfa-idstub mfa-be-L mfa-be-A mfa-be-M mfa-gw"
cleanup() { docker rm -f -v $CONTAINERS >/dev/null 2>&1; docker network rm $NET >/dev/null 2>&1; rm -rf "$W"; }
trap cleanup EXIT INT TERM HUP
for c in $CONTAINERS; do docker inspect "$c" >/dev/null 2>&1 && { echo "container $c already exists; refusing to touch it" >&2; trap - EXIT; exit 1; }; done
docker network inspect $NET >/dev/null 2>&1 && { echo "network $NET already exists; refusing to touch it" >&2; trap - EXIT; exit 1; }

mkdir -p "$W/src_L" "$W/src_A" "$W/src_M" "$W/outbox"
git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive d733776 backend | tar -x -C "$W/src_L"     || exit 1
bash "$HERE/build_tree.sh" "$W/src_A" >/dev/null                                                          || exit 1
[ $ENFORCE = 1 ] && { mkdir -p "$W/src_E"; bash "$HERE/build_tree_enforcement.sh" "$W/src_E" >/dev/null || exit 1; }
[ $NEWER = 1 ] && { git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive e121583 backend | tar -x -C "$W/src_M" || exit 1; }

docker network create --internal $NET >/dev/null || exit 1
docker run -d --name mfa-pg --network $NET -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17-alpine >/dev/null || exit 1
MSYS_NO_PATHCONV=1 docker run -d --name mfa-idstub --network $NET -v "$HW:/h:ro" --entrypoint python "$IMAGE" -I /h/idstub.py >/dev/null || exit 1
for i in $(seq 1 60); do docker exec mfa-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec mfa-pg psql -U postgres -c "CREATE DATABASE dbl" -c "CREATE DATABASE dba" -c "CREATE DATABASE dbm" >/dev/null 2>&1

start() { # name srcdir db
  MSYS_NO_PATHCONV=1 docker run -d --name mfa-be-$1 --network $NET -w /src/backend -v "${WW}${BS}$2:/src:ro" -v "$HW:/h:ro" -v "${WW}${BS}outbox:/outbox" \
    -e BACKEND_NAME=$1 -e DATABASE_URL="postgresql://postgres@mfa-pg/db$3" -e FIREBASE_PROJECT_ID=demo-epicverse -e GCP_PROJECT_ID=demo-epicverse \
    -e FIREBASE_AUTH_EMULATOR_HOST=mfa-idstub:9099 -e OPENAI_API_KEY=dummy-no-egress -e SENDGRID_API_KEY=dummy-no-egress \
    -e SENDGRID_FROM_EMAIL=noreply@example.invalid -e OTP_RATE_LIMIT_HASH_SECRET=local-test-secret-1 -e MFA_OTP_HASH_SECRET=local-test-secret-2 \
    -e ALLOWED_ORIGINS=http://localhost -e PYTHONPATH=/src/backend -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$IMAGE" /h/server.py >/dev/null
}
start L src_L l || exit 1; start A src_A a || exit 1; [ $NEWER = 1 ] && { start M src_M m || exit 1; }
MSYS_NO_PATHCONV=1 docker run -d --name mfa-gw -p 127.0.0.1:18081:8081 -p 127.0.0.1:18082:8082 -p 127.0.0.1:18083:8083 -v "$HW:/h:ro" --entrypoint python "$IMAGE" -I /h/fwd.py >/dev/null || exit 1
docker network connect $NET mfa-gw || exit 1
for n in L A $([ $NEWER = 1 ] && echo M); do
  ok=0; for i in $(seq 1 90); do docker logs mfa-be-$n 2>&1 | grep -q "Server ready" && { ok=1; break; }; sleep 1; done
  [ $ok = 1 ] || { echo "backend $n did not start"; docker logs mfa-be-$n 2>&1 | tail -15; exit 1; }
done
MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" --entrypoint python "$IMAGE" -I /h/seed.py || exit 1

rc=0
echo "################ BACKEND TESTS (additive backend vs deployed backend)"
MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" -v "${WW}${BS}outbox:/outbox" --entrypoint python "$IMAGE" -I /h/test_additive_backend.py || rc=1
echo "################ LIMITER FILE UNCHANGED vs deployed d733776 (LF-normalized sha256)"
a=$(sed 's/\r$//' "$W/src_A/backend/app/services/otp_rate_limiter.py" | sha256sum | cut -d' ' -f1)
b=$(git -C "$ROOT" show d733776:backend/app/services/otp_rate_limiter.py | sha256sum | cut -d' ' -f1)
[ "$a" = "$b" ] && echo "PASS  otp_rate_limiter.py identical ($a)" || { echo "FAIL  otp_rate_limiter.py differs"; rc=1; }

if [ $FLUTTER = 1 ]; then
  OB=$(cygpath -m "$W/outbox")
  cd "$ROOT/frontend/EpicVerseApp" || exit 1
  run() { # backend port files...
    b=$1; p=$2; shift 2
    echo "################ FLUTTER CONTRACT TESTS vs backend $b"
    rm -f "$W/outbox/"*.jsonl
    MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" --entrypoint python "$IMAGE" -I /h/seed.py >/dev/null || rc=1
    flutter test "$@" --dart-define=API_BASE_URL=http://127.0.0.1:$p --dart-define=COMPAT_BACKEND=$b --dart-define=COMPAT_OUTBOX=$OB --reporter expanded 2>&1 \
      | grep -E "^[0-9:]+ \+[0-9]+.*(All tests passed|Some tests failed)|\[E\]$"
    [ "${PIPESTATUS[0]}" -ne 0 ] && rc=1
  }
  run L 18081 test/compat/compat_contract_test.dart test/compat/compat_mfa_additive_test.dart
  run A 18083 test/compat/compat_contract_test.dart test/compat/compat_mfa_additive_test.dart
  [ $NEWER = 1 ] && run M 18082 test/compat/compat_contract_test.dart
fi

if [ $ENFORCE = 1 ]; then
  # Phase 1 enforcement. The comparison backends above are stopped first (they are not needed any more and the enforcement
  # tests start five more backends of their own); the throwaway PostgreSQL and the fake identity service stay up.
  docker stop mfa-be-L mfa-be-A mfa-be-M mfa-gw >/dev/null 2>&1
  echo "################ ENFORCEMENT: mfa_additive.patch hash and unchanged files"
  ah=$(sed 's/\r$//' "$HERE/mfa_additive.patch" | sha256sum | cut -d' ' -f1)
  [ "$ah" = "edab0188c0b01bd70704812efa4166a64d876e33b5da5f938274220b98f94ba2" ] && echo "PASS  mfa_additive.patch is the verified one ($ah)" || { echo "FAIL  mfa_additive.patch changed ($ah)"; rc=1; }
  lim_e=$(sed 's/\r$//' "$W/src_E/backend/app/services/otp_rate_limiter.py" | sha256sum | cut -d' ' -f1)
  lim_d=$(git -C "$ROOT" show d733776:backend/app/services/otp_rate_limiter.py | sha256sum | cut -d' ' -f1)
  [ "$lim_e" = "$lim_d" ] && echo "PASS  otp_rate_limiter.py still identical in the enforcement tree" || { echo "FAIL  otp_rate_limiter.py differs in the enforcement tree"; rc=1; }
  echo "################ ENFORCEMENT: startup configuration validation"
  MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" -v "${WW}${BS}src_E:/src:ro" --entrypoint python "$IMAGE" -I /h/test_config_validation.py || rc=1
  echo "################ ENFORCEMENT: WebSocket watchdog failure injection (in-process, seconds)"
  MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" -v "${WW}${BS}src_E:/src:ro" --entrypoint python "$IMAGE" -I /h/test_watchdog_failures.py || rc=1
  echo "################ ENFORCEMENT: endpoints, WebSocket, modes, outages (takes several minutes)"
  MSYS_NO_PATHCONV=1 docker run --rm --network $NET -v "$HW:/h:ro" -v "${WW}${BS}src_E:/src:ro" --entrypoint python "$IMAGE" -I /h/test_enforcement.py || rc=1
fi
[ $rc = 0 ] && echo "ALL LOCAL MFA TESTS PASSED" || echo "SOME LOCAL MFA TESTS FAILED"
exit $rc
