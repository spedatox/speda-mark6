# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The intent polisher — turns the owner's one-line wish into the executable
instruction the agent will actually run when the automation fires.

The owner types "her sabah bana finans durumumu özetle" into a box. What has to
be stored is what the hand-tuned briefings carry (`scripts/briefings_seed.json`):
named tools, an explicit collection phase, a shape for the output, and the
anti-fabrication rules that keep a briefing from inventing a rent figure nobody
gave it. Nobody is going to type that by hand every time, and a vague intent
produces a vague briefing.

Three properties this is built around:

  · IT NEVER BLOCKS A CREATE. The automation is live with the owner's own words
    the moment he hits save (Rule 7 — post-turn work is a durable job, never
    inline). This upgrades the wording afterwards and republishes the workflow.
  · IT NEVER LOSES HIS WORDS. `instruction_raw` is kept forever, so the editor
    can show him what he actually asked for and a bad polish can be redone.
  · A FAILURE IS NOT A BROKEN AUTOMATION. If the provider is down or the reply
    is unusable, the status goes to `failed` and the raw instruction keeps
    running. A plainly worded briefing beats no briefing.

One job polishes every raw automation in one pass, which is why the queue's
dedupe-by-kind is correct here rather than a limitation: a second enqueue while
one is pending would only do the same sweep twice (`task_queue.enqueue_one`).

Model: whatever the caller resolved from the owning agent's profile and put on
the job. This module names no model (Rule 10) and skips the work entirely if it
was given none.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.automation import Automation

logger = logging.getLogger(__name__)

# Long enough for a full collection-phase instruction, short enough that a model
# which starts writing the briefing itself gets cut off rather than stored.
_MAX_TOKENS = 1200

# Past this the reply is not an instruction, it is an essay — and it would be
# prepended to every firing of this automation forever.
_MAX_CHARS = 4000

_SYSTEM = """You turn a short wish into an EXECUTABLE instruction that an AI agent will run by itself, on a schedule, with no human present.

The agent reading your output is the same agent that will run it. Write in the second person, as an instruction to itself.

LANGUAGE — read this twice. The owner's wish below is written in one language. EVERY word you write — section labels, rules, the instruction itself — must be in that SAME language. Do not write section headers like "COLLECTION" or "OUTPUT" in English when the wish is in Turkish or any other language: translate them too ("TOPLAMA", "ÇIKTI", or whatever that language would naturally use). A bilingual instruction, half in English and half in the owner's language, is a wrong answer even if every sentence in it is individually correct.

GENDER — never assume the owner's gender. Refer to "the owner" (or that word's exact equivalent in the wish's language) rather than a gendered pronoun. If the wish's language has no grammatical gender (Turkish, for instance — "o" covers everyone), this is automatic and needs no extra care; if you are writing in a language that does (English "he/she"), do not guess — write "the owner" or a neutral form instead.

Your output must have two phases:

1. COLLECTION — which tools to call, with what arguments, in what order. Name real tools. If the wish implies data (mail, calendar, news, health, spending, system state), the instruction must say to go and fetch it. State that these steps are internal and must not be narrated to the owner.

2. OUTPUT — what to write. Prose, not a dashboard of headings and bullet lists. Say how long. Say what to lead with.

Always include, adapted to the subject:
- Report the exception, not the routine. If nothing is unusual, say so in one sentence and stop.
- Never state a number without what it means or what it is normally measured against.
- Never claim an action that was not actually taken this turn. Offer instead.
- Never invent data a tool did not return. If a source came back empty, one plain sentence at the end — never a section about it.
- Never narrate which tools ran, which returned nothing, or what happens next.

Rules for you:
- Output ONLY the instruction. No preamble, no explanation, no markdown fence.
- Do NOT write the briefing itself. You are writing the instructions for it.
- Do NOT mention how the message is delivered, and never mention send_telegram_message or any reminders tool — the delivery mechanics are appended separately and yours would conflict with them.
- Keep it under 300 words."""


