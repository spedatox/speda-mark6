import json
import logging

from app.core.context import AgentContext
from app.skills.base import Skill
from app.automations import manager

logger = logging.getLogger(__name__)


class AutomationsSkill(Skill):
    def __init__(self) -> None:
        # Late-bound engine refs action='test' needs to fire a real turn —
        # none of them exist yet when Tier-1 skills register (main.py); they
        # are only ready once the orchestrator/turn registry/etc. are built,
        # so this mirrors AgentDispatcher.wire()'s exact pattern for the exact
        # same reason. Every other action works with these left None.
        self._profiles = None
        self._orchestrator = None
        self._turns = None
        self._session_manager = None
        self._telegram_bots = None
        self._agent_proxy = None
        self._ws_manager = None

    def wire(self, *, profiles, orchestrator, turns, session_manager,
             telegram_bots, agent_proxy=None, ws_manager=None) -> None:
        """Called once from main.py's lifespan, at the same point the trigger
        reporters are wired — reuses that exact dependency set (main.py's
        `reporter_deps`), since action='test' needs precisely what a finished
        legionnaire's report turn needs: a way to start a real trigger turn."""
        self._profiles = profiles
        self._orchestrator = orchestrator
        self._turns = turns
        self._session_manager = session_manager
        self._telegram_bots = telegram_bots
        self._agent_proxy = agent_proxy
        self._ws_manager = ws_manager

    name = "manage_automations"
    deferred = True
    search_keywords = "automation schedule cron recurring reminder briefing hook watcher workflow n8n job daily routine trigger keyword mail domain webhook rss"
    description = (
        "Your CONTROL PLANE over n8n — the engine behind every scheduled briefing, "
        "reminder, and proactive watcher (a page changes, a keyword appears, mail "
        "arrives from someone, a feed posts). You do NOT write n8n JSON, hit n8n "
        "endpoints, or manage webhooks yourself: you hand this tool a structured "
        "`spec` and it composes the correct n8n workflow, POSTs it, and activates it. "
        "n8n is the sole scheduler — never promise an internal timer or a one-off "
        "delay; if it must happen later, it is an automation. This is also what the "
        "owner's OWN Settings → Automations panel manages, so anything created here "
        "is editable, testable and deletable from there too, and vice versa. "
        "Use this when the owner asks you to watch/track/monitor something, remind "
        "them (once or on a schedule), send him a recurring report, or asks what "
        "you're running ('what are you watching?', 'stop the evening reminder'). Do "
        "NOT use it for one-off questions you can answer now, or in-conversation "
        "reminders that don't outlive the chat. "
        "\n\nTWO WAYS TO SHAPE `spec` — pick ONE per automation:"
        "\n\n(A) TEMPLATE — set `template`, for anything that fires on a CLOCK or on "
        "a specific EVENT type this tool already understands well. Preferred whenever "
        "it fits: the owner's own panel renders these with a real form (frequency "
        "picker, url field, …) instead of a raw JSON blob, and can re-poll/edit them "
        "the same way. Three fire on a schedule (`schedule` object required: "
        "frequency 'once'|'daily'|'weekly'|'monthly', at 'HH:MM', plus days [1-7, "
        "Mon=1] for weekly / dom [1-31] for monthly / date 'YYYY-MM-DD' for once — "
        "'once' retires itself automatically after firing, no cleanup needed):"
        "\n  'briefing' — a recurring report you WRITE by calling real tools each time "
        "(gather then compose), e.g. a morning news/mail/calendar summary."
        "\n  'reminder' — a plain nudge, not data-gathering. Any frequency including "
        "'once' for a single date. Use this for 'her hafta X yap', 'yarın Y hatırlat'."
        "\n  'proactive_ask' — a reminder delivered with ANSWER BUTTONS that keeps "
        "re-asking until the owner responds (e.g. medication checks). Needs `options` "
        "(list of button labels, e.g. ['✅ Yaptım', '⏭️ Atlıyorum']), optional "
        "`every_minutes` (default 5) and `max_asks` (default 10)."
        "\nAll three optionally take `day_flags`: [{label, days:[1-7]}] — weekday "
        "classes the agent must be TOLD rather than compute itself (e.g. gym days), "
        "computed fresh at fire time and appended to the instruction as hard facts."
        "\n\nThree fire on an EVENT instead (no `schedule` — they poll on plain "
        "`interval_minutes`, default 360 for the web ones / 15 for mail):"
        "\n  'hook_keyword' — fires the FIRST time a word/phrase appears on a page. "
        "Needs `url` and `look_for`."
        "\n  'hook_address' — fires whenever a specific page changes AT ALL (no "
        "keyword). Needs `url` only."
        "\n  'hook_mail' — fires when mail arrives from a sender domain and/or "
        "addressed to a specific recipient (the forwarded-mailbox case). Needs "
        "`domain` and/or `recipient` — at least one."
        "\nFor ALL six templates: `name` (short label) and `instruction` are required. "
        "Write `instruction` as an EXECUTABLE instruction to your future self — name "
        "the exact tools/steps and real data to gather for a briefing/reminder; for a "
        "Hook, the fired event's data (the page text, the mail) is ALREADY in the "
        "payload, so say what to DO with it, not how to fetch it. Never invent data a "
        "tool didn't return. Do not mention delivery mechanics (send_telegram_message, "
        "the reminders tool) — those are bolted on automatically and yours would only "
        "conflict."
        "\n\n(B) RAW `kind` — for the two shapes with no template: 'rss_watch' "
        "(feed_url, optional interval_minutes) fires on new feed items; 'webhook' "
        "creates an INBOUND URL an external system POSTs to (optional webhook_path) — "
        "the result includes 'webhook_url', relay it to the owner (it's reachable "
        "externally only if the deployment exposes n8n's /webhook). A raw spec needs "
        "`kind`, `name`, and `intent` (same executable-instruction bar as `instruction` "
        "above — write it yourself, it is NOT rewritten before it runs). "
        "`web_watch`/`schedule` also still work raw (url/look_for or cron) for a "
        "one-off case a template doesn't cover, but prefer the template when it fits."
        "\n\nBOTH ways accept `duration_days` for time-boxed tracking ('for a month' → "
        "30) — an auto-expiry on top of whatever else ends it. On any PUSH automation "
        "(everything except proactive_ask), `voice: true` speaks the reply in your own "
        "TTS voice and sends it as a Telegram audio message instead of text — only when "
        "the owner asked for that, not by default. "
        "IDEMPOTENCY: before creating anything scheduled, action='list' first and "
        "REUSE or delete+replace an existing one for the same job — never stack "
        "duplicates. "
        "LIFECYCLE: action='list' returns everything with its id; pause/resume/delete "
        "take that automation_id; action='test' (automation_id) fires it RIGHT NOW — "
        "the real turn, not a mock, useful to prove a new one actually works before "
        "trusting its schedule. Returns the created/affected automation as JSON (with "
        "webhook_url for webhooks), the full list for 'list', or an actionable error to "
        "fix and retry (missing field, n8n not configured, etc.)."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "pause", "resume", "delete", "test"],
                "description": "What to do. 'list' shows everything with ids; pause/resume/delete/test need automation_id from a prior list.",
            },
            "automation_id": {
                "type": "integer",
                "description": "Target automation id (from 'list') — required for pause, resume, delete, and test.",
            },
            "spec": {
                "type": "object",
                "description": (
                    "For 'create'. EITHER set `template` (preferred — see the tool "
                    "description for the six template names and their required "
                    "fields) OR set raw `kind` (web_watch|rss_watch|schedule|webhook) "
                    "for the two shapes with no template. Do not mix: a templated spec "
                    "ignores `kind`/`cron`/`intent` in favour of `schedule`/`instruction`."
                ),
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": ["briefing", "reminder", "proactive_ask",
                                 "hook_keyword", "hook_address", "hook_mail"],
                        "description": "Preferred path. See the tool description for what each needs.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["web_watch", "rss_watch", "schedule", "webhook"],
                        "description": "Raw path — only for rss_watch/webhook, or when no template fits.",
                    },
                    "name": {"type": "string", "description": "Short human label. Required either way."},
                    "instruction": {"type": "string", "description": "Templated path: the executable instruction. Written in the owner's language."},
                    "intent": {"type": "string", "description": "Raw path: the executable instruction (same bar as `instruction`)."},
                    "schedule": {
                        "type": "object",
                        "description": "Required for briefing/reminder/proactive_ask.",
                        "properties": {
                            "frequency": {"type": "string", "enum": ["once", "daily", "weekly", "monthly"]},
                            "at": {"type": "string", "description": "'HH:MM', 24-hour, owner's timezone."},
                            "days": {"type": "array", "items": {"type": "integer"}, "description": "weekly only — 1=Mon … 7=Sun."},
                            "dom": {"type": "integer", "description": "monthly only — day of month 1-31."},
                            "date": {"type": "string", "description": "once only — 'YYYY-MM-DD'."},
                        },
                    },
                    "day_flags": {
                        "type": "array",
                        "description": "Optional, schedule templates only. Weekday classes computed fresh at fire time.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "days": {"type": "array", "items": {"type": "integer"}},
                            },
                        },
                    },
                    "options": {"type": "array", "items": {"type": "string"}, "description": "proactive_ask only — answer button labels, at least one."},
                    "every_minutes": {"type": "integer", "description": "proactive_ask only — re-ask cadence, default 5."},
                    "max_asks": {"type": "integer", "description": "proactive_ask only — give-up count, default 10."},
                    "url": {"type": "string", "description": "hook_keyword/hook_address, or raw web_watch."},
                    "look_for": {"type": "string", "description": "hook_keyword (required there), or raw web_watch (optional)."},
                    "domain": {"type": "string", "description": "hook_mail — sender domain, e.g. 'tdv.org'."},
                    "recipient": {"type": "string", "description": "hook_mail — forwarded-mailbox delivered-to address."},
                    "interval_minutes": {"type": "integer", "description": "Hook templates and raw web_watch/rss_watch — polling cadence."},
                    "feed_url": {"type": "string", "description": "raw rss_watch only."},
                    "cron": {"type": "string", "description": "raw schedule only, e.g. '0 8 * * *' for 8am daily."},
                    "webhook_path": {"type": "string", "description": "raw webhook only, optional."},
                    "duration_days": {"type": "number", "description": "Either path — auto-expiry, e.g. 30 for 'a month'."},
                    "voice": {
                        "type": "boolean",
                        "description": (
                            "Any PUSH automation (not proactive_ask, which already delivers "
                            "through the reminders tool). When true, the reply is spoken in the "
                            "firing agent's own TTS voice and sent as a Telegram audio message "
                            "instead of text — set it only when the owner actually asked for a "
                            "voice reply, e.g. 'sesli gönder', 'bunu dinlemek istiyorum'."
                        ),
                    },
                },
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        action = args.get("action")
        if context.db is None:
            return "Automation management needs a database session — none available on this context."
        try:
            if action == "create":
                spec = args.get("spec") or {}
                result = await manager.create_automation(spec, context.db, context.agent_id)
                logger.info(
                    "automation_created_by_agent",
                    extra={"request_id": context.request_id, "automation_id": result["id"]},
                )
                tail = (
                    "\nConfirm to the owner in one sentence what you're now watching and until when."
                )
                if result.get("webhook_url"):
                    tail = (
                        f"\nGive the owner this inbound webhook URL so their system can POST to it: "
                        f"{result['webhook_url']} — it fires the watcher on each call. (It's on n8n's "
                        f"network; it's reachable from outside only if n8n's /webhook is exposed publicly.)"
                    )
                return (
                    "Automation created and live in n8n:\n"
                    + json.dumps(result, indent=2)
                    + tail
                )

            if action == "list":
                items = await manager.list_automations(context.db)
                if not items:
                    return "No automations exist yet. Nothing is being watched."
                return "Current automations:\n" + json.dumps(items, indent=2)

            if action in ("pause", "resume"):
                aid = args.get("automation_id")
                if aid is None:
                    return "pause/resume needs automation_id — call action='list' first to get ids."
                result = await manager.set_automation_active(int(aid), action == "resume", context.db)
                return f"Automation {aid} is now {'active' if result['active'] else 'paused'}:\n" + json.dumps(result, indent=2)

            if action == "delete":
                aid = args.get("automation_id")
                if aid is None:
                    return "delete needs automation_id — call action='list' first to get ids."
                result = await manager.delete_automation(int(aid), context.db)
                return f"Deleted automation '{result['name']}' (id {aid}) and its n8n workflow."

            if action == "test":
                aid = args.get("automation_id")
                if aid is None:
                    return "test needs automation_id — call action='list' first to get ids."
                if self._orchestrator is None:
                    return "Test firing isn't wired up yet — the backend is still starting."
                result = await manager.test_fire(
                    int(aid), context.db,
                    profiles=self._profiles, orchestrator=self._orchestrator,
                    turns=self._turns, session_manager=self._session_manager,
                    telegram_bots=self._telegram_bots, agent_proxy=self._agent_proxy,
                    ws_manager=self._ws_manager,
                )
                return (
                    f"Test fired for automation {aid} (request_id {result['request_id']}) — "
                    "this is a REAL run, not a mock: a push automation will really push, a "
                    "proactive ask will really nag. Tell the owner you're testing it now; do "
                    "NOT report a result yet, it hasn't happened."
                )

            return f"Unknown action {action!r} — use create, list, pause, resume, delete, or test."

        except ValueError as exc:
            # Actionable composition/infrastructure error — surface for repair.
            return f"Automation error: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "automation_skill_error",
                extra={"request_id": context.request_id, "error": str(exc)},
            )
            return f"Automation system error: {exc}"
