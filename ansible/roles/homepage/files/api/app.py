"""homepage-api: what the admin Homepage cannot do by itself.

GET /stats returns pve01 CPU, temperature, RAM and storage as display strings
for a customapi widget. homepage-admins calls it over the internal
`homepage-api` network, so no user headers are involved.

/_homelab/agent/ is the start/hibernate control for the agent VM, shown in an
iframe widget. Hibernate suspends the VM to disk, which frees its memory on
pve01; start resumes it where it left off. The browser reaches it through edge (Authentik forward auth)
and the apps01 proxy, which routes it only for `homelab admins`. The app
checks the group again and accepts actions only as same-origin POSTs.
"""

import json
import os
import ssl
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONFIG_DIR = Path(os.environ.get("HOMEPAGE_API_CONFIG_DIR", "/config"))
CONFIG = json.loads((CONFIG_DIR / "config.json").read_text())
PVE_TOKEN = os.environ["PVE_AGENT_POWER_TOKEN"]
AGENT_PREFIX = "/_homelab/agent/"
PAGE = (Path(__file__).parent / "agent.html").read_bytes()

# Verify the Proxmox API against the cluster CA. The node certificate names
# the host, not the IP address the app connects to, and the CA that Proxmox
# generates has no keyUsage extension, which Python 3.13's strict mode rejects.
PVE_TLS = ssl.create_default_context(cafile=str(CONFIG_DIR / "pve-root-ca.pem"))
PVE_TLS.check_hostname = False
PVE_TLS.verify_flags &= ~ssl.VERIFY_X509_STRICT

GIB = 1024**3

# Backend failures are reported as 200 with ok=false: Cloudflare replaces 5xx
# responses from the origin with its own HTML page, which would hide the message.
BACKEND_FAILED = 200


def prometheus(query: str) -> float | None:
    url = f"{CONFIG['prometheus_url']}/api/v1/query?{urlencode({'query': query})}"
    with urlopen(url, timeout=5) as response:
        result = json.load(response)["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def used_of_total(used: float | None, total: float | None) -> str:
    if used is None or not total:
        return "?"
    digits = 0 if total >= 100 * GIB else 1
    return f"{used / GIB:.{digits}f} / {total / GIB:.{digits}f} GB"


def stats() -> dict:
    values = {}
    for name, query in CONFIG["queries"].items():
        try:
            values[name] = prometheus(query)
        except (URLError, OSError, ValueError, KeyError):
            values[name] = None
    cpu, temperature = values["cpu"], values["temperature"]
    return {
        "cpu": "?" if cpu is None else f"{cpu:.0f} %",
        "temperature": "?" if temperature is None else f"{temperature:.0f} °C",
        "memory": used_of_total(values["memory_used"], values["memory_total"]),
        "storage": used_of_total(values["storage_used"], values["storage_total"]),
    }


def pve(method: str, path: str, params: dict | None = None) -> object:
    request = Request(
        f"{CONFIG['pve_url']}/api2/json{path}",
        data=urlencode(params).encode() if params else None,
        method=method,
        headers={"Authorization": f"PVEAPIToken={PVE_TOKEN}"},
    )
    with urlopen(request, context=PVE_TLS, timeout=10) as response:
        return json.load(response)["data"]


def agent_vm() -> dict:
    # The token only sees this VM, and the lookup also tells which node runs it.
    for vm in pve("GET", "/cluster/resources?type=vm"):
        if vm.get("vmid") == CONFIG["agent_vmid"]:
            return vm
    raise LookupError("agent VM not visible to the API token")


def agent_status(vm: dict) -> dict:
    # Only the node's status call reports the lock: "suspending" while the
    # memory is written to disk, "suspended" once the VM is hibernated.
    return pve("GET", f"/nodes/{vm['node']}/qemu/{vm['vmid']}/status/current")


# Proxmox calls for each page action. A start resumes a hibernated VM.
ACTIONS = {
    "start": ("start", None),
    "hibernate": ("suspend", {"todisk": 1, "statestorage": CONFIG["agent_state_storage"]}),
}


def describe(exc: Exception) -> str:
    """Error text for the page; the full traceback goes to the container log."""
    traceback.print_exception(exc)
    if isinstance(exc, HTTPError):
        return f"Proxmox API {exc.code}: {exc.reason}"
    if isinstance(exc, URLError):
        return f"Proxmox API: {exc.reason}"
    return f"{exc.__class__.__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "homepage-api"

    def send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, data: dict) -> None:
        self.send(status, json.dumps(data).encode(), "application/json")

    def is_admin(self) -> bool:
        groups = self.headers.get("X-authentik-groups", "").split("|")
        if CONFIG["admin_group"] in groups:
            return True
        self.send_json(403, {"ok": False, "message": "forbidden"})
        return False

    def do_GET(self) -> None:
        if self.path == "/stats":
            self.send_json(200, stats())
        elif self.path == AGENT_PREFIX:
            if self.is_admin():
                self.send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == AGENT_PREFIX + "status":
            if self.is_admin():
                try:
                    vm = agent_status(agent_vm())
                except Exception as exc:
                    self.send_json(BACKEND_FAILED, {"ok": False, "message": describe(exc)})
                    return
                self.send_json(200, {
                    "ok": True,
                    "name": vm.get("name"),
                    "status": vm.get("status"),
                    "lock": vm.get("lock", ""),
                    "uptime": vm.get("uptime", 0),
                })
        else:
            self.send_json(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:
        action = self.path.removeprefix(AGENT_PREFIX)
        if not self.path.startswith(AGENT_PREFIX) or action not in ACTIONS:
            self.send_json(404, {"ok": False, "message": "not found"})
            return
        if not self.is_admin():
            return
        # A cross-site page can neither send this Origin nor the custom header
        # without a CORS preflight, which this app never answers.
        if (self.headers.get("Origin") != CONFIG["origin"]
                or self.headers.get("X-Homelab-Action") != action):
            self.send_json(403, {"ok": False, "message": "cross-origin request refused"})
            return
        user = self.headers.get("X-authentik-username", "?")
        command, params = ACTIONS[action]
        try:
            vm = agent_vm()
            pve("POST", f"/nodes/{vm['node']}/qemu/{vm['vmid']}/status/{command}", params)
        except Exception as exc:
            message = describe(exc)
            print(f"agent VM {action} by {user} failed: {message}", flush=True)
            self.send_json(BACKEND_FAILED, {"ok": False, "message": message})
            return
        print(f"agent VM {action} by {user}", flush=True)
        self.send_json(200, {"ok": True})

    def log_message(self, format: str, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
