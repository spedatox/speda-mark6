"""
SPEDA Sandbox — isolated command-execution service.

A minimal HTTP exec server that runs inside its OWN container, separate from the
API. SPEDA's run_command skill posts commands here; this runs them in a
persistent /workspace directory and returns the output.

Security model:
  - Runs in an isolated container (no host mounts except the workspace volume,
    resource-limited, reachable only on the internal Docker network).
  - It holds NO secrets — the API's .env, database, and keys are not present here.
  - Every command runs with a hard timeout so nothing hangs forever.
  - State (files, installed packages in /workspace) persists across commands so
    SPEDA can work like it's using a real computer.

This is deliberately tiny and dependency-light (stdlib only) so the sandbox image
stays small and fast to build.
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = os.environ.get("SANDBOX_WORKSPACE", "/workspace")
MAX_TIMEOUT = int(os.environ.get("SANDBOX_MAX_TIMEOUT", "120"))
MAX_OUTPUT = 100_000  # cap returned bytes so a runaway command can't flood the API


def _run(command: str, timeout: int, workdir: str) -> dict:
    os.makedirs(workdir, exist_ok=True)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=min(max(timeout, 1), MAX_TIMEOUT),
        )
        return {
            "stdout": proc.stdout[:MAX_OUTPUT],
            "stderr": proc.stderr[:MAX_OUTPUT],
            "exit_code": proc.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": (e.stdout or "")[:MAX_OUTPUT] if isinstance(e.stdout, str) else "",
            "stderr": f"Command timed out after {timeout}s.",
            "exit_code": 124,
            "timed_out": True,
        }
    except Exception as e:  # noqa: BLE001
        return {"stdout": "", "stderr": f"Execution error: {e}", "exit_code": 1, "timed_out": False}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "workspace": WORKSPACE})
        elif self.path.startswith("/file?"):
            self._serve_file()
        else:
            self._send(404, {"error": "not found"})

    def _serve_file(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        rel = (q.get("path", [""])[0]).lstrip("/")
        target = os.path.normpath(os.path.join(WORKSPACE, rel))
        if not target.startswith(WORKSPACE) or not os.path.isfile(target):
            self._send(404, {"error": "file not found in workspace"})
            return
        with open(target, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path != "/exec":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            self._send(400, {"error": "invalid JSON body"})
            return

        command = (data.get("command") or "").strip()
        if not command:
            self._send(400, {"error": "missing 'command'"})
            return
        timeout = int(data.get("timeout", 30))
        # Optional subdir under the workspace (kept inside /workspace)
        sub = (data.get("workdir") or "").lstrip("/")
        workdir = os.path.normpath(os.path.join(WORKSPACE, sub))
        if not workdir.startswith(WORKSPACE):
            workdir = WORKSPACE

        self._send(200, _run(command, timeout, workdir))

    def log_message(self, *args):  # silence default stderr logging
        return


if __name__ == "__main__":
    port = int(os.environ.get("SANDBOX_PORT", "9000"))
    os.makedirs(WORKSPACE, exist_ok=True)
    print(f"SPEDA sandbox exec server on :{port}, workspace={WORKSPACE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
