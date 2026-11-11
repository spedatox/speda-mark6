"""State-bound Telegram outbox. Unknown delivery is reconciled, never blindly resent."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import and_, case, select, update
from app.models.delivery import TelegramLink, TelegramOwner
from app.models.iacon import IaconFollowup, IaconRecord, IaconTelegramUpdate, IaconWork, IaconLink
from app.models.memory_serving import MemoryChange
from app.schemas.iacon import Command
from app.services.memory_store import capture_source

QUESTION_TAG = re.compile(r"\[Iacon:([a-f0-9]{32})\]")


class TelegramFollowups:
    def __init__(self, catalogue, budget, config, *, deliveries=None, transport=None):
        self.catalogue, self.sessions, self.budget, self.config = catalogue, catalogue.sessions, budget, config
        self.deliveries = deliveries
        self.http = httpx.AsyncClient(timeout=3, transport=transport, follow_redirects=False)

    async def close(self):
        await self.http.aclose()

    async def native_chat(self, db, user_id):
        if not self.deliveries or not self.deliveries.token("orion"):
            return None
        owner = await db.get(TelegramOwner, user_id)
        link = await db.get(TelegramLink, (user_id, "orion"))
        if owner and link and link.chat_id == owner.sender_id:
            configured = self.config.telegram_owner_id
            if not configured or configured == owner.sender_id:
                return link.chat_id
        return None

    @staticmethod
    def matches_native(event):
        if callback := event.get("callback_query"):
            return isinstance(callback, dict) and str(callback.get("data", "")).startswith("ia:")
        message = event.get("message")
        reply = message.get("reply_to_message") if isinstance(message, dict) else None
        return (isinstance(reply, dict) and reply.get("from", {}).get("is_bot") is True
                and bool(QUESTION_TAG.search(str(reply.get("text", "")))))

    async def resolve(self, user_id, identity, body):
        async with self.sessions() as db, db.begin():
            item = await db.get(IaconFollowup, identity)
            if not item or item.user_id != user_id or item.status != body.expected_status:
                raise ValueError("Follow-up status changed or item not found")
            if body.action == "confirmed_sent" and not body.message_id:
                raise ValueError("Confirmed delivery requires the Telegram message ID")
            values = {"status": {"mute": "muted", "snooze": "snoozed", "confirmed_not_sent": "scheduled",
                                 "confirmed_sent": "sent"}[body.action]}
            if body.action == "snooze":
                values["due_at"] = datetime.now(timezone.utc) + timedelta(days=7)
            if body.action == "confirmed_sent":
                values["message_id"] = body.message_id
            if body.action == "confirmed_not_sent":
                values["asked_at"] = None
            result = await db.execute(update(IaconFollowup).where(IaconFollowup.id == identity,
                IaconFollowup.status == body.expected_status).values(**values))
            if result.rowcount != 1:
                raise ValueError("Concurrent follow-up resolution")
            return {"id": identity, **values}

    async def send_due(self, context, *, now=None):
        now = now or datetime.now(timezone.utc)
        _, policy = await self.budget.policy(context.user_id)
        if policy.paused or not policy.telegram_enabled:
            return {"status": "disabled"}
        dedicated = bool(self.config.iacon_telegram_token and self.config.iacon_telegram_chat_id
                         and self.config.iacon_telegram_owner_id)
        hour = now.astimezone(ZoneInfo(context.timezone)).hour
        quiet = (policy.quiet_start <= hour < policy.quiet_end if policy.quiet_start < policy.quiet_end
                 else hour >= policy.quiet_start or hour < policy.quiet_end) if policy.quiet_start != policy.quiet_end else False
        if quiet:
            return {"status": "quiet_hours"}
        async with self.sessions() as db, db.begin():
            # Serialize daily notification admission across workers.
            from app.models.iacon import IaconControl
            await db.execute(update(IaconControl).where(IaconControl.user_id == context.user_id)
                             .values(version=IaconControl.version))
            control = await db.get(IaconControl, context.user_id)
            if not control or control.policy.get("paused") or not control.policy.get("telegram_enabled"):
                return {"status": "disabled"}
            chat_id = self.config.iacon_telegram_chat_id if dedicated else await self.native_chat(db, context.user_id)
            if not chat_id:
                return {"status": "unconfigured"}
            recent = (await db.execute(select(IaconFollowup.id).where(IaconFollowup.user_id == context.user_id,
                IaconFollowup.asked_at >= now - timedelta(hours=24)).limit(1))).scalar_one_or_none()
            if recent:
                return {"status": "cooldown"}
            payload = IaconRecord.payload
            high = payload["salience"].as_string() == "high"
            active = payload["status"].as_string() == "active"
            item = (await db.execute(select(IaconFollowup).join(IaconRecord, and_(
                IaconRecord.id == IaconFollowup.state_id, IaconRecord.user_id == IaconFollowup.user_id
            )).where(IaconFollowup.user_id == context.user_id,
                IaconFollowup.status.in_(["scheduled", "snoozed"]), IaconFollowup.due_at <= now
            ).order_by(case((and_(high, active), 0), (high, 1), else_=2),
                       case((high, payload["verified_on"].as_string())).desc().nulls_last(),
                       IaconFollowup.due_at, IaconFollowup.id).limit(1))).scalar_one_or_none()
            if not item:
                return {"status": "nothing_due"}
            state = await db.get(IaconRecord, item.state_id)
            if not state or state.version != item.state_version or state.status != "accepted" or state.payload.get("closed_on"):
                item.status = "superseded"
                return {"status": "superseded"}
            sources = {s.id: s for s in (await db.execute(select(MemoryChange).where(
                MemoryChange.user_id == context.user_id,
                MemoryChange.id.in_([e["source_id"] for e in state.evidence]),
                MemoryChange.revoked.is_(False)))).scalars()}
            scope = await self.catalogue.scope(db, context.user_id, state, sources)
            if len(sources) != len({e["source_id"] for e in state.evidence}) or not scope["eligible_for_context"]:
                item.status = "needs_review"
                return {"status": "awaiting_evidence_review"}
            pending_sources = (await db.execute(select(MemoryChange).where(MemoryChange.user_id == context.user_id,
                MemoryChange.source_role == "owner", MemoryChange.revoked.is_(False),
                MemoryChange.created_at > state.updated_at,
                MemoryChange.outcome.in_(["pending", "iacon_pending", "iacon_queued"])
            ).order_by(MemoryChange.id.desc()).limit(101))).scalars().all()
            from app.services.memory_runtime import query_terms
            from app.services.memory_store import fold
            terms = query_terms(state.title + " " + state.payload["summary"][:500])
            pending = len(pending_sources) > 100 or any(
                s.provenance.get("state_binding", {}).get("id") == state.id or any(
                    t in fold(s.content + " " + (s.provenance.get("prior_context") or {}).get("text", ""))
                    for t in terms) for s in pending_sources)
            reviews = (await db.execute(select(IaconWork.id).where(IaconWork.user_id == context.user_id,
                IaconWork.kind == "review", IaconWork.status.in_(["pending", "running", "held"]),
                IaconWork.payload["command"]["record_id"].as_string() == state.id
            ).limit(1))).scalar_one_or_none()
            if pending or reviews:
                return {"status": "awaiting_new_evidence_review"}
            claimed = await db.execute(update(IaconFollowup).where(IaconFollowup.id == item.id,
                IaconFollowup.status.in_(["scheduled", "snoozed"])).values(status="sending",
                asked_at=now, attempts=IaconFollowup.attempts + 1))
            if claimed.rowcount != 1:
                return {"status": "busy"}
            identity, question, title = item.id, item.question, state.title
        buttons = [[{"text": label, "callback_data": f"ia:{identity}:{action}"} for label, action in row] for row in [
            [("Completed", "done"), ("Still open", "wait"), ("Cancelled", "cancel")],
            [("Ask in a week", "snooze"), ("Stop asking", "mute")]]]
        try:
            payload = {"chat_id": chat_id, "text": f"{title}\n{question}\n[Iacon:{identity}]",
                       "reply_markup": {"inline_keyboard": buttons}}
            if dedicated:
                response = await self.http.post(
                    f"https://api.telegram.org/bot{self.config.iacon_telegram_token}/sendMessage", json=payload)
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    raise ValueError("Telegram rejected delivery")
                message_id = data["result"]["message_id"]
            else:
                message_id = (await self.deliveries.telegram("orion", "sendMessage", payload))["message_id"]
        except Exception:
            async with self.sessions() as db, db.begin():
                await db.execute(update(IaconFollowup).where(IaconFollowup.id == identity,
                    IaconFollowup.status == "sending").values(status="unknown"))
            return {"status": "delivery_unknown", "id": identity}
        async with self.sessions() as db, db.begin():
            await db.execute(update(IaconFollowup).where(IaconFollowup.id == identity,
                IaconFollowup.status == "sending").values(status="sent", message_id=message_id))
        return {"status": "sent", "id": identity}

    async def receive(self, context, event, *, native=False):
        update_id = event.get("update_id")
        callback = event.get("callback_query")
        message = (callback or {}).get("message") or event.get("message", {})
        sender = (callback or message).get("from", {}).get("id")
        chat = message.get("chat", {}).get("id")
        if type(update_id) is not int or not 0 <= update_id <= 2**31 - 1:
            raise ValueError("Invalid Telegram update identity")
        if not native and (not self.config.iacon_telegram_owner_id
                or sender != self.config.iacon_telegram_owner_id or chat != self.config.iacon_telegram_chat_id):
            raise ValueError("Update does not belong to the configured Telegram owner and chat")
        if native:
            # Dedicated and Orion bots have independent Telegram update counters.
            update_id = -1 - update_id
            if message.get("forward_origin") or message.get("sender_chat"):
                raise ValueError("Send a direct owner reply to the Orion question")
        identity, action = None, None
        text = message.get("text", "")
        if callback:
            match = re.fullmatch(r"ia:([a-f0-9]{32}):(done|wait|cancel|snooze|mute)", callback.get("data", ""))
            if match:
                identity, action = match.groups()
        else:
            reply = message.get("reply_to_message", {})
            match = QUESTION_TAG.search(reply.get("text", ""))
            identity = match.group(1) if match else None
        if not identity or len(text) > 8000:
            raise ValueError("Reply must reference a specific Iacon question")
        async with self.sessions() as db, db.begin():
            if native:
                linked = await self.native_chat(db, context.user_id)
                if not linked or sender != linked or chat != linked or message.get("chat", {}).get("type") != "private":
                    raise ValueError("Update does not belong to the linked Orion owner and chat")
            # Serialize webhook duplicate detection and state mutation.
            await db.execute(update(IaconFollowup).where(IaconFollowup.id == identity,
                IaconFollowup.user_id == context.user_id).values(attempts=IaconFollowup.attempts))
            prior = await db.get(IaconTelegramUpdate, update_id)
            if prior:
                if prior.user_id != context.user_id:
                    raise ValueError("Update belongs to another owner")
                return prior.result
            item = await db.get(IaconFollowup, identity)
            if not item or item.user_id != context.user_id:
                raise ValueError("Unknown follow-up")
            if native and item.message_id is not None:
                original = message if callback else reply
                if original.get("message_id") != item.message_id:
                    raise ValueError("Reply does not belong to the recorded Telegram message")
            state = await db.get(IaconRecord, item.state_id)
            if state.version != item.state_version or item.status not in {"sent", "unknown", "sending", "reply_pending", "needs_review"}:
                result = {"status": "stale_reply", "state_id": item.state_id}
            elif action in {"snooze", "mute"}:
                item.status = "snoozed" if action == "snooze" else "muted"
                item.due_at = datetime.now(timezone.utc) + timedelta(days=7)
                result = {"status": item.status, "state_id": state.id}
            else:
                today = datetime.now(ZoneInfo(context.timezone)).date()
                content = (f"Reply to state {state.id} version {state.version}: " +
                    {"done": "Completed", "cancel": "Cancelled", "wait": "Still open"}[action]) if action else text
                source = await capture_source(db, user_id=context.user_id, source_key=f"telegram:{update_id}",
                    content=content, provenance={"state_binding": {"id": state.id, "version": state.version},
                        "prior_context": {"text": item.question}, "telegram_update": update_id})
                item.status = "answered" if action else "reply_pending"
                if action:
                    review_days = 1 if action == "wait" and state.payload.get("salience") == "high" else 7
                    payload = {**state.payload, "verified_on": today.isoformat(),
                        "review_on": (today + timedelta(days=review_days)).isoformat(),
                        "status": {"done": "completed", "cancel": "cancelled", "wait": state.payload["status"]}[action],
                        "closed_on": today.isoformat() if action in {"done", "cancel"} else None}
                    command = Command(command_id=f"telegram:{update_id}", record_id=state.id,
                        expected_version=state.version, kind="state", title=state.title, payload=payload,
                        evidence=[{"source_id": source.id, "start": 0, "end": len(content), "quote": content}],
                        links=[{"target_id": l.target_id, "relation": l.relation} for l in (
                            await db.execute(select(IaconLink).where(IaconLink.source_id == state.id))).scalars()])
                    result = await self.catalogue.apply(context.user_id, command, actor="telegram_owner", db=db)
                    source.outcome = "iacon_interpreted"
                else:
                    result = {"status": "pending_review", "source_id": source.id, "state_id": state.id}
            db.add(IaconTelegramUpdate(update_id=update_id, user_id=context.user_id, result=result))
            return result
