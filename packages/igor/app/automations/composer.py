# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Workflow composer — turns Speda's structured intent into valid n8n workflow JSON.

Speda decides the *semantics* (poll this URL every 6h, watch for a change, stop
after 30 days, ping me) and emits a `spec`; this module deterministically
assembles correct n8n node graphs from a validated block library. That keeps
"Speda, track this site for a month" reliable — no gambling on hand-written n8n
JSON — while still letting it compose any combination of triggers/conditions.

Every composed workflow terminates in an HTTP Request node that calls Speda's
`POST /trigger/speda` with `output_mode: "push"` and the owner's natural-language
intent, so when the watcher fires the orchestrator composes the actual message
and Telegram delivers it.

Robustness: change/expiry gating is done by a Code node that returns NO items
when it shouldn't fire (n8n stops the branch on an empty return) instead of a
fragile IF node — far fewer schema surfaces to get wrong across n8n versions.
"""

import json
import uuid

from app.automations import schedule as sched
from app.automations import templates
from app.config import settings

# Pinned node type versions known-good on modern n8n (1.x).
_T_SCHEDULE = ("n8n-nodes-base.scheduleTrigger", 1.2)
_T_HTTP = ("n8n-nodes-base.httpRequest", 4.2)
_T_CODE = ("n8n-nodes-base.code", 2)
_T_RSS = ("n8n-nodes-base.rssFeedReadTrigger", 1)
_T_WEBHOOK = ("n8n-nodes-base.webhook", 2)


def _node(name: str, type_ver: tuple, x: int, params: dict, **extra) -> dict:
    type_, ver = type_ver
    return {
        # n8n's public-API workflow schema REQUIRES a per-node id (a UUID) and
        # rejects the whole POST /workflows body without it. Connections still
        # reference nodes by name (below), so this id only satisfies the schema.
        "id": str(uuid.uuid4()),
        "name": name,
        "type": type_,
        "typeVersion": ver,
        "position": [x, 300],
        "parameters": params,
        # retryOnFail/maxTries/etc. are node-level siblings of "parameters",
        # not entries inside it — n8n silently ignores them if nested.
        **extra,
    }


def _interval_rule(minutes: int) -> dict:
    """Schedule-trigger rule for an every-N-minutes cadence.

    n8n compiles a "minutes" interval into the minute field of a cron
    expression, which only spans 0-59. Anything >= 60 therefore cannot be
    expressed that way and silently collapses to firing every hour — so a
    720-minute watcher ends up hammering the target 12x more often than asked.
    Promote whole hours to the "hours" field, which compiles to the hour slot.
    """
    if minutes >= 60 and minutes % 60 == 0:
        return {"interval": [{"field": "hours", "hoursInterval": minutes // 60}]}
    return {"interval": [{"field": "minutes", "minutesInterval": minutes}]}


def _secret_headers() -> dict:
    """The two secrets every internal HTTP call back into Igor must carry —
    n8n's own shared secret plus the service API key. One place, because three
    node builders (callback, mail scan, mail ack) all need it identically and a
    header set that drifted between them would 401 silently on whichever one
    was edited alone."""
    return {"parameters": [
        {"name": "X-API-Key", "value": settings.speda_api_key},
        {"name": "X-N8N-Secret", "value": settings.n8n_secret},
    ]}


def _callback_body(kind: str, name: str, intent: str, output_mode: str = "push",
                   with_facts: bool = False, allow_override: bool = False,
                   voice: bool = False) -> str:
    """n8n expression building the /trigger/speda body. Static strings are
    JSON-escaped (valid JS literals); `$json` carries the upstream item so Speda
    sees what actually fired (the new email, the changed page, the feed item).

    `output_mode` is a parameter rather than a constant because a proactive-ask
    automation MUST fire silent — the `reminders` tool does its delivering, with
    the answer buttons attached, and a push would send the same checklist a
    second time in a form nobody can answer (app/automations/templates.py).

    `allow_override` lets the upstream gate replace the stored intent for ONE
    firing — the mail-watch gate's health alert ("Gmail is unreachable") must
    override the owner's polished per-domain intent rather than compete with
    it, and $json.intent_override is how it says so without this function
    knowing anything about mail.

    `voice`: the owner's "reply as voice" checkbox. Carried in `payload`
    itself rather than as a peer of `output_mode` — TriggerRequest.payload is
    an open dict (schemas/trigger.py), so no schema change was needed —
    and read by core.trigger_runner._deliver, which speaks the reply through
    whichever TTS engine the firing agent's profile names instead of pushing
    plain text.
    """
    # With a day-flags node upstream, the computed facts are APPENDED to the
    # intent here rather than baked into it — they are only knowable at fire
    # time, and a workflow that stored "today is a gym day" would be wrong by
    # the next morning.
    intent_expr = json.dumps(intent) + (" + ($json.facts || '')" if with_facts else "")
    if allow_override:
        intent_expr = f"($json.intent_override || ({intent_expr}))"
    return (
        "={{ ({ \"payload\": { "
        f"\"type\": {json.dumps(kind)}, "
        "\"event\": \"automation_fired\", "
        f"\"automation\": {json.dumps(name)}, "
        f"\"intent\": {intent_expr}, "
        f"\"voice\": {json.dumps(bool(voice))}, "
        "\"data\": $json }, "
        f"\"output_mode\": {json.dumps(output_mode)} }}) }}}}"
    )


def _callback_node(kind: str, name: str, intent: str, x: int, agent_id: str = "speda",
                   output_mode: str = "push", with_facts: bool = False,
                   allow_override: bool = False, voice: bool = False) -> dict:
    """The terminal HTTP Request → the owning agent. Carries both required
    secrets and fires /trigger/{agent_id} so the push is composed in that
    agent's voice."""
    return _node("Notify Speda", _T_HTTP, x, {
        "method": "POST",
        "url": f"{settings.speda_callback_url.rstrip('/')}/trigger/{agent_id}",
        "sendHeaders": True,
        "headerParameters": _secret_headers(),
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": _callback_body(kind, name, intent, output_mode, with_facts, allow_override, voice),
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


def _day_flags_code(flags: list[dict]) -> str:
    """JS computing whether today and tomorrow carry each declared day-flag.

    THIS IS THE ONE THING A MODEL MAY NOT WORK OUT FOR ITSELF. Atomix once told
    the owner "you trained today, tomorrow is a rest day" on a Tuesday, having
    derived it from a prose description of his gym schedule. A calendar never
    gets that wrong, so the calendar settles it here and the model is handed the
    answer — deterministic input, prose output, never the reverse.

    The block also restates today's date and both weekdays. That is redundant
    with the `[Ddd YYYY-MM-DD HH:MM TZ]` stamp every user message carries, and
    deliberately so: the intents that depend on this block were written against
    exactly this wording, and both sources compute the same fact from the same
    real clock in the same zone, so they state one truth twice rather than
    competing. The FLAGS are the half a stamp cannot supply — which weekdays
    count as gym days is this automation's own configuration.

    The zone is read explicitly rather than trusted from the container's TZ: a
    container assumed to be in the owner's zone is what once put every schedule
    in this system three hours out.
    """
    return (
        f"const TZ = {json.dumps(settings.owner_timezone)};\n"
        f"const FLAGS = {json.dumps(flags, ensure_ascii=False)};\n"
        "const DOW = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };\n"
        "const TR = ['', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', "
        "'Cumartesi', 'Pazar'];\n"
        "const p = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour12: false,\n"
        "  year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' })\n"
        "  .formatToParts(new Date())\n"
        "  .reduce((a, x) => (a[x.type] = x.value, a), {});\n"
        "const today = DOW[p.weekday];\n"
        "const tomorrow = (today % 7) + 1;\n"
        "const NL = String.fromCharCode(10);\n"
        "let out = NL + NL + '— DEĞİŞMEZ GERÇEKLER (hesaplama, aynen kullan) —'\n"
        "  + NL + 'Bugün: ' + p.year + '-' + p.month + '-' + p.day + ', ' "
        "+ TR[today] + '.'\n"
        "  + NL + 'Yarın: ' + TR[tomorrow] + '.';\n"
        "for (const f of FLAGS) {\n"
        "  const days = (f.days || []).map(Number);\n"
        "  out += NL + 'Bugün ' + f.label + ': ' + "
        "(days.includes(today) ? 'EVET' : 'HAYIR') + '.';\n"
        "  out += NL + 'Yarın ' + f.label + ': ' + "
        "(days.includes(tomorrow) ? 'EVET' : 'HAYIR') + '.';\n"
        "}\n"
        "return [{ json: { facts: out } }];"
    )


def _mail_gate_code(label: str) -> str:
    """JS for a mail-watch gate: fires only on a real, unread hit, and alerts
    once — edge-triggered — if the Gmail connection goes bad, then stays quiet
    until it recovers.

    Mirrors `scripts/n8n/mail_watch.json`'s shared-pipeline Gate node exactly,
    field for field, so a per-domain automation behaves identically to the
    hand-edited file it complements rather than introducing a second contract
    for the same thing. That file's Gate has been correct in production; this
    is not a redesign, only a per-automation instantiation of it.

    `label` is baked in here rather than read off the scan response, because
    `MailScanResponse` never echoes it back (app/schemas/mail.py) — it is this
    automation's own fixed configuration, known at compose time, and the
    downstream Ack call needs it from wherever this gate publishes it.
    """
    lbl = json.dumps(label)
    alert_prefix = json.dumps(
        "Bu mail izleyicisi Gmail'e ulaşamıyor. Owner'a TEK cümlede söyle: "
        "izleme kör, Google bağlantısını Ayarlar > Bağlantılar'dan yeniden "
        "kurması gerekebilir. Hiçbir araç çağırma, mail içeriği uydurma. Hata: "
    )
    return (
        "const store = $getWorkflowStaticData('global');\n"
        "const res = $input.first().json;\n"
        "const status = res.status || 'error';\n"
        "\n"
        "if (status !== 'ok') {\n"
        "  // Edge-triggered: alert once when the connection breaks, then stay\n"
        "  // quiet on every poll after that — a broken watch nagging every 15\n"
        "  // minutes is worse than one that goes silent until it recovers.\n"
        "  if (store.broken) { return []; }\n"
        "  store.broken = true;\n"
        "  return [{ json: {\n"
        f"    intent_override: {alert_prefix} + (res.detail || status),\n"
        f"    message_ids: [], label: {lbl}\n"
        "  } }];\n"
        "}\n"
        "store.broken = false;\n"
        "\n"
        "// The normal path: nothing arrived. No items = branch stops = no\n"
        "// turn, no tokens spent — the entire point of this being a gate.\n"
        "if (!res.count) { return []; }\n"
        "\n"
        "return [{ json: {\n"
        "  count: res.count, message_ids: res.message_ids || [],\n"
        f"  messages: res.messages || [], label: {lbl}\n"
        "} }];"
    )


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
    name = spec.get("name") or "Speda automation"
    expires_at = spec.get("expires_at")
    # A templated automation's instruction is assembled, never taken raw: the
    # transport mechanics (push vs silent, the reminders call) are bolted on by
    # the template rather than trusted to whoever wrote the spec. Untemplated
    # specs — the agent-facing tool's own kinds — keep the old behaviour.
    template = spec.get("template")
    if template:
        intent = templates.build_intent(spec)
        mode = templates.output_mode(template)
    else:
        intent = spec.get("intent") or name
        mode = "push"
    # Meaningless on anything but a push: a silent run delivers nothing to
    # speak, and proactive_ask's reply already goes out through the
    # `reminders` tool with buttons, not a Telegram audio message.
    voice = bool(spec.get("voice")) and mode == "push"

    if kind == "schedule":
        # Structured schedule first: cron is compiled from it, never stored as
        # the source of truth (app/automations/schedule.py). `cron` is still
        # accepted for specs written before this existed and for the agent tool.
        if spec.get("schedule"):
            cron = sched.to_cron(spec["schedule"])
            expires_at = expires_at or sched.expiry_for(spec["schedule"])
        else:
            cron = spec.get("cron")
        if not cron:
            raise ValueError(
                "schedule automations need either a 'schedule' block "
                "(frequency/at/…) or a raw 'cron' expression"
            )
        trigger = _node("Schedule", _T_SCHEDULE, 0, {
            "rule": {"interval": [{"field": "cronExpression", "expression": cron}]}
        })
        flags = spec.get("day_flags") or []
        nodes, chain = [trigger], ["Schedule"]
        x = 220
        if expires_at:
            nodes.append(_node("Gate", _T_CODE, x, {"jsCode": _expiry_gate_code(expires_at)}))
            chain.append("Gate")
            x += 220
        if flags:
            # LAST before the callback, so a gate that returned [] stops the
            # branch before this runs — and so nothing downstream can drop the
            # facts item the callback reads.
            nodes.append(_node("Day facts", _T_CODE, x, {"jsCode": _day_flags_code(flags)}))
            chain.append("Day facts")
            x += 220
        nodes.append(_callback_node(kind, name, intent, x, agent_id, mode, bool(flags), voice=voice))
        chain.append("Notify Speda")

    elif kind == "web_watch":
        url = spec.get("url")
        if not url:
            raise ValueError("web_watch automations need a 'url'")
        every = int(spec.get("interval_minutes", 360))
        trigger = _node("Schedule", _T_SCHEDULE, 0, {"rule": _interval_rule(every)})
        # Watched pages are third-party sites on networks we do not control, so a
        # single dropped connection (ECONNRESET, reset by a WAF, a brief 5xx) is
        # expected background noise rather than a real failure. Without a retry
        # the whole run dies on the first blip and the watcher stays silent until
        # the next tick — for a 12-hour cadence that is a 12-hour blind spot.
        fetch = _node("Fetch page", _T_HTTP, 220, {
            "url": url,
            "options": {"response": {"response": {"responseFormat": "text"}}},
        }, retryOnFail=True, maxTries=3, waitBetweenTries=5000)
        gate = _node("Detect change", _T_CODE, 440, {
            "jsCode": _gate_code(spec.get("look_for"), expires_at)
        })
        cb = _callback_node(kind, name, intent, 660, agent_id, mode, voice=voice)
        nodes = [trigger, fetch, gate, cb]
        chain = ("Schedule", "Fetch page", "Detect change", "Notify Speda")

    elif kind == "rss_watch":
        feed = spec.get("feed_url")
        if not feed:
            raise ValueError("rss_watch automations need a 'feed_url'")
        every = int(spec.get("interval_minutes", 60))
        trigger = _node("RSS", _T_RSS, 0, {
            "feedUrl": feed,
            "pollTimes": {"item": [{"mode": "everyX", "value": every, "unit": "minutes"}]},
        })
        cb = _callback_node(kind, name, intent, 220, agent_id, mode, voice=voice)
        nodes, chain = [trigger, cb], ("RSS", "Notify Speda")

    elif kind == "mail_watch":
        domain = (spec.get("domain") or "").strip()
        recipient = (spec.get("recipient") or "").strip()
        if not (domain or recipient):
            raise ValueError("mail_watch automations need a 'domain' or a 'recipient'")
        every = int(spec.get("interval_minutes", 15))
        label = spec.get("label") or "SPEDA-Seen"
        trigger = _node("Schedule", _T_SCHEDULE, 0, {"rule": _interval_rule(every)})
        # Same tolerance as web_watch's fetch: Gmail/the token endpoint having a
        # bad moment is expected background noise, not a reason to skip a poll.
        scan = _node("Scan mail", _T_HTTP, 220, {
            "method": "POST",
            "url": f"{settings.speda_callback_url.rstrip('/')}/mail/watch/scan",
            "sendHeaders": True,
            "headerParameters": _secret_headers(),
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": json.dumps({
                "domain": domain, "recipient": recipient,
                "max_results": 10, "newer_than_days": 2,
                "include_body": True, "body_chars": 2000, "label": label,
            }),
            "options": {},
        }, retryOnFail=True, maxTries=3, waitBetweenTries=5000)
        gate = _node("Gate", _T_CODE, 440, {"jsCode": _mail_gate_code(label)})
        cb = _callback_node(kind, name, intent, 660, agent_id, mode, allow_override=True, voice=voice)
        # Exactly-once, same contract as scripts/n8n/mail_watch.json: this call
        # commits LAST, after the trigger already succeeded, so a failed ack
        # leaves the mail unlabelled and it is simply re-scanned next poll —
        # recoverable — rather than labelling first and losing a notification
        # to a mid-flight crash, which is not.
        ack = _node("Mark seen", _T_HTTP, 880, {
            "method": "POST",
            "url": f"{settings.speda_callback_url.rstrip('/')}/mail/watch/seen",
            "sendHeaders": True,
            "headerParameters": _secret_headers(),
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": (
                "={{ ({ \"message_ids\": $('Gate').item.json.message_ids, "
                "\"label\": $('Gate').item.json.label }) }}"
            ),
            "options": {},
        }, retryOnFail=True, maxTries=2, waitBetweenTries=3000)
        nodes = [trigger, scan, gate, cb, ack]
        chain = ("Schedule", "Scan mail", "Gate", "Notify Speda", "Mark seen")

    elif kind == "webhook":
        path = spec.get("webhook_path") or uuid.uuid4().hex[:16]
        spec["webhook_path"] = path  # echo back so the caller can store/show the URL
        trigger = _node("Webhook", _T_WEBHOOK, 0, {
            "path": path, "httpMethod": "POST", "responseMode": "onReceived",
        })
        cb = _callback_node(kind, name, intent, 220, agent_id, mode, voice=voice)
        nodes, chain = [trigger, cb], ("Webhook", "Notify Speda")

    else:
        raise ValueError(f"unknown automation kind: {kind!r}")

    return {
        "name": name,
        "nodes": nodes,
        "connections": _connect(*chain),
        # Pin the workflow's timezone explicitly. n8n interprets a cron in its
        # own GENERIC_TIMEZONE otherwise, which is how "brief me at 8am" became
        # a workflow scheduled for 05:00 that someone had hand-compensated —
        # correct only for as long as nobody touched the container's TZ.
        # A per-workflow timezone outranks the global one, so this stays right
        # regardless of how the n8n service is configured.
        "settings": {
            "executionOrder": "v1",
            "timezone": settings.owner_timezone,
        },
    }


