# agent01: the sandboxed AI agent

A Debian 13 VM where Claude Code works unattended, with permission prompts off. The agent
user has passwordless sudo and Docker inside the VM. The VM itself is the sandbox: the
isolation is enforced outside it, by OpenWrt and the egress proxy, so nothing the agent
does inside can loosen it.

## Network

- The VM is on `vmbr2` (`10.0.20.0/24`), a bridge with no physical port. OpenWrt has a
  NIC on it and is the only gateway.
- The `agents` zone has no forwarding to the LAN. The router itself answers only NTP
  and ping.
- DNS and TCP 80/443 leaving the zone are redirected to **egress01**. Everything else
  is rejected and logged.
- egress01's dnsmasq answers every name with the sinkhole address `198.18.0.1` and
  forwards nothing upstream, so DNS cannot carry data out.
- NGINX on egress01 decides by host name: the TLS server name (SNI) for HTTPS, the
  `Host` header for HTTP. It connects to that name itself, never to the IP the client
  asked for, so one allowlist entry also covers the site's CDN. HTTPS without SNI and
  every name not on the allowlist is refused.
- monitor01 may scrape node_exporter in the VM; Ansible reaches it through OpenWrt as a
  jump host.

### No shortcut through the Proxmox host

A bridge without an address would still give the host an IPv6 link-local address and
make it answer ARP for its LAN address, so the VM could reach the Proxmox UI without
passing OpenWrt. A script hooked into the bridge's `post-up` closes that:

- IPv6 is disabled on `vmbr2`, so there is no link-local address,
- `arp_ignore=8` and `arp_announce=2`, so the host never answers ARP there,
- `INPUT` from `vmbr2` is dropped for IPv4 and IPv6,
- `FORWARD` from `vmbr2` to any other interface is dropped, even with `ip_forward` on.

The Proxmox firewall must stay off on `vmbr2` NICs, because it would put the NIC behind
its own bridge and around these rules. The role fails if it finds one.

## agentnet

agentnet shows what the agent tried to reach and toggles the allowlist. It has a CLI on
egress01 and a web UI behind Authentik for admins; the web UI reaches egress01 over SSH
with a key whose forced command permits only list, allow and deny.

For every host name it shows the allowlist status, how many connections were allowed or
denied and why the last decision was made: not on the allowlist, destination unreachable,
not TLS, or TLS without a host name. A row opens the recent connections with client,
bytes, duration and request line.

The live list on egress01 is the source of truth and every Ansible run saves it back to
the repository. Entries that must win over manual toggles are enforced on every run, and
the agentnet domain itself can never be allowed.

**Residual risk:** allowed hosts that store arbitrary content (package registries, object
storage, GitHub) can still carry data. A name-based proxy cannot filter by path, so the VM
is treated as disposable and its tokens are kept narrow, such as a fine-grained GitHub
token for the agent's repositories only.

## Hibernate and wake

The agent01 tile on the admin Homepage hibernates the VM instead of shutting it down.
Proxmox writes the VM's memory to disk and stops it, so the host gets the RAM back;
the wake button resumes it exactly where it was, with tmux, Claude Code and running jobs. The
API token behind the buttons may only power-manage this one VM and allocate its state
volume.

## Access

There is deliberately no Tailscale or other VPN in the VM: it would tunnel past the
egress proxy. Claude Code is driven through Remote Control from the phone, and the
fallback is SSH through OpenWrt.

Ansible merges only its own keys (permission mode, status line) into Claude Code's
`settings.json` and keeps everything else, such as plugins and extra permission rules.
