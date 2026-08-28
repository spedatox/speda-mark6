# The official Microsoft @playwright/mcp server, in its own container.
#
# This is the SECOND, SEPARATE browser path — see docs/BROWSER.md. It gives
# full upstream tool parity (click, drag, evaluate, tabs, dialogs, uploads,
# network/console inspection, resize, snapshot…) for the OPEN PUBLIC WEB ONLY.
# packages/browser (the other container) stays the ONLY path for anything
# touching one of the owner's saved logins — a login through this server
# means the MODEL types the password, which is exactly what that sidecar
# exists to prevent. Never point this container at a login form.
#
# Pinned like packages/browser/Dockerfile: CVE-2025-9611 (DNS rebinding via
# missing Origin/Host header validation) is fixed in @playwright/mcp 0.0.40+.
# This pins well above that, deliberately — bump it by hand, never `@latest`.
FROM node:22-bookworm-slim

# A shared, explicit browser path rather than whatever HOME the install runs
# under — the install below runs as root (chromium's OS deps need apt), but
# the server itself runs as an unprivileged user, and the two must agree on
# where the browser binaries live.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN npm install -g @playwright/mcp@0.0.79 \
    # Installs the exact Chromium build @playwright/mcp's own pinned
    # playwright-core dependency expects. A pre-baked "Playwright base image"
    # would have to guess that version, and a mismatch between driver and
    # browser is a broken automated-detection signature at best, a launch
    # failure at worst — installing here, from the package that was just
    # pinned above, is the only way the two are guaranteed to agree.
    && npx --yes playwright install --with-deps chromium \
    && groupadd -r pwuser && useradd -r -g pwuser -m -d /home/pwuser pwuser \
    && chown -R pwuser:pwuser /ms-playwright

# This container renders pages the owner did not write and holds no cookies
# worth stealing (--isolated below keeps every profile in memory only) — it
# still does not get to run as root.
USER pwuser
EXPOSE 8931

# --isolated: every session's browser profile lives in memory only, never
# written to disk. This container must never accumulate cookies for
# anything — a flow that needs to stay signed in belongs on packages/browser.
# --host 0.0.0.0: reachable from the app container over the internal compose
# network. Never published to the host — see docker-compose.yml (`expose`,
# not `ports`) and CLAUDE.md's CVE-2025-9611 rule.
CMD ["npx", "@playwright/mcp", "--headless", "--isolated", "--host", "0.0.0.0", "--port", "8931"]
