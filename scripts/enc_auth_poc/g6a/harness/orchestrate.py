"""G6a lab: builds the hash-gated test tree, starts the LOCAL Firebase Auth emulator and an isolated Docker network, runs the patched
backend in several configurations, and tears down ONLY what it started.

Isolation (all of it is checked at start-up, and the lab refuses to continue if any check fails):
  * the Auth emulator listens on 127.0.0.1:9099 only (demo- project, no login, proxies pointing at a dead port, no telemetry);
  * Postgres and every backend container live on an `--internal` Docker network (no route to the internet, no external DNS);
  * the only bridge between that network and the emulator is a fixed-target TCP relay container (g6a-relay) that connects to
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
G6A = HERE.parent
POC = G6A.parent
REPO = POC.parent.parent
sys.path.insert(0, str(POC))
sys.path.insert(0, str(POC / "g2"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402
import run_g2 as g2  # noqa: E402  (read-only helpers: process tree and network inspection)

IMAGE = "epicverse-backend:tests-c185263"
PG_IMAGE = "postgres:17-alpine"
NET = "g6a-net"
PROJECT = "demo-epicverse-g6a"
EMULATOR_PORT = 9099
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"

# name -> (container port, host port, database, environment overrides)
LOW_LIMITS = {"ENC_AUTH_IP_LIMIT": "1000000", "ENC_AUTH_SIGNUP_IP_LIMIT": "1000000", "ENC_AUTH_IP_FAIL_LIMIT": "1000000"}
BACKENDS = {
    "main": (8090, 18090, "g6a_main", dict(LOW_LIMITS)),
    "disabled": (8091, 18091, "g6a_disabled", {"ENC_AUTH_ENABLED": "false"}),
    "nokey": (8092, 18092, "g6a_nokey", {"ENC_AUTH_KEYS": ""}),
    "rate": (8093, 18093, "g6a_rate", {"ENC_AUTH_IP_LIMIT": "40", "ENC_AUTH_SIGNUP_IP_LIMIT": "3", "ENC_AUTH_EMAIL_FAIL_LIMIT": "3",
                                       "ENC_AUTH_IP_FAIL_LIMIT": "6"}),
    "rotate": (8094, 18094, "g6a_rotate", {**LOW_LIMITS, "KEYSET": "both"}),
    "retired": (8095, 18095, "g6a_retired", {**LOW_LIMITS, "KEYSET": "new_only"}),
    "nodb": (8096, 18096, "g6a_nodb", {**LOW_LIMITS, "DATABASE_URL": "postgresql://postgres@g6a-no-such-host/none"}),
    "nohash": (8097, 18097, "g6a_nohash", {"ENC_AUTH_HASH_SECRET": ""}),
    "notdemo": (8098, 18098, "g6a_notdemo", {**LOW_LIMITS, "FIREBASE_PROJECT_ID": "g6a-notdemo-project"}),
    "noemu": (8099, 18099, "g6a_noemu", {**LOW_LIMITS, "FIREBASE_AUTH_EMULATOR_HOST": ""}),
}
CONTAINERS = ["g6a-pg", "g6a-relay", "g6a-gw"] + ["g6a-be-" + n for n in BACKENDS]


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
    def __init__(self, scratch=None):
        self.scratch = Path(scratch or tempfile.mkdtemp(prefix="g6a_lab_"))
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
            raise LabError("a g6a-* container already exists; refusing to touch it")
        if NET in docker("network", "ls", "--format", "{{.Name}}").stdout.split():
            raise LabError("network %s already exists; refusing to touch it" % NET)
        listening = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        wanted = [EMULATOR_PORT, 4400, 4500] + [v[1] for v in BACKENDS.values()]
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
        res = subprocess.run([GIT_BASH, posix(G6A / "build_tree_enc_auth.sh"), posix(self.tree)], capture_output=True, text=True)
        if res.returncode != 0:
            raise LabError("hash-gated build refused or failed: " + res.stderr.strip()[-300:])
        mutant = os.environ.get("G6A_MUTANT")          # test-of-the-tests only: break the SCRATCH tree on purpose, after the official build
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
        self._run("g6a-pg", PG_IMAGE, ["--network", NET, "-e", "POSTGRES_HOST_AUTH_METHOD=trust"])
        for _ in range(60):
            if docker("exec", "g6a-pg", "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise LabError("postgres did not become ready")
        time.sleep(2)
        for _, _, db, _ in BACKENDS.values():
            docker("exec", "g6a-pg", "psql", "-U", "postgres", "-c", "CREATE DATABASE %s" % db)

        # relay: internal network + the default bridge, fixed target host.docker.internal:9099, no DNS server
        docker("create", "--name", "g6a-relay", "--network", NET, "--add-host", "host.docker.internal:host-gateway", "--dns", "127.0.0.1",
               "-v", "%s:/h:ro" % HERE, "-e", "FWD_MAP=%d=host.docker.internal:%d" % (EMULATOR_PORT, EMULATOR_PORT),
               "--entrypoint", "python", IMAGE, "-I", "/h/fwd.py")
        self.started.append("g6a-relay")
        docker("network", "connect", "bridge", "g6a-relay")
        docker("start", "g6a-relay")

        self._make_keys()
        for name, (port, _host, db, overrides) in BACKENDS.items():
            self._start_backend(name, port, db, dict(overrides))

        mapping = ",".join("%d=g6a-be-%s:%d" % (v[0], n, v[0]) for n, v in BACKENDS.items())
        ports = []
        for v in BACKENDS.values():
            ports += ["-p", "127.0.0.1:%d:%d" % (v[1], v[0])]
        docker("run", "-d", "--name", "g6a-gw", *ports, "-v", "%s:/h:ro" % HERE, "-e", "FWD_MAP=" + mapping, "--entrypoint", "python",
               IMAGE, "-I", "/h/fwd.py")
        self.started.append("g6a-gw")
        docker("network", "connect", NET, "g6a-gw")
        for n, v in BACKENDS.items():
            self.wait_http("http://127.0.0.1:%d/__g6a/outbox" % v[1], n)

    def _make_keys(self):
        self.keys = {}
        for kid in ("g6a-key-1", "g6a-key-2"):
            self.keys[kid] = generate_test_private_key()

    def keys_json(self, kids):
        return json.dumps({kid: pem(self.keys[kid]) for kid in kids})

    def public_key(self, kid):
        return self.keys[kid].public_key()

    def _start_backend(self, name, port, db, overrides):
        keyset = overrides.pop("KEYSET", "first")
        kids = {"first": ["g6a-key-1"], "both": ["g6a-key-1", "g6a-key-2"], "new_only": ["g6a-key-2"]}[keyset]
        env_values = {
            "DATABASE_URL": "postgresql://postgres@g6a-pg/%s" % db, "FIREBASE_PROJECT_ID": PROJECT, "GCP_PROJECT_ID": PROJECT,
            "FIREBASE_AUTH_EMULATOR_HOST": "g6a-relay:%d" % EMULATOR_PORT, "OPENAI_API_KEY": "dummy-no-egress",
            "SENDGRID_API_KEY": "dummy-no-egress", "SENDGRID_FROM_EMAIL": "noreply@example.invalid",
            "OTP_RATE_LIMIT_HASH_SECRET": "local-test-secret-1", "MFA_OTP_HASH_SECRET": "local-test-secret-2",
            "ALLOWED_ORIGINS": "http://localhost", "PYTHONPATH": "/src/backend", "PYTHONDONTWRITEBYTECODE": "1",
            "ENC_AUTH_ENABLED": "true", "ENC_AUTH_HASH_SECRET": "local-test-secret-3", "ENC_AUTH_ALLOWED_IDENTITY_HOSTS": "g6a-relay",
            "G6A_PORT": str(port),
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
        docker("run", "-d", "--name", "g6a-be-" + name, *args, "--entrypoint", "python", IMAGE, "/h/server_g6a.py", env=cli_env)
        self.started.append("g6a-be-" + name)

    def wait_http(self, url, label, seconds=90):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                opener.open(url, timeout=3).read()
                return
            except Exception:
                time.sleep(1)
        logs = docker("logs", "--tail", "15", "g6a-be-" + label, check=False).stdout[-500:] if label in BACKENDS else ""
        raise LabError("%s did not become ready. %s" % (label, logs))

    # ---------------------------------------------------------------- isolation checks
    def verify_isolation(self):
        info = json.loads(docker("network", "inspect", NET).stdout)[0]
        if not info.get("Internal"):
            raise LabError("lab network is not internal")
        for name in ["g6a-pg"] + ["g6a-be-" + n for n in BACKENDS]:
            nets = json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", name).stdout)
            if set(nets) != {NET}:
                raise LabError("%s is attached to %s" % (name, sorted(nets)))
        relay_nets = set(json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", "g6a-relay").stdout))
        if relay_nets != {NET, "bridge"}:
            raise LabError("relay networks unexpected: %s" % sorted(relay_nets))
        for line in docker("port", "g6a-gw").stdout.splitlines():
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
        for name in ["g6a-be-main", "g6a-be-notdemo", "g6a-pg"]:
            exe = "python" if name != "g6a-pg" else "sh"
            if name == "g6a-pg":
                res = docker("exec", name, "sh", "-c", "wget -q -T 3 -O /dev/null http://1.1.1.1 || nc -w 3 -z 1.1.1.1 443", check=False)
                reached = res.returncode == 0
            else:
                reached = docker("exec", name, exe, "-I", "-c", probe, check=False).returncode != 0
            if reached:
                raise LabError("%s can reach the outside world" % name)
        # the relay can reach the emulator (positive control) and nothing resolves
        res = docker("exec", "g6a-be-main", "python", "-I", "-c",
                     "import urllib.request as u;print(b'authEmulator' in u.urlopen('http://g6a-relay:%d/',timeout=5).read())" % EMULATOR_PORT,
                     check=False)
        if res.stdout.strip() != "True":
            raise LabError("backend cannot reach the emulator through the relay: " + res.stderr.strip()[-200:])
        self.report["docker"] = ("internal network; backends and Postgres have no route out (probe to public IPs and DNS failed); "
                                 "only the fixed-target relay reaches host:9099; published ports are 127.0.0.1-only")

    # ---------------------------------------------------------------- helpers for tests
    def psql(self, db, sql):
        res = docker("exec", "g6a-pg", "psql", "-U", "postgres", "-d", db, "-At", "-c", sql)
        return res.stdout.strip()

    def logs(self, backend):
        res = docker("logs", "g6a-be-" + backend, check=False)
        return res.stdout + res.stderr

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
