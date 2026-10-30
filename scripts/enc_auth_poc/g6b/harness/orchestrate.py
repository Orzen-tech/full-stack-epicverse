"""G6b lab (a G6b-only copy of the G6a lab, extended; G6a stays untouched): builds the hash-gated test tree, starts the LOCAL Firebase Auth emulator and an isolated Docker network, runs the patched
backend in several configurations, and tears down ONLY what it started.

Isolation (all of it is checked at start-up, and the lab refuses to continue if any check fails):
  * the Auth emulator listens on 127.0.0.1:9099 only (demo- project, no login, proxies pointing at a dead port, no telemetry);
  * Postgres and every backend container live on an `--internal` Docker network (no route to the internet, no external DNS);
  * the only bridge between that network and the emulator is a fixed-target TCP relay container (g6b-relay) that connects to
    host.docker.internal:9099 and nothing else; it has no DNS server, so it cannot resolve any other name;
  * published ports are bound to 127.0.0.1 only; no image is pulled; no unrelated container or network is touched.
Private keys are generated in memory for the run and handed to containers through the docker CLI's environment (never on a command
line, never written to disk, never printed). Windows only.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
G6B = HERE.parent
POC = G6B.parent
REPO = POC.parent.parent
sys.path.insert(0, str(POC))
sys.path.insert(0, str(POC / "g2"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402
import run_g2 as g2  # noqa: E402  (read-only helpers: process tree and network inspection)

IMAGE = "epicverse-backend:tests-c185263"
PG_IMAGE = "postgres:17-alpine"
NET = "g6b-net"
PROJECT = "demo-epicverse-g6b"
EMULATOR_PORT = 9099
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"

# name -> (container port, host port, database, environment overrides)
HIGH = {"ENC_AUTH_IP_LIMIT": "1000000", "ENC_AUTH_SIGNUP_IP_LIMIT": "1000000", "ENC_AUTH_IP_FAIL_LIMIT": "1000000",
        "ENC_AUTH_PAIR_FAIL_LIMIT": "1000000", "ENC_AUTH_EMAIL_FAIL_LIMIT": "1000000", "ENC_AUTH_GLOBAL_FAIL_LIMIT": "100000000"}
BACKENDS = {
    # --- variants shared with the G6a regression suite (same names so the adapted G6a tests run unchanged where possible)
    "main": (8110, 18110, "g6b_main", dict(HIGH)),
    "disabled": (8111, 18111, "g6b_disabled", {"ENC_AUTH_ENABLED": "false"}),
    "nokey": (8112, 18112, "g6b_nokey", {"ENC_AUTH_KEYS": ""}),
    "rate": (8113, 18113, "g6b_rate", {"ENC_AUTH_IP_LIMIT": "40", "ENC_AUTH_SIGNUP_IP_LIMIT": "3", "ENC_AUTH_PAIR_FAIL_LIMIT": "3",
                                       "ENC_AUTH_IP_FAIL_LIMIT": "6", "ENC_AUTH_EMAIL_FAIL_LIMIT": "1000000", "ENC_AUTH_GLOBAL_FAIL_LIMIT": "100000000"}),
    "rotate": (8114, 18114, "g6b_rotate", {**HIGH, "KEYSET": "both"}),
    "retired": (8115, 18115, "g6b_retired", {**HIGH, "KEYSET": "new_only"}),
    "nodb": (8116, 18116, "g6b_nodb", {**HIGH, "DATABASE_URL": "postgresql://postgres@g6b-no-such-host/none"}),
    "nohash": (8117, 18117, "g6b_nohash", {"ENC_AUTH_HASH_SECRET": ""}),
    "notdemo": (8118, 18118, "g6b_notdemo", {**HIGH, "FIREBASE_PROJECT_ID": "g6b-notdemo-project"}),
    "noemu": (8119, 18119, "g6b_noemu", {**HIGH, "FIREBASE_AUTH_EMULATOR_HOST": ""}),
    # --- G6b-only variants
    "policy": (8120, 18120, "g6b_policy", {"ENC_AUTH_IP_LIMIT": "1000000", "ENC_AUTH_SIGNUP_IP_LIMIT": "1000000"}),   # DEFAULT failure thresholds
    "scaled": (8121, 18121, "g6b_scaled", {"ENC_AUTH_IP_LIMIT": "1000000", "ENC_AUTH_SIGNUP_IP_LIMIT": "1000000", "ENC_AUTH_PAIR_FAIL_LIMIT": "3",
                                           "ENC_AUTH_IP_FAIL_LIMIT": "6", "ENC_AUTH_EMAIL_FAIL_LIMIT": "8", "ENC_AUTH_GLOBAL_FAIL_LIMIT": "14",
                                           "ENC_AUTH_ABUSE_IP_FAIL_LIMIT": "2"}),
    "shortwin": (8122, 18122, "g6b_shortwin", {"ENC_AUTH_IP_LIMIT": "1000000", "ENC_AUTH_SIGNUP_IP_LIMIT": "1000000", "ENC_AUTH_PAIR_FAIL_LIMIT": "3",
                                               "ENC_AUTH_IP_FAIL_LIMIT": "1000000", "ENC_AUTH_EMAIL_FAIL_LIMIT": "1000000",
                                               "ENC_AUTH_GLOBAL_FAIL_LIMIT": "100000000", "ENC_AUTH_WINDOW_SECONDS": "6"}),
    "retry0": (8123, 18123, "g6b_retry0", {**HIGH, "ENC_AUTH_RETRY_MAX": "0"}),
    "deadline": (8124, 18124, "g6b_deadline", {**HIGH, "ENC_AUTH_REQUEST_DEADLINE_SECONDS": "4"}),
    "poolq": (8125, 18125, "g6b_poolq", {**HIGH, "ENC_AUTH_DB_ACQUIRE_TIMEOUT_SECONDS": "1"}),
    "proofage": (8126, 18126, "g6b_proofage", {**HIGH, "ENC_AUTH_PROOF_MAX_AGE_SECONDS": "60"}),
}
REGRESSION = ["main", "disabled", "nokey", "rate", "rotate", "retired", "nodb", "nohash", "notdemo", "noemu"]
G6B_ONLY = ["main", "disabled", "policy", "scaled", "shortwin", "retry0", "deadline", "poolq", "nodb", "proofage"]
RELAY_CONTROL_PORT = 9100
RELAY_CONTROL_HOST_PORT = 18139
CONTAINERS = ["g6b-pg", "g6b-relay", "g6b-gw"] + ["g6b-be-" + n for n in BACKENDS]


def posix(path):
    """A Windows path in the /c/... form that Git Bash tools expect."""
    p = Path(path).resolve()
    return "/" + p.drive[0].lower() + p.as_posix()[2:]


class LabError(RuntimeError):
    pass


def docker(*args, env=None, check=True, timeout=180):
    res = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    if check and res.returncode != 0:
        raise LabError("docker %s failed: %s" % (" ".join(args[:2]), res.stderr.strip()[-300:]))
    return res


def pem(private_key):
    return private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode("ascii")


class Lab:
    def __init__(self, scratch=None, variants=None):
        self.variants = {n: BACKENDS[n] for n in (variants or BACKENDS)}
        self.diag = []
        self.scratch = Path(scratch or tempfile.mkdtemp(prefix="g6b_lab_"))
        self.own_scratch = scratch is None
        self.tree = self.scratch / "tree"
        self.emulator = None
        self.emulator_log = None
        self.started = []
        self.network_created = False
        self.keys = {}          # kid -> private key object (memory only)
        self.report = {}

    # ---------------------------------------------------------------- preflight
    def preflight(self):
        if docker("info", "--format", "{{.ServerVersion}}", check=False).returncode != 0:
            raise LabError("Docker daemon is not running")
        images = docker("images", "--format", "{{.Repository}}:{{.Tag}}").stdout.split()
        for image in (IMAGE, PG_IMAGE):
            if image not in images:
                raise LabError("image %s is not present locally (nothing is ever pulled)" % image)
        existing = set(docker("ps", "-a", "--format", "{{.Names}}").stdout.split())
        if existing & set(CONTAINERS):
            raise LabError("a g6b-* container already exists; refusing to touch it")
        if NET in docker("network", "ls", "--format", "{{.Name}}").stdout.split():
            raise LabError("network %s already exists; refusing to touch it" % NET)
        listening = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        wanted = [EMULATOR_PORT, 4400, 4500, RELAY_CONTROL_HOST_PORT] + [v[1] for v in BACKENDS.values()]
        taken = [p for p in wanted if re.search(r":%d\s+\S+\s+LISTENING" % p, listening)]
        if taken:
            raise LabError("ports already in use: %s" % taken)
        for name in ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_TOKEN", "FIREBASE_CONFIG", "GOOGLE_CLOUD_PROJECT"):
            if os.environ.get(name):
                raise LabError("%s is set in this environment; refusing to start" % name)
        if not Path(g2.NODE).exists() or not g2.CLI.exists():
            raise LabError("cached Node / firebase-tools not found")
        if not Path(GIT_BASH).exists():
            raise LabError("Git Bash not found")

    # ---------------------------------------------------------------- tree
    def build_tree(self):
        self.tree.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([GIT_BASH, posix(G6B / "build_tree_enc_auth_g6b.sh"), posix(self.tree)], capture_output=True, text=True)
        if res.returncode != 0:
            raise LabError("hash-gated build refused or failed: " + res.stderr.strip()[-300:])
        mutant = os.environ.get("G6B_MUTANT")          # test-of-the-tests only: break the SCRATCH tree on purpose, after the official build
        if mutant:
            spec = json.loads(mutant)
            target = self.tree / "backend" / spec["file"]
            text = target.read_bytes().decode("utf-8")
            if text.count(spec["old"]) != 1:
                raise LabError("mutation pattern not found exactly once")
            target.write_bytes(text.replace(spec["old"], spec["new"]).encode("utf-8"))

    # ---------------------------------------------------------------- emulator
    def start_emulator(self):
        cfg_dir = self.scratch / "cfg"
        cfg_dir.mkdir(exist_ok=True)
        config = self.scratch / "firebase.emulator.json"
        config.write_text(json.dumps({"emulators": {"auth": {"host": "127.0.0.1", "port": EMULATOR_PORT},
                                                    "hub": {"host": "127.0.0.1", "port": 4400},
                                                    "logging": {"host": "127.0.0.1", "port": 4500}, "ui": {"enabled": False}}}))
        keep = ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
                "WINDIR", "HOMEDRIVE", "HOMEPATH")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update({"XDG_CONFIG_HOME": str(cfg_dir), "NO_UPDATE_NOTIFIER": "1", "CI": "1", "GCLOUD_PROJECT": PROJECT,
                    "NODE_USE_ENV_PROXY": "1", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                    "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost,::1",
                    "no_proxy": "127.0.0.1,localhost,::1"})
        self.emulator_log = open(self.scratch / "emulator.log", "wb")
        self.emulator = subprocess.Popen([g2.NODE, str(g2.CLI), "emulators:start", "--only", "auth", "--project", PROJECT, "--config",
                                          str(config), "--non-interactive"], cwd=str(self.scratch), env=env, stdout=self.emulator_log,
                                         stderr=subprocess.STDOUT, creationflags=0x00000200 | 0x08000000)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            if self.emulator.poll() is not None:
                raise LabError("emulator exited early")
            try:
                if b"authEmulator" in opener.open("http://127.0.0.1:%d/" % EMULATOR_PORT, timeout=2).read():
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise LabError("emulator did not become ready")
        external, listeners = g2.network_report(g2.descendants(self.emulator.pid))
        if external or listeners:
            raise LabError("emulator isolation check failed")
        self.report["emulator"] = "127.0.0.1:%d, no external connections, no non-loopback listener" % EMULATOR_PORT

    # ---------------------------------------------------------------- docker lab
    def _run(self, name, image, args, env=None):
        docker("run", "-d", "--name", name, *args, image, env=env)
        self.started.append(name)

    def start_docker(self):
        docker("network", "create", "--internal", NET)
        self.network_created = True
        self._run("g6b-pg", PG_IMAGE, ["--network", NET, "-e", "POSTGRES_HOST_AUTH_METHOD=trust"])
        for _ in range(60):
            if docker("exec", "g6b-pg", "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise LabError("postgres did not become ready")
        time.sleep(2)
        for _, _, db, _ in self.variants.values():
            docker("exec", "g6b-pg", "psql", "-U", "postgres", "-c", "CREATE DATABASE %s" % db)

        # relay: internal network + the default bridge, fixed target host.docker.internal:9099, no DNS server
        docker("create", "--name", "g6b-relay", "--network", NET, "--add-host", "host.docker.internal:host-gateway", "--dns", "127.0.0.1",
               "-v", "%s:/h:ro" % HERE, "-e", "FWD_MAP=%d=host.docker.internal:%d" % (EMULATOR_PORT, EMULATOR_PORT), "-e", "CONTROL_PORT=%d" % RELAY_CONTROL_PORT,
               "--entrypoint", "python", IMAGE, "-I", "/h/fwd_g6b.py")
        self.started.append("g6b-relay")
        docker("network", "connect", "bridge", "g6b-relay")
        docker("start", "g6b-relay")

        self._make_keys()
        for name, (port, _host, db, overrides) in self.variants.items():
            self._start_backend(name, port, db, dict(overrides))

        mapping = ",".join("%d=g6b-be-%s:%d" % (v[0], n, v[0]) for n, v in self.variants.items()) + ",%d=g6b-relay:%d" % (RELAY_CONTROL_PORT, RELAY_CONTROL_PORT)
        ports = []
        ports += ["-p", "127.0.0.1:%d:%d" % (RELAY_CONTROL_HOST_PORT, RELAY_CONTROL_PORT)]
        for v in self.variants.values():
            ports += ["-p", "127.0.0.1:%d:%d" % (v[1], v[0])]
        docker("run", "-d", "--name", "g6b-gw", *ports, "-v", "%s:/h:ro" % HERE, "-e", "FWD_MAP=" + mapping, "--entrypoint", "python",
               IMAGE, "-I", "/h/fwd.py")
        self.started.append("g6b-gw")
        docker("network", "connect", NET, "g6b-gw")
        for n, v in self.variants.items():
            self.wait_http("http://127.0.0.1:%d/__g6b/outbox" % v[1], n)

    def _make_keys(self):
        self.keys = {}
        for kid in ("g6b-key-1", "g6b-key-2"):
            self.keys[kid] = generate_test_private_key()

    def keys_json(self, kids):
        return json.dumps({kid: pem(self.keys[kid]) for kid in kids})

    def public_key(self, kid):
        return self.keys[kid].public_key()

    def _start_backend(self, name, port, db, overrides):
        keyset = overrides.pop("KEYSET", "first")
        kids = {"first": ["g6b-key-1"], "both": ["g6b-key-1", "g6b-key-2"], "new_only": ["g6b-key-2"]}[keyset]
        env_values = {
            "DATABASE_URL": "postgresql://postgres@g6b-pg/%s" % db, "FIREBASE_PROJECT_ID": PROJECT, "GCP_PROJECT_ID": PROJECT,
            "FIREBASE_AUTH_EMULATOR_HOST": "g6b-relay:%d" % EMULATOR_PORT, "OPENAI_API_KEY": "dummy-no-egress",
            "SENDGRID_API_KEY": "dummy-no-egress", "SENDGRID_FROM_EMAIL": "noreply@example.invalid",
            "OTP_RATE_LIMIT_HASH_SECRET": "local-test-secret-1", "MFA_OTP_HASH_SECRET": "local-test-secret-2",
            "ALLOWED_ORIGINS": "http://localhost", "PYTHONPATH": "/src/backend", "PYTHONDONTWRITEBYTECODE": "1",
            "ENC_AUTH_ENABLED": "true", "ENC_AUTH_HASH_SECRET": "local-test-secret-3", "ENC_AUTH_ALLOWED_IDENTITY_HOSTS": "g6b-relay",
            "G6B_PORT": str(port),
        }
        env_values.update(overrides)
        args = ["--network", NET, "-w", "/src/backend", "-v", "%s:/src:ro" % self.tree, "-v", "%s:/h:ro" % HERE]
        for key, value in env_values.items():
            if value == "" and key == "FIREBASE_AUTH_EMULATOR_HOST":
                continue                                            # variant "noemu": the variable is simply not set
            args += ["-e", "%s=%s" % (key, value)]
        cli_env = dict(os.environ)
        if "ENC_AUTH_KEYS" not in overrides:
            cli_env["ENC_AUTH_KEYS"] = self.keys_json(kids)
            args += ["-e", "ENC_AUTH_KEYS"]                         # value taken from the docker CLI's environment: not on any command line
        else:
            args += ["-e", "ENC_AUTH_KEYS="]
        docker("run", "-d", "--name", "g6b-be-" + name, *args, "--entrypoint", "python", IMAGE, "/h/server_g6b.py", env=cli_env)
        self.started.append("g6b-be-" + name)

    def wait_http(self, url, label, seconds=90):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                opener.open(url, timeout=3).read()
                return
            except Exception:
                time.sleep(1)
        logs = docker("logs", "--tail", "15", "g6b-be-" + label, check=False).stdout[-500:] if label in self.variants else ""
        raise LabError("%s did not become ready. %s" % (label, logs))

    # ---------------------------------------------------------------- isolation checks
    def verify_isolation(self):
        info = json.loads(docker("network", "inspect", NET).stdout)[0]
        if not info.get("Internal"):
            raise LabError("lab network is not internal")
        for name in ["g6b-pg"] + ["g6b-be-" + n for n in self.variants]:
            nets = json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", name).stdout)
            if set(nets) != {NET}:
                raise LabError("%s is attached to %s" % (name, sorted(nets)))
        relay_nets = set(json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", "g6b-relay").stdout))
        if relay_nets != {NET, "bridge"}:
            raise LabError("relay networks unexpected: %s" % sorted(relay_nets))
        for line in docker("port", "g6b-gw").stdout.splitlines():
            if not line.split("->")[-1].strip().startswith("127.0.0.1:"):
                raise LabError("published port is not loopback-only: " + line)
        probe = ("import socket,sys\n"
                 "ok=[]\n"
                 "for t in (('1.1.1.1',443),('8.8.8.8',53)):\n"
                 "    try: socket.create_connection(t,timeout=3); ok.append(t)\n"
                 "    except OSError: pass\n"
                 "try: socket.getaddrinfo('example.com',443); ok.append('dns')\n"
                 "except OSError: pass\n"
                 "sys.exit(1 if ok else 0)\n")
        for name in ["g6b-be-main", "g6b-pg"]:
            exe = "python" if name != "g6b-pg" else "sh"
            if name == "g6b-pg":
                res = docker("exec", name, "sh", "-c", "wget -q -T 3 -O /dev/null http://1.1.1.1 || nc -w 3 -z 1.1.1.1 443", check=False)
                reached = res.returncode == 0
            else:
                reached = docker("exec", name, exe, "-I", "-c", probe, check=False).returncode != 0
            if reached:
                raise LabError("%s can reach the outside world" % name)
        # the relay can reach the emulator (positive control) and nothing resolves
        res = docker("exec", "g6b-be-main", "python", "-I", "-c",
                     "import urllib.request as u;print(b'authEmulator' in u.urlopen('http://g6b-relay:%d/',timeout=5).read())" % EMULATOR_PORT,
                     check=False)
        if res.stdout.strip() != "True":
            raise LabError("backend cannot reach the emulator through the relay: " + res.stderr.strip()[-200:])
        self.report["docker"] = ("internal network; backends and Postgres have no route out (probe to public IPs and DNS failed); "
                                 "only the fixed-target relay reaches host:9099; published ports are 127.0.0.1-only")

    # ---------------------------------------------------------------- helpers for tests
    def psql(self, db, sql):
        res = docker("exec", "g6b-pg", "psql", "-U", "postgres", "-d", db, "-At", "-c", sql)
        return res.stdout.strip()

    def logs(self, backend):
        res = docker("logs", "g6b-be-" + backend, check=False)
        return res.stdout + res.stderr

    # ---------------------------------------------------------------- G6b fault-injection controls
    def relay(self, method, path, body=None):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (RELAY_CONTROL_HOST_PORT, path), method=method,
                                         data=json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None),
                                         headers={"Content-Type": "application/json"})
        return json.loads(opener.open(request, timeout=10).read())

    def relay_stats(self):
        return self.relay("GET", "/stats")

    def relay_reset(self):
        return self.relay("POST", "/reset")

    def relay_faults(self, **faults):
        return self.relay("POST", "/faults", faults)

    def relay_kill(self):
        return self.relay("POST", "/kill")

    def _emulator_pids(self):
        return sorted(g2.descendants(self.emulator.pid))

    def pause_emulator(self):
        """Freezes the emulator's whole process tree (NtSuspendProcess): connections are accepted by the OS but never answered."""
        import ctypes
        kernel, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
        self._suspended = []
        for pid in self._emulator_pids():
            handle = kernel.OpenProcess(0x0800, False, pid)           # PROCESS_SUSPEND_RESUME
            if handle:
                ntdll.NtSuspendProcess(handle)
                self._suspended.append(handle)

    def resume_emulator(self):
        import ctypes
        kernel, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
        for handle in getattr(self, "_suspended", []):
            ntdll.NtResumeProcess(handle)
            kernel.CloseHandle(handle)
        self._suspended = []

    def stop_postgres(self):
        docker("stop", "-t", "2", "g6b-pg")

    def start_postgres(self):
        docker("start", "g6b-pg")
        for _ in range(60):
            if docker("exec", "g6b-pg", "pg_isready", "-U", "postgres", check=False).returncode == 0:
                return
            time.sleep(1)
        raise LabError("postgres did not come back")

    def capture(self, label, backend="main"):
        """Failure-time diagnostics, taken BEFORE teardown. Only fixed codes, counters and container states: the backend log lines are
        the gateway's own `[ENC-AUTH]` lines (no credentials by construction; the leak scan checks that) plus the last few raw lines."""
        parts = ["=== %s ===" % label]
        try:
            parts.append("relay: %s" % json.dumps(self.relay_stats()))
        except Exception as exc:
            parts.append("relay: unreachable (%s)" % type(exc).__name__)
        try:
            state = docker("ps", "-a", "--format", "{{.Names}}={{.Status}}", check=False).stdout.split()
            parts.append("containers: " + ", ".join(s for s in state if s.startswith("g6b-")))
        except Exception:
            pass
        parts.append("emulator alive: %s" % (self.emulator is not None and self.emulator.poll() is None))
        port = self.variants[backend][1] if backend in self.variants else None
        if port:
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                parts.append("diag: " + opener.open("http://127.0.0.1:%d/__g6b/diag" % port, timeout=5).read().decode()[:1500])
            except Exception as exc:
                parts.append("diag: unreachable (%s)" % type(exc).__name__)
            log = self.logs(backend).splitlines()
            gateway_lines = [line for line in log if "[ENC-AUTH]" in line][-15:]
            parts.append("gateway log lines:" + "".join(chr(10) + "  " + line for line in gateway_lines))
            parts.append("last raw log lines:" + "".join(chr(10) + "  " + line for line in log[-8:]))
        text = chr(10).join(parts)
        self.diag.append(text)
        return text

    # ---------------------------------------------------------------- lifecycle
    def up(self):
        try:
            self.preflight()
            self.build_tree()
            self.start_emulator()
            self.start_docker()
            self.verify_isolation()
        except BaseException:
            self.down()
            raise
        return self

    def down(self):
        """Stops ONLY what this lab started."""
        for text in self.diag:
            print(text[:4000], flush=True)
        self.diag = []
        for name in reversed(self.started):
            docker("rm", "-f", "-v", name, check=False)
        self.started = []
        if self.network_created:
            docker("network", "rm", NET, check=False)
            self.network_created = False
        if self.emulator is not None:
            subprocess.run(["taskkill", "/PID", str(self.emulator.pid), "/T", "/F"], capture_output=True)
            self.emulator = None
        if self.emulator_log:
            self.emulator_log.close()
            self.emulator_log = None
        self.keys = {}
        if self.own_scratch and self.scratch.exists():
            shutil.rmtree(self.scratch, ignore_errors=True)


if __name__ == "__main__":
    lab = Lab(sys.argv[2] if len(sys.argv) > 2 else None)
    if sys.argv[1:2] == ["probe"]:
        lab.up()
        print("LAB OK:", json.dumps(lab.report))
        lab.down()
        print("LAB DOWN")
