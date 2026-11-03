"""Offline capability audit. Registration is never reported as behavioral parity."""
import argparse
import ast
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "final"


def normalized(path):
    return re.sub(r"\{[^}]+\}", "{}", path)


def prefixes(path):
    result = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "APIRouter":
                prefix = next((k.value.value for k in call.keywords if k.arg == "prefix"
                    and isinstance(k.value, ast.Constant)), "")
                for name in node.targets:
                    if isinstance(name, ast.Name):
                        result[name.id] = prefix
    return result


async def run(args):
    baseline = Path(args.baseline_root).resolve(strict=True) if args.baseline_root else SOURCE
    if not (baseline / 'packages/igor/app').is_dir():
        raise ValueError('Provide --baseline-root pointing to a checkout of the retained final branch')
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    identity = uuid4().hex
    os.environ.update(DATABASE_URL="sqlite+aiosqlite:///" + (scratch / f"audit-{identity}.db").as_posix(),
        SPEDA_DATA_DIR=str(scratch / identity), EXTERNAL_INTEGRATIONS_ENABLED="false",
        MEMORY_CAPTURE_ENABLED="false", PYTHONDONTWRITEBYTECODE="1", ANTHROPIC_API_KEY="not-set")
    for name in ("OPENAI", "GEMINI", "KIMI", "MOONSHOT", "ZAI", "DEEPSEEK", "NVIDIA", "OLLAMA"):
        os.environ[name + "_API_KEY"] = ""
    sys.path.insert(0, str(ROOT))
    from app.main import create_app
    from app.core.context import AgentContext
    app = create_app()
    inventory_path = ROOT / "docs/integration/capabilities.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    native = {(method, normalized(route.path)) for route in app.routes for method in
              getattr(route, "methods", {"WEBSOCKET"})}
    for route in inventory["routes"]:
        tree = ast.parse((baseline / route["source"]).read_text(encoding="utf-8-sig"))
        table = prefixes(baseline / route["source"])
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == route["handler"]:
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr.upper() == route["method"] and decorator.args
                            and isinstance(decorator.args[0], ast.Constant)):
                        receiver = decorator.func.value
                        route["path"] = table.get(receiver.id, "") + decorator.args[0].value if isinstance(receiver, ast.Name) else decorator.args[0].value
        route["status"] = "mounted_contract_unverified" if (route["method"], normalized(route["path"])) in native else "pending"
        if route["path"].startswith("/agents/ws/"):
            route.update(status="superseded", replacement="Coordinator rooms and collaboration; retired remote dispatch refuses production connections")
        if route["path"] == "/memory/sources" and route["method"] == "PUT":
            route.update(status="superseded", replacement="Shared Iacon catalogue; no per-agent canonical file writers")
        if route["path"].startswith("/memory/files"):
            route.update(status="verified_local_mocked", evidence=["tests/test_client_memory.py"])
        if route["path"] == "/hisar/dirs":
            route.update(status="verified_local_mocked", evidence=["tests/compatibility/test_hisar_skill.py",
                "tests/compatibility/test_hisar_workspace_create.py"])
        if (route['path'] in {'/connections', '/connections/mcp', '/connections/mcp/{name}'}
                or re.fullmatch(r'/connections/(google|microsoft|notion)/(login|status|disconnect)', route['path'])
                or re.fullmatch(r'/oauth/(google|microsoft|notion)/callback', route['path'])):
            route.update(status='verified_local_mocked',
                replacement='Native connection manager and browser-bound one-use OAuth service',
                evidence=['tests/test_connections.py', 'tests/test_connections_postgres.py'])
        if route['path'].startswith('/legion/') or route['path']=='/agents/legion-models':
            route.update(status='verified_local_mocked',replacement='Native Coordinator child records and Forge project projection',
                evidence=['tests/test_legion_api.py','tests/test_legion_integration.py','tests/test_legion_postgres.py','tests/test_worker_policy.py'])
    async with app.router.lifespan_context(app):
        registry = app.state.registry
        grants = {}
        for profile in app.state.profiles.roster():
            context = AgentContext(user_id=1, session_id=0, request_id="audit", triggered_by="user",
                trigger_payload={}, output_mode="respond", model="unused", system_prompt="",
                conversation_history=[], db=None, timezone="Europe/Istanbul", agent_id=profile["agent_id"],
                allowed_tools=frozenset(profile["tools"]))
            grants[profile["agent_id"]] = {t["name"] for t in registry.list_tools(context, include_deferred=True)}
        for skill in inventory["skills"]:
            skill["agents"] = [agent for agent, names in grants.items() if skill["name"] in names]
            skill["status"] = "registered_unverified" if skill["agents"] else "pending"
            if skill["name"] in {"speech_to_text", "text_to_speech"}:
                skill["status"] = "pending_stub"
            if skill["name"] == "generate_document":
                skill.update(status="verified_local_document_runtime", evidence=["tests/test_document_integration.py"])
            if skill["name"] in {"hisar", "bus_arrivals", "send_push_notification"}:
                skill.update(status="verified_local_mocked", evidence=[
                    "tests/test_notification_integration.py" if skill["name"] == "send_push_notification"
                    else "tests/test_hisar_transit_integration.py"])
            if skill['name'] in {'legion_inspect','legion_control','legion_status'}:
                skill.update(status='verified_local_mocked',evidence=['tests/test_legion_integration.py','tests/test_legion_api.py'])
            evidence={
                'speech_to_text':'tests/test_voice.py','text_to_speech':'tests/test_voice.py',
                'manage_automations':'tests/test_automations.py','reminders':'tests/test_reminders.py',
                'send_telegram_message':'tests/test_delivery.py','send_telegram_file':'tests/test_delivery.py',
                'onyx':'tests/test_remote_tools.py','run_command':'tests/test_remote_tools.py','deliver_file':'tests/test_remote_tools.py',
                'search_history':'tests/test_history_lookup.py',
            }.get(skill['name'])
            if evidence: skill.update(status='verified_local_mocked',evidence=[evidence])
            if skill['name']=='generate_daily_training_program':
                skill.update(status='verified_local_pdf_runtime',evidence=['tests/test_atomix_reports.py'])
        inventory["native_routes"] = len(native)
        inventory["registered_skill_count"] = len(registry._skills)
    inventory["audit_scope"] = "Offline registration plus named local contract tests; no blanket parity, live-provider or deployment claim"
    # A retired interface is not a missing native capability. Keep replacements
    # explicit and separate from registration or successful fixture execution.
    replacements = json.loads((ROOT / 'docs/integration/replacements.json').read_text(encoding='utf-8'))
    for route in inventory['routes']:
        mapping = replacements['routes'].get(route['method']+' '+route['path'])
        if mapping: route.update(mapping)
        if route['path'].startswith('/solus/') and route['status']=='mounted_contract_unverified':
            route['evidence']=['tests/test_solus_core.py','tests/test_solus_http.py']
    for skill in inventory['skills']:
        if skill['name'] in replacements['skills']: skill.update(replacements['skills'][skill['name']])
        if skill['name']=='solus_accounting':
            skill.update(status='verified_local_mocked',evidence=['tests/test_solus_capture.py','tests/test_solus_core.py'])
    inventory["route_status_counts"] = dict(Counter(r["status"] for r in inventory["routes"]))
    inventory["skill_status_counts"] = dict(Counter(s["status"] for s in inventory["skills"]))
    from app.services.capability_acceptance import assess,fingerprint
    from app.services.behavior_config import BehaviorConfiguration
    acceptance=json.loads((ROOT/'docs/integration/outcome-acceptance.json').read_text(encoding='utf-8'))
    inventory['product_acceptance']=assess(inventory,acceptance,root=ROOT,
        native_fingerprint=fingerprint(BehaviorConfiguration.source_hashes()),
        artifacts_root=args.acceptance_artifacts)
    output = Path(args.output).resolve() if args.output else inventory_path
    output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"routes": inventory["route_status_counts"], "skills": inventory["skill_status_counts"]}))
    if args.check_superior and not inventory['product_acceptance']['complete']:
        print('Superior Mark VI acceptance remains open: named, executed owner-workflow comparisons and release checks are required.')
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True)
    parser.add_argument('--baseline-root', help='Checkout of the retained final branch; defaults to ../final for the original workspace')
    parser.add_argument('--output', help='Write the audit to an external report instead of updating the checked-in inventory')
    parser.add_argument('--acceptance-artifacts',help='Directory of retained JUnit reports; never generated inside the checkout')
    parser.add_argument('--check-superior',action='store_true',help='Fail if any original outcome or release gate lacks accepted workflow evidence')
    asyncio.run(run(parser.parse_args()))
