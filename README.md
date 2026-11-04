<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
  <img alt="homelab project: IaC Proxmox server" src="assets/banner-light.svg" width="100%">
</picture>

<p align="center">
  <a href="https://github.com/diamond447/homelab-showcase/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/diamond447/homelab-showcase/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Proxmox VE 9" src="https://img.shields.io/badge/Proxmox_VE-9-E57000?logo=proxmox&logoColor=white">
  <img alt="Ansible" src="https://img.shields.io/badge/Ansible-24_roles-EE0000?logo=ansible&logoColor=white">
  <img alt="Debian 13" src="https://img.shields.io/badge/Debian-13-A81D33?logo=debian&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white">
  <img alt="OpenWrt" src="https://img.shields.io/badge/OpenWrt-firewall-00B5E2?logo=openwrt&logoColor=white">
  <br>
  <img alt="Cloudflare Tunnel" src="https://img.shields.io/badge/Cloudflare-Tunnel-F38020?logo=cloudflare&logoColor=white">
  <img alt="Authentik" src="https://img.shields.io/badge/Authentik-SSO-FD4B2D?logo=authentik&logoColor=white">
  <img alt="Prometheus" src="https://img.shields.io/badge/Prometheus-alerts-E6522C?logo=prometheus&logoColor=white">
  <img alt="Grafana" src="https://img.shields.io/badge/Grafana-dashboards-F46800?logo=grafana&logoColor=white">
  <img alt="Nextcloud" src="https://img.shields.io/badge/Nextcloud-files-0082C9?logo=nextcloud&logoColor=white">
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-sandboxed-D97757?logo=claude&logoColor=white">
</p>

<p align="center">
  <b>11</b> hosts &nbsp;·&nbsp; <b>24</b> Ansible roles &nbsp;·&nbsp; <b>16</b> web services &nbsp;·&nbsp; <b>0</b> open ports &nbsp;·&nbsp; <b>1</b> command to rebuild
</p>

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/architecture-dark.svg">
  <img alt="Architecture: Cloudflare Tunnel into an NGINX edge, Authentik SSO, app, self-developed app, health, Nextcloud and monitoring containers on a LAN zone, an AI agent VM on an isolated zone that can only reach allowlisted sites, and nightly backups to Proxmox Backup Server" src="assets/architecture-light.svg" width="100%">
</picture>

| | |
|---|---|
| 🧱 **Everything as code** | `ansible-playbook site.yml` creates the containers and VMs through the Proxmox API and configures them. Reruns are idempotent. |
| 🔒 **No open ports** | The internet comes in only through an outbound Cloudflare Tunnel. Backends accept connections from the edge proxy only. |
| 🪪 **One login** | Authentik (Google sign-in) guards every service, by forward auth at the edge or by OIDC (Grafana, Nextcloud, Actual). One service list drives NGINX, Authentik and the uptime monitors. |
| 💤 **Scale to zero** | Rarely used apps sleep. Sablier starts them on the first request and stops them after 15 idle minutes. |
| 💾 **Backups** | Proxmox Backup Server takes deduplicated nightly snapshots and keeps the newest 2. One script downloads the newest set to a laptop. |
| 📈 **Alerts on the phone** | Prometheus, Grafana, Alertmanager and Uptime Kuma, with pushes through a self-hosted ntfy. Disks, thin pool, guests and backups are watched. |
| ❤️ **Health stack** | My [health-coach](https://github.com/diamond447/health-coach) project runs here: wearable data, an MCP server for AI coaching and a stats PWA, plus the openGym tracker for friends and family. |
| 🤖 **Sandboxed AI agent** | Claude Code works unattended in a VM that can reach only an allowlist of sites. |

## How a request gets in

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant CF as Cloudflare
    participant E as edge01 · NGINX
    participant A as auth01 · Authentik
    participant S as Service
    B->>CF: https://budget.example.com
    CF->>E: through the Tunnel (edge01 dialed out)
    E->>A: forward auth: who is this?
    A-->>B: not signed in: Google login
    A-->>E: 200 + user and groups
    E->>S: proxied request (only edge01 may connect)
    S-->>B: page
```

## The agent sandbox

```mermaid
flowchart LR
    agent["agent01<br/>Claude Code"] -->|all traffic| fw{{"OpenWrt"}}
    fw -->|DNS| dns["egress01 dnsmasq<br/>every name → 198.18.0.1"]
    fw -->|TCP 80 / 443| px["egress01 NGINX<br/>reads SNI / Host"]
    px -->|on the allowlist| net(("Internet"))
    px -.->|not allowed| deny["refused + logged"]
    fw -.->|anything else| drop["rejected + logged"]
    ui["agentnet web UI"] -.->|toggle a domain| px
```

- No route to the LAN or the Proxmox host, not even over IPv6 link-local or ARP.
- DNS answers are fake, so DNS cannot carry data out.
- The proxy connects to the host name itself, so one allowlist entry covers its CDN.

## Repository

```
ansible/
  playbooks/   provision.yml (Proxmox API + bootstrap), configure.yml (roles), site.yml (both)
  roles/       24 roles: edge, authentik, on_demand_apps, egress_proxy, prometheus, ...
  inventory/   hosts, versions and the service list
  vars/        container and VM definitions, vault.example.yml
docs/          architecture, backups and the agent sandbox
```

Read more:

- [Architecture](docs/architecture.md): hosts, the request path, sign-in, on-demand apps and monitoring
- [The agent sandbox](docs/agent-vm.md): network isolation, the egress allowlist and how to change it
- [Backups](docs/backups.md): what is backed up, least-privilege tokens and the restore steps

<sub>A sanitized copy of my private infrastructure repository: the domain, addresses, IDs and allowlist are replaced and the encrypted vault is left out (see <code>ansible/vars/vault.example.yml</code>).</sub>

<sub>MIT licensed, see <a href="LICENSE">LICENSE</a>.</sub>
