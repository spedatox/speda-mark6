# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Doormat Protocol: the old door stays open until the new one is proven.

Changing the domain is the operation where a failure takes away the thing you
would have used to recover. So the invariants pinned here are all shaped the same
way — at no point may both hostnames be unserved, and at no point may a phase
claim more than it did:

  * a stage that fails ANYWHERE rolls its own site file back out and leaves the
    current domain untouched;
  * DNS that does not point here refuses before Caddy is ever handed the name,
    because an ACME retry loop against a rate-limited CA is not a recoverable
    mistake;
  * cutover cannot run before stage proved the domain serves, and retire cannot
    run before the process has actually loaded the cutover settings;
  * the site file the protocol owns never names the same host as the
    deployment's own {$DOMAIN} block — Caddy refuses a duplicate site address,
    and a refused config on a recreate is a Caddy that does not come up;
  * nothing moves the domain without the owner in the conversation.
"""

import pytest

from app.services import doormat
from app.skills.doormat import DoormatProtocolSkill

INSPECT = (
    "mount=/opt/mark6/caddy-sites|/etc/caddy/sites\n"
    "mount=/opt/mark6/Caddyfile|/etc/caddy/Caddyfile\n"
    "env=DOMAIN=old.example.com\n"
    "env=PATH=/usr/bin\n"
    "cid=abc123\n"
)


@pytest.fixture
def host(monkeypatch):
    """Canned host responses; records every command and every state write."""

    class Host:
        def __init__(self):
            self.commands: list[str] = []
            self.results: dict[str, tuple[int, str, str]] = {}
            self.state: dict = {}
            self.managed: dict = {}
            self.serving = True          # does _verify's curl loop find a 200
            self.reload_ok = True

        async def run(self, command, timeout=30):
            self.commands.append(command)
            for needle, result in self.results.items():
                if needle in command:
                    return result
            if "docker ps -q -f label" in command and "docker inspect" in command:
                return 0, INSPECT, ""
            if "ip -o addr show" in command:
                return 0, "203.0.113.10\n", ""
            if "caddy reload" in command:
                return (0, "", "") if self.reload_ok else (1, "", "adapt: duplicate site")
            if "http=" in command or "curl" in command:
                return 0, ("http=200" if self.serving else "http=000"), ""
            return 0, "", ""

    h = Host()
    monkeypatch.setattr(doormat, "run", h.run)
    monkeypatch.setattr(doormat, "get_doormat", lambda: dict(h.state))
    monkeypatch.setattr(doormat, "set_doormat", lambda s: h.state.clear() or h.state.update(s))
    monkeypatch.setattr(doormat, "read_managed_env", lambda: dict(h.managed))
    monkeypatch.setattr(
        doormat, "write_managed_env",
        lambda updates: h.managed.update({k: v for k, v in updates.items() if v is not None}),
    )
    monkeypatch.setattr(doormat.settings, "doormat_protocol_enabled", True)
    monkeypatch.setattr(doormat.settings, "google_client_id", "g-client")
    monkeypatch.setattr(doormat.settings, "microsoft_client_id", "ms-client")
    monkeypatch.setattr(doormat.settings, "notion_client_id", "")
    monkeypatch.setattr(doormat.settings, "telegram_mode", "webhook")

    async def _points_here(domain):
        return {"resolved": ["203.0.113.10"], "host": ["203.0.113.10"],
                "points_here": True, "resolves": True}

    monkeypatch.setattr(doormat, "dns_check", _points_here)
    return h


class _Ctx:
    request_id = "test-request"
    agent_id = "orion"
    user_id = 1
    triggered_by = "user"
    trigger_payload: dict = {}
    extra: dict = {}


def _ctx(triggered_by="user"):
    c = _Ctx()
    c.triggered_by = triggered_by
    return c


def _site_writes(commands):
    return [c for c in commands if "doormat.caddy" in c and "rm -f" not in c]


def _site_removals(commands):
    return [c for c in commands if "rm -f" in c and "doormat.caddy" in c]


# ── Input handling ───────────────────────────────────────────────────────────

def test_what_people_actually_type_is_trimmed():
    assert doormat.normalize("HTTPS://Speda.Example.com:443/path/") == "speda.example.com"
    assert doormat.normalize("  new.example.com.  ") == "new.example.com"


def test_the_hostname_pattern_is_also_the_injection_guard():
    """The value reaches a shell heredoc and a Caddyfile. Nothing else may."""
    assert doormat.valid("speda.example.com")
    for bad in ["no-dots", "a;rm -rf /.com", "-lead.com", "x .com", "x/y.com",
                "$(whoami).com", "a".join(["", "b" * 300, ".com"])]:
        assert not doormat.valid(bad), bad


async def test_a_bad_hostname_never_reaches_the_host(host):
    ok, report = await doormat.stage("not a domain; rm -rf /")
    assert not ok and "REFUSED" in report
    assert host.commands == []


# ── The DNS precondition ─────────────────────────────────────────────────────

async def test_dns_that_does_not_point_here_refuses_before_caddy_sees_it(host, monkeypatch):
    async def _elsewhere(domain):
        return {"resolved": ["198.51.100.4"], "host": ["203.0.113.10"],
                "points_here": False, "resolves": True}

    monkeypatch.setattr(doormat, "dns_check", _elsewhere)
    ok, report = await doormat.stage("new.example.com")

    assert not ok
    assert "203.0.113.10" in report, "the refusal must name the address to point at"
    assert _site_writes(host.commands) == []


async def test_a_domain_that_does_not_resolve_at_all_says_so(host, monkeypatch):
    async def _nothing(domain):
        return {"resolved": [], "host": ["203.0.113.10"], "points_here": False,
                "resolves": False}

    monkeypatch.setattr(doormat, "dns_check", _nothing)
    ok, report = await doormat.stage("new.example.com")
    assert not ok
    assert "does not resolve" in report


async def test_force_is_the_documented_escape_for_a_proxy(host, monkeypatch):
    async def _elsewhere(domain):
        return {"resolved": ["198.51.100.4"], "host": ["203.0.113.10"],
                "points_here": False, "resolves": True}

    monkeypatch.setattr(doormat, "dns_check", _elsewhere)
    ok, _ = await doormat.stage("new.example.com", force=True)
    assert ok


# ── Staging, and rolling itself back ─────────────────────────────────────────

async def test_staging_adds_the_new_domain_and_touches_nothing_else(host):
    ok, report = await doormat.stage("new.example.com")

    assert ok
    written = _site_writes(host.commands)
    assert len(written) == 1
    assert "new.example.com {" in written[0]
    assert "old.example.com" not in written[0], "the protocol's file holds the OTHER door only"
    assert host.state["phase"] == doormat.STAGED
    assert host.state["previous"] == "old.example.com"
    assert "untouched" in report


async def test_a_rejected_caddy_config_removes_the_file_it_added(host):
    host.reload_ok = False
    ok, report = await doormat.stage("new.example.com")

    assert not ok
    assert _site_removals(host.commands), "the bad file must not survive to break the next boot"
    assert host.state == {}, "a failed stage leaves no phase behind"


async def test_a_domain_that_never_gets_a_certificate_is_rolled_back(host):
    host.serving = False
    ok, report = await doormat.stage("new.example.com")

    assert not ok
    assert "ROLLED BACK" in report
    assert _site_removals(host.commands)
    assert host.state == {}


async def test_staging_the_domain_already_being_served_is_a_no_op(host):
    ok, report = await doormat.stage("old.example.com")
    assert not ok
    assert "already serving" in report
    assert _site_writes(host.commands) == []


async def test_no_caddy_means_there_is_no_domain_to_change(host):
    host.results["docker inspect"] = (0, "no_caddy\n", "")
    ok, report = await doormat.stage("new.example.com")
    assert not ok
    assert "deploy concern" in report


# ── The checklist ────────────────────────────────────────────────────────────

def test_the_checklist_only_lists_integrations_this_deployment_uses(host):
    steps = doormat.checklist("new.example.com", "old.example.com")
    providers = [s["provider"] for s in steps]

    assert "Google" in providers
    assert "Microsoft" in providers
    assert "Notion" not in providers, "notion_client_id is unset here"


def test_every_console_step_says_add_not_replace(host):
    steps = doormat.checklist("new.example.com", "old.example.com")
    for step in steps:
        if step["provider"] in {"Google", "Microsoft", "Notion"}:
            assert "ADD" in step["note"]
            assert step["value"].startswith("https://new.example.com/oauth/")


def test_an_n8n_subdomain_of_the_old_domain_is_flagged_as_collateral(host):
    host.managed["N8N_DOMAIN"] = "n8n.old.example.com"
    steps = doormat.checklist("new.example.com", "old.example.com")
    warning = [s for s in steps if s["provider"] == "n8n editor"]

    assert warning, "a hostname that dies with the retired domain must be named"
    assert "WARNING" in warning[0]["note"]


# ── Cutover ──────────────────────────────────────────────────────────────────

async def test_cutover_refuses_without_a_stage(host):
    ok, report = await doormat.cutover()
    assert not ok
    assert "no staged domain" in report
    assert host.managed == {}


async def test_cutover_refuses_when_the_staged_domain_stopped_serving(host):
    await doormat.stage("new.example.com")
    host.serving = False

    ok, report = await doormat.cutover()
    assert not ok
    assert host.managed == {}, "nothing may repoint at an address that does not answer"


async def test_cutover_repoints_every_derived_setting(host):
    await doormat.stage("new.example.com")
    ok, report = await doormat.cutover()

    assert ok
    assert host.managed["TELEGRAM_WEBHOOK_BASE"] == "https://new.example.com"
    assert host.managed["GOOGLE_OAUTH_REDIRECT"] == "https://new.example.com/oauth/google/callback"
    assert host.managed["MICROSOFT_OAUTH_REDIRECT"] == "https://new.example.com/oauth/microsoft/callback"
    assert host.managed["NOTION_OAUTH_REDIRECT"] == "https://new.example.com/oauth/notion/callback"
    assert host.state["phase"] == doormat.CUTOVER


async def test_cutover_leaves_the_old_domain_serving(host):
    await doormat.stage("new.example.com")
    _, report = await doormat.cutover()

    assert "STILL SERVING" in report
    assert _site_removals(host.commands) == []


async def test_cutover_names_the_restart_as_the_only_remaining_step(host):
    await doormat.stage("new.example.com")
    _, report = await doormat.cutover()
    assert 'restart_service", service="app"' in report


# ── Retire ───────────────────────────────────────────────────────────────────

async def test_retire_refuses_before_cutover(host):
    await doormat.stage("new.example.com")
    ok, report = await doormat.retire()

    assert not ok
    assert _site_removals(host.commands) == []


async def test_retire_refuses_while_the_process_has_not_loaded_the_new_settings(host):
    """The managed file says the new domain; this object was built at boot."""
    await doormat.stage("new.example.com")
    await doormat.cutover()

    ok, report = await doormat.retire()
    assert not ok
    assert "has not restarted" in report
    assert _site_removals(host.commands) == []


async def test_retire_removes_the_site_before_domain_becomes_the_same_host(host, monkeypatch):
    """Both naming one hostname is a Caddyfile Caddy refuses — on a recreate that
    is not a safe no-op, it is a proxy that does not come back up."""
    await doormat.stage("new.example.com")
    await doormat.cutover()
    for key, value in host.managed.items():
        monkeypatch.setattr(doormat.settings, key.lower(), value)

    ok, report = await doormat.retire()
    assert ok

    script = next(c for c in host.commands if "docker compose" in c)
    assert script.index("rm -f") < script.index("DOMAIN=new.example.com")
    assert host.state["phase"] == doormat.IDLE


async def test_retire_reminds_the_owner_to_clean_up_the_old_redirect_uris(host, monkeypatch):
    await doormat.stage("new.example.com")
    await doormat.cutover()
    for key, value in host.managed.items():
        monkeypatch.setattr(doormat.settings, key.lower(), value)

    _, report = await doormat.retire()
    assert "OLD redirect URIs" in report


# ── Abort ────────────────────────────────────────────────────────────────────

async def test_abort_undoes_a_stage_completely(host):
    await doormat.stage("new.example.com")
    ok, report = await doormat.abort()

    assert ok
    assert _site_removals(host.commands)
    assert host.state["phase"] == doormat.IDLE


async def test_abort_refuses_after_cutover_and_says_how_to_go_back(host):
    await doormat.stage("new.example.com")
    await doormat.cutover()

    ok, report = await doormat.abort()
    assert not ok
    assert "old.example.com" in report, "it must name the domain to stage back"
    assert host.state["phase"] == doormat.CUTOVER


# ── The gate ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["stage", "cutover", "retire", "abort"])
async def test_no_phase_moves_the_domain_without_the_owner(host, action):
    result = await DoormatProtocolSkill().execute(
        {"action": action, "domain": "new.example.com"}, _ctx(triggered_by="n8n")
    )

    assert "REFUSED" in result
    assert host.commands == []


async def test_status_is_readable_from_a_background_turn(host):
    """Reading is not acting — noticing a week-old unfinished move is useful."""
    await doormat.stage("new.example.com")
    result = await DoormatProtocolSkill().execute(
        {"action": "status"}, _ctx(triggered_by="n8n")
    )
    assert "REFUSED" not in result
    assert "new.example.com" in result


async def test_stage_without_a_domain_asks_rather_than_guesses(host):
    result = await DoormatProtocolSkill().execute({"action": "stage"}, _ctx())
    assert "REFUSED" in result
    assert "Do not guess" in result
    assert host.commands == []


async def test_a_disabled_deployment_touches_nothing(host, monkeypatch):
    monkeypatch.setattr(doormat.settings, "doormat_protocol_enabled", False)
    result = await DoormatProtocolSkill().execute(
        {"action": "stage", "domain": "new.example.com"}, _ctx()
    )
    assert "disabled" in result
    assert host.commands == []


# ── Status reporting ─────────────────────────────────────────────────────────

async def test_status_surfaces_a_skipped_restart(host, monkeypatch):
    await doormat.stage("new.example.com")
    await doormat.cutover()

    state = await doormat.status()
    assert state["restart_pending"] is True

    report = DoormatProtocolSkill()._status_report(state)
    assert "RESTART OUTSTANDING" in report


async def test_status_reports_phase_and_the_firewall_of_facts_separately(host):
    await doormat.stage("new.example.com")
    host.serving = False

    state = await doormat.status()
    assert state["phase"] == doormat.STAGED
    assert state["target_serving"] is False, "staged-but-not-serving must stay visible"
    assert state["current_domain"] == "old.example.com"
