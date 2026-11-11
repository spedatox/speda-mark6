# Orion host maintenance

Orion administers the live Mark VI host. In owner-started conversations, System
Ops runs Bash commands and file operations as root, including during Orion's
collaboration turns. Commands start in `/root` and run
until they finish or reach an explicitly requested timeout. For a running
command, use its operation ID with `operation_status`. Results contain up to
8,000 bytes per stream. For longer output, redirect it to a host file and inspect
the relevant sections.

Other personas use `collaborate` to request server evidence from Orion in an
owner-started conversation. Orion can check service health, incidents, recent
service logs, load, storage, listening sockets, failed systemd units, the host
timezone and recent successful SSH root password logins during these exchanges.
An app restart requested through `restart_service` waits for
the backend admission gate and verifies a different ready instance.

Install `scripts/orion_host_ops.py` as `/usr/local/libexec/speda-orion-host-ops`
and `scripts/orion-host-ops.service` as a systemd system unit. Create
`/opt/speda-operations/orion-host-ops/socket` as root with mode 0700 and mount
that directory at `/run/speda-orion-ops` in the SPEDA backend as read-only.
The broker creates the socket with mode 0600 and accepts root peers.
The production application container uses UID 0.

The broker retains command receipts in its root-owned directory. To revoke
host execution, stop and disable orion-host-ops.service. Keep its receipt and
workspace directories for review. Remove the socket bind mount during the next
normal deployment if retiring the integration.
