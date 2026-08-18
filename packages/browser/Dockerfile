# SPEDA Browser — Playwright in its own container.
#
# The official Playwright image, pinned. The pin matters twice: it carries the
# matching Chromium build (a floating tag would eventually pair a browser with a
# driver that does not speak to it), and the CVE-2025-9611 rule in CLAUDE.md
# means the version of anything Playwright-shaped on this box has to be a
# deliberate, greppable decision rather than "whatever latest was that morning".
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# Only the HTTP surface — Playwright and Chromium are already in the image.
RUN pip install --no-cache-dir --break-system-packages fastapi "uvicorn[standard]"

ENV BROWSER_STATE_DIR=/state \
    BROWSER_PORT=9200 \
    BROWSER_HEADLESS=1 \
    PYTHONUNBUFFERED=1

# pwuser ships with the image. This service renders pages the owner did not
# write — a research fallback goes wherever the research goes — so it does not
# get to be root, even inside its own container.
RUN mkdir -p /state && chown -R pwuser:pwuser /state
USER pwuser

COPY --chown=pwuser:pwuser server.py /srv/server.py

EXPOSE 9200
CMD ["python", "/srv/server.py"]
