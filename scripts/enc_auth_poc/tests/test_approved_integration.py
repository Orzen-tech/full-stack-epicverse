"""The approved-changes allowlist used by the historical isolation checks: it must accept exactly the approved integration and REJECT
everything else. Pure string tests (synthetic `git status --porcelain` text), a test on the real repository's current state, and tests on a
throw-away Git repository created in a temporary directory (never the real repository)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import approved_integration as ai  # noqa: E402

APP = "frontend/EpicVerseApp/"


def mod(path):
    return " M " + path


def new(path):
    return "?? " + path


class Accepts(unittest.TestCase):
    def test_every_approved_modified_file_and_the_approved_new_folders(self):
        lines = [mod(p) for p in sorted(ai.APPROVED_TRACKED_CHANGES)]
        lines += [new(APP + "lib/core/security/enc_auth/password_auth.dart"), new(APP + "test/enc_auth/x_test.dart"),
                  new(APP + "tool/enc_auth_local/g4_sealer.dart"), new("scripts/enc_auth_poc/g3/g3_guard.py")]
        self.assertEqual(ai.unapproved("\n".join(lines)), [])

    def test_the_14_approved_backend_paths_exactly_as_modified_or_new(self):
        lines = [mod(p) for p in sorted(ai.APPROVED_BACKEND_MODIFIED)] + [new(p) for p in sorted(ai.APPROVED_BACKEND_NEW)]
        self.assertEqual(len(lines), 14)
        self.assertEqual(ai.unapproved("\n".join(lines)), [])

    def test_the_allowlist_is_small_and_exact(self):
        self.assertEqual(len(ai.APPROVED_TRACKED_CHANGES), 6)
        self.assertTrue(all(p.startswith(APP) for p in ai.APPROVED_TRACKED_CHANGES))
        self.assertEqual(len(ai.APPROVED_NEW_PREFIXES), 4)
        self.assertFalse(any(p == APP or p == "frontend/" or p == "scripts/" for p in ai.APPROVED_NEW_PREFIXES))

    def test_the_backend_allowlist_is_exactly_14_named_files_never_a_folder(self):
        self.assertEqual((len(ai.APPROVED_BACKEND_MODIFIED), len(ai.APPROVED_BACKEND_NEW), len(ai.APPROVED_BACKEND)), (4, 10, 14))
        self.assertTrue(all(p.startswith("backend/") and not p.endswith("/") for p in ai.APPROVED_BACKEND))
        self.assertEqual(ai.APPROVED_BACKEND_MODIFIED & ai.APPROVED_BACKEND_NEW, frozenset())
        self.assertEqual(set(ai.APPROVED_BACKEND_SHA256), set(ai.APPROVED_BACKEND))
        self.assertFalse(any(p.startswith("backend") for p in ai.APPROVED_NEW_PREFIXES))             # no backend folder is approved as a whole
        self.assertFalse(any(p.startswith("backend") for p in ai.APPROVED_TRACKED_CHANGES))           # the Flutter list is unchanged


class Rejects(unittest.TestCase):
    def test_any_other_tracked_file(self):
        for path in (APP + "lib/main.dart", APP + "lib/core/network/api_client.dart", APP + "lib/core/network/ssl_pinning_service.dart",
                     APP + "android/app/src/main/res/xml/network_security_config.xml", APP + "pubspec.yaml.bak", APP + "lib/presentation/screens/splash_screen.dart",
                     "backend/app/main.py", "backend/app/api/dependencies.py", "backend/app/services/otp_rate_limiter.py", "backend/app/services/mfa_challenge.py",
                     "backend/Dockerfile", "backend/tests/conftest.py", "backend/requirements.txt", "README.md", "scripts/mfa_additive/mfa_additive.patch",
                     APP + "lib/core/security/enc_auth/password_auth.dart"):          # an approved NEW file shown as a tracked modification
            self.assertEqual(ai.unapproved(mod(path)), [mod(path)], path)

    def test_the_approved_backend_paths_in_the_wrong_state(self):
        for path in sorted(ai.APPROVED_BACKEND_NEW):                         # a NEW file shown as a tracked modification
            self.assertEqual(ai.unapproved(mod(path)), [mod(path)], path)
        for path in sorted(ai.APPROVED_BACKEND_MODIFIED):                    # a TRACKED file shown as untracked
            self.assertEqual(ai.unapproved(new(path)), [new(path)], path)
        for path in sorted(ai.APPROVED_BACKEND):
            for status in ("M ", "MM", "A ", " D", "D ", "R ", "UU", "AM"):
                line = status + " " + path
                self.assertEqual(ai.unapproved(line), [line], line)

    def test_near_miss_backend_names_and_folders(self):
        for path in ("backend/app/api/enc_gateway2.py", "backend/app/api/enc_gateway.pyc", "backend/app/services/enc_auth_extra.py",
                     "backend/app/services/enc_auth_identity.py.bak", "backend/app/enc_auth_store.py", "backend/tests/test_other.py",
                     "backend/tests/test_enc_auth_port2.py", "backend/app/api/", "backend/app/api/new_route.py", "backend/app/core/secrets.py",
                     "backend/Enc_Auth.py", "backend/app/services/ENC_AUTH_STORE.py"):
            self.assertEqual(ai.unapproved(new(path)), [new(path)], path)

    def test_staged_deleted_renamed_or_added_changes_even_for_approved_names(self):
        path = "frontend/EpicVerseApp/pubspec.yaml"
        for status in ("M ", "MM", "A ", " D", "D ", "R ", "UU", "AM"):
            line = status + " " + path
            self.assertEqual(ai.unapproved(line), [line], status)

    def test_untracked_files_outside_the_approved_folders(self):
        for path in (APP + "lib/core/security/other/x.dart", APP + "lib/main2.dart", APP + "test/other_test.dart", APP + "tool/other/x.dart",
                     "backend/app/new.py", "scripts/other/x.py", "scripts/enc_auth_poc_evil/x.py", "frontend/EpicVerseApp/lib/core/security/enc_authx/y.dart"):
            self.assertEqual(ai.unapproved(new(path)), [new(path)], path)

    def test_paths_that_climb_out_of_an_approved_folder(self):
        for path in (APP + "lib/core/security/enc_auth/../../../main.dart", "scripts/enc_auth_poc/../../backend/app/x.py", "/etc/passwd",
                     "backend/app/api/../main.py", "backend/app/services/../../app/api/enc_gateway.py"):
            self.assertEqual(ai.unapproved(new(path)), [new(path)], path)

    def test_one_bad_line_among_good_ones_is_reported_alone(self):
        good = ([mod(p) for p in sorted(ai.APPROVED_TRACKED_CHANGES | ai.APPROVED_BACKEND_MODIFIED)] + [new(p) for p in sorted(ai.APPROVED_BACKEND_NEW)]
                + [new("scripts/enc_auth_poc/g3/g3_guard.py")])
        bad = mod("backend/app/main.py")
        self.assertEqual(ai.unapproved("\n".join(good + [bad])), [bad])


class Repository(unittest.TestCase):
    def test_the_repository_right_now_contains_only_approved_changes(self):
        repo = Path(__file__).resolve().parents[3]
        self.assertEqual(ai.unapproved(ai.porcelain(repo)), [])

    def test_the_14_approved_backend_files_are_exactly_the_verified_content(self):
        repo = Path(__file__).resolve().parents[3]
        self.assertEqual(ai.backend_content_problems(repo), [])


class TemporaryGitFixture(unittest.TestCase):
    """A throw-away Git repository (never the real one): the approved backend changes are accepted by the REAL `git status` output, and an unrelated
    backend modification, addition, deletion, staged change or staging of an approved file is still reported."""

    def git(self, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="f@example.invalid",
                   GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="f@example.invalid")
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="approved_fixture_")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        for rel in sorted(ai.APPROVED_BACKEND_MODIFIED) + ["backend/app/main.py", "backend/app/services/otp_rate_limiter.py", "README.md"]:
            self.write(rel, "original\n")
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture baseline")
        for rel in sorted(ai.APPROVED_BACKEND_MODIFIED):
            self.write(rel, "approved change\n")
        for rel in sorted(ai.APPROVED_BACKEND_NEW):
            self.write(rel, "approved new file\n")

    def unapproved(self):
        return ai.unapproved(ai.porcelain(self.root))

    def test_the_approved_changes_alone_are_accepted_by_real_git_output(self):
        self.assertEqual(len(ai.porcelain(self.root).splitlines()), 14)
        self.assertEqual(self.unapproved(), [])

    def test_an_unrelated_backend_modification_is_rejected(self):
        self.write("backend/app/main.py", "tampered\n")
        self.assertEqual(self.unapproved(), [" M backend/app/main.py"])

    def test_the_otp_rate_limiter_in_particular_is_protected(self):
        self.write("backend/app/services/otp_rate_limiter.py", "weakened\n")
        self.assertEqual(self.unapproved(), [" M backend/app/services/otp_rate_limiter.py"])

    def test_an_unrelated_new_backend_file_or_a_lookalike_is_rejected(self):
        self.write("backend/app/services/enc_auth_extra.py", "x\n")
        self.write("backend/app/api/enc_gateway_copy.py", "x\n")
        self.assertEqual(sorted(self.unapproved()), ["?? backend/app/api/enc_gateway_copy.py", "?? backend/app/services/enc_auth_extra.py"])

    def test_an_unrelated_deletion_and_a_staged_change_are_rejected(self):
        (self.root / "README.md").unlink()
        self.assertEqual(self.unapproved(), [" D README.md"])
        self.git("checkout", "--", "README.md")
        self.write("backend/app/main.py", "staged\n")
        self.git("add", "backend/app/main.py")
        self.assertEqual(self.unapproved(), ["M  backend/app/main.py"])

    def test_staging_an_approved_file_is_rejected_too(self):
        self.git("add", "backend/app/api/routes.py")
        self.assertEqual(self.unapproved(), ["M  backend/app/api/routes.py"])

    def test_content_pins_notice_that_these_files_are_not_the_verified_content(self):
        self.assertEqual(set(ai.backend_content_problems(self.root)), set(ai.APPROVED_BACKEND))         # the fixture files hold placeholder text


if __name__ == "__main__":
    unittest.main()
