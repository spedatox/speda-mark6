"""
The automation templates the owner picks from in Heartbreaker, and the
deterministic half of the instruction each one fires with.

Two families. SCHEDULE_TEMPLATES fire on a clock (briefing/reminder/
proactive_ask) — their `spec.schedule` block is the structured frequency/at/
days machinery in `schedule.py`. HOOK_TEMPLATES fire on an EVENT — a keyword
appearing, a page changing, mail arriving — and poll on a plain
`interval_minutes` cadence instead; they carry no `schedule` block at all, and
`manager._prepare()` branches on which family a spec belongs to before either
validating or composing it. A Hook's `describe()`/`display()` half is
`composer.hook_display()`, the watcher counterpart of `schedule.describe()`.

`reminder` is deliberately schedule-agnostic — it started as a one-off-only
template ("reminder_once") and was generalized because the owner's actual need
("hatırlat, haftada bir") is the same content whether it fires once or every
week; forcing a template switch to get a different frequency would have been
the module drawing a distinction the owner never asked for. A 'once' schedule
still retires itself (schedule.expiry_for) — that behaviour rides on the
SCHEDULE, not on which template chose it.

An automation's `intent` is not a message — it is an instruction the agent
executes with no human in the loop when n8n calls back. Two things go into it:

  1. WHAT THE OWNER WANTS, in his own words, later upgraded in place by
     `services/automation_intent.py`.
  2. THE MECHANICS, which are never left to a model: which output mode this
     template fires in, whether the reply IS the delivery or something else
     delivers it, and the exact tool call that makes answer buttons appear.

The split matters because (2) is where the silent failures live, and both were
paid for once already by the briefings this module absorbed (the history is in
`scripts/migrate_briefings.py`):

  · A `push` automation's reply is ALREADY the Telegram message. An intent that
    also says "send it with send_telegram_message" double-sends.
  · A proactive ask must run `silent`, because the `reminders` tool does the
    delivering — with the buttons attached. On `push` the owner gets the same
    checklist twice, and the second copy cannot be answered.

So a model never writes those lines. It writes the content; `build_intent()`
bolts the mechanics on underneath — the same principle as
`composer._day_flags_code`, where a fact a model gets wrong is computed and
handed over rather than described.
"""

from __future__ import annotations

import re

from app.automations import schedule as sched

SCHEDULE_TEMPLATES = ("briefing", "reminder", "proactive_ask")
# hook_keyword and hook_address are BOTH a web_watch — the only difference is
# whether 'look_for' is set (wait for a specific word) or absent (fire on any
# change at all). Kept as two template names rather than one "web watch" pick
# with a checkbox because the owner picking between them IS the wizard's first
# question, the same shape as picking briefing/reminder/proactive_ask.
HOOK_TEMPLATES = ("hook_keyword", "hook_address", "hook_mail")
TEMPLATES = SCHEDULE_TEMPLATES + HOOK_TEMPLATES

# What every push-mode automation must be told, once, at the end. Kept here
# rather than in the polisher's prompt because it is a fact about the transport,
# not a matter of style — a model may not rephrase or omit it.
_PUSH_GUARD = (
    "Yazdığın metin owner'a otomatik push olarak iletilir — "
    "send_telegram_message ÇAĞIRMA."
)


class TemplateError(ValueError):
    """A template spec that cannot produce a working automation. Owner-readable."""


def output_mode(template: str) -> str:
    """Which output mode this template's trigger fires with.

    `proactive_ask` is silent on purpose — see the module docstring. This is the
    one place that mapping is written down; the composer reads it rather than
    hardcoding "push" as it used to.
    """
    return "silent" if template == "proactive_ask" else "push"


def _slug(text: str, fallback: str) -> str:
    """A stable, ASCII reminder_id from the automation's name.

    Folded through `lexical.fold` rather than by hand: it translates BEFORE
    lowercasing, which is the only way `İ` survives. Python's `.lower()` turns
    it into 'i' plus a combining dot, and a slug built after that reads
    "aksam_i_lac" — the same trap Rule 20 documents for the search index, and
    there is no reason for this module to fall into it separately.
    """
    from app.services.lexical import fold

    out = re.sub(r"[^a-z0-9]+", "_", fold(text)).strip("_")
    return (out or fallback)[:48]


