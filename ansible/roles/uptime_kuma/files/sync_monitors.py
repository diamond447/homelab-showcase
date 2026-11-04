#!/usr/bin/env python3
"""Sync desired Uptime Kuma monitors idempotently via Socket.IO API.

Add-only automation: never modifies or deletes existing monitors, notifications,
or status pages. Skips any monitor already covered by an existing one.
"""

import json
import os
import sys
import time
import urllib.parse
import socketio


def normalize_url(url: str) -> str:
    """Normalize URL by comparing scheme, host, and path without trailing slash."""
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def normalize_str(s: str) -> str:
    return s.strip().lower() if s else ""


def monitor_matches(desired: dict, existing: dict) -> bool:
    """Check if an existing monitor covers the desired monitor.

    Matches if:
    - Same name, OR
    - Same type and:
      - For http/keyword: same normalized URL (scheme+host+path, ignore trailing slash)
      - For ping/port: same hostname (and same port for port type if specified)
      - For dns: same hostname and same resolver server
    """
    d_name = desired.get("name", "").strip()
    e_name = existing.get("name", "").strip()
    if d_name and e_name and d_name == e_name:
        return True

    d_type = desired.get("type", "").strip().lower()
    e_type = existing.get("type", "").strip().lower()
    if d_type != e_type:
        return False

    if d_type in ("http", "keyword"):
        d_url = normalize_url(desired.get("url", ""))
        e_url = normalize_url(existing.get("url", ""))
        if d_url and e_url and d_url == e_url:
            return True

    elif d_type in ("ping", "port"):
        d_host = normalize_str(str(desired.get("hostname", "")))
        e_host = normalize_str(str(existing.get("hostname", "")))
        if d_host and e_host and d_host == e_host:
            if d_type == "port":
                d_port = desired.get("port")
                e_port = existing.get("port")
                if d_port is not None and e_port is not None:
                    return int(d_port) == int(e_port)
            return True

    elif d_type == "dns":
        d_host = normalize_str(str(desired.get("hostname", "")))
        e_host = normalize_str(str(existing.get("hostname", "")))
        d_res = normalize_str(str(desired.get("dns_resolve_server", "")))
        e_res = normalize_str(str(existing.get("dns_resolve_server", "")))
        if d_host and e_host and d_host == e_host and d_res == e_res:
            return True

    return False


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: sync_monitors.py <path-to-monitors.json>\n")
        sys.exit(1)

    json_path = sys.argv[1]
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            desired_monitors = json.load(f)
    except Exception as e:
        sys.stderr.write(f"Error reading monitors JSON file '{json_path}': {e}\n")
        sys.exit(1)

    if not isinstance(desired_monitors, list):
        sys.stderr.write("Error: Expected a JSON array of monitor definitions\n")
        sys.exit(1)

    url = os.environ.get("UPTIME_KUMA_URL", "http://127.0.0.1:3001").rstrip("/")
    username = os.environ.get("UPTIME_KUMA_USERNAME")
    password = os.environ.get("UPTIME_KUMA_PASSWORD")

    if not username or not password:
        sys.stderr.write("Error: UPTIME_KUMA_USERNAME and UPTIME_KUMA_PASSWORD environment variables are required\n")
        sys.exit(1)

    sio = socketio.Client(reconnection=False)

    existing_monitors = None
    notification_list = None

    @sio.on("monitorList")
    def on_monitor_list(data):
        nonlocal existing_monitors
        existing_monitors = data if isinstance(data, dict) else {}

    @sio.on("notificationList")
    def on_notification_list(data):
        nonlocal notification_list
        notification_list = data if isinstance(data, list) else []

    # Connect with retry
    connected = False
    last_err = None
    for attempt in range(15):
        try:
            sio.connect(url, wait_timeout=10, transports=["websocket", "polling"])
            connected = True
            break
        except Exception as e:
            last_err = e
            time.sleep(1)

    if not connected:
        sys.stderr.write(f"Error: Could not connect to Uptime Kuma at {url}: {last_err}\n")
        sys.exit(1)

    sio.sleep(0.5)

    # Login without leaking password in tracebacks
    try:
        login_res = sio.call("login", {"username": username, "password": password}, timeout=20)
    except Exception as e:
        sio.disconnect()
        sys.stderr.write(f"Error: Failed during login request: {e}\n")
        sys.exit(1)

    if not isinstance(login_res, dict) or not login_res.get("ok"):
        sio.disconnect()
        msg = login_res.get("msg") if isinstance(login_res, dict) else "Login failed"
        sys.stderr.write(f"Error: Uptime Kuma login failed for user '{username}': {msg}\n")
        sys.exit(1)

    # Wait for monitorList and notificationList
    deadline = time.time() + 5.0
    while (existing_monitors is None or notification_list is None) and time.time() < deadline:
        sio.sleep(0.1)

    if existing_monitors is None:
        try:
            sio.call("getMonitorList", timeout=5)
            sio.sleep(0.5)
        except Exception:
            pass
        if existing_monitors is None:
            existing_monitors = {}

    if notification_list is None:
        notification_list = []

    # Map default notifications: isDefault is True
    default_notifications = {
        str(n["id"]): True for n in notification_list if n.get("isDefault")
    }

    added = []
    skipped = []

    for desired in desired_monitors:
        name = desired.get("name", "Unnamed")
        matching_existing = None
        for existing in existing_monitors.values():
            if monitor_matches(desired, existing):
                matching_existing = existing
                break

        if matching_existing:
            skipped.append({
                "name": name,
                "reason": f"covered by existing monitor '{matching_existing.get('name')}' (id: {matching_existing.get('id')})"
            })
            continue

        # Prepare payload with Kuma required defaults
        payload = {
            "interval": 60,
            "retryInterval": 60,
            "maxretries": 2,
            "accepted_statuscodes": ["200-299"],
            "conditions": [],
            "kafkaProducerBrokers": [],
            "kafkaProducerSaslOptions": {"mechanism": "None"},
            "rabbitmqNodes": [],
            "active": True,
        }
        payload.update(desired)

        # Ensure retryInterval defaults to interval if not explicitly set
        if "retryInterval" not in desired and "interval" in desired:
            payload["retryInterval"] = desired["interval"]

        # Ensure accepted_statuscodes is always list of strings
        if "accepted_statuscodes" in payload:
            payload["accepted_statuscodes"] = [str(c) for c in payload["accepted_statuscodes"]]
        else:
            payload["accepted_statuscodes"] = ["200-299"]

        # Attach default notifications
        payload["notificationIDList"] = default_notifications

        try:
            res = sio.call("add", payload, timeout=10)
        except Exception as e:
            sio.disconnect()
            sys.stderr.write(f"Error adding monitor '{name}': {e}\n")
            sys.exit(1)

        if not isinstance(res, dict) or not res.get("ok"):
            sio.disconnect()
            msg = res.get("msg") if isinstance(res, dict) else "unknown error"
            sys.stderr.write(f"Error adding monitor '{name}': {msg}\n")
            sys.exit(1)

        added.append(name)
        new_id = res.get("monitorID", len(existing_monitors) + 1)
        existing_monitors[new_id] = {**payload, "id": new_id}

    sio.disconnect()

    # Report results
    print(f"Added {len(added)} monitors:")
    for item in added:
        print(f"  + {item}")
    print(f"Skipped {len(skipped)} monitors:")
    for item in skipped:
        print(f"  - {item['name']}: {item['reason']}")

    changed = len(added) > 0
    print(f"Changed: {'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
