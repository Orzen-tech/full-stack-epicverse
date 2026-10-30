"""Fixed-target TCP relay of the G6b lab, with COUNTERS and FAULT INJECTION. TEST HARNESS ONLY (never part of a patch or deployment).

Data port:    FWD_MAP="<listen-port>=<host>:<port>,..." as in G6a. Only ever connects to the fixed targets named there.
Control port: CONTROL_PORT (HTTP, inside the lab network only; also published on 127.0.0.1 through the lab's gateway container):
    GET  /stats                 -> counters {accepted, connect_ok, connect_fail, dropped, refused, replied, delayed, active}
    POST /reset                 -> zero the counters and clear every fault
    POST /faults  {"drop_first": N,                       close the next N new connections at once (connection dropped)
                   "refuse_for_s": S,                     close every new connection for S seconds (relay temporarily unavailable)
                   "delay_ms": D,                         wait D ms before connecting upstream
                   "reply": {"status": 503, "body": "...", "count": N, "match": "signInWithPassword"}
                                                          answer the next N requests whose first line contains `match` with a canned
                                                          HTTP response instead of forwarding them}
    POST /kill                  -> close every live connection now (like a relay restart)
Nothing about the traffic content is logged or stored; only the first request line is looked at, to decide whether a canned reply applies.
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATS = {"accepted": 0, "connect_ok": 0, "connect_fail": 0, "dropped": 0, "refused": 0, "replied": 0, "delayed": 0}
FAULTS = {"drop_first": 0, "refuse_until": 0.0, "delay_ms": 0, "reply": None}
LIVE = set()


def bump(name, n=1):
    with LOCK:
        STATS[name] += n


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def canned(status, body):
    reason = {400: "Bad Request", 401: "Unauthorized", 502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}.get(status, "Error")
    data = body.encode("utf-8")
    return ("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (status, reason, len(data))).encode() + data


def handle(client, host, port):
    bump("accepted")
    with LOCK:
        if FAULTS["drop_first"] > 0:
            FAULTS["drop_first"] -= 1
            drop = True
        else:
            drop = False
        refuse = time.time() < FAULTS["refuse_until"]
        delay = FAULTS["delay_ms"]
    if drop or refuse:
        bump("dropped" if drop else "refused")
        try:
            client.close()
        except OSError:
            pass
        return
    if delay:
        bump("delayed")
        time.sleep(delay / 1000.0)
    buffered = b""
    reply = None
    with LOCK:
        pending = FAULTS["reply"]
    if pending:
        client.settimeout(5)
        try:
            while b"\r\n" not in buffered and len(buffered) < 4096:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buffered += chunk
        except OSError:
            pass
        client.settimeout(None)
        first_line = buffered.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        with LOCK:
            current = FAULTS["reply"]
            if current and current["count"] > 0 and current["match"] in first_line:
                current["count"] -= 1
                if current["count"] <= 0:
                    FAULTS["reply"] = None
                reply = canned(current["status"], current["body"])
    if reply is not None:
        try:
            client.recv(0)
            client.sendall(reply)
        except OSError:
            pass
        bump("replied")
        try:
            client.close()
        except OSError:
            pass
        return
    upstream = None
    for attempt in range(3):
        try:
            upstream = socket.create_connection((host, port), timeout=10)
            bump("connect_ok")
            break
        except OSError:
            bump("connect_fail")
            time.sleep(0.2 * (attempt + 1))
    if upstream is None:
        client.close()
        return
    if buffered:
        upstream.sendall(buffered)
    with LOCK:
        LIVE.update((client, upstream))
    t1 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
    t2 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    with LOCK:
        LIVE.discard(client)
        LIVE.discard(upstream)


def serve(listen_port, host, port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", listen_port))
    server.listen(256)
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client, host, port), daemon=True).start()


class Control(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with LOCK:
            self._send(200, {**STATS, "active": len(LIVE) // 2})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if self.path == "/reset":
            with LOCK:
                for key in STATS:
                    STATS[key] = 0
                FAULTS.update({"drop_first": 0, "refuse_until": 0.0, "delay_ms": 0, "reply": None})
            return self._send(200, {"ok": True})
        if self.path == "/kill":
            with LOCK:
                victims = list(LIVE)
            for sock in victims:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            return self._send(200, {"killed": len(victims) // 2})
        if self.path == "/faults":
            with LOCK:
                if "drop_first" in data:
                    FAULTS["drop_first"] = int(data["drop_first"])
                if "refuse_for_s" in data:
                    FAULTS["refuse_until"] = time.time() + float(data["refuse_for_s"])
                if "delay_ms" in data:
                    FAULTS["delay_ms"] = int(data["delay_ms"])
                if "reply" in data:
                    r = data["reply"]
                    FAULTS["reply"] = None if not r else {"status": int(r["status"]), "body": str(r.get("body", "{}")),
                                                          "count": int(r.get("count", 1)), "match": str(r.get("match", ""))}
            return self._send(200, {"ok": True})
        self._send(404, {})


def main():
    for item in os.environ["FWD_MAP"].split(","):
        listen, target = item.split("=")
        host, port = target.rsplit(":", 1)
        threading.Thread(target=serve, args=(int(listen), host, int(port)), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ["CONTROL_PORT"])), Control).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