def validate(spec: dict) -> None:
    """Refuse a template spec that would produce a broken automation, naming the
    field and the fix. Called before anything is sent to n8n."""
    template = spec.get("template")
    if template not in TEMPLATES:
        raise TemplateError(
            f"Template must be one of {', '.join(TEMPLATES)}, got {template!r}."
        )
    if not (spec.get("instruction") or spec.get("intent") or "").strip():
        raise TemplateError(
            "Say what this automation should do — the instruction is what the "
            "agent actually runs when it fires."
        )

    if template == "proactive_ask":
        options = spec.get("options")
        if not isinstance(options, (list, tuple)) or len(options) < 1:
            raise TemplateError(
                "A proactive reminder needs at least one answer button in "
                "'options' — without one the owner has no way to close it and "
                "it will keep asking until max_asks runs out."
            )
        if any(not str(o).strip() for o in options):
            raise TemplateError("Answer buttons cannot be blank.")
        every = int(spec.get("every_minutes") or 5)
        if every < 1:
            raise TemplateError("'every_minutes' must be at least 1.")
        asks = int(spec.get("max_asks") or 10)
        if asks < 1:
            raise TemplateError("'max_asks' must be at least 1.")

    if template == "hook_keyword":
        if not str(spec.get("url") or "").strip():
            raise TemplateError("A keyword watch needs a 'url' — the page to watch.")
        if not str(spec.get("look_for") or "").strip():
            raise TemplateError(
                "A keyword watch needs 'look_for' — the word or phrase to wait "
                "for. Watching for ANY change instead? Pick the address-watch "
                "template."
            )
    elif template == "hook_address":
        if not str(spec.get("url") or "").strip():
            raise TemplateError("An address watch needs a 'url' — the page to watch.")
    elif template == "hook_mail":
        if not (str(spec.get("domain") or "").strip() or str(spec.get("recipient") or "").strip()):
            raise TemplateError(
                "A mail watch needs a 'domain' (e.g. 'tdv.org') or a "
                "'recipient' address for a forwarded mailbox."
            )

    for flag in spec.get("day_flags") or []:
        if not isinstance(flag, dict) or not str(flag.get("label") or "").strip():
            raise TemplateError(
                "Every day flag needs a 'label' — it is the words the agent is "
                "told, e.g. 'gym günü'."
            )
        days = flag.get("days")
        if not isinstance(days, (list, tuple)) or not days:
            raise TemplateError(
                f"Day flag '{flag.get('label')}' needs at least one weekday, "
                "1 (Monday) to 7 (Sunday)."
            )
        for d in days:
            if not isinstance(d, int) or not 1 <= d <= 7:
                raise TemplateError(
                    f"Day flag '{flag.get('label')}' has an invalid weekday "
                    f"{d!r} — must be 1 (Monday) to 7 (Sunday)."
                )

    # No frequency restriction on 'reminder' — that is precisely the point of
    # this template over the old 'reminder_once': the owner picks 'once' for a
    # single date (it retires itself, see schedule.expiry_for) or any other
    # frequency for a plain recurring push, without switching templates.


def build_intent(spec: dict) -> str:
    """Assemble the instruction the agent will execute when this fires.

    Takes the owner's content half (polished or raw — see `intent_status` on the
    spec) and appends the mechanics this template cannot work without.
    """
    template = spec.get("template")
    body = (spec.get("instruction") or spec.get("intent") or "").strip()

    if template == "proactive_ask":
        return _ask_intent(spec, body)

    parts = [body]
    # Keyed off the SCHEDULE, not the template: any push automation firing on a
    # 'once' schedule has no tomorrow to correct a mistake in, and the date is
    # already fixed in the cron — restating it here keeps the agent from
    # re-deriving "today" and reminding him about the wrong day.
    schedule = spec.get("schedule") or {}
    if schedule.get("frequency") == "once":
        when = schedule.get("date")
        if when:
            parts.append(
                f"Bu tek seferlik bir hatırlatmadır ve bugün {when} tarihinde "
                "tetiklendi. Tarihi kendin hesaplama, yukarıdakini kullan."
            )
    parts.append(_PUSH_GUARD)
    return "\n\n".join(p for p in parts if p)


def _ask_intent(spec: dict, body: str) -> str:
    """The proactive-ask instruction: content, then the exact `reminders` call.

    The tool call is spelled out rather than described because its shape is what
    produces the answer buttons and the re-ask loop. A model asked to "send this
    as a reminder" will sometimes send a plain message instead, which looks
    identical in the transcript and silently loses the nagging that was the
    entire point.
    """
    options = [str(o).strip() for o in (spec.get("options") or []) if str(o).strip()]
    every = int(spec.get("every_minutes") or 5)
    asks = int(spec.get("max_asks") or 10)
    rid = spec.get("reminder_id") or _slug(spec.get("name") or "", "owner_ask")
    rendered = ", ".join(f"'{o}'" for o in options)

    return (
        f"{body}\n\n"
        "SONRA — ve bu kritik — yazdığın mesajı `reminders` aracıyla gönder:\n"
        f"  action='ask', reminder_id='{rid}', text=<yazdığın mesaj>,\n"
        f"  options=[{rendered}],\n"
        f"  every_minutes={every}, max_asks={asks}\n\n"
        "Bu araç mesajı butonlarla gönderir ve owner cevaplayana kadar "
        f"{every} dakikada bir TEKRAR sorar. send_telegram_message ÇAĞIRMA ve "
        "mesajı sadece cevap olarak yazma — tek gönderim yolu bu araçtır. "
        "Araç 'already_open' derse ikinci bir mesaj gönderme."
    )


def summarize(spec: dict) -> str:
    """One-line English summary for logs and the agent-facing tool. The owner's
    screen renders `describe()`/`hook_display()` structurally instead (see
    schedule.py and composer.py)."""
    template = spec.get("template") or spec.get("kind") or "automation"
    name = spec.get("name") or "automation"
    if template in HOOK_TEMPLATES:
        every = spec.get("interval_minutes") or (15 if template == "hook_mail" else 360)
        target = (
            (spec.get("domain") or spec.get("recipient"))
            if template == "hook_mail" else spec.get("url")
        )
        return f"{template} '{name}' — watching {target} every {every}m"
    when = sched.summarize(spec.get("schedule") or {})
    return f"{template} '{name}' — {when}"
