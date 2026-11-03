"""Stage an explicit offline policy migration. Never opens a provider or live DB."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.profiles.speda import SPEDAProfile
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.model_policy import parse_model_ref, thinking_wire


def migrate_legacy(raw):
    from app.profiles.registry import ProfileRegistry
    profiles = ProfileRegistry()
    specs = {s.ref: s for s in SPEDAProfile.model_specs}
    policy, dispositions = PolicyDocument(), []
    for agent, ref in raw.get("agent_models", {}).items():
        try:
            parse_model_ref(ref)
            native_agent = profiles.canonical(agent)
            if native_agent in policy.agent_models and policy.agent_models[native_agent] != ref:
                raise ValueError('Conflicting alias pins require review')
            policy.agent_models[native_agent] = ref
            dispositions.append({"kind": "route", "key": agent, "native_key": native_agent, "outcome": "preserved", "model": ref})
        except (ValueError, TypeError, AttributeError):
            dispositions.append({"kind": "route", "key": agent, "outcome": "requires_review"})
    for agent,ref in raw.get('telegram_models',{}).items():
        parse_model_ref(ref)
        policy.telegram_models[profiles.canonical(agent)]=ref
    from app.profiles.workers import normalize_worker_mapping
    policy.worker_models=normalize_worker_mapping(raw.get('legion_models',{}))
    for ref in policy.worker_models.values(): parse_model_ref(ref)
    policy.budget_mode=bool(raw.get('budget_mode',False))
    for ref, value in raw.get("model_thinking", {}).items():
        spec = specs.get(ref)
        try:
            if spec is None:
                raise ValueError("Unknown model capabilities")
            mapped = SPEDAProfile.legacy_thinking.get(ref, {}).get(value, value)
            if mapped != value:
                request = ThinkingRequest(mode="effort", value=mapped)
            elif value == "none":
                request = ThinkingRequest(mode="off")
            elif spec.control == "budget":
                request = ThinkingRequest(mode="budget", value={"low": 1024, "medium": 4096, "high": 8192}[value])
            elif spec.control == "toggle":
                request = ThinkingRequest(mode="enabled" if value in {"medium", "high"} else "off")
            else:
                request = ThinkingRequest(mode="effort", value=value)
            wire = thinking_wire(spec, request)
            policy.models[ref] = request
            dispositions.append({"kind": "thinking", "model": ref, "legacy": value,
                "outcome": "mapped", "requested": request.model_dump(), "applied": wire})
            if mapped != value:
                dispositions[-1]['note'] = 'Explicit legacy compatibility mapping; native thinking remains enabled.'
        except (ValueError, KeyError, TypeError):
            dispositions.append({"kind": "thinking", "model": ref, "legacy": value, "outcome": "requires_review"})
    return policy, dispositions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists() or args.output.resolve() == args.report.resolve():
        parser.error("Output and report must be distinct new paths")
    original = args.source.read_bytes()
    policy, dispositions = migrate_legacy(json.loads(original))
    # No automatic activation: held settings require review before an API save.
    args.output.write_text(json.dumps(policy.model_dump(mode="json"), indent=2)+"\n", encoding="utf-8")
    args.report.write_text(json.dumps({"source_sha256": hashlib.sha256(original).hexdigest(),
        "dispositions": dispositions, "activated": False,
        "scope": "agent model pins and per-model thinking; other legacy settings remain in the original"}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
