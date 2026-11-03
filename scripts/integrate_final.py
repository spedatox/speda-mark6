"""Reproducible, non-overwriting source intake and parity inventory.

Only tracked reference files are eligible. Backend authority is never copied by
this script. Run from any directory; final is read-only throughout.
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "final"

# Independent domain slices. Engine, memory and execution owners are excluded.
BACKEND = {
    "core/clock.py", "core/files.py", "news/__init__.py", "news/dedup.py", "news/collector.py", "news/feeds.py",
    "mcp/google_rest.py", "mcp/microsoft_rest.py",
    *{f"{kind}/{name}.py" for kind, names in {
        "models": ("academic", "route", "place", "health_sample", "news_item", "news_watch", "news_quota"),
        "schemas": ("academic", "health", "web"),
        "services": ("academic", "fcm", "aircraft", "routes", "places", "chat_history", "attachments", "health", "browser", "web_watch", "transit", "hisar_workspaces", "google_auth"),
        "routers": ("academic", "aircraft", "navigation", "files", "browser", "web_watch", "news", "hisar"),
        "skills": ("attendance", "schedule", "aircraft", "navigation", "weather", "pharmacies", "osint", "save_file", "read_skill", "health_data", "browser", "news", "documents", "system", "notifications", "hisar", "transit"),
    }.items() for name in names},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=SOURCE).decode().split("\0")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE).decode().strip()
    manifest_path = ROOT / "docs/integration/source-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"source_commit": commit, "files": {}}
    if manifest["source_commit"] != commit:
        raise RuntimeError("Reference revision changed; review before importing more source")
    prefixes = tuple(f"packages/{name}/" for name in (
        "heartbreaker", "striker", "speda-go", "browser", "sandbox", "playwright-mcp", "solus"))
    for relative in tracked:
        backend = (relative.removeprefix("packages/igor/app/") in BACKEND or relative.startswith("packages/igor/app/skills/skill_docs/")) and relative.startswith("packages/igor/app/")
        if not relative or not (backend or relative.startswith(prefixes) or relative in {"package.json", "package-lock.json"}):
            continue
        # Never intake credentials or local signing material, even if tracked.
        path = Path(relative)
        if path.suffix.lower() in {".jks", ".keystore", ".p12", ".pem"} or path.name == ".env":
            continue
        data = (SOURCE / relative).read_bytes()
        target_relative = relative.removeprefix("packages/igor/") if backend else relative
        target = ROOT / target_relative
        previous = manifest["files"].get(target_relative)
        if previous and previous["mode"] == "adapted":
            if previous["sha256"] != digest(data):
                raise RuntimeError(f"Adapted source changed: {relative}")
            continue
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError(f"Refusing to overwrite changed file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        manifest["files"][target_relative] = {"source": relative, "sha256": digest(data), "mode": "unchanged"}
    routes, skills, modules = [], [], []
    for relative in tracked:
        if not relative.startswith("packages/igor/app/") or not relative.endswith(".py"):
            continue
        text = (SOURCE / relative).read_text(encoding="utf-8-sig")
        tree = ast.parse(text)
        short = relative.removeprefix("packages/igor/")
        modules.append({"source": relative, "target": short, "sha256": digest((SOURCE / relative).read_bytes())})
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in {"get", "post", "put", "patch", "delete", "websocket"}:
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            routes.append({"source": relative, "handler": node.name, "method": dec.func.attr.upper(), "path": dec.args[0].value, "status": "pending"})
            if "/skills/" in relative and isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "name" for t in item.targets) and isinstance(item.value, ast.Constant):
                        skills.append({"source": relative, "class": node.name, "name": item.value.value, "status": "pending"})
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    inventory = ROOT / "docs/integration/capabilities.json"
    if not inventory.exists():
        inventory.write_text(json.dumps({"source_commit": commit, "routes": routes, "skills": skills, "modules": modules}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"preserved_files": len(manifest["files"]), "original_routes": len(routes), "original_skills": len(skills), "backend_modules": len(modules)}))


if __name__ == "__main__":
    main()
