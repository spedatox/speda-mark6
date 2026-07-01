"""
OSINT / threat-intelligence skills (Tier 1).

A bundle of read-only lookups over public threat-intel and OSINT sources. Every
skill here is `read_only=True` (pure retrieval — enables parallel tool execution
per CLAUDE.md Rule 9) and `requires_network=True` (needs an uplink — filtered out
under the Dead Zone Protocol, where a local model calling them would only burn
context on guaranteed failures).

Sources & auth:
  - ip-api.com          geolocation           — keyless
  - AbuseIPDB           IP reputation         — free key (ABUSEIPDB_API_KEY)
  - URLhaus (abuse.ch)  malicious URLs        — keyless / abuse.ch Auth-Key
  - ThreatFox (abuse.ch) IOC search           — keyless / abuse.ch Auth-Key
  - MalwareBazaar (abuse.ch) malware samples  — keyless / abuse.ch Auth-Key
  - HIBP Pwned Passwords breach count         — keyless (k-anonymity)
  - Ahmia               dark-web (.onion) search — keyless

These are intelligence/lookup tools for authorized defensive security and
research. They never fetch or execute anything from the sources they report on —
they return metadata and reputation only.
"""

import asyncio
import hashlib
import html
import ipaddress
import logging
import re
import urllib.parse

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_UA = "SPEDA-Mark-VI/1.0 (OSINT skill)"
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)
# Ahmia's clearnet index is slow and a popular query returns a large (100s of KB)
# result page — give the read plenty of room so big responses don't time out.
_SLOW_TIMEOUT = httpx.Timeout(45.0, connect=8.0)


def _exc_msg(exc: Exception) -> str:
    """Human-readable error text. Timeouts (httpx.ReadTimeout) carry an EMPTY
    str(), which would render as 'failed ()' — fall back to the class name."""
    return str(exc) or type(exc).__name__


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


# ── ip-api.com — geolocation (keyless) ───────────────────────────────────────


