#!/usr/bin/env python3
"""
SPEDA terminal client.
Usage:
    python speda.py                  # interactive REPL
    python speda.py "your message"   # single shot
    echo "your message" | python speda.py
"""

import json
import os
import sys

import httpx

BASE_URL = os.getenv("SPEDA_URL", "http://localhost:8000")
API_KEY = os.getenv("SPEDA_API_KEY", "dev-key")
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# ANSI colours
CYAN = "\033[96m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


def chat(message: str, session_id: int | None = None) -> int | None:
    """Send a message, stream the response. Returns session_id for continuity."""
    returned_session_id: int | None = session_id

    with httpx.Client(timeout=120.0) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/chat",
            headers=HEADERS,
            json={"message": message, "session_id": session_id},
        ) as r:
            if r.status_code != 200:
                print(f"{YELLOW}Error {r.status_code}: {r.text}{RESET}", file=sys.stderr)
                return session_id

            first_chunk = True

            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                if etype == "start":
                    returned_session_id = event.get("session_id")

                elif etype == "chunk":
                    if first_chunk:
                        print(f"{CYAN}SPEDA:{RESET}", end=" ", flush=True)
                        first_chunk = False
                    print(event.get("data", ""), end="", flush=True)

                elif etype == "tool":
                    tool_name = event.get("data", {}).get("tool") if isinstance(event.get("data"), dict) else event.get("data")
                    print(f"\n{DIM}  [tool: {tool_name}]{RESET}", end="", flush=True)

                elif etype == "done":
                    print()  # newline after stream

                elif etype == "error":
                    print(f"\n{YELLOW}[error: {event.get('data')}]{RESET}")

    return returned_session_id


def repl():
    print(f"{BOLD}SPEDA Mark VI — Terminal Client{RESET}")
    print(f"{DIM}Server: {BASE_URL}  |  type 'exit' or Ctrl+C to quit  |  'new' to start a fresh session{RESET}\n")

    session_id: int | None = None

    while True:
        try:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            break
        if user_input.lower() == "new":
            session_id = None
            print(f"{DIM}New session started.{RESET}")
            continue

        session_id = chat(user_input, session_id)


if __name__ == "__main__":
    if not sys.stdin.isatty():
        # Piped input — single shot
        message = sys.stdin.read().strip()
        if message:
            chat(message)
    elif len(sys.argv) > 1:
        # Argument — single shot
        chat(" ".join(sys.argv[1:]))
    else:
        # Interactive REPL
        repl()
