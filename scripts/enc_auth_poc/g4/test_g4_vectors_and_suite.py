"""Keeps the generated Dart vectors in step with the Python reference, runs the Dart suite, and re-runs the OAEP gate."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DART, DART_PKG, HERE, dart_env  # noqa: E402


class VectorsAndSuite(unittest.TestCase):
    def test_vectors_file_matches_what_the_python_reference_generates(self):
        res = subprocess.run([sys.executable, "-B", "-I", str(HERE / "gen_vectors.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, "test/vectors.dart is out of date: run gen_vectors.py")

    @unittest.skipUnless(DART, "dart not available")
    def test_dart_suite_passes(self):
        res = subprocess.run([DART, "--packages=.dart_tool/package_config.json", "test/run_all.dart"], cwd=str(DART_PKG),
                             env=dart_env(), capture_output=True, text=True, timeout=600)
        self.assertEqual(res.returncode, 0, res.stdout[-600:])
        self.assertIn("failed 0", res.stdout)
        print("  [dart suite]", res.stdout.strip().splitlines()[-1])

    @unittest.skipUnless(DART, "dart not available")
    def test_rsa_oaep_label_interoperability_gate(self):
        res = subprocess.run([sys.executable, "-B", "-I", str(HERE / "oaep_gate.py")], capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0, res.stdout[-800:])
        self.assertIn("OAEP INTEROPERABILITY GATE: PASS", res.stdout)


if __name__ == "__main__":
    unittest.main()
