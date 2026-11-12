"""Bounded diagnostic context for owner review and incident assessment."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings

_identifier = re.compile(r"[A-Za-z][A-Za-z0-9_.:/ -]{0,95}")
_url = re.compile(r"https?://[^\s<>'\"\x60]+", re.I)
_secret = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[^\s,;]+|"
    r"\b(?:[\w.-]*(?:token|secret|password|api[_-]?key)|authorization|cookie)\b"
    r"\s*['\"]?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)|"
    r"\b\d{6,15}:[A-Za-z0-9_-]{20,}\b|\b[A-Za-z0-9_+/=-]{36,}\b|"
    r"\b[^\s<>]+@[^\s<>]+\.[^\s<>]+"
)
_static_routes = {"/config", "/chat", "/health", "/health/ready", "/health/live", "/n8n", "/models", "/iacon/drain"}

_guide = {
    "security_ssh_brute_force": ("SSH authentication failures crossed the watchdog threshold.",
                                 "Compare failed attempts and successful SSH logins around the observation time."),
    "security_http_brute_force": ("HTTP credential rejections crossed the watchdog threshold.",
                                  "Check whether the rejected requests match client configuration changes or repeated credential guesses."),
    "security_http_flood": ("Caddy logged a high volume of HTTP responses.",
                            "Compare origin request rates, response health, and provider traffic metrics."),
    "security_ingress_bps": ("The public interface received an unusually high volume of traffic.",
                             "Compare interface utilization, provider traffic metrics, and origin reachability."),
    "security_ingress_pps": ("The public interface received an unusually high packet rate.",
                             "Compare provider packet rates and host packet drops around the observation time."),
    "security_ssh_visibility": ("SSH journal coverage was incomplete.",
                                 "Check the SSH unit, journal access and log rate limits."),
    "security_http_visibility": ("Caddy access log coverage was incomplete.",
                                  "Check the access logger, file permissions, rotation, and request rates."),
    "security_network_visibility": ("Public interface counter reads failed.",
                                     "Verify the interface name and that its counters are readable by the watchdog."),
    "auth_rejected": ("SPEDA rejected an HTTP request without a valid application credential.",
                      "If this coincided with your own request, check the client connection settings."),
    "n8n_auth_rejected": ("SPEDA rejected a scheduler callback because its authentication check failed.",
                          "Check the n8n execution and its SPEDA credential configuration."),
    "news_feed_failed": ("An RSS feed could not be fetched during the news collection pass.",
                         "Check the feed host and HTTP status before retrying the collection."),
    "provider_model_catalog_unavailable": ("SPEDA could not refresh a provider's list of available models.",
                                           "Check the provider connection and its model catalog endpoint."),
    "mcp_skip": ("An optional MCP integration was skipped during startup.",
                 "Check the integration settings if this connector was expected to be available."),
    "startup_adapters_degraded": ("One or more adapter readiness checks failed during startup.",
                                  "Inspect the named adapter and its service health."),
    "service_unexpected_exit": ("Docker recorded a SPEDA service exiting unexpectedly.",
                                "Check the exit code, container logs and whether the service recovered."),
    "service_unhealthy": ("Docker marked a SPEDA service unhealthy after a health check.",
                          "Inspect the container health check output and its latest startup logs."),
    "service_out_of_memory": ("Docker reported an out-of-memory event for the service.",
                              "Check the container memory limit and usage around the incident time."),
    "tool_stopped_turn": ("A tool error ended the turn before the remaining steps could run.",
                          "Check the tool receipt before retrying an operation with uncertain effects."),
    "tool_returned_error": ("A tool returned a failure while the agent was able to continue.",
                            "Review the tool and its recorded error before relying on that result."),
    "turn_error": ("An error ended the agent's turn.",
                   "Check the error code and the last model or tool receipt for the turn."),
    "turn_exception": ("An unhandled exception ended the agent's turn.",
                       "Use the stack locations and request ID to locate the failing operation."),
    "turn_interrupted": ("The turn closed before reporting a terminal result.",
                         "Check whether the caller disconnected or the execution was cancelled."),
    "safety_guard_triggered": ("The turn reached its tool-iteration limit.",
                               "Inspect repeated tool calls and whether they made progress."),
    "coordination_execution_stopped": ("The coordinator stopped an execution after an error.",
                                       "Open the affected run and check the last completed action and any unsettled receipts."),
    "agent_observed_incident": ("An agent reported an unexpected result during its work.",
                                "Review the affected turn and the agent's recorded observation."),
    "backend_stopped_with_active_execution": ("The backend restarted with an execution still marked active.",
                                              "Check the saved tool and model receipts before resuming."),
}


def hostname(value):
    if not isinstance(value, str):
        return None
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return None
    return host if host and re.fullmatch(r"[a-zA-Z0-9.:-]{1,100}", host) else None


def clean_message(value, limit=650):
    if not isinstance(value, str):
        return None
    value = value[:4096]
    for name in type(settings).model_fields:
        if not any(part in name for part in ("token", "secret", "password", "api_key")):
            continue
        secret = getattr(settings, name, None)
        if hasattr(secret, "get_secret_value"):
            secret = secret.get_secret_value()
        if isinstance(secret, str) and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
            if secret.startswith("{"):
                try:
                    values = json.loads(secret)
                except ValueError:
                    values = None
                if isinstance(values, dict):
                    for item in values.values():
                        if isinstance(item, str) and len(item) >= 4:
                            value = value.replace(item, "[redacted]")
    value = _url.sub(lambda match: hostname(match[0]) or "[redacted URL]", value)
    value = _secret.sub("[redacted]", value)
    return " ".join(value.split())[:limit] or None


def log_details(event, fields):
    result = {}
    for key in ("provider", "server", "outlet", "error_type"):
        value = fields.get(key)
        if isinstance(value, str) and _identifier.fullmatch(value):
            cleaned = clean_message(value)
            if cleaned:
                result[key] = cleaned
    for key in ("http_status", "iterations", "attempt"):
        value = fields.get(key)
        if type(value) is int:
            result[key] = value
    if event in {"auth_rejected", "n8n_auth_rejected"}:
        method, route = fields.get("method"), fields.get("route") or fields.get("path")
        if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            result["method"] = method
        if isinstance(route, str) and route in _static_routes:
            result["endpoint"] = route
    if event == "mcp_skip":
        reason = fields.get("reason")
        if isinstance(reason, str) and re.fullmatch(r"[A-Z_]+ not set", reason):
            result["diagnostic_message"] = reason
    if event == "news_feed_failed":
        if host := hostname(fields.get("url")):
            result["feed_host"] = host
        error = fields.get("error")
        if "http_status" not in result and isinstance(error, str) and (
                match := re.match(r"(?:Client|Server) error '([45][0-9]{2})\b", error)):
            result["http_status"] = int(match[1])
    if event == "startup_adapters_degraded":
        adapters = fields.get("degraded")
        if isinstance(adapters, list):
            names = [item for item in adapters[:20] if isinstance(item, str) and _identifier.fullmatch(item)]
            if names:
                result["adapters"] = ", ".join(names)
    return result


def stack_locations(trace):
    frames = []
    while trace:
        frame = trace.tb_frame.f_code
        frames.append(f"{Path(frame.co_filename).name}:{trace.tb_lineno} in {frame.co_name}")
        trace = trace.tb_next
    return frames[-8:]


def explain(facts):
    event = facts.get("event", "runtime_log")
    summary, next_check = _guide.get(event, (
        f"SPEDA recorded {event.replace('_', ' ')}.",
        "Use the incident time, source and run or request ID to inspect the related logs.",
    ))
    return {**facts, "summary": facts.get("summary") or summary, "next_check": next_check}
