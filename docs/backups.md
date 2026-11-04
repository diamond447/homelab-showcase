# Backups

Proxmox Backup Server runs on pve01 next to Proxmox VE. Every night Proxmox backs up the
guests that hold data, PBS keeps the newest 2 backups of each, and the newest set can be
downloaded to a laptop as the off-machine copy.

| What | When | Where |
|---|---|---|
| Health database and Fit data dumps | 03:15 | inside health01 |
| Nextcloud database dump | 03:30 | inside nextcloud01, next to the user files |
| Snapshot backup of auth01, apps01, monitor01, health01, myapps01, nextcloud01 | 04:00 | PBS datastore |
| Prune: keep the newest 2 per guest | 05:30 | PBS |
| Garbage collection | 06:00 | PBS |
| Download of the newest set | on demand | laptop |

edge01 and egress01 hold nothing that Ansible does not recreate, and agent01 is
disposable, so they are not backed up.

## What goes in

Docker images and layers are left out of the backups: Ansible pulls or builds them again
from pinned versions. Docker volumes and bind mounts are included, so application data
always lives there and never in a container's writable layer. Prometheus history is left
out as well. Together with PBS deduplication, a nightly backup only stores the chunks that
changed.

The in-guest database dumps exist next to the snapshots on purpose: a single database can
be restored without rolling back the whole container.

## Least privilege

| PBS user | Rights | Used by |
|---|---|---|
| `pve@pbs` | DatastoreBackup | Proxmox VE; the token is created by Ansible and its secret exists only in PVE's storage config |
| `laptop@pbs` | DatastoreReader (read and restore, no delete) | the laptop download script |

A stolen laptop token can read backups but cannot delete them.

## Laptop copy

`scripts/pbs-download-latest` downloads the newest backup of every container into a new
dated directory and removes the older ones only after the download has finished, so there
is always one complete copy. It runs as root so files keep their owners; the copy holds
secrets and belongs on an encrypted disk.

## Restore

A whole container is restored from PBS and then configured once with Ansible, which pulls
its images and recreates the containers on top of the restored volumes. Single files can
be taken from a snapshot in the PBS or PVE web UI.
