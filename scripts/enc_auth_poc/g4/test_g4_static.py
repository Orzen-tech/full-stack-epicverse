"""Static guarantees for the Dart package: offline, no plaintext fallback, no Firebase, no HTTP, nothing outside the PoC."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DART, DART_PKG, POC, dart_env  # noqa: E402

REPO = POC.parent.parent
LIB = sorted((DART_PKG / "lib").rglob("*.dart"))
ALLOWED_LIB_IMPORTS = re.compile(r"^(dart:(convert|math|typed_data)|package:pointycastle/(export|asn1)\.dart|[a-z_]+\.dart|src/[a-z_]+\.dart)$")
FORBIDDEN_IN_LIB = ["dart:io", "dart:html", "dart:ui", "dart:ffi", "dart:isolate", "package:http", "package:dio", "package:flutter",
                    "firebase", "signInWith", "createUserWith", "reauthenticate", "updatePassword", "SharedPreferences",
                    "secure_storage", "HttpClient", "Socket", "Uri.http", "Platform.", "File(", "print(", "stdout", "stderr"]
NETWORK_WORDS = ["HttpClient", "Socket", "package:http", "package:dio", "Uri.https", "Uri.http", "WebSocket", "firebase", "Firebase"]


class StaticChecks(unittest.TestCase):
    def test_library_imports_only_the_allowed_libraries(self):
        self.assertTrue(LIB)
        for path in LIB:
            for line in path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*(?:import|export)\s+'([^']+)'", line)
                if m:
                    self.assertRegex(m.group(1), ALLOWED_LIB_IMPORTS, "%s imports %s" % (path.name, m.group(1)))

    def test_library_has_no_network_file_firebase_or_logging_code(self):
        for path in LIB:
            src = path.read_text(encoding="utf-8")
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
            for word in FORBIDDEN_IN_LIB:
                self.assertNotIn(word, code, "%s contains %s" % (path.name, word))

    def test_tools_and_tests_make_no_network_calls(self):
        for sub in ("tool", "test"):
            for path in (DART_PKG / sub).rglob("*.dart"):
                src = path.read_text(encoding="utf-8")
                for word in NETWORK_WORDS:
                    self.assertNotIn(word, src, "%s contains %s" % (path.name, word))

    def test_the_only_dependency_is_the_cached_pointycastle(self):
        pub = (DART_PKG / "pubspec.yaml").read_text(encoding="utf-8")
        deps = re.search(r"^dependencies:\n((?:  .*\n)+)", pub, re.M).group(1)
        self.assertEqual([l.strip() for l in deps.splitlines() if l.strip() and not l.strip().startswith("#")], ["pointycastle: 4.0.0"])
        self.assertNotIn("dev_dependencies", pub)
        lock = (DART_PKG / "pubspec.lock").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"^  ([a-z_]+):$", lock, re.M)), ["collection", "convert", "pointycastle", "typed_data"])

    def test_the_flutter_app_no_longer_depends_on_the_poc_package_and_vendors_its_sealer_unchanged(self):
        # Updated on approval (release-safe packaging): the development path dependency is gone; the app carries a byte-identical copy of the five
        # sealer files under lib/core/security/enc_auth/sealer/ and a direct pointycastle 4.0.0 dependency. Nothing under lib/ names the package.
        app = REPO / "frontend" / "EpicVerseApp"
        pub = (app / "pubspec.yaml").read_text(encoding="utf-8")
        self.assertNotIn("epicverse_enc_envelope", pub)
        self.assertNotIn("enc_auth_poc", pub)
        self.assertEqual(len(re.findall(r"^  pointycastle: 4\.0\.0$", pub, re.M)), 1)
        self.assertLess(pub.index("pointycastle: 4.0.0"), pub.index("dev_dependencies:"), "pointycastle must be a direct dependency")
        lock = (app / "pubspec.lock").read_text(encoding="utf-8")
        self.assertNotIn("epicverse_enc_envelope", lock)
        self.assertNotIn("enc_auth_poc", lock)
        self.assertRegex(lock, r'pointycastle:\s+dependency: "direct main"')
        vendored = app / "lib" / "core" / "security" / "enc_auth" / "sealer"
        for name in ("encoding", "envelope", "errors", "spki_key", "validation"):
            self.assertEqual((vendored / (name + ".dart")).read_bytes().replace(b"\r", b""), (DART_PKG / "lib" / "src" / (name + ".dart")).read_bytes().replace(b"\r", b""), name)
        for path in (app / "lib").rglob("*.dart"):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            text = chr(10).join(line for line in lines if not line.lstrip().startswith("//"))
            self.assertNotIn("epicverse_enc_envelope", text, path.name)      # comments may name the package; code and imports may not
            self.assertNotIn("enc_auth_poc", text, path.name)

    @unittest.skipUnless(DART, "dart not available")
    def test_dart_analyzer_reports_no_issues(self):
        res = subprocess.run([DART, "analyze"], cwd=str(DART_PKG), env=dart_env(), capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout[-400:])
        self.assertIn("No issues found", res.stdout)


if __name__ == "__main__":
    unittest.main()
