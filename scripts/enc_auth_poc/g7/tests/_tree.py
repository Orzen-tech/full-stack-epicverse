"""Helpers for the G7 tests: the hash-gated G7 tree, a NETWORK DENIAL layer, and async helpers.

Importing this module installs the denial layer: name resolution and socket connections raise NetworkDenied (a BaseException, so no broad
`except Exception` can hide it) before anything leaves the process, and are recorded in ATTEMPTS.
"""
import asyncio
import atexit
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
G7 = HERE.parent
POC = G7.parent
REPO = POC.parent.parent
sys.path.insert(0, str(POC))
BUILD = G7 / "build_tree_enc_auth_g7.sh"
_built = {}
ATTEMPTS = []


class NetworkDenied(BaseException):
    pass


def _deny(name):
    def denied(*args, **kwargs):
        ATTEMPTS.append(name)
        raise NetworkDenied("network access attempted: " + name)
    return denied


def _deny_unless_loopback(original):
    """asyncio's Windows event loop connects a socket pair over 127.0.0.1 internally; that is allowed. Everything else is denied."""
    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else None
        if host in ("127.0.0.1", "::1", "localhost"):
            return original(self, address, *args, **kwargs)
        ATTEMPTS.append("socket.connect")
        raise NetworkDenied("network access attempted: socket.connect")
    return guarded


for _owner, _name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket, "gethostbyname"), (ssl.SSLContext, "wrap_socket")):
    setattr(_owner, _name, _deny("%s.%s" % (getattr(_owner, "__name__", "obj"), _name)))
socket.socket.connect = _deny_unless_loopback(socket.socket.connect)
socket.socket.connect_ex = _deny_unless_loopback(socket.socket.connect_ex)


def posix(path):
    p = Path(path).resolve()
    return "/" + p.drive[0].lower() + p.as_posix()[2:]


def bash():
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Git Bash not found")


def tree_dir():
    env = os.environ.get("G7_TREE")
    if env:
        return Path(env)
    if "dir" not in _built:
        tmp = Path(tempfile.mkdtemp(prefix="g7_tree_"))
        res = subprocess.run([bash(), posix(BUILD), posix(tmp)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("build failed: " + res.stderr[-300:])
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        _built["dir"] = tmp
    return _built["dir"]


def use_backend_tree():
    """Puts the built tree's backend on sys.path (idempotent) and returns it."""
    backend = str(tree_dir() / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    return backend


def run(coro):
    return asyncio.run(coro)