def display(spec: dict) -> dict | None:
    """The STRUCTURED schedule for the owner's screen, or None for a spec with
    no clock (a watcher fires on an event, not at a time).

    Structure rather than a sentence, because Heartbreaker renders it in the
    owner's chosen language — a backend that returned "Günde bir" would have
    picked one for him. See app/automations/schedule.py.
    """
    if not spec.get("schedule"):
        return None
    return sched.describe(spec["schedule"])


def hook_display(spec: dict) -> dict | None:
    """The STRUCTURED watcher config for the owner's screen — url/domain and
    polling interval, never a sentence, for the same reason `display()` above
    is structural. None for anything that isn't one of the three Hook
    templates (app/automations/templates.py).
    """
    template = spec.get("template")
    if template not in ("hook_keyword", "hook_address", "hook_mail"):
        return None
    if template == "hook_mail":
        every = int(spec.get("interval_minutes") or 15)
        return {
            "type": "mail",
            "domain": spec.get("domain") or "",
            "recipient": spec.get("recipient") or "",
            "interval_minutes": every,
        }
    every = int(spec.get("interval_minutes") or 360)
    return {
        "type": "keyword" if template == "hook_keyword" else "address",
        "url": spec.get("url") or "",
        "look_for": spec.get("look_for") or "",
        "interval_minutes": every,
    }


def describe(spec: dict) -> str:
    """One-line ENGLISH summary for logs, confirmations and the agent-facing
    tool. Never the owner-facing rendering — that is `display()`."""
    kind = spec.get("kind")
    if spec.get("template"):
        return templates.summarize(spec)
    if kind == "schedule":
        return f"Scheduled ({spec.get('cron')}) → {spec.get('intent')}"
    if kind == "web_watch":
        lf = spec.get("look_for")
        what = f"for '{lf}'" if lf else "for changes"
        return f"Watching {spec.get('url')} {what} every {spec.get('interval_minutes', 360)}m"
    if kind == "rss_watch":
        return f"Watching feed {spec.get('feed_url')} for new items"
    if kind == "mail_watch":
        who = spec.get("domain") or spec.get("recipient")
        return f"Watching mail from/to {who} every {spec.get('interval_minutes', 15)}m"
    if kind == "webhook":
        return f"Inbound webhook → {spec.get('intent')}"
    return spec.get("intent", "automation")
