"""agentnet-web: web UI for the agents egress allowlist.

Authentication happens in front of this app (nginx + Authentik forward auth
on edge01). The app only accepts connections from that proxy and forwards
list/allow/deny to the egress proxy, where an SSH forced command runs agentnet.
"""

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BACKEND = os.environ["AGENTNET_BACKEND"]
ORIGIN = os.environ["AGENTNET_ORIGIN"]
TRUSTED_PROXY = os.environ["AGENTNET_TRUSTED_PROXY"]
SSH_DIR = os.environ.get("AGENTNET_SSH_DIR", "/ssh")

SSH = [
    "ssh",
    "-i", f"{SSH_DIR}/id_ed25519",
    "-o", f"UserKnownHostsFile={SSH_DIR}/known_hosts",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    BACKEND,
]

DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
USER_RE = re.compile(r"[^A-Za-z0-9._@-]")
INDEX = (Path(__file__).parent / "index.html").read_bytes()


def backend(*args: str) -> dict:
    try:
        result = subprocess.run(
            SSH + [" ".join(args)], capture_output=True, text=True, timeout=30
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"egress proxy unreachable: {exc.__class__.__name__}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "agentnet-web"

    def send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, data: dict) -> None:
        self.send(status, json.dumps(data).encode(), "application/json")

    def from_proxy(self) -> bool:
        # Requests that bypass edge01 would bypass Authentik too.
        if self.client_address[0] != TRUSTED_PROXY:
            self.send_json(403, {"ok": False, "message": "forbidden"})
            return False
        return True

    def do_GET(self) -> None:
        if not self.from_proxy():
            return
        url = urlsplit(self.path)
        if url.path == "/":
            self.send(200, INDEX, "text/html; charset=utf-8")
        elif url.path == "/api/list":
            self.send_json(200, backend("list"))
        elif url.path == "/api/events":
            # "-" stands for connections that carried no host name.
            host = parse_qs(url.query).get("host", [""])[0].strip().lower()
            if host != "-" and not DOMAIN_RE.match(host):
                self.send_json(400, {"ok": False, "message": "invalid host"})
                return
            self.send_json(200, backend("events", host))
        else:
            self.send_json(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:
        if not self.from_proxy():
            return
        action = {"/api/allow": "allow", "/api/deny": "deny"}.get(self.path)
        if action is None:
            self.send_json(404, {"ok": False, "message": "not found"})
            return
        # A JSON body plus a matching Origin cannot be forged by a
        # cross-site form, so the Authentik session cookie alone is not enough.
        if (
            self.headers.get("Origin") != ORIGIN
            or self.headers.get("Content-Type") != "application/json"
        ):
            self.send_json(403, {"ok": False, "message": "cross-site request rejected"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 4096)
            domain = str(json.loads(self.rfile.read(length))["domain"]).strip().lower()
        except (ValueError, KeyError, TypeError):
            self.send_json(400, {"ok": False, "message": "expected {\"domain\": ...}"})
            return
        if not DOMAIN_RE.match(domain):
            self.send_json(400, {"ok": False, "message": f"invalid domain: {domain}"})
            return
        user = USER_RE.sub("", self.headers.get("X-authentik-username", ""))[:64] or "unknown"
        self.send_json(200, backend(action, domain, user))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
