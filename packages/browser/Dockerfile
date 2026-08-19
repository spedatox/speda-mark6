# SPEDA Browser — Playwright in its own container.
#
# The official Playwright image, pinned. The pin matters twice: it carries the
# matching Chromium build (a floating tag would eventually pair a browser with a
# driver that does not speak to it), and the CVE-2025-9611 rule in CLAUDE.md
# means the version of anything Playwright-shaped on this box has to be a
# deliberate, greppable decision rather than "whatever latest was that morning".
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# Chromium and the OS deps are already in the image — the `playwright` pip
# package itself is not. The base image installs it only into a throwaway venv
# to fetch the browser binaries, then deletes that venv (see its build history),
# so the app's own import needs its own install, pinned to match the browser
# build the tag carries.
RUN pip install --no-cache-dir --break-system-packages \
    "playwright==1.62.0" fastapi "uvicorn[standard]" pillow pytesseract httpx

# tesseract-ocr backs solve_arithmetic_captcha() in server.py — some portals
# (OSTİM's OBS among them) gate login behind a "what's N + M" image captcha.
# It stays a non-fatal import there if this ever goes missing, so a broken OCR
# path degrades to "captcha unreadable" instead of crash-looping the container
# the way the missing `playwright` package once did. httpx backs the primary
# solver, solve_captcha_with_vision() — see its docstring in server.py.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

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
