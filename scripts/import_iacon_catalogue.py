"""Catalogue an already imported offline snapshot without model calls or source rewrites."""
import json
import re
from sqlalchemy import select
from app.models.memory_serving import MemoryChange
from app.schemas.iacon import Command, State
from app.services.iacon_catalogue import Catalogue


def legacy_state(content):
    marker = "<!-- memory-state-v1 "
    line = next((s for s in content.splitlines() if s.startswith(marker)), "")
    if not line.endswith(" -->"):
        raise ValueError("Missing state lifecycle contract")
    row = json.loads(line[len(marker):-4])
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["key"]):
        raise ValueError("Invalid legacy state key")
    canonical = (f"# {row['key']}\n\n{marker}{json.dumps(row, ensure_ascii=False, sort_keys=True)} -->\n\n"
        f"{row['summary']}\n\n- Status: {row['status']}\n- Verified: {row['verified_on']}\n"
        f"- Review: {row['review_on']}\n- Source: {row['source']}\n")
    if canonical != content:
        raise ValueError("State metadata and original readable text disagree")
    payload = {key: row[key] for key in ("summary", "status", "review_on", "verified_on", "starts_on",
               "ends_on", "closed_on", "salience") if key in row}
    payload["question"] = f"Is this still current: {row['summary'][:400]}?"
    return row["key"], State.model_validate(payload).model_dump(mode="json")


async def import_catalogue(snapshot, store, context):
    catalogue = Catalogue(store)
    result = {"documents": 0, "entities": 0, "states": 0, "review_required": [], "source_mapping": {}}
    entity_ids, states = {}, {}
    async with store.sessions() as db:
        originals = (await db.execute(select(MemoryChange).where(MemoryChange.user_id == context.user_id,
            MemoryChange.source_key.like(f"snapshot:{snapshot['sha256']}:file:%"))
            .order_by(MemoryChange.id))).scalars().all()
    sources_by_file = {s.provenance["original_file_id"]: s for s in originals}
    current_entities = {s.provenance["entity_id"]: s for s in originals
        if s.provenance.get("entity_id") and s.provenance.get("eligible_for_context")
        and not s.provenance.get("historical") and s.content.strip()}
    for row in snapshot["files"]:
        source = sources_by_file[row["id"]]
        content = source.content
        if not content.strip():
            continue
        evidence = [{"source_id": source.id, "start": 0, "end": min(8000, len(content)), "quote": content[:8000]}]
        key = f"migration:{snapshot['sha256'][:16]}:{row['id']}"
        entity = snapshot["entities"].get(snapshot["meta"].get(row["id"], {}).get("entity_id"))
        links = []
        if entity and entity["id"] not in entity_ids and entity["id"] in current_entities:
            kind = entity.get("entity_type", "person")
            if kind in {"person", "project", "course", "organization", "place"}:
                aliases = entity.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = json.loads(aliases)
                canonical = current_entities[entity["id"]]
                canonical_evidence = [{"source_id": canonical.id, "start": 0,
                    "end": min(8000, len(canonical.content)), "quote": canonical.content[:8000]}]
                created = await catalogue.apply(context.user_id, Command(command_id=key+":entity", kind="entity",
                    title=entity["canonical_name"][:256], payload={"entity_type": kind,
                    "aliases": [a for a in aliases if len(a) <= 128][:32]}, evidence=canonical_evidence), actor="migration")
                entity_ids[entity["id"]] = created["id"]
                result["entities"] += 1
        if entity and entity["id"] in entity_ids:
            links = [{"target_id": entity_ids[entity["id"]], "relation": "about"}]
        # The source retains arbitrarily large originals. Never truncate them into a native document.
        if len(content) <= 1000000:
            doc = await catalogue.apply(context.user_id, Command(command_id=key+":document", kind="document",
                title=row["path"][-256:], payload={"content": content}, evidence=evidence, links=links), actor="migration")
            result["source_mapping"][str(source.id)] = doc["id"]
            result["documents"] += 1
        else:
            result["review_required"].append({"source_id": source.id, "reason": "Oversized native document retained as original source"})
        if "/states/" in row["path"] and not source.provenance.get("historical"):
            try:
                state_key, payload = legacy_state(content)
                if row["path"].rsplit("/", 1)[-1] != state_key + ".md":
                    raise ValueError("State key/path mismatch")
                month = re.search(r"/states/(\d{2})-(\d{2})/", row["path"])
                order = (payload["verified_on"], (month[2] + month[1]) if month else "", row["path"])
                if state_key not in states or order > states[state_key][0]:
                    states[state_key] = (order, key, payload, evidence)
            except (ValueError, KeyError, TypeError) as exc:
                result["review_required"].append({"source_id": source.id, "reason": str(exc)[:160]})
    for state_key, (_, key, payload, evidence) in states.items():
        await catalogue.apply(context.user_id, Command(command_id=key+":state", kind="state", title=state_key,
            payload=payload, evidence=evidence), actor="migration")
        result["states"] += 1
    return result
