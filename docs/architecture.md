# Architecture

## Hosts

| Host | Type | Address | Purpose |
|---|---|---|---|
| pve01 | Proxmox VE host | 10.0.10.254 | Hypervisor, Proxmox Backup Server |
| openwrt | VM | 10.0.10.1 (lan), 10.0.20.1 (agents) | Gateway and firewall |
| edge01 | LXC | 10.0.10.100 | NGINX reverse proxy, Cloudflare Tunnel |
| auth01 | LXC | 10.0.10.102 | Authentik |
| apps01 | LXC | 10.0.10.103 | Homepage and on-demand apps |
| monitor01 | LXC | 10.0.10.104 | Prometheus, Grafana, Alertmanager, Uptime Kuma, ntfy |
| egress01 | LXC | 10.0.10.105 | Egress proxy and DNS sinkhole for the agents zone |
| health01 | LXC | 10.0.10.106 | Health Coach and Fit |
| myapps01 | LXC | 10.0.10.107 | Place for self-developed apps and little projects |
| nextcloud01 | LXC | 10.0.10.108 | Nextcloud |
| agent01 | VM | 10.0.20.110 | Claude Code agent, see [agent-vm.md](agent-vm.md) |

| Bridge | Subnet | Zone | |
|---|---|---|---|
| vmbr1 | 10.0.10.0/24 | lan | Homelab services |
| vmbr2 | 10.0.20.0/24 | agents | No physical port, no route to lan, HTTP(S) only through egress01 |

Every guest is created through the Proxmox API (`provision.yml`) and configured by
roles (`configure.yml`). Containers are unprivileged Debian 13 with Docker where needed;
images are pinned by digest.

## How a request gets in

There are no port forwards. `cloudflared` on edge01 dials out to Cloudflare, and every
public host name goes through that tunnel to NGINX on edge01. NGINX routes by host name,
asks Authentik whether the request may pass (forward auth) and proxies it to the backend.

Each backend publishes its ports on its LAN address only, and an iptables chain bound to
`docker.service` (`HEALTH-FW`, `NEXTCLOUD-FW`, `MONITORING-FW`) lets in edge01 and the
monitoring host, nobody else on the LAN. The chains are rebuilt atomically on every run
and whenever Docker restarts, so removed rules never linger.

### One service list

`edge_services` in `inventory/group_vars/all.yml` is the single list of web services.
From it Ansible renders the NGINX server blocks, the Authentik providers and applications
in the blueprint, and the Uptime Kuma monitors. Each entry picks an access mode:

| `access` | Meaning |
|---|---|
| `users` | Forward auth through one domain-wide Authentik provider; any member of `users` |
| `admins` | Forward auth through a dedicated provider bound to `homelab admins` |
| `none` | Plain proxy, for services with their own login (OIDC) or public pages; single paths can still be put behind admins (`protected_paths`) |

Options per service cover streaming (`buffering: false`), long timeouts, upload limits,
HSTS and paths that skip forward auth (`public_paths`, e.g. PWA icons for the iOS home
screen).

## Sign-in

Authentik is the only identity provider; people sign in with Google. A new account gets
no group, so it can open nothing until it is added to `users` or `homelab admins`
(`homelab admins` inherits `users`). Both default authorization flows are bound to
`users`, which closes every application to accounts without a group, even when the
application has no binding of its own.

All Authentik configuration is a blueprint rendered by Ansible and reapplied on every
deploy; nothing is clicked together in the UI.

| Service | How it is protected |
|---|---|
| Actual Budget, BentoPDF, OmniTools, CyberChef, openGym | Forward auth, `users` |
| agentnet, Prometheus, Alertmanager, Healthstats | Forward auth, `homelab admins` |
| Grafana | Native OIDC, admins only; local login form disabled |
| Actual Budget | Additionally native OIDC: one Actual user per person |
| Nextcloud | Native OIDC (`user_oidc`), so desktop and mobile sync clients work |
| Uptime Kuma | Public status page; admin UI and its Socket.IO channel behind admins |
| Health Coach MCP, Fit MCP | OAuth 2.0 resource servers validating Authentik tokens, for Claude, ChatGPT and Gemini connectors |
| ntfy | Own login, deny-all by default |

## On-demand apps (apps01)

Rarely used apps scale to zero. Edge forward auth runs first, then a Proxy-Wasm NGINX on
apps01 asks [Sablier](https://github.com/sablierapp/sablier) to start the app's
container and shows a waiting page until its health check passes. After 15 idle minutes
Sablier stops it again. Ansible only creates the app containers, it never starts them.

Sablier reaches Docker through a socket proxy that allows only the container calls it
needs; the raw socket is mounted in the proxy alone. The proxy, Sablier and the apps sit
on separate internal networks, and only the Wasm NGINX publishes a port (to edge01).

| Container | Memory limit |
|---|---|
| Actual Budget, BentoPDF, OmniTools, CyberChef (when running) | 768 / 768 / 384 / 512 MB |
| Wasm NGINX, Sablier, socket proxy (always on) | 512 / 256 / 128 MB |

## Health Coach and Fit (health01)

health01 runs my [health-coach](https://github.com/diamond447/health-coach) project:
a PostgreSQL database of wearable data, the ingest worker, an MCP server for AI coaching
and the Healthstats phone PWA. Next to it, Fit runs the
[openGym](https://github.com/DuarteSantos8/openGym) gym tracker for friends and family
with its own MCP server, which lets each person's assistant plan sessions in their own
account. Fit has no access to the health database. Grafana reads the health database
through a read-only user.

## Nextcloud (nextcloud01)

The official Nextcloud image with PostgreSQL, Redis for caching and file locking, and a
cron container. User files live on a separate Proxmox mount point, and a nightly dump
writes the database, its roles and `config.php` next to them, so that one volume is
enough to rebuild the instance. A failed dump sends a push notification.

## Monitoring and alerts (monitor01)

Prometheus scrapes node_exporter on every guest, pve_exporter and smartctl_exporter.
Alertmanager pushes to the self-hosted ntfy.

| Alert | Fires when |
|---|---|
| `TargetDown` | a scrape target is down for 5 minutes |
| `FilesystemAlmostFull` | less than 15% disk space is free |
| `MemoryLow` | less than 10% memory is available |
| `ThinPoolAlmostFull` / `ThinPoolFull` | the LVM-thin pool is over 80% / 90% |
| `GuestDown` | a guest set to start on boot is not running |
| `SmartFailed` / `NvmeCriticalWarning` | a disk reports a SMART failure or an NVMe warning |

**ntfy** carries every notification: alerts, failed backups and the health coach. It is
deny-all with no sign-up; accounts, ACLs and tokens are declared in its config, and each
publisher may only write to its own topic. On iOS, ntfy.sh only receives a poll request
with the message ID, never the message, and the phone fetches the content from the homelab.

**Uptime Kuma** checks public URLs, internal endpoints, the DNS sinkhole and one auth
gate per protected service. The gates expect a redirect to Authentik, so they verify
forward auth without waking on-demand apps. Monitors are synced from Ansible add-only:
monitors created in the UI are never changed or deleted.

**Grafana** has the Node Exporter dashboards and the health dashboards, provisioned from
the repository.
