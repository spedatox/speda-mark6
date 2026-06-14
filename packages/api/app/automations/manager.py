"""
Automation manager — the one place automations are created, listed, toggled and
deleted. Both SPEDA's tool (skills/automations.py) and the Settings UI router
(routers/automations.py) call through here, so the two views can never drift.

Flow for create: validate + compose the spec into n8n workflow JSON → POST it
to n8n → activate it → persist the local Automation row mapping name/intent/
spec to the n8n workflow id. n8n stays the sole scheduler; the local row is
metadata for display and delivery context only.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automations import composer
from app.models.automation import Automation
from app.services.n8n_api import N8nClient

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dict(a: Automation) -> dict:
    return {
        "id": a.id,
        "agent_id": a.agent_id,
        "n8n_workflow_id": a.n8n_workflow_id,
        "name": a.name,
        "kind": a.kind,
        "intent": a.intent,
        "spec": json.loads(a.spec or "{}"),
        "active": a.active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "last_fired_at": a.last_fired_at.isoformat() if a.last_fired_at else None,
        "summary": composer.describe(json.loads(a.spec or "{}")),
    }


async def create_automation(spec: dict, db: AsyncSession, agent_id: str = "speda") -> dict:
    """Compose → push to n8n → activate → persist. Returns the automation dict,
    or raises ValueError with a actionable message (bad spec / n8n unreachable)
    that SPEDA can read and repair. agent_id is the creating agent — the watcher
    fires back through that agent's /trigger and is voiced by it."""
    # "track this for a month" → concrete expiry the gate node enforces.
    duration_days = spec.pop("duration_days", None)
    if duration_days and not spec.get("expires_at"):
        spec["expires_at"] = (_now() + timedelta(days=float(duration_days))).isoformat()

    workflow = composer.compose(spec, agent_id)  # raises ValueError on a bad spec

    n8n = N8nClient()
    if not n8n.configured:
        raise ValueError(
            "n8n is not configured (N8N_API_KEY missing). Open n8n → Settings → "
            "n8n API → create a key, then set N8N_API_KEY in the backend .env."
        )

    created = await n8n.create_workflow(workflow)
    if not created or not created.get("id"):
        raise ValueError(
            "n8n rejected the composed workflow or is unreachable — check the "
            "backend logs (n8n_request_failed) for the exact API error."
        )
    workflow_id = str(created["id"])

    activated = await n8n.set_active(workflow_id, True)
    if not activated:
        logger.warning("automation_created_inactive", extra={"workflow_id": workflow_id})

    row = Automation(
        user_id=1,
        agent_id=agent_id,
        n8n_workflow_id=workflow_id,
        name=spec.get("name") or workflow["name"],
        kind=spec["kind"],
        intent=spec.get("intent", ""),
        spec=json.dumps(spec),
        active=bool(activated),
        expires_at=(
            datetime.fromisoformat(spec["expires_at"]) if spec.get("expires_at") else None
        ),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "automation_created",
        extra={"automation_id": row.id, "workflow_id": workflow_id, "kind": row.kind},
    )
    return _as_dict(row)


async def list_automations(db: AsyncSession) -> list[dict]:
    """All automations, newest first. Lazily deactivates anything past its
    expiry (both locally and in n8n) so the list always tells the truth."""
    rows = (
        (await db.execute(select(Automation).order_by(Automation.created_at.desc())))
        .scalars().all()
    )
    n8n = N8nClient()
    dirty = False
    for a in rows:
        expires = a.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if a.active and expires and expires < _now():
            a.active = False
            dirty = True
            if a.n8n_workflow_id:
                await n8n.set_active(a.n8n_workflow_id, False)
            logger.info("automation_expired", extra={"automation_id": a.id})
    if dirty:
        await db.commit()
    return [_as_dict(a) for a in rows]


async def set_automation_active(automation_id: int, active: bool, db: AsyncSession) -> dict:
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")
    if row.n8n_workflow_id:
        ok = await N8nClient().set_active(row.n8n_workflow_id, active)
        if not ok:
            raise ValueError("n8n did not accept the change — is it running?")
    row.active = active
    await db.commit()
    return _as_dict(row)


async def delete_automation(automation_id: int, db: AsyncSession) -> dict:
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")
    if row.n8n_workflow_id:
        await N8nClient().delete_workflow(row.n8n_workflow_id)  # best-effort
    snapshot = _as_dict(row)
    await db.delete(row)
    await db.commit()
    logger.info("automation_deleted", extra={"automation_id": automation_id})
    return snapshot


async def mark_fired(automation_name: str, db: AsyncSession) -> None:
    """Stamp last_fired_at when a trigger arrives carrying this automation's
    name. Best-effort — a miss must never break delivery."""
    try:
        row = (
            (await db.execute(select(Automation).where(Automation.name == automation_name)))
            .scalars().first()
        )
        if row:
            row.last_fired_at = _now()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_fired_failed", extra={"automation": automation_name, "error": str(exc)})
