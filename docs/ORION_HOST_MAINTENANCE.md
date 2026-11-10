# Orion host maintenance

Orion's System Ops tool runs real host commands from the owner's direct Orion chat.
The host broker accepts requests over a private Unix socket. A separate Linux
account runs shell and file operations in bounded systemd units. Its writable
maintenance workspace is /var/lib/speda-orion-ops/workspace. The broker handles
named SPEDA service logs and restarts with fixed commands. An app restart waits
for the normal backend admission gate and confirms a different ready instance.
Use the returned operation ID to check queued or uncertain results.

Install scripts/orion_host_ops.py as /usr/local/libexec/speda-orion-host-ops and
scripts/orion-host-ops.service as a systemd system unit. Create the
speda-orion-ops system user and group with a nologin shell and no supplemental
groups. Give it ownership of its workspace with mode 0700. Create
/opt/speda-operations/orion-host-ops/socket as root with mode 0700 and mount
that directory at /run/speda-orion-ops in the SPEDA backend as read-only.
The broker creates the socket with mode 0600 and accepts root peers.
The production application container uses UID 0.

The broker retains command receipts in its root-owned directory. To revoke
host execution, stop and disable orion-host-ops.service. Keep its receipt and
workspace directories for review. Remove the socket bind mount during the next
normal deployment if retiring the integration.
