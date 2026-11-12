# Mark VI security watchdog

A systemd service reads the host SSH journal, Caddy access logs for the Mark VI
site, and receive counters for the public interface. It runs independently of
Igor. The service writes bounded, structured observations to a local inbox.
Igor's Damage Control worker imports them into the Incident Journal, requests
a tool-free Orion assessment under the existing background model and spending
policy, and sends a short message through SPEDA's paired private Telegram chat.
An unavailable model still allows the factual warning to be sent. Orion can
retrieve recent entries with `system_ops` and `action=security_incidents`.

| Signal | Default threshold |
| --- | --- |
| SSH failures or invalid-user probes | 12 from one IP or 60 total in 60 seconds |
| HTTP authentication failures | 20 HTTP 401s from one IP or 100 total in 60 seconds |
| HTTP flood | 600 logged responses to one IP or 3,000 total in 60 seconds |
| Network flood | 200 Mbit/s or 50,000 packets/s for two consecutive ten-second samples |

Each signal has a 15-minute cooldown that survives restarts. Missing or capped
inputs produce a separate warning. The host queues up to 512 observations while
Igor is unavailable. Full inboxes are reported in the systemd journal. Telegram
delivery needs Igor, its paired owner chat, and an outbound path to Telegram.
Damage Control records uncertain sends for inspection.

These thresholds indicate possible abuse. Confirm impact against service
health, login successes, and provider traffic metrics. Network counters cover
traffic that reaches the host. Caddy counts completed requests and attributes
them to its immediate peer, which may be a proxy. The watchdog only observes
and reports.

## Install on the host

1. Confirm the live `/state` bind mount, Caddy's `/data` volume, the SSH
   journal unit, and the public network interface. The example below uses the
   current Mark VI container names and interface `eth0`. Keep a copy of the
   active Caddy configuration and the site source before editing.

   ```sh
   IGOR_STATE=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/state"}}{{.Source}}{{end}}{{end}}' speda-renovated-app-1)
   CADDY_DATA=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' speda-caddy-1)
   test -n "$IGOR_STATE" && test -d "$IGOR_STATE"
   test -n "$CADDY_DATA" && test -d "$CADDY_DATA"
   ip -o route show default
   ```

2. Create the dedicated account and directories. Setgid on the Caddy directory
   preserves group readability through log rotation. These modes assume Caddy
   and Igor run as root inside their existing containers. For non-root
   containers, verify the corresponding host UIDs and GIDs first.

   ```sh
   getent passwd speda-watchdog >/dev/null ||
     useradd --system --user-group --no-create-home --shell /usr/sbin/nologin speda-watchdog
   test -d "$IGOR_STATE/damage-control" || install -d -m 0700 "$IGOR_STATE/damage-control"
   install -d -o speda-watchdog -g speda-watchdog -m 0700 "$IGOR_STATE/damage-control/security-inbox"
   install -d -o root -g speda-watchdog -m 2750 "$CADDY_DATA/security-watchdog"
   ```

3. Add [the access log directive](security-access.caddy) inside the existing
   Mark VI HTTPS site. Keep its routes and other site directives. Validate and
   reload the merged Caddy configuration using the normal host procedure. Make
   one ordinary health request and confirm the log contains a timestamp, status,
   and peer IP with headers and URI removed. The logger retains at most two
   rotated files alongside the active file.

4. Release the Igor changes with Damage Control enabled and the SPEDA bot paired
   to the owner's private Telegram chat. Igor checks for the inbox at startup.
   With the inbox enabled, individual API-key rejections remain in the application
   log and the watchdog handles burst notifications. Confirm the release is healthy.

5. Install the host script and service from the same checkout. Bind only the
   two dedicated directories into the service. This gives the watchdog no
   Docker socket, application credentials, or network access.

   ```sh
   install -D -m 0644 app/security_watchdog.py /usr/local/libexec/speda-security-watchdog.py
   install -m 0644 scripts/speda-security-watchdog.service /etc/systemd/system/
   install -d /etc/systemd/system/speda-security-watchdog.service.d
   cat >/etc/systemd/system/speda-security-watchdog.service.d/site.conf <<EOF
   [Service]
   BindReadOnlyPaths=$CADDY_DATA/security-watchdog:/run/speda-security-watchdog/caddy
   BindPaths=$IGOR_STATE/damage-control/security-inbox:/run/speda-security-watchdog/inbox
   EOF
   systemd-analyze verify /etc/systemd/system/speda-security-watchdog.service
   systemctl daemon-reload
   systemctl enable --now speda-security-watchdog.service
   journalctl -u speda-security-watchdog.service -n 30 --no-pager
   ```

   The host script also accepts `--check` to verify the SSH journal, Caddy log
   and interface counters without creating incidents. Run it with the same
   permissions and bind mounts as the service. To tune thresholds, pass
   `--thresholds /etc/speda-security-watchdog/thresholds.json` through
   `Environment="WATCHDOG_OPTIONS=--thresholds /etc/speda-security-watchdog/thresholds.json"`
   in the drop-in. A JSON object can override any key in `DEFAULTS` in the
   host script. Set `WATCHDOG_INTERFACE` and `WATCHDOG_SSH_UNIT` in the
   drop-in if the verified host uses different names.

## Stop or roll back

Disable the watchdog with `systemctl disable --now speda-security-watchdog.service`.
Restore the saved Caddy configuration, validate it, and reload Caddy. Let Igor
drain the inbox and retain the Incident Journal. To restore individual
Damage Control auth notifications, move the inbox out of the Damage Control
directory after draining, then restart Igor through the managed release procedure.

Sources for the input formats are the [Caddy access log documentation](https://caddyserver.com/docs/caddyfile/directives/log),
the [journalctl manual](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html),
and the [Linux network interface statistics documentation](https://docs.kernel.org/networking/statistics.html).
