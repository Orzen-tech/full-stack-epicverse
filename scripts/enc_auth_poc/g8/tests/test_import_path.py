"""The runner must import from a bare isolated interpreter started the way the operator starts it (`python -B -I g8_live_run.py ...`), from any working
directory. Subprocesses with sockets and name resolution disabled in-process before the runner is imported; nothing is requested."""
import subprocess
import sys
import unittest
from pathlib import Path

G8 = Path(__file__).resolve().parent.parent

DENY = ("import socket\n"
        "def _d(*a, **k): raise SystemExit('network attempted')\n"
        "socket.getaddrinfo = socket.create_connection = _d\n"
        "socket.socket.connect = socket.socket.connect_ex = _d\n")


class ImportPath(unittest.TestCase):
    def run_py(self, code, cwd):
        return subprocess.run([sys.executable, "-B", "-I", "-c", DENY + code], cwd=str(cwd), capture_output=True, text=True, timeout=60)

    def test_runner_imports_in_isolated_mode_from_any_directory(self):
        code = ("import runpy, sys; sys.argv = ['g8_live_run.py']; "
                "m = runpy.run_path(%r, run_name='not_main'); print(sorted(k for k in ('g3', 'guard', 'wiring') if k in m))" % str(G8 / "g8_live_run.py"))
        for cwd in (G8, G8.parent, Path(sys.executable).parent):
            done = self.run_py(code, cwd)
            self.assertEqual(done.returncode, 0, done.stderr[-300:])
            self.assertEqual(done.stdout.strip(), "['g3', 'guard', 'wiring']")

    def test_running_the_script_itself_with_i_refuses_cleanly_without_a_terminal(self):
        done = subprocess.run([sys.executable, "-B", "-I", str(G8 / "g8_live_run.py"), "stage-a"], cwd=str(G8.parent), capture_output=True, text=True,
                              input="x\n", timeout=60)
        self.assertNotIn("ModuleNotFoundError", done.stderr)
        self.assertEqual(done.returncode, 2)
        self.assertIn("G8 stopped: live_execution_not_approved", done.stdout)           # stage-b is disabled: stops before any network or secret

    def test_usage_error_needs_no_imports_beyond_the_path_fix(self):
        done = subprocess.run([sys.executable, "-B", "-I", str(G8 / "g8_live_run.py")], cwd=str(G8.parent), capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, "usage" in done.stdout), (2, True))

    def test_disabled_modes_print_one_line_and_never_prompt_with_the_exact_windows_invocation(self):
        launcher = ["py", "-3.11"] if sys.platform == "win32" else [sys.executable]
        for mode in ("stage-a", "stage-b", "cleanup"):
            done = subprocess.run(launcher + ["-B", "-I", str(G8 / "g8_live_run.py"), mode], cwd=str(G8), capture_output=True, text=True, input="", timeout=60)
            self.assertEqual((done.returncode, done.stdout.strip(), done.stderr.strip()), (2, "G8 stopped: live_execution_not_approved", ""), mode)
            self.assertNotIn("Path of", done.stdout)


if __name__ == "__main__":
    unittest.main()