def _prompt(spec: dict, agent_name: str) -> str:
    template = spec.get("template")
    kind = {
        "briefing": "a recurring briefing the owner reads on a schedule",
        "reminder": (
            "a plain reminder message — not a data-gathering briefing, just a "
            "direct nudge. It may fire once or repeatedly; its schedule is "
            "given below, so do not invent a different one"
        ),
        "proactive_ask": (
            "a recurring reminder that will be delivered with answer buttons and "
            "will keep re-asking until the owner answers. Write only the message "
            "content and what it should say — the sending mechanism is added "
            "separately, so do not describe it"
        ),
        "hook_keyword": (
            "a watcher that fires the FIRST time a specific word or phrase "
            "appears on a web page. The page's text is already in the payload "
            "when this runs — write what to do with it; do not describe "
            "fetching or re-checking the page, that already happened"
        ),
        "hook_address": (
            "a watcher that fires whenever a specific web page changes AT "
            "ALL, not for any particular word. The changed page's text is "
            "already in the payload when this runs — write what to do with "
            "it; do not describe fetching the page"
        ),
        "hook_mail": (
            "a watcher that fires when mail arrives from a specific sender "
            "domain or to a specific address. The sender, subject and body of "
            "the mail are already in the payload when this runs — do NOT call "
            "Gmail or load a toolset, the mail is already there; read it and "
            "act on it"
        ),
    }.get(template, "a scheduled automation")

    # A voice automation's reply is CONVERTED TO AUDIO and sent as a Telegram
    # voice message (composer.py's `voice: true`, core/trigger_runner.py's
    # _deliver_voice) — never shown as text. Reading tolerates the density a
    # text briefing already uses; listening does not, and a reference number
    # read aloud is actively unpleasant regardless of length. This is why the
    # instruction gets a SEPARATE, stricter brief instead of relying on the
    # general "say how long" guidance in _SYSTEM's OUTPUT phase to catch it.
    voice_block = ""
    if spec.get("voice"):
        voice_block = (
            "\n\nTHIS AUTOMATION IS SPOKEN, NOT READ — its reply is converted to "
            "audio through ElevenLabs and sent as a voice message, never shown "
            "as text. Two real constraints follow from that, not just style:\n"
            "- ElevenLabs bills PER CHARACTER — every word is a real cost against "
            "a finite quota, every firing, forever. State the OUTPUT phase's "
            "target as 80–120 words, not the usual 150–300, and do not pad to "
            "reach even that: say the thing once, stop. Shorter than the target "
            "is a better outcome than hitting it with filler.\n"
            "- Never read a reference number, ticket code, PNR, or long ID "
            "aloud verbatim unless the owner must act on that EXACT string "
            "right now. Name the thing plainly instead (\"the bus ticket "
            "reminder\", \"the exemption application\") rather than spelling "
            "out a code — if he needs the code itself, say it is in the app, "
            "don't recite it.\n"
            "- Plain, simple language, whatever the target language is: common "
            "words over rare ones, short sentences, one idea each. No "
            "parenthetical asides, no stacked clauses, no idioms that only "
            "land in writing — this is followed BY EAR, in real time, with no "
            "way to re-read a sentence that ran too long.\n"
            "- Numbers and units are read aloud automatically (a time range "
            "like 08:00–13:00, a temperature like 26.5°C, a percentage like "
            "~44% all come out as words on their own) — write them as you "
            "normally would, do not spell them out yourself.\n"
            "- Stay in ONE language for the whole reply. A name, place or "
            "company that belongs to the OTHER language is fine to say once, "
            "plainly — but do not build a sentence that switches language "
            "mid-way for anything else (a common noun, a phrase, a unit); "
            "that reads as two different people talking, not one voice."
        )

    return (
        f"The agent is {agent_name}. This automation is {kind}.\n"
        f"It runs: {spec.get('_when', 'on a schedule')}."
        f"{voice_block}\n\n"
        f"The owner asked for:\n{spec.get('instruction_raw') or spec.get('instruction')}\n\n"
        "Write the instruction."
    )


async def polish_pending(request_id: str = "", model: str = "") -> int:
    """Upgrade every automation still carrying a raw instruction. Returns how
    many were rewritten. Never raises — the queue records a failure, but a
    failure here must not mark the whole drain bad for the other handlers."""
    if not model:
        logger.info("automation_polish_skipped", extra={"reason": "no model on job"})
        return 0

    from app.automations import manager, schedule as sched, templates
    from app.services.llm_client import LLMClient

    polished = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Automation))).scalars().all()
        pending = []
        for row in rows:
            try:
                spec = json.loads(row.spec or "{}")
            except ValueError:
                continue
            if spec.get("template") and spec.get("intent_status") == "raw":
                pending.append((row, spec))

        if not pending:
            return 0

        client = LLMClient()
        for row, spec in pending:
            # A Hook has no clock — it fires on an event, not "at" a time — so
            # sched.summarize() would just say "on a schedule" and confuse a
            # model already told exactly what event triggers it above.
            spec["_when"] = (
                "whenever the watched event happens (see above)"
                if spec.get("template") in templates.HOOK_TEMPLATES
                else sched.summarize(spec.get("schedule") or {})
            )
            try:
                response = await client.create_message(
                    model=model,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": _prompt(spec, row.agent_id)}],
                    # Same shape as the title job: reasoning models otherwise
                    # spend the whole budget thinking and return nothing, which
                    # here would silently mark every automation 'failed'. This
                    # is a rewriting task, not a reasoning one.
                    max_tokens=_MAX_TOKENS,
                    reasoning_effort="minimal",
                )
                text = _text_of(response).strip()
            except Exception as exc:  # noqa: BLE001 — provider down is not a broken automation
                logger.warning(
                    "automation_polish_failed",
                    extra={"automation_id": row.id, "error": str(exc)},
                )
                spec.pop("_when", None)
                spec["intent_status"] = "failed"
                row.spec = json.dumps(spec)
                continue

            spec.pop("_when", None)
            if not text or len(text) > _MAX_CHARS:
                # An empty reply (a reasoning model on a tight budget) or a
                # runaway one. The owner's wording already works; leaving it
                # alone is the correct outcome, not a retry loop.
                logger.warning(
                    "automation_polish_unusable",
                    extra={"automation_id": row.id, "chars": len(text)},
                )
                spec["intent_status"] = "failed"
                row.spec = json.dumps(spec)
                continue

            spec["instruction"] = text
            spec["intent_status"] = "polished"
            row.spec = json.dumps(spec)
            row.intent = templates.build_intent(spec)

            # The instruction is baked into the workflow's callback body at
            # compose time, so the polish is invisible to the thing that
            # actually fires until the workflow is republished. Skipping this
            # would leave a row that reads "polished" and an n8n workflow still
            # sending the raw sentence — the worst of both.
            if row.n8n_workflow_id:
                try:
                    await manager.push_to_n8n(spec, row.agent_id, row.n8n_workflow_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "automation_polish_republish_failed",
                        extra={"automation_id": row.id, "error": str(exc)},
                    )
                    spec["intent_status"] = "raw"  # try again on the next drain
                    row.spec = json.dumps(spec)
                    continue
            polished += 1

        await db.commit()

    logger.info("automation_polish", extra={"request_id": request_id, "polished": polished})
    return polished


def _text_of(response) -> str:
    """Pull the plain text out of a completion, provider-agnostically — the
    same shape-tolerant read the other background jobs do."""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "".join(parts)
    return ""
