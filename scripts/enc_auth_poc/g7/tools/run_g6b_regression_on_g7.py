"""Runs the EXISTING G6b integration tests (emulator adapter, default configuration) unchanged against the G7-built tree, to prove the new
patch did not change the emulator gateway. The G6b tests are not edited: the G6b lab is swapped for the G7 lab (same lab, tree built by the
G7 build script) before they are imported.

    python -B -I run_g6b_regression_on_g7.py
"""
import sys
import unittest
from pathlib import Path

G7 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(G7 / "harness"))
sys.path.insert(0, str(G7.parent / "g6b" / "tests"))
import orchestrate_g7  # noqa: E402,F401  (swaps in the G7 lab before any G6b test module imports it)

MODULES = ["test_integration_proof_email_uid", "test_integration_reliability", "test_integration_ratelimit", "test_g6a_regression_on_g6b_tree"]

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
