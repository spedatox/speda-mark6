# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Onyx — the owner's internal client ticketing and task platform.

Onyx is a lightweight internal ticketing and request-management system the owner
uses to track client work (e-commerce, web development, graphic design, video,
technical support, and automations for clients like Arel Tarım, Kara Makine, etc.).

This skill talks to Onyx's `/api/v1/speda` API via Bearer token authentication,
letting agents (principally Ultron and Speda) inspect open work orders, update
progress, post comments, and resolve tickets.
"""

import logging
from typing import Any

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _headers() -> dict[str, str]:
    token = settings.onyx_api_token
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


class OnyxSkill(Skill):
    name = "onyx"
    deferred = True
    search_keywords = (
        "onyx ticket tickets task work client arel tarim order issue bug support "
        "request e-commerce dev"
    )
    read_only = False
    requires_network = True

    description = (
        "Interacts with Onyx, the owner's internal ticketing and task-management platform "
        "for commercial client deliverables (e-commerce, web development, graphic design, "
        "video, and automations for clients like Arel Tarım). Use this tool to list active "
        "tickets, inspect ticket details and discussion history, update ticket status "
        "(OPEN, IN_PROGRESS, WAITING, COMPLETED, CLOSED, CANCELLED), post comments or notes, "
        "and complete tickets with a resolution summary. Do NOT use this tool for academic "
        "coursework or university tasks (use the academic/outlook tools for those). Returns "
        "structured ticket records including requester, organization, priority, and deadlines."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "update_status", "add_comment", "complete", "delete"],
                "description": (
                    "'list' finds tickets by status; 'get' reads a specific ticket with discussion; "
                    "'update_status' moves a ticket; 'add_comment' posts a comment or progress note; "
                    "'complete' marks a ticket done with a summary; 'delete' permanently deletes a ticket."
                ),
            },
            "ticket_number": {
                "type": "integer",
                "description": "The ticket number, e.g. 1002. Required for get, update_status, add_comment, complete, delete.",
            },
            "status": {
                "type": "string",
                "enum": ["OPEN", "IN_PROGRESS", "WAITING", "COMPLETED", "CLOSED", "CANCELLED"],
                "description": "Filter status for 'list', or target status for 'update_status'.",
            },
            "content": {
                "type": "string",
                "description": "Comment or note body for 'add_comment'.",
            },
            "completion_summary": {
                "type": "string",
                "description": "Summary of what was done to resolve the ticket for 'complete'.",
            },
            "completion_url": {
                "type": "string",
                "description": "Optional deliverable URL (e.g. deployed link, asset link) for 'complete'.",
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict[str, Any], context: AgentContext) -> str:
        action = args.get("action")
        base_url = settings.onyx_api_url.rstrip("/")
        endpoint = f"{base_url}/api/v1/speda"

        if not settings.onyx_api_token:
            return "Onyx API token is not configured in settings (ONYX_API_TOKEN)."

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                if action == "list":
                    params: dict[str, Any] = {}
                    if status := args.get("status"):
                        params["status"] = status
                    resp = await client.get(endpoint, headers=_headers(), params=params)
                    if resp.status_code == 401:
                        return "Onyx API authentication failed: invalid or expired bearer token."
                    resp.raise_for_status()
                    data = resp.json()
                    tickets = data.get("tickets", [])
                    if not tickets:
                        return f"No tickets found{f' with status {status}' if status else ''}."
                    lines = [f"Found {len(tickets)} Onyx ticket(s):"]
                    for t in tickets:
                        num = t.get("ticket_number") or t.get("id")
                        title = t.get("title", "Untitled")
                        st = t.get("status", "UNKNOWN")
                        org = t.get("organization", "N/A")
                        req = t.get("requester", "N/A")
                        pri = t.get("priority", "NORMAL")
                        lines.append(f"- #{num} [{st}] ({pri}) {title} | Client: {org} (by {req})")
                    return "\n".join(lines)

                elif action == "get":
                    ticket_number = args.get("ticket_number")
                    if not ticket_number:
                        return "Action 'get' requires 'ticket_number'."
                    resp = await client.get(
                        endpoint, headers=_headers(), params={"ticket_number": ticket_number}
                    )
                    if resp.status_code == 404:
                        return f"Ticket #{ticket_number} not found in Onyx."
                    resp.raise_for_status()
                    data = resp.json()
                    t = data.get("ticket", {})
                    num = t.get("ticketNumber", ticket_number)
                    title = t.get("title", "")
                    desc = t.get("description", "")
                    st = t.get("status", "")
                    pri = t.get("priority", "")
                    cat = t.get("category", "")
                    org_data = t.get("organization") or {}
                    org = org_data.get("name") if isinstance(org_data, dict) else str(org_data)
                    req_data = t.get("createdBy") or {}
                    req = req_data.get("fullName") if isinstance(req_data, dict) else str(req_data)
                    target = t.get("targetDate") or "none"
                    summary = t.get("completionSummary") or ""

                    comments = t.get("comments") or []
                    c_lines = []
                    for c in comments:
                        u = c.get("user") or {}
                        author = u.get("fullName") if isinstance(u, dict) else "User"
                        c_lines.append(f"  * {author}: {c.get('content', '')}")

                    lines = [
                        f"Onyx Ticket #{num}: {title}",
                        f"Status: {st} | Priority: {pri} | Category: {cat}",
                        f"Client: {org} | Requester: {req}",
                        f"Target Date: {target}",
                        f"Description: {desc}",
                    ]
                    if summary:
                        lines.append(f"Completion Summary: {summary}")
                    if c_lines:
                        lines.append("Comments:")
                        lines.extend(c_lines)
                    return "\n".join(lines)

                elif action == "update_status":
                    ticket_number = args.get("ticket_number")
                    status = args.get("status")
                    if not ticket_number or not status:
                        return "Action 'update_status' requires both 'ticket_number' and 'status'."
                    resp = await client.post(
                        endpoint,
                        headers=_headers(),
                        json={
                            "action": "update_status",
                            "ticket_number": ticket_number,
                            "status": status,
                        },
                    )
                    if resp.status_code == 404:
                        return f"Ticket #{ticket_number} not found."
                    resp.raise_for_status()
                    return f"Ticket #{ticket_number} status updated to {status}."

                elif action == "add_comment":
                    ticket_number = args.get("ticket_number")
                    content = args.get("content")
                    if not ticket_number or not content:
                        return "Action 'add_comment' requires 'ticket_number' and 'content'."
                    resp = await client.post(
                        endpoint,
                        headers=_headers(),
                        json={
                            "action": "add_comment",
                            "ticket_number": ticket_number,
                            "content": content,
                        },
                    )
                    if resp.status_code == 404:
                        return f"Ticket #{ticket_number} not found."
                    resp.raise_for_status()
                    return f"Comment added to ticket #{ticket_number}."

                elif action == "complete":
                    ticket_number = args.get("ticket_number")
                    summary = args.get("completion_summary") or args.get("content") or "Completed."
                    if not ticket_number:
                        return "Action 'complete' requires 'ticket_number'."
                    body: dict[str, Any] = {
                        "action": "complete_ticket",
                        "ticket_number": ticket_number,
                        "completion_summary": summary,
                    }
                    if url := args.get("completion_url"):
                        body["completion_url"] = url
                    resp = await client.post(endpoint, headers=_headers(), json=body)
                    if resp.status_code == 404:
                        return f"Ticket #{ticket_number} not found."
                    resp.raise_for_status()
                    return f"Ticket #{ticket_number} marked as COMPLETED. Summary: {summary}"

                elif action == "delete":
                    ticket_number = args.get("ticket_number")
                    if not ticket_number:
                        return "Action 'delete' requires 'ticket_number'."
                    resp = await client.post(
                        endpoint,
                        headers=_headers(),
                        json={
                            "action": "delete_ticket",
                            "ticket_number": ticket_number,
                        },
                    )
                    if resp.status_code == 404:
                        return f"Ticket #{ticket_number} not found."
                    resp.raise_for_status()
                    return f"Ticket #{ticket_number} deleted successfully from Onyx."

                else:
                    return f"Unknown action: {action!r}."

        except httpx.ConnectError:
            logger.warning("onyx_unreachable", extra={"url": base_url})
            hint = ""
            if "localhost" in base_url or "127.0.0.1" in base_url:
                hint = " If Speda is running inside Docker on your VPS, 'localhost' refers to the container; set ONYX_API_URL to http://host.docker.internal:3000 (or the container name / public URL) in packages/igor/.env."
            return f"Could not connect to Onyx at {base_url} — the service is unreachable or not running.{hint}"
        except httpx.HTTPStatusError as exc:
            logger.error("onyx_api_error", extra={"status": exc.response.status_code, "detail": exc.response.text[:200]})
            return f"Onyx API returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:
            logger.error("onyx_skill_error", extra={"error": str(exc)})
            return f"Onyx error: {exc}"
