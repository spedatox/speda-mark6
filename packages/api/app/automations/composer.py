"""
Workflow composer — turns SPEDA's structured intent into valid n8n workflow JSON.

SPEDA decides the *semantics* (poll this URL every 6h, watch for a change, stop
after 30 days, ping me) and emits a `spec`; this module deterministically
assembles correct n8n node graphs from a validated block library. That keeps
"SPEDA, track this site for a month" reliable — no gambling on hand-written n8n
JSON — while still letting it compose any combination of triggers/conditions.

Every composed workflow terminates in an HTTP Request node that calls SPEDA's
`POST /trigger/speda` with `output_mode: "push"` and the owner's natural-language
intent, so when the watcher fires the orchestrator composes the actual message
and Telegram delivers it.

Robustness: change/expiry gating is done by a Code node that returns NO items
when it shouldn't fire (n8n stops the branch on an empty return) instead of a
fragile IF node — far fewer schema surfaces to get wrong across n8n versions.
"""

import json
import uuid

from app.config import settings

# Pinned node type versions known-good on modern n8n (1.x).
_T_SCHEDULE = ("n8n-nodes-base.scheduleTrigger", 1.2)
_T_HTTP = ("n8n-nodes-base.httpRequest", 4.2)
_T_CODE = ("n8n-nodes-base.code", 2)
_T_RSS = ("n8n-nodes-base.rssFeedReadTrigger", 1)
_T_WEBHOOK = ("n8n-nodes-base.webhook", 2)


def _node(name: str, type_ver: tuple, x: int, params: dict) -> dict:
    type_, ver = type_ver
    return {
        "name": name,
        "type": type_,
        "typeVersion": ver,
        "position": [x, 300],
        "parameters": params,
    }


def _callback_body(kind: str, name: str, intent: str) -> str:
    """n8n expression building the /trigger/speda body. Static strings are
    JSON-escaped (valid JS literals); `$json` carries the upstream item so SPEDA
    sees what actually fired (the new email, the changed page, the feed item)."""
    return (
        "={{ ({ \"payload\": { "
        f"\"type\": {json.dumps(kind)}, "
        "\"event\": \"automation_fired\", "
        f"\"automation\": {json.dumps(name)}, "
        f"\"intent\": {json.dumps(intent)}, "
        "\"data\": $json }, \"output_mode\": \"push\" }) }}"
    )


def _callback_node(kind: str, name: str, intent: str, x: int, agent_id: str = "speda") -> dict:
    """The terminal HTTP Request → the owning agent. Carries both required
    secrets and fires /trigger/{agent_id} so the push is composed in that
    agent's voice."""
    return _node("Notify SPEDA", _T_HTTP, x, {
        "method": "POST",
        "url": f"{settings.speda_callback_url.rstrip('/')}/trigger/{agent_id}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "X-API-Key", "value": settings.speda_api_key},
            {"name": "X-N8N-Secret", "value": settings.n8n_secret},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": _callback_body(kind, name, intent),
        "options": {},
    })


def _gate_code(look_for: str | None, expires_at: str | None) -> str:
    """JS for the change/keyword/expiry gate. Returns [] (fire nothing) unless a
    real change/keyword hit occurs and the watcher hasn't expired. Persists state
    in the workflow's global static data so it survives between runs."""
    lf = json.dumps(look_for or "")
    exp = json.dumps(expires_at or "")
    return f"""
const store = $getWorkflowStaticData('global');
const item = $input.first().json;
const body = String(item.data ?? item.body ?? JSON.stringify(item));
const lookFor = {lf};
const expiresAt = {exp};

// Expiry guard — "track this for a month" self-stops here.
if (expiresAt && new Date() > new Date(expiresAt)) {{
  return [];
}}

let fire = false;
if (lookFor) {{
  const found = body.toLowerCase().includes(lookFor.toLowerCase());
  fire = found && !store.matched;      // edge-trigger: notify once when it appears
  store.matched = found;
}} else {{
  const crypto = require('crypto');
  const hash = crypto.createHash('sha256').update(body).digest('hex');
  fire = Boolean(store.lastHash) && store.lastHash !== hash;
  store.lastHash = hash;
}}
return fire ? [{{ json: {{ changed: true, matched: lookFor || null }} }}] : [];
""".strip()


