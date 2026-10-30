"""Shared helpers for the G6a tests: where the built backend tree is, and loading its pure modules by path."""
import atexit
import importlib.util
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
G6A = HERE.parent
POC = G6A.parent
REPO = POC.parent.parent
sys.path.insert(0, str(POC))

BUILD = G6A / "build_tree_enc_auth.sh"
_built = {}


def _posix(path):
    p = Path(path).resolve()
    return "/" + p.drive[0].lower() + p.as_posix()[2:]


def bash():
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Git Bash not found")


def tree_dir():
    """The built tree (from $G6A_TREE if the orchestrator provided one, else built once into a temporary directory)."""
    env = os.environ.get("G6A_TREE")
    if env:
        return Path(env)
    if "dir" not in _built:
        tmp = Path(tempfile.mkdtemp(prefix="g6a_tree_"))
        res = subprocess.run([bash(), _posix(BUILD), _posix(tmp)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("build failed: " + res.stderr[-300:])
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        _built["dir"] = tmp
    return _built["dir"]


def load_module(relative_path, name):
    path = tree_dir() / "backend" / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
