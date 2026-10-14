"""Fake Firebase Auth emulator endpoint (accounts:lookup only). Fake users only; no network egress."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
USERS = {}      # uid -> email
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/_admin/add"):
            USERS[body["uid"]] = body["email"].lower(); return self._send({"ok": True})
        if self.path.endswith("accounts:lookup"):
            out = []
            for uid, em in USERS.items():
                if uid in body.get("localId", []) or em in [e.lower() for e in body.get("email", [])]:
                    out.append({"localId": uid, "email": em, "emailVerified": False, "disabled": False, "createdAt": "0", "lastLoginAt": "0",
                                "providerUserInfo": []})
            return self._send({"kind": "identitytoolkit#GetAccountInfoResponse", **({"users": out} if out else {})})
        self._send({"error": "unsupported"}, 404)
    def do_GET(self): self._send({"ok": True})
HTTPServer(("0.0.0.0", 9099), H).serve_forever()