def _expiry_gate_code(expires_at: str) -> str:
    """JS for a pure expiry gate (schedules): pass the item through until the
    deadline, then fire nothing ever again."""
    exp = json.dumps(expires_at)
    return (
        f"const expiresAt = {exp};\n"
        "if (expiresAt && new Date() > new Date(expiresAt)) { return []; }\n"
        "return [{ json: { fired: true } }];"
    )


def _connect(*names: str) -> dict:
    """Linear main-chain connections for the given node names, in order."""
    conns: dict = {}
    for a, b in zip(names, names[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    return conns


def compose(spec: dict, agent_id: str = "speda") -> dict:
    """spec → n8n workflow JSON ready to POST. Raises ValueError on a bad spec.
    agent_id is the agent that owns the watcher; the terminal callback fires
    /trigger/{agent_id} so the push is composed in that agent's voice."""
    kind = spec.get("kind")
    name = spec.get("name") or "SPEDA automation"
    intent = spec.get("intent") or name
    expires_at = spec.get("expires_at")

    if kind == "schedule":
        cron = spec.get("cron")
        if not cron:
            raise ValueError("schedule automations need a 'cron' expression")
        trigger = _node("Schedule", _T_SCHEDULE, 0, {
            "rule": {"interval": [{"field": "cronExpression", "expression": cron}]}
        })
        if expires_at:
            gate = _node("Gate", _T_CODE, 220, {"jsCode": _expiry_gate_code(expires_at)})
            cb = _callback_node(kind, name, intent, 440, agent_id)
            nodes, chain = [trigger, gate, cb], ("Schedule", "Gate", "Notify SPEDA")
        else:
            cb = _callback_node(kind, name, intent, 220, agent_id)
            nodes, chain = [trigger, cb], ("Schedule", "Notify SPEDA")

    elif kind == "web_watch":
        url = spec.get("url")
        if not url:
            raise ValueError("web_watch automations need a 'url'")
        every = int(spec.get("interval_minutes", 360))
        trigger = _node("Schedule", _T_SCHEDULE, 0, {
            "rule": {"interval": [{"field": "minutes", "minutesInterval": every}]}
        })
        fetch = _node("Fetch page", _T_HTTP, 220, {
            "url": url,
            "options": {"response": {"response": {"responseFormat": "text"}}},
        })
        gate = _node("Detect change", _T_CODE, 440, {
            "jsCode": _gate_code(spec.get("look_for"), expires_at)
        })
        cb = _callback_node(kind, name, intent, 660, agent_id)
        nodes = [trigger, fetch, gate, cb]
        chain = ("Schedule", "Fetch page", "Detect change", "Notify SPEDA")

    elif kind == "rss_watch":
        feed = spec.get("feed_url")
        if not feed:
            raise ValueError("rss_watch automations need a 'feed_url'")
        every = int(spec.get("interval_minutes", 60))
        trigger = _node("RSS", _T_RSS, 0, {
            "feedUrl": feed,
            "pollTimes": {"item": [{"mode": "everyX", "value": every, "unit": "minutes"}]},
        })
        cb = _callback_node(kind, name, intent, 220, agent_id)
        nodes, chain = [trigger, cb], ("RSS", "Notify SPEDA")

    elif kind == "webhook":
        path = spec.get("webhook_path") or uuid.uuid4().hex[:16]
        spec["webhook_path"] = path  # echo back so the caller can store/show the URL
        trigger = _node("Webhook", _T_WEBHOOK, 0, {
            "path": path, "httpMethod": "POST", "responseMode": "onReceived",
        })
        cb = _callback_node(kind, name, intent, 220, agent_id)
        nodes, chain = [trigger, cb], ("Webhook", "Notify SPEDA")

    else:
        raise ValueError(f"unknown automation kind: {kind!r}")

    return {
        "name": name,
        "nodes": nodes,
        "connections": _connect(*chain),
        "settings": {"executionOrder": "v1"},
    }


def describe(spec: dict) -> str:
    """One-line human summary for logs / confirmations."""
    kind = spec.get("kind")
    if kind == "schedule":
        return f"Scheduled ({spec.get('cron')}) → {spec.get('intent')}"
    if kind == "web_watch":
        lf = spec.get("look_for")
        what = f"for '{lf}'" if lf else "for changes"
        return f"Watching {spec.get('url')} {what} every {spec.get('interval_minutes', 360)}m"
    if kind == "rss_watch":
        return f"Watching feed {spec.get('feed_url')} for new items"
    if kind == "webhook":
        return f"Inbound webhook → {spec.get('intent')}"
    return spec.get("intent", "automation")