class IPGeolocateSkill(Skill):
    name = "ip_geolocate"
    description = (
        "Geolocates an IPv4/IPv6 address or hostname using ip-api.com, returning "
        "country, region, city, coordinates, timezone, ISP, organization and the "
        "owning AS. Use it to place an IP on the map, identify the hosting provider "
        "or ISP behind an address, or enrich a log entry with location context. Do "
        "NOT use it to judge whether an IP is malicious — that is what ip_reputation "
        "(AbuseIPDB) is for; geolocation says where an address is, not whether it is "
        "hostile. Returns a plain-text summary of the location and network owner."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "IP address (v4 or v6) or hostname to geolocate, e.g. '8.8.8.8' or 'example.com'.",
            }
        },
        "required": ["target"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        target = str(args.get("target", "")).strip()
        if not target:
            return "ip_geolocate: no target provided."
        fields = "status,message,query,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,reverse,mobile,proxy,hosting"
        url = f"http://ip-api.com/json/{urllib.parse.quote(target)}?fields={fields}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                d = resp.json()
        except Exception as exc:
            logger.warning("ip_geolocate_failed", extra={"error": str(exc), "request_id": context.request_id})
            return f"ip_geolocate: lookup failed ({_exc_msg(exc)})."

        if d.get("status") != "success":
            return f"ip_geolocate: no result for '{target}' ({d.get('message', 'unknown error')})."

        flags = []
        if d.get("proxy"):
            flags.append("proxy/VPN")
        if d.get("hosting"):
            flags.append("hosting/datacenter")
        if d.get("mobile"):
            flags.append("mobile network")
        return (
            f"Geolocation for {d.get('query', target)}:\n"
            f"- Location: {d.get('city', '?')}, {d.get('regionName', '?')}, "
            f"{d.get('country', '?')} ({d.get('countryCode', '?')})\n"
            f"- Coordinates: {d.get('lat', '?')}, {d.get('lon', '?')}  ·  TZ: {d.get('timezone', '?')}\n"
            f"- Network: {d.get('isp', '?')} / {d.get('org', '?')}  ·  {d.get('as', '?')}\n"
            f"- Reverse DNS: {d.get('reverse') or 'n/a'}\n"
            f"- Flags: {', '.join(flags) if flags else 'none'}"
        )


# ── AbuseIPDB — IP reputation (free key) ─────────────────────────────────────


class IPReputationSkill(Skill):
    name = "ip_reputation"
    description = (
        "Checks an IP address against AbuseIPDB, returning its abuse-confidence "
        "score (0–100), how many times it has been reported, the categories/recency "
        "of those reports, and whether it is a known Tor exit node. Use it to triage "
        "a suspicious source IP from logs, a firewall alert, or a phishing header — "
        "it answers 'is this address known-bad?'. Do NOT use it for location or ISP "
        "lookups (use ip_geolocate) or for domains/URLs/file hashes (use urlhaus_lookup, "
        "threatfox_lookup or malwarebazaar_lookup). Returns a plain-text reputation "
        "summary; requires ABUSEIPDB_API_KEY to be configured."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "ip": {
                "type": "string",
                "description": "The IPv4 or IPv6 address to check, e.g. '118.25.6.39'.",
            },
            "max_age_days": {
                "type": "integer",
                "description": "Only consider reports from the last N days (1–365). Default 90.",
                "default": 90,
            },
        },
        "required": ["ip"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.abuseipdb_api_key:
            return (
                "ip_reputation: AbuseIPDB is not configured. Set ABUSEIPDB_API_KEY in "
                ".env (free key at https://www.abuseipdb.com/register). Meanwhile, "
                "ip_geolocate still works keyless for location/ISP context."
            )
        ip = str(args.get("ip", "")).strip()
        if not _looks_like_ip(ip):
            return f"ip_reputation: '{ip}' is not a valid IP address."
        max_age = max(1, min(365, int(args.get("max_age_days", 90) or 90)))
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.abuseipdb.com/api/v2/check",
                    params={"ipAddress": ip, "maxAgeInDays": max_age},
                    headers={"Key": settings.abuseipdb_api_key, "Accept": "application/json", "User-Agent": _UA},
                )
                if resp.status_code in (401, 403):
                    return "ip_reputation: AbuseIPDB rejected the key (401/403) — check ABUSEIPDB_API_KEY."
                if resp.status_code == 429:
                    return "ip_reputation: AbuseIPDB rate limit reached (free tier is 1,000/day). Try again later."
                resp.raise_for_status()
                d = resp.json().get("data", {})
        except Exception as exc:
            logger.warning("ip_reputation_failed", extra={"error": str(exc), "request_id": context.request_id})
            return f"ip_reputation: lookup failed ({_exc_msg(exc)})."

        score = d.get("abuseConfidenceScore", 0)
        verdict = "CLEAN" if score == 0 else "LOW" if score < 25 else "SUSPICIOUS" if score < 75 else "MALICIOUS"
        return (
            f"AbuseIPDB reputation for {d.get('ipAddress', ip)} — {verdict} "
            f"(confidence {score}/100):\n"
            f"- Total reports: {d.get('totalReports', 0)} from {d.get('numDistinctUsers', 0)} distinct reporters\n"
            f"- Last reported: {d.get('lastReportedAt') or 'never'}\n"
            f"- Tor exit node: {'yes' if d.get('isTor') else 'no'}  ·  Whitelisted: {'yes' if d.get('isWhitelisted') else 'no'}\n"
            f"- Domain/ISP: {d.get('domain') or '?'} / {d.get('isp') or '?'}  "
            f"({d.get('usageType') or 'unknown usage'})\n"
            f"- Country: {d.get('countryCode') or '?'}"
        )


# ── abuse.ch shared POST helper ──────────────────────────────────────────────


async def _abuse_ch_post(url: str, *, data: dict | None = None, json_body: dict | None = None) -> tuple[dict | None, str | None]:
    """POST to an abuse.ch API. abuse.ch now issues a free Auth-Key; send it when
    configured and still attempt keyless otherwise. Returns (json, error_msg)."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if settings.abuse_ch_api_key:
        headers["Auth-Key"] = settings.abuse_ch_api_key
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            resp = await client.post(url, data=data, json=json_body)
            if resp.status_code in (401, 403):
                body = resp.json() if resp.text else {}
                status = body.get("query_status", "")
                if "unknown_auth_key" in str(status).lower():
                    return None, (
                        "abuse.ch rejected the Auth-Key as unrecognized. The key format may be wrong "
                        "or not yet activated. Verify ABUSE_CH_API_KEY at https://auth.abuse.ch."
                    )
                return None, (
                    "abuse.ch requires a free Auth-Key now — register at "
                    "https://auth.abuse.ch and set ABUSE_CH_API_KEY in .env."
                )
            resp.raise_for_status()
            return resp.json(), None
    except Exception as exc:
        return None, _exc_msg(exc)


# ── URLhaus (abuse.ch) — malicious URL / host lookup ─────────────────────────


class URLhausLookupSkill(Skill):
    name = "urlhaus_lookup"
    description = (
        "Looks up a URL or hostname/domain in abuse.ch URLhaus, the malware-URL "
        "database, reporting whether it is a known malware-distribution URL, its "
        "threat type and status (online/offline), associated tags and payloads. Use "
        "it to check a suspicious link from an email, a domain seen in traffic, or an "
        "indicator during incident triage. Do NOT use it for IP reputation (use "
        "ip_reputation), file hashes (use malwarebazaar_lookup), or general IOC search "
        "across types (use threatfox_lookup). Returns a plain-text summary of any "
        "URLhaus records found, or a clear 'not listed' result."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "A full URL (http[s]://…) or a bare hostname/domain to look up.",
            }
        },
        "required": ["indicator"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        ind = str(args.get("indicator", "")).strip()
        if not ind:
            return "urlhaus_lookup: no indicator provided."
        is_url = ind.lower().startswith(("http://", "https://"))
        endpoint = "https://urlhaus-api.abuse.ch/v1/url/" if is_url else "https://urlhaus-api.abuse.ch/v1/host/"
        payload = {"url": ind} if is_url else {"host": ind}
        d, err = await _abuse_ch_post(endpoint, data=payload)
        if err:
            logger.warning("urlhaus_failed", extra={"error": err, "request_id": context.request_id})
            return f"urlhaus_lookup: lookup failed ({err})."

        status = d.get("query_status")
        if status in ("no_results", "http_error"):
            return f"urlhaus_lookup: '{ind}' is NOT listed in URLhaus (no known malicious records)."
        if status != "ok":
            return f"urlhaus_lookup: URLhaus returned status '{status}'."

        if is_url:
            tags = ", ".join(d.get("tags") or []) or "none"
            payloads = d.get("payloads") or []
            names = ", ".join(p.get("filename") or p.get("file_type", "?") for p in payloads[:3]) or "none"
            return (
                f"URLhaus MATCH for {ind}:\n"
                f"- Status: {d.get('url_status', '?')}  ·  Threat: {d.get('threat', '?')}\n"
                f"- Date added: {d.get('date_added', '?')}  ·  Reporter: {d.get('reporter', '?')}\n"
                f"- Tags: {tags}\n"
                f"- Payloads ({len(payloads)}): {names}\n"
                f"- Reference: {d.get('urlhaus_reference', 'n/a')}"
            )
        urls = d.get("urls") or []
        online = sum(1 for u in urls if u.get("url_status") == "online")
        lines = [
            f"URLhaus host MATCH for {ind}:",
            f"- Total malicious URLs on this host: {d.get('url_count', len(urls))} ({online} currently online)",
            f"- Blacklists: SURBL={d.get('blacklists', {}).get('surbl', '?')}, "
            f"Spamhaus DBL={d.get('blacklists', {}).get('spamhaus_dbl', '?')}",
        ]
        for u in urls[:5]:
            lines.append(f"  · [{u.get('url_status', '?')}] {u.get('threat', '?')} — {u.get('url', '')[:100]}")
        return "\n".join(lines)


# ── ThreatFox (abuse.ch) — IOC search ────────────────────────────────────────


class ThreatFoxLookupSkill(Skill):
    name = "threatfox_lookup"
    description = (
        "Searches abuse.ch ThreatFox for an indicator of compromise — an IP:port, "
        "domain, URL, or file hash — and returns any matching IOCs with their malware "
        "family, threat type, confidence level and tags. Use it as the general-purpose "
        "IOC check when you have an indicator and want to know if it is tied to known "
        "malware or C2 infrastructure, especially across mixed indicator types. Do NOT "
        "use it for a pure IP abuse score (use ip_reputation), URL-specific checks (use "
        "urlhaus_lookup), or malware-sample metadata by hash (use malwarebazaar_lookup). "
        "Returns a plain-text list of matching IOCs, or a clear 'no result'."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "ioc": {
                "type": "string",
                "description": "The indicator to search — an IP, IP:port, domain, URL, or md5/sha256 hash.",
            }
        },
        "required": ["ioc"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        ioc = str(args.get("ioc", "")).strip()
        if not ioc:
            return "threatfox_lookup: no IOC provided."
        d, err = await _abuse_ch_post(
            "https://threatfox-api.abuse.ch/api/v1/",
            json_body={"query": "search_ioc", "search_term": ioc},
        )
        if err:
            logger.warning("threatfox_failed", extra={"error": err, "request_id": context.request_id})
            return f"threatfox_lookup: lookup failed ({err})."

        status = d.get("query_status")
        if status == "no_result":
            return f"threatfox_lookup: '{ioc}' is NOT listed in ThreatFox (no known IOC match)."
        if status != "ok":
            return f"threatfox_lookup: ThreatFox returned status '{status}' — {d.get('data', '')}"

        rows = d.get("data") or []
        lines = [f"ThreatFox MATCH for {ioc} — {len(rows)} record(s):"]
        for r in rows[:5]:
            lines.append(
                f"- {r.get('ioc', '?')}  ·  malware: {r.get('malware_printable', r.get('malware', '?'))}  "
                f"·  type: {r.get('threat_type', '?')}  ·  confidence: {r.get('confidence_level', '?')}%\n"
                f"    first seen: {r.get('first_seen', '?')}  ·  tags: {', '.join(r.get('tags') or []) or 'none'}"
            )
        return "\n".join(lines)


# ── MalwareBazaar (abuse.ch) — sample metadata by hash ───────────────────────


class MalwareBazaarLookupSkill(Skill):
    name = "malwarebazaar_lookup"
    description = (
        "Looks up a file hash (SHA256, SHA1 or MD5) in abuse.ch MalwareBazaar and "
        "returns the sample's metadata: file name and type, size, detected malware "
        "family/signature, tags, first-seen date and vendor intelligence. Use it when "
        "you have the hash of a suspicious file and want to know if it is known malware "
        "and what it is. Do NOT use it to look up URLs, domains, or IPs (use "
        "urlhaus_lookup, threatfox_lookup, ip_reputation) — it is keyed on file hashes "
        "only, and it does NOT download the sample. Returns a plain-text metadata "
        "summary, or a clear 'hash not found'."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "hash": {
                "type": "string",
                "description": "A SHA256 (64 hex), SHA1 (40 hex) or MD5 (32 hex) file hash.",
            }
        },
        "required": ["hash"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        h = str(args.get("hash", "")).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}", h):
            return "malwarebazaar_lookup: not a valid MD5/SHA1/SHA256 hash."
        d, err = await _abuse_ch_post(
            "https://mb-api.abuse.ch/api/v1/",
            data={"query": "get_info", "hash": h},
        )
        if err:
            logger.warning("malwarebazaar_failed", extra={"error": err, "request_id": context.request_id})
            return f"malwarebazaar_lookup: lookup failed ({err})."

        status = d.get("query_status")
        if status == "hash_not_found":
            return f"malwarebazaar_lookup: hash {h} is NOT in MalwareBazaar (no known sample)."
        if status != "ok":
            return f"malwarebazaar_lookup: MalwareBazaar returned status '{status}'."

        r = (d.get("data") or [{}])[0]
        vendors = r.get("vendor_intel") or {}
        return (
            f"MalwareBazaar MATCH for {h}:\n"
            f"- File: {r.get('file_name', '?')} ({r.get('file_type', '?')}, {r.get('file_size', '?')} bytes)\n"
            f"- Signature: {r.get('signature') or 'unclassified'}  ·  first seen: {r.get('first_seen', '?')}\n"
            f"- SHA256: {r.get('sha256_hash', '?')}\n"
            f"- Tags: {', '.join(r.get('tags') or []) or 'none'}\n"
            f"- Delivery: {r.get('delivery_method') or '?'}  ·  intel sources: {', '.join(vendors.keys()) or 'none'}"
        )


# ── HIBP Pwned Passwords — breach count (keyless, k-anonymity) ────────────────


class PwnedPasswordSkill(Skill):
    name = "pwned_password_check"
    description = (
        "Checks whether a password appears in Have I Been Pwned's Pwned Passwords "
        "corpus and how many breaches contain it, using the k-anonymity range API — "
        "only the first 5 characters of the password's SHA-1 hash ever leave this "
        "server, so the password itself is NEVER transmitted. Use it to tell the user "
        "if a specific password is compromised and should not be used. Do NOT use it "
        "to check email addresses or accounts (that is a different HIBP API that needs "
        "a paid key), and never log or echo the password back. Returns whether the "
        "password was found and its breach-exposure count."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "password": {
                "type": "string",
                "description": "The password to check. Only a 5-char hash prefix is sent; the value stays local.",
            }
        },
        "required": ["password"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        pw = args.get("password", "")
        if not pw:
            return "pwned_password_check: no password provided."
        sha1 = hashlib.sha1(pw.encode("utf-8")).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA, "Add-Padding": "true"}) as client:
                resp = await client.get(f"https://api.pwnedpasswords.com/range/{prefix}")
                resp.raise_for_status()
                body = resp.text
        except Exception as exc:
            logger.warning("pwned_password_failed", extra={"error": str(exc), "request_id": context.request_id})
            return f"pwned_password_check: lookup failed ({_exc_msg(exc)})."

        count = 0
        for line in body.splitlines():
            frag, _, cnt = line.partition(":")
            if frag.strip().upper() == suffix:
                try:
                    count = int(cnt.strip())
                except ValueError:
                    count = 0
                break

        if count == 0:
            return "pwned_password_check: this password was NOT found in any known breach. (Absence isn't a guarantee of strength — length and uniqueness still matter.)"
        return (
            f"pwned_password_check: COMPROMISED — this password appears in {count:,} known "
            "breaches. It must not be used anywhere; advise the user to choose a unique, "
            "unused password."
        )


# ── Ahmia — dark-web (.onion) search (keyless) ───────────────────────────────

_AHMIA_RESULT_RE = re.compile(r'<li class="result"[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
_AHMIA_TITLE_RE = re.compile(r"<h4>\s*<a[^>]*>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_AHMIA_CITE_RE = re.compile(r"<cite>(.*?)</cite>", re.DOTALL | re.IGNORECASE)
_AHMIA_DESC_RE = re.compile(r"<p>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_AHMIA_REDIRECT_RE = re.compile(r"redirect_url=([^&\"']+)", re.IGNORECASE)
_AHMIA_HIDDEN_RE = re.compile(r"<input[^>]*type=\"hidden\"[^>]*>", re.IGNORECASE)
_ATTR_NAME_RE = re.compile(r'name="([^"]+)"', re.IGNORECASE)
_ATTR_VALUE_RE = re.compile(r'value="([^"]*)"', re.IGNORECASE)
# Ahmia's search form ships a per-page-load hidden anti-bot token (random field
# NAME and value). A request without it is tarpitted (read timeout) or handed
# the empty search page — which is exactly why token-less lookups "didn't work".
# We also present a browser UA; the SPEDA UA gets the same cold shoulder.
_AHMIA_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _harvest_hidden(html_text: str) -> dict:
    """Pull every hidden <input> (name→value) out of the Ahmia search form so the
    anti-bot token is echoed back on the actual search request."""
    fields: dict[str, str] = {}
    for tag in _AHMIA_HIDDEN_RE.findall(html_text):
        name = _ATTR_NAME_RE.search(tag)
        if name:
            value = _ATTR_VALUE_RE.search(tag)
            fields[name.group(1)] = value.group(1) if value else ""
    return fields


class DarkWebSearchSkill(Skill):
    name = "darkweb_search"
    description = (
        "Searches the Ahmia index of Tor hidden services (.onion sites) for a query "
        "and returns matching titles, .onion addresses and snippets — all over the "
        "clearnet, so no Tor connection is made or required. Use it for OSINT into "
        "whether a brand, credential dump, leak, or keyword surfaces on indexed dark-web "
        "sites during authorized threat research. Do NOT use it as a general web search "
        "(use the normal web_search tools) and do NOT attempt to open or fetch the "
        "returned .onion links — this tool only surfaces their existence and metadata. "
        "Returns a plain-text list of results, or a note that nothing was found."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term(s) to look up across indexed Tor hidden services.",
            }
        },
        "required": ["query"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "darkweb_search: no query provided."
        try:
            async with httpx.AsyncClient(timeout=_SLOW_TIMEOUT, headers={"User-Agent": _AHMIA_UA}, follow_redirects=True) as client:
                # 1. Load the form to harvest its hidden anti-bot token, reusing
                #    this same client so any session cookie Ahmia sets is kept.
                form_page = await client.get("https://ahmia.fi/search/")
                hidden = _harvest_hidden(form_page.text)
                # 2. Search WITH the token echoed back.
                resp = await client.get("https://ahmia.fi/search/", params={"q": query, **hidden})
                if resp.status_code >= 500:
                    # Ahmia's own index backend tarpits/times out on very broad
                    # terms (502/504) — that's upstream, not our request.
                    return (
                        f"darkweb_search: Ahmia's index returned {resp.status_code} for '{query}' — its "
                        "search backend is overloaded or timed out on this term. Retry shortly or use a "
                        "more specific query."
                    )
                resp.raise_for_status()
                body = resp.text
        except httpx.TimeoutException:
            logger.warning("darkweb_search_timeout", extra={"query": query, "request_id": context.request_id})
            return (
                f"darkweb_search: Ahmia timed out for '{query}' — its clearnet index is slow or overloaded "
                "right now. Retry shortly or narrow the query to something more specific."
            )
        except Exception as exc:
            logger.warning("darkweb_search_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"darkweb_search: search failed ({_exc_msg(exc)})."

        results = []
        for block in _AHMIA_RESULT_RE.findall(body):
            title_m = _AHMIA_TITLE_RE.search(block)
            cite_m = _AHMIA_CITE_RE.search(block)
            desc_m = _AHMIA_DESC_RE.search(block)
            onion = _strip_tags(cite_m.group(1)) if cite_m else ""
            if not onion:
                red_m = _AHMIA_REDIRECT_RE.search(block)
                if red_m:
                    onion = urllib.parse.unquote(red_m.group(1))
            title = _strip_tags(title_m.group(1)) if title_m else "(untitled)"
            desc = _strip_tags(desc_m.group(1)) if desc_m else ""
            if onion:
                results.append((title, onion, desc[:200]))
            if len(results) >= 10:
                break

        if not results:
            return f"darkweb_search: no indexed .onion results for '{query}'."
        lines = [f"Ahmia dark-web results for '{query}' ({len(results)} shown):"]
        for title, onion, desc in results:
            lines.append(f"- {title}\n    {onion}" + (f"\n    {desc}" if desc else ""))
        lines.append("\n(Links are reported for intelligence only — do not open or fetch them.)")
        return "\n".join(lines)


# ── AlienVault OTX — threat intelligence (free key) ──────────────────────────


class OTXLookupSkill(Skill):
    name = "otx_lookup"
    description = (
        "Queries AlienVault OTX (Open Threat Exchange) for an indicator — an IP, "
        "domain, hostname, URL, or file hash — and reports how many community threat "
        "'pulses' reference it, along with the associated malware families, adversary "
        "tags and pulse names. Use it as a broad, free reputation check to see whether "
        "an indicator is tied to documented threat campaigns across any indicator type. "
        "Do NOT use it for a single-vendor IP abuse score (use ip_reputation) or for "
        "device/port exposure (use shodan_lookup). Returns a plain-text threat summary; "
        "requires OTX_API_KEY (free at otx.alienvault.com)."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "The IP, domain, hostname, URL, or MD5/SHA1/SHA256 hash to look up.",
            },
            "type": {
                "type": "string",
                "description": "Indicator type. Usually leave as 'auto'.",
                "enum": ["auto", "ip", "domain", "hostname", "url", "hash"],
                "default": "auto",
            },
        },
        "required": ["indicator"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.otx_api_key:
            return "otx_lookup: set OTX_API_KEY in .env (free at https://otx.alienvault.com)."
        ind = str(args.get("indicator", "")).strip()
        if not ind:
            return "otx_lookup: no indicator provided."
        itype = str(args.get("type", "auto")).strip().lower()
        if itype in ("", "auto"):
            if _looks_like_ip(ind):
                itype = "IPv6" if ":" in ind else "IPv4"
            elif re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", ind):
                itype = "file"
            elif ind.lower().startswith(("http://", "https://")):
                itype = "url"
            else:
                itype = "domain"
        else:
            itype = {"ip": "IPv4", "ipv4": "IPv4", "ipv6": "IPv6", "domain": "domain",
                     "hostname": "hostname", "host": "hostname", "hash": "file",
                     "file": "file", "url": "url"}.get(itype, itype)
        path_ind = urllib.parse.quote(ind, safe="") if itype == "url" else ind
        url = f"https://otx.alienvault.com/api/v1/indicators/{itype}/{path_ind}/general"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers={"X-OTX-API-KEY": settings.otx_api_key, "User-Agent": _UA})
                if resp.status_code in (401, 403):
                    return "otx_lookup: OTX rejected the key (401/403) — check OTX_API_KEY."
                if resp.status_code == 404:
                    return f"otx_lookup: OTX has no record for '{ind}'."
                resp.raise_for_status()
                d = resp.json()
        except Exception as exc:
            logger.warning("otx_lookup_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"otx_lookup: lookup failed ({_exc_msg(exc)})."

        pinfo = d.get("pulse_info") or {}
        count = pinfo.get("count", 0)
        pulses = pinfo.get("pulses") or []
        if not count:
            return f"otx_lookup: '{ind}' ({itype}) is NOT referenced by any OTX pulse (no known threat reports)."
        families, tags, names = set(), set(), []
        for p in pulses[:20]:
            names.append(p.get("name", "?"))
            for fam in p.get("malware_families") or []:
                families.add(fam.get("display_name") if isinstance(fam, dict) else str(fam))
            for t in p.get("tags") or []:
                tags.add(str(t))
        return (
            f"OTX threat intel for {ind} ({itype}) — referenced by {count} pulse(s):\n"
            f"- Top pulses: {'; '.join(names[:5])}\n"
            f"- Malware families: {', '.join(sorted(f for f in families if f)) or 'none named'}\n"
            f"- Tags: {', '.join(sorted(tags)[:12]) or 'none'}"
        )


# ── Shodan — device/service discovery (free tier) ────────────────────────────


class ShodanLookupSkill(Skill):
    name = "shodan_lookup"
    description = (
        "Queries Shodan either for a single host (pass 'ip' — returns open ports, "
        "running services/banners, hostnames, the owning org, and known CVEs) or as a "
        "search across Shodan's internet-wide scan data (pass 'query' — e.g. "
        "'apache country:DE port:443'). Use it to assess a host's external exposure or "
        "to discover devices/services matching a fingerprint during authorized recon. "
        "Do NOT use it for reputation scoring (use ip_reputation/otx_lookup) or plain "
        "geolocation (use ip_geolocate); note each call spends a Shodan query credit. "
        "Returns a plain-text summary; requires SHODAN_API_KEY."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "ip": {"type": "string", "description": "A single IP to profile (host lookup)."},
            "query": {"type": "string", "description": "A Shodan search query (used only when 'ip' is absent)."},
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.shodan_api_key:
            return "shodan_lookup: set SHODAN_API_KEY in .env (https://account.shodan.io)."
        ip = str(args.get("ip", "")).strip()
        query = str(args.get("query", "")).strip()
        key = settings.shodan_api_key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                if ip:
                    if not _looks_like_ip(ip):
                        return f"shodan_lookup: '{ip}' is not a valid IP."
                    resp = await client.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": key})
                    if resp.status_code == 404:
                        return f"shodan_lookup: Shodan has no scan data for {ip}."
                    if resp.status_code in (401, 403):
                        return "shodan_lookup: Shodan rejected the key (check SHODAN_API_KEY)."
                    resp.raise_for_status()
                    d = resp.json()
                    ports = d.get("ports") or []
                    vulns = list((d.get("vulns") or []))
                    services = []
                    for item in (d.get("data") or [])[:6]:
                        prod = item.get("product") or item.get("_shodan", {}).get("module", "?")
                        services.append(f"{item.get('port', '?')}/{prod}")
                    return (
                        f"Shodan host {d.get('ip_str', ip)}:\n"
                        f"- Org: {d.get('org') or '?'}  ·  ISP: {d.get('isp') or '?'}  ·  OS: {d.get('os') or '?'}\n"
                        f"- Location: {d.get('city') or '?'}, {d.get('country_name') or '?'}\n"
                        f"- Hostnames: {', '.join(d.get('hostnames') or []) or 'none'}\n"
                        f"- Open ports ({len(ports)}): {', '.join(str(p) for p in sorted(ports)) or 'none'}\n"
                        f"- Services: {', '.join(services) or 'none'}\n"
                        f"- Known CVEs: {', '.join(vulns[:15]) if vulns else 'none reported'}"
                    )
                if query:
                    resp = await client.get(
                        "https://api.shodan.io/shodan/host/search",
                        params={"key": key, "query": query},
                    )
                    if resp.status_code in (401, 403):
                        return "shodan_lookup: Shodan rejected the key (check SHODAN_API_KEY)."
                    resp.raise_for_status()
                    d = resp.json()
                    matches = d.get("matches") or []
                    lines = [f"Shodan search '{query}' — {d.get('total', 0)} total results (showing {min(len(matches), 8)}):"]
                    for m in matches[:8]:
                        loc = (m.get("location") or {}).get("country_name", "?")
                        lines.append(
                            f"- {m.get('ip_str', '?')}:{m.get('port', '?')}  "
                            f"{m.get('product') or ''} — {m.get('org') or '?'} ({loc})"
                        )
                    return "\n".join(lines)
                return "shodan_lookup: provide either 'ip' (host lookup) or 'query' (search)."
        except Exception as exc:
            logger.warning("shodan_lookup_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"shodan_lookup: lookup failed ({_exc_msg(exc)})."


# ── SecurityTrails — DNS / WHOIS history (free tier) ─────────────────────────


class DNSIntelSkill(Skill):
    name = "dns_intel"
    description = (
        "Queries SecurityTrails for DNS and WHOIS intelligence on a domain: current DNS "
        "records (A/MX/NS/TXT), the list of known subdomains, WHOIS registration/registrar "
        "details, or historical A-record (hosting IP) changes over time. Use it to map a "
        "domain's infrastructure, enumerate subdomains, or trace where a domain was hosted "
        "historically during OSINT/attack-surface work. Do NOT use it for live device "
        "scanning (use shodan_lookup) or threat reputation (use otx_lookup). Returns a "
        "plain-text summary for the requested section; requires SECURITYTRAILS_API_KEY."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "The apex domain to query, e.g. 'example.com'."},
            "section": {
                "type": "string",
                "description": "Which dataset to return.",
                "enum": ["current_dns", "subdomains", "whois", "history_a"],
                "default": "current_dns",
            },
        },
        "required": ["domain"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.securitytrails_api_key:
            return "dns_intel: set SECURITYTRAILS_API_KEY in .env (https://securitytrails.com)."
        domain = str(args.get("domain", "")).strip().lower()
        if not domain or "." not in domain:
            return "dns_intel: provide a valid domain, e.g. 'example.com'."
        section = str(args.get("section", "current_dns")).strip().lower()
        paths = {
            "current_dns": f"/v1/domain/{domain}",
            "subdomains": f"/v1/domain/{domain}/subdomains",
            "whois": f"/v1/domain/{domain}/whois",
            "history_a": f"/v1/history/{domain}/dns/a",
        }
        path = paths.get(section, paths["current_dns"])
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"https://api.securitytrails.com{path}",
                    headers={"APIKEY": settings.securitytrails_api_key, "Accept": "application/json", "User-Agent": _UA},
                )
                if resp.status_code in (401, 403):
                    return "dns_intel: SecurityTrails rejected the key (check SECURITYTRAILS_API_KEY)."
                if resp.status_code == 429:
                    return "dns_intel: SecurityTrails quota exhausted (free tier is 50/month)."
                resp.raise_for_status()
                d = resp.json()
        except Exception as exc:
            logger.warning("dns_intel_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"dns_intel: lookup failed ({_exc_msg(exc)})."

        if section == "current_dns":
            cur = d.get("current_dns") or {}
            def vals(rec):
                return ", ".join(
                    v.get("ip") or v.get("nameserver") or v.get("hostname") or v.get("value", "?")
                    for v in (cur.get(rec, {}).get("values") or [])
                ) or "none"
            return (
                f"SecurityTrails current DNS for {domain}:\n"
                f"- A: {vals('a')}\n- MX: {vals('mx')}\n- NS: {vals('ns')}\n- TXT: {vals('txt')}\n"
                f"- Alexa rank: {d.get('alexa_rank') or 'n/a'}"
            )
        if section == "subdomains":
            subs = d.get("subdomains") or []
            shown = ", ".join(f"{s}.{domain}" for s in subs[:30])
            return f"SecurityTrails subdomains for {domain} — {d.get('subdomain_count', len(subs))} found:\n{shown}" + (" …" if len(subs) > 30 else "")
        if section == "whois":
            contacts = d.get("contacts") or []
            org = next((c.get("organization") for c in contacts if c.get("organization")), None)
            return (
                f"SecurityTrails WHOIS for {domain}:\n"
                f"- Registrar: {d.get('registrarName') or '?'}\n"
                f"- Created: {d.get('createdDate') or '?'}  ·  Expires: {d.get('expiresDate') or '?'}\n"
                f"- Organization: {org or '?'}  ·  Contacts: {len(contacts)}"
            )
        records = d.get("records") or []
        lines = [f"SecurityTrails A-record history for {domain} — {len(records)} entries:"]
        for r in records[:10]:
            ips = ", ".join(v.get("ip", "?") for v in (r.get("values") or []))
            lines.append(f"- {r.get('first_seen', '?')} → {r.get('last_seen', '?')}: {ips}")
        return "\n".join(lines)


# ── Hunter.io — email discovery (free tier) ──────────────────────────────────


class EmailDiscoverySkill(Skill):
    name = "email_discovery"
    description = (
        "Uses Hunter.io to either find the email addresses associated with a company "
        "domain (pass 'domain' — returns addresses with names, roles and confidence "
        "scores) or verify whether a specific address is deliverable (pass 'email' — "
        "returns its status and score). Use it for OSINT on an organization's contactable "
        "addresses or to sanity-check an address before relying on it. Do NOT use it to "
        "check if a password/account was breached (use pwned_password_check) — it finds "
        "and validates addresses, it does not report breaches. Returns a plain-text "
        "summary; requires HUNTER_API_KEY."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "Company domain to enumerate emails for, e.g. 'stripe.com'."},
            "email": {"type": "string", "description": "A specific address to verify (used when 'domain' is absent)."},
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.hunter_api_key:
            return "email_discovery: set HUNTER_API_KEY in .env (https://hunter.io)."
        domain = str(args.get("domain", "")).strip().lower()
        email = str(args.get("email", "")).strip().lower()
        key = settings.hunter_api_key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                if email:
                    resp = await client.get("https://api.hunter.io/v2/email-verifier",
                                            params={"email": email, "api_key": key})
                    if resp.status_code in (401, 403):
                        return "email_discovery: Hunter rejected the key (check HUNTER_API_KEY)."
                    resp.raise_for_status()
                    d = resp.json().get("data", {})
                    return (
                        f"Hunter verification for {email}:\n"
                        f"- Result: {d.get('result', '?')} (status: {d.get('status', '?')})  ·  score: {d.get('score', '?')}/100\n"
                        f"- Deliverable: MX={d.get('mx_records')}, SMTP={d.get('smtp_check')}, "
                        f"disposable={d.get('disposable')}, webmail={d.get('webmail')}"
                    )
                if domain:
                    resp = await client.get("https://api.hunter.io/v2/domain-search",
                                            params={"domain": domain, "api_key": key, "limit": 10})
                    if resp.status_code in (401, 403):
                        return "email_discovery: Hunter rejected the key (check HUNTER_API_KEY)."
                    if resp.status_code == 429:
                        return "email_discovery: Hunter quota exhausted (free tier is 25/month)."
                    resp.raise_for_status()
                    d = resp.json().get("data", {})
                    emails = d.get("emails") or []
                    lines = [
                        f"Hunter domain search for {domain} "
                        f"({d.get('organization') or 'org unknown'}) — {len(emails)} shown:"
                    ]
                    for e in emails[:10]:
                        name = f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() or "?"
                        lines.append(
                            f"- {e.get('value', '?')}  ·  {name}"
                            f"{' (' + e.get('position') + ')' if e.get('position') else ''}"
                            f"  ·  {e.get('type', '?')}, confidence {e.get('confidence', '?')}%"
                        )
                    return "\n".join(lines)
                return "email_discovery: provide either 'domain' (find emails) or 'email' (verify one)."
        except Exception as exc:
            logger.warning("email_discovery_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"email_discovery: lookup failed ({_exc_msg(exc)})."


# ── Etherscan + Blockchair — crypto address tracing ──────────────────────────

# chain → (blockchair slug, decimals, symbol) for keyless Blockchair dashboards.
_CHAINS = {
    "ethereum": ("ethereum", 18, "ETH"),
    "bitcoin": ("bitcoin", 8, "BTC"),
    "litecoin": ("litecoin", 8, "LTC"),
    "dogecoin": ("dogecoin", 8, "DOGE"),
    "bitcoin-cash": ("bitcoin-cash", 8, "BCH"),
    "dash": ("dash", 8, "DASH"),
}


class CryptoTraceSkill(Skill):
    name = "crypto_trace"
    description = (
        "Traces a cryptocurrency address, returning its balance, total received/spent "
        "and transaction count, plus recent transactions for Ethereum. Ethereum "
        "addresses (0x…) are read via Etherscan when ETHERSCAN_API_KEY is set (richer "
        "tx detail) and otherwise via Blockchair; Bitcoin and other UTXO chains use "
        "Blockchair, which works keyless. Use it to check on-chain activity and holdings "
        "for a wallet during crypto OSINT or fraud tracing. Do NOT expect identity "
        "attribution — it reports on-chain data only, not who owns the address. Returns a "
        "plain-text on-chain summary."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "The wallet address to trace."},
            "chain": {
                "type": "string",
                "description": "Blockchain. Leave 'auto' to detect Ethereum vs Bitcoin from the address.",
                "enum": ["auto", "ethereum", "bitcoin", "litecoin", "dogecoin", "bitcoin-cash", "dash"],
                "default": "auto",
            },
        },
        "required": ["address"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        addr = str(args.get("address", "")).strip()
        if not addr:
            return "crypto_trace: no address provided."
        chain = str(args.get("chain", "auto")).strip().lower()
        is_eth = bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", addr))
        if chain in ("", "auto"):
            chain = "ethereum" if is_eth else "bitcoin"

        if chain == "ethereum" and settings.etherscan_api_key:
            return await self._etherscan(addr, context)
        return await self._blockchair(addr, chain, context)

    async def _etherscan(self, addr: str, context: AgentContext) -> str:
        base = "https://api.etherscan.io/v2/api"
        key = settings.etherscan_api_key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                bal = (await client.get(base, params={
                    "chainid": 1, "module": "account", "action": "balance",
                    "address": addr, "tag": "latest", "apikey": key,
                })).json()
                txs = (await client.get(base, params={
                    "chainid": 1, "module": "account", "action": "txlist",
                    "address": addr, "page": 1, "offset": 5, "sort": "desc", "apikey": key,
                })).json()
        except Exception as exc:
            logger.warning("crypto_trace_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"crypto_trace: Etherscan lookup failed ({_exc_msg(exc)})."

        if str(bal.get("status")) == "0" and "Invalid" in str(bal.get("result", "")):
            return f"crypto_trace: Etherscan rejected the request ({bal.get('result')})."
        try:
            eth = int(bal.get("result", "0")) / 1e18
        except ValueError:
            eth = 0.0
        tx_rows = txs.get("result") or []
        lines = [f"Etherscan trace for {addr}:", f"- Balance: {eth:.6f} ETH"]
        if isinstance(tx_rows, list) and tx_rows:
            lines.append(f"- Recent transactions ({len(tx_rows)} shown):")
            for t in tx_rows[:5]:
                val = int(t.get("value", "0")) / 1e18
                outgoing = t.get("from", "").lower() == addr.lower()
                direction = "OUT ->" if outgoing else "IN  <-"
                other = t.get("to") if outgoing else t.get("from")
                lines.append(f"    {direction} {val:.5f} ETH  {other}  (tx {t.get('hash', '')[:12]}...)")
        else:
            lines.append("- No transactions found (or none returned).")
        return "\n".join(lines)

    async def _blockchair(self, addr: str, chain: str, context: AgentContext) -> str:
        slug, decimals, symbol = _CHAINS.get(chain, _CHAINS["bitcoin"])
        params = {}
        if settings.blockchair_api_key:
            params["key"] = settings.blockchair_api_key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = await client.get(
                    f"https://api.blockchair.com/{slug}/dashboards/address/{urllib.parse.quote(addr)}",
                    params=params,
                )
                if resp.status_code == 404:
                    return f"crypto_trace: Blockchair has no data for {addr} on {chain}."
                if resp.status_code == 430 or resp.status_code == 429:
                    return "crypto_trace: Blockchair rate limit reached — try again shortly (or set BLOCKCHAIR_API_KEY)."
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("crypto_trace_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"crypto_trace: Blockchair lookup failed ({_exc_msg(exc)})."

        data = (payload.get("data") or {}).get(addr) or (payload.get("data") or {}).get(addr.lower())
        if not data:
            return f"crypto_trace: {addr} not found on {chain} (Blockchair returned no address record)."
        a = data.get("address") or {}
        try:
            bal = int(a.get("balance", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            bal = 0.0
        return (
            f"Blockchair trace for {addr} ({chain}):\n"
            f"- Balance: {bal:.8f} {symbol}"
            f"{'  (~$' + format(a.get('balance_usd', 0), ',.2f') + ')' if a.get('balance_usd') else ''}\n"
            f"- Transactions: {a.get('transaction_count', a.get('call_count', '?'))}\n"
            f"- Received: {a.get('received') or a.get('received_approximate') or '?'}  ·  "
            f"Spent: {a.get('spent') or a.get('spent_approximate') or '?'}\n"
            f"- First seen: {a.get('first_seen_receiving') or '?'}  ·  Last: {a.get('last_seen_spending') or a.get('last_seen_receiving') or '?'}"
        )


# ── Intelligence X — leak / dark-web search (free tier) ──────────────────────


class IntelXSearchSkill(Skill):
    name = "intelx_search"
    description = (
        "Searches Intelligence X for a selector — an email, domain, IP, URL, Bitcoin "
        "address, or other identifier — across its archive of leaks, breaches, pastes and "
        "dark-web captures, returning matching records with their name, date, bucket and "
        "media type. Use it to discover whether a selector appears in leaked/breached data "
        "or dark-web sources during authorized investigation. Do NOT use it as a general "
        "web search (use web_search) or to browse .onion sites (use darkweb_search); it "
        "returns record metadata, not the leaked content itself. Returns a plain-text list "
        "of hits; requires INTELX_API_KEY."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "The selector to search — email, domain, IP, URL, phone, or crypto address.",
            }
        },
        "required": ["term"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.intelx_api_key:
            return "intelx_search: set INTELX_API_KEY in .env (free tier at https://intelx.io)."
        term = str(args.get("term", "")).strip()
        if not term:
            return "intelx_search: no term provided."
        headers = {"x-key": settings.intelx_api_key, "User-Agent": _UA, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
                start = await client.post(
                    "https://2.intelx.io/intelligent/search",
                    json={"term": term, "maxresults": 10, "media": 0, "sort": 2, "terminate": []},
                )
                if start.status_code in (401, 402, 403):
                    return "intelx_search: Intelligence X rejected the key or the free tier is exhausted (check INTELX_API_KEY)."
                start.raise_for_status()
                search_id = start.json().get("id")
                if not search_id:
                    return "intelx_search: Intelligence X did not return a search id."

                records: list = []
                for _ in range(4):  # poll: status 3 = results not ready yet
                    res = await client.get(
                        "https://2.intelx.io/intelligent/search/result",
                        params={"id": search_id, "limit": 10},
                    )
                    res.raise_for_status()
                    body = res.json()
                    records.extend(body.get("records") or [])
                    status = body.get("status")
                    if status in (0, 1) and records:
                        break
                    if status == 1:  # done, no more results
                        break
                    await asyncio.sleep(1.0)
        except Exception as exc:
            logger.warning("intelx_search_failed", extra={"error": _exc_msg(exc), "request_id": context.request_id})
            return f"intelx_search: search failed ({_exc_msg(exc)})."

        if not records:
            return f"intelx_search: no Intelligence X records for '{term}'."
        # De-dupe by systemid, keep order.
        seen, uniq = set(), []
        for r in records:
            sid = r.get("systemid")
            if sid in seen:
                continue
            seen.add(sid)
            uniq.append(r)
        lines = [f"Intelligence X hits for '{term}' — {len(uniq)} record(s):"]
        for r in uniq[:10]:
            lines.append(
                f"- {r.get('name') or '(unnamed)'}  ·  {r.get('date', '?')[:10]}  "
                f"·  bucket: {r.get('bucket', '?')}  ·  type: {r.get('type', '?')}"
            )
        lines.append("\n(Record metadata only — leaked content is not fetched.)")
        return "\n".join(lines)


# Ordered list for one-line registration in main.py.
OSINT_SKILLS: list[type[Skill]] = [
    IPGeolocateSkill,
    IPReputationSkill,
    URLhausLookupSkill,
    ThreatFoxLookupSkill,
    MalwareBazaarLookupSkill,
    PwnedPasswordSkill,
    DarkWebSearchSkill,
    OTXLookupSkill,
    ShodanLookupSkill,
    DNSIntelSkill,
    EmailDiscoverySkill,
    CryptoTraceSkill,
    IntelXSearchSkill,
]
