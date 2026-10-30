"""Subprocess probe for test_hooks.py: the G8 urllib3 hook and socket backstop against a REAL loopback HTTP server (127.0.0.1 only).

The only addresses this process can reach are 127.0.0.1 ports: name resolution and connect() are restricted to loopback BEFORE the G8 hooks are
installed, so even a defect in the G8 hooks cannot make this probe talk to anything else. Prints one JSON document; the test asserts on it.
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

G8 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, G8)

_real = (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex)
RESOLVED = []
CONNECTS = []


def strict_getaddrinfo(host, *args, **kwargs):
    RESOLVED.append("lookup")
    if host != "127.0.0.1":
        raise OSError("probe: only 127.0.0.1 may be resolved")
    return _real[0](host, *args, **kwargs)


def strict_connect(self, address):
    CONNECTS.append("connect")
    if not (isinstance(address, tuple) and address[0] == "127.0.0.1"):
        raise OSError("probe: only 127.0.0.1 may be connected")
    return _real[1](self, address)


def strict_connect_ex(self, address):
    if not (isinstance(address, tuple) and address[0] == "127.0.0.1"):
        return 1
    return _real[2](self, address)


socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = strict_getaddrinfo, strict_connect, strict_connect_ex

import requests  # noqa: E402
from firebase_admin import _http_client  # noqa: E402

import g8_guard as guard  # noqa: E402

HITS = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _answer(self):
        HITS.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.startswith("/s503"):
            self.send_response(503)
            body = b"{}"
        elif self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", "/ok")
            body = b""
        else:
            self.send_response(200)
            body = b"{}"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT


def session():
    s = requests.Session()
    s.mount("http://", requests.adapters.HTTPAdapter(max_retries=_http_client.DEFAULT_RETRY_CONFIG))     # the SDK's own retry policy
    return s


def fresh(quota):
    egress = guard.Egress("A", _loopback_test_port=PORT)
    egress._quota["lookup"] = quota
    egress.total_cap = quota
    return egress


def scenario(name, quota, action, backstop=False):
    del HITS[:]
    resolved_before = len(RESOLVED)
    egress = fresh(quota)
    guard.install_urllib3_hook(egress)
    if backstop:
        guard.install_socket_backstop(egress)
    outcome = "returned"
    try:
        action(egress)
    except guard.LiveSafetyStop as stop:
        outcome = stop.code
    except Exception as exc:
        outcome = "exception:" + type(exc).__name__
    finally:
        guard.uninstall_all()
    used = egress.used()
    return {"outcome": outcome, "server_hits": len(HITS), "charged": used[1], "connections": egress.connections, "tripped": egress.tripped,
            "resolutions": len(RESOLVED) - resolved_before}


def status_retries(egress):
    session().post(BASE + "/s503", data=b"{}", timeout=5)


def sequential_closed(egress):
    s = session()
    for _ in range(3):
        s.get(BASE + "/ok", headers={"Connection": "close"}, timeout=5)


def keep_alive(egress):
    s = session()
    for _ in range(3):
        s.get(BASE + "/ok", timeout=5)


def redirect(egress):
    session().get(BASE + "/redir", timeout=5)


def other_host(egress):
    session().get("http://example.org/x", timeout=2)


def https_to_loopback(egress):
    session().get("https://127.0.0.1:%d/ok" % PORT, timeout=2)


def no_timeout(egress):
    session().get(BASE + "/ok")                      # no timeout at all: the hook must bound it, not forward "forever"


def after_trip(egress):
    try:
        session().get("http://example.org/x", timeout=2)
    except guard.LiveSafetyStop:
        pass
    session().get(BASE + "/ok", timeout=5)


def backstop_direct(egress):
    socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
    try:
        socket.socket().connect(("93.184.216.34", 80))
    except guard.LiveSafetyStop as stop:
        egress.refused_code = stop.code
        raise


def backstop_dns(egress):
    socket.getaddrinfo("example.org", 443)


results = {
    "status_retries": scenario("status_retries", 2, status_retries),
    "status_retries_unbounded_by_cap": scenario("status_retries_wide", 9, status_retries),
    "sequential_closed": scenario("sequential_closed", 5, sequential_closed, backstop=True),
    "keep_alive": scenario("keep_alive", 5, keep_alive, backstop=True),
    "redirect": scenario("redirect", 5, redirect),
    "other_host": scenario("other_host", 5, other_host),
    "https_to_loopback": scenario("https_to_loopback", 5, https_to_loopback),
    "no_timeout": scenario("no_timeout", 5, no_timeout),
    "after_trip": scenario("after_trip", 5, after_trip),
    "backstop_direct": scenario("backstop_direct", 5, backstop_direct, backstop=True),
    "backstop_dns": scenario("backstop_dns", 5, backstop_dns, backstop=True),
}
results["connect_calls_total"] = len(CONNECTS)

# can the cap be guaranteed in this process once both layers are installed?
egress = fresh(5)
guard.install_urllib3_hook(egress)
guard.install_socket_backstop(egress)
results["cap_guaranteed_installed"] = guard.cap_guaranteed(env={}, retry_max=0)
results["cap_guaranteed_retry_on"] = guard.cap_guaranteed(env={}, retry_max=2)
guard.uninstall_all()
results["cap_guaranteed_uninstalled"] = guard.cap_guaranteed(env={}, retry_max=0)
server.shutdown()
print(json.dumps(results))
