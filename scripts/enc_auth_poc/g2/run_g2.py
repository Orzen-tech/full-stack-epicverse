"""G2 orchestrator: starts the cached Firebase Auth emulator (127.0.0.1:9099 only), runs the G2 tests, ALWAYS stops the emulator.

Usage:  python -B -I run_g2.py <scratch-dir>      (the scratch dir receives the CLI's log files and a private CLI config dir)

Safety: nothing is installed or downloaded; the CLI runs with a demo- project, no login, update checks off, usage tracking unset,
all proxy settings pointing at a dead local port (so any attempt to leave the machine fails instead of succeeding), its config
redirected into the scratch dir, and it is only ever stopped by killing the process tree this script started.
Windows only (uses netstat / taskkill / PowerShell queries).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = "demo-epicverse-g2"
HOST, PORT = "127.0.0.1", 9099
CLI = Path(os.environ["LOCALAPPDATA"]) / "npm-cache" / "_npx" / "ba4f1959e38407b5" / "node_modules" / "firebase-tools" / "lib" / "bin" / "firebase.js"
NODE = r"C:\Program Files\nodejs\node.exe"
LOOPBACK = ("127.0.0.1", "[::1]", "::1", "0.0.0.0", "[::]", "*")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def descendants(root_pid):
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | ForEach-Object { '{0},{1}' -f $_.ProcessId,$_.ParentProcessId }"]).stdout
    kids = {}
    for line in out.split():
        if "," in line:
            pid, ppid = line.split(",")
            kids.setdefault(int(ppid), []).append(int(pid))
    found, stack = {root_pid}, [root_pid]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def network_report(pids):
    """Returns (external_connections, non_loopback_listeners) for the given process ids."""
    external, listeners = [], []
    for line in run(["netstat", "-ano"]).stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("TCP", "UDP"):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid not in pids:
            continue
        local = parts[1]
        foreign = parts[2] if parts[0] == "TCP" else "*:*"
        state = parts[3] if parts[0] == "TCP" else "UDP"
        lhost, fhost = local.rsplit(":", 1)[0], foreign.rsplit(":", 1)[0]
        if state == "LISTENING" and lhost not in ("127.0.0.1", "[::1]"):
            listeners.append(line.strip())
        if state not in ("LISTENING", "UDP", "TIME_WAIT") and fhost not in LOOPBACK:
            external.append(line.strip())
    return external, listeners


def port_free():
    listening = [l for l in run(["netstat", "-ano", "-p", "TCP"]).stdout.splitlines() if "LISTENING" in l]
    return not any(re.search(r":%d\s" % PORT, l) for l in listening)


def main():
    scratch = Path(sys.argv[1]).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    assert CLI.exists(), "cached firebase-tools not found"
    assert port_free(), "port %d is already in use; refusing to start" % PORT
    cfg_dir = scratch / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    config = scratch / "firebase.emulator.json"
    config.write_text(json.dumps({"emulators": {"auth": {"host": HOST, "port": PORT}, "hub": {"host": HOST, "port": 4400},
                                                "logging": {"host": HOST, "port": 4500}, "ui": {"enabled": False}}}, indent=2))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA",
                                                       "LOCALAPPDATA", "COMSPEC", "PATHEXT", "WINDIR", "HOMEDRIVE", "HOMEPATH")}
    env.update({
        "XDG_CONFIG_HOME": str(cfg_dir), "NO_UPDATE_NOTIFIER": "1", "CI": "1", "FIREBASE_AUTH_EMULATOR_HOST": "%s:%d" % (HOST, PORT),
        "GCLOUD_PROJECT": PROJECT, "NODE_USE_ENV_PROXY": "1", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    log = open(scratch / "emulator_stdout.log", "wb")
    proc = subprocess.Popen([NODE, str(CLI), "emulators:start", "--only", "auth", "--project", PROJECT, "--config", str(config),
                             "--non-interactive"], cwd=str(scratch), env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=0x00000200 | 0x08000000)  # new process group, no window
    status = 1
    try:
        for _ in range(120):
            if proc.poll() is not None:
                print("EMULATOR EXITED EARLY, see %s" % (scratch / "emulator_stdout.log"))
                return 2
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://%s:%d/" % (HOST, PORT), timeout=2) as r:
                    if b"authEmulator" in r.read():
                        break
            except Exception:
                time.sleep(0.5)
        else:
            print("EMULATOR DID NOT BECOME READY")
            return 2
        pids = descendants(proc.pid)
        ext, lst = network_report(pids)
        print("emulator ready; process ids: %s; external connections: %d; non-loopback listeners: %d" % (sorted(pids), len(ext), len(lst)))
        if ext or lst:
            print("ISOLATION CHECK FAILED:", ext, lst)
            return 3
        test_env = dict(env)
        res = subprocess.run([sys.executable, "-B", "-I", str(HERE / "test_g2_emulator.py")], env=test_env, capture_output=True, text=True)
        out = res.stdout + res.stderr
        print(out)
        status = res.returncode
        if "G2pw-" in out:
            print("LEAK CHECK FAILED: a dummy password marker appeared in test output")
            status = 4
        ext2, lst2 = network_report(descendants(proc.pid))
        print("after tests: external connections: %d; non-loopback listeners: %d" % (len(ext2), len(lst2)))
        if ext2 or lst2:
            print("ISOLATION CHECK FAILED AFTER TESTS:", ext2, lst2)
            status = 3
        text = (scratch / "emulator_stdout.log").read_bytes().decode("utf-8", "replace")
        leaks = re.findall(r"G2pw-\S+", text)
        print("emulator log contains dummy password markers: %d (the emulator logs its own loopback traffic; dummy data only)" % len(leaks))
        return status
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True)
        log.close()
        time.sleep(1)
        print("emulator stopped; port %d free: %s" % (PORT, port_free()))


if __name__ == "__main__":
    sys.exit(main())
