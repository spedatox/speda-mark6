# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Lockdown Protocol's one load-bearing invariant, and the bug it comes from.

The first implementation sealed the host in steps: insert the DROP on the SSH
port, then insert the exemptions above it, then roll back if the exemptions
failed. Every one of those steps is a separate `host_bridge.run()`, and every
`run()` is a NEW SSH connection to the port the first step just started dropping.
So step two dialled a sealed port, timed out, and so did the rollback — leaving
the host sealed with no exemptions at all, unreachable from the container, from
CI and from the owner's laptop, while `engage()` returned "the host is NOT
sealed" because no single step had reported success.

What these pin down:

  * the DROP and its exemptions travel in ONE command, so no round trip is ever
    dialled across a seal that is already live;
  * inside that command the exemptions come first and the hook comes last, so a
    build that dies halfway fails open rather than locking the host;
  * a failed seal does not set the flag, and a refusal explicitly says nothing
    went live;
  * standing down clears the flat rules the old scheme left behind, because the
    host carrying those is exactly the host someone is trying to get back into.
"""

import pytest

from app.services import lockdown


@pytest.fixture
def host(monkeypatch):
    """Records every command sent to the host; replays canned results."""

    class Host:
        def __init__(self):
            self.commands: list[str] = []
            self.result = (0, "", "")
            self.results: dict[str, tuple[int, str, str]] = {}

        async def run(self, command, timeout=30):
            self.commands.append(command)
            for needle, result in self.results.items():
                if needle in command:
                    return result
            if "docker network" in command or "docker0" in command:
                return 0, "172.18.0.0/16\n", ""
            return self.result

    h = Host()
    monkeypatch.setattr(lockdown, "run", h.run)
    monkeypatch.setattr(lockdown.settings, "lockdown_protocol_enabled", True)
    monkeypatch.setattr(lockdown.settings, "system_ops_ssh_port", 22)
    monkeypatch.setattr(lockdown, "remote_enabled", lambda: True)
    monkeypatch.setattr(lockdown, "set_lockdown", lambda v: h.__setattr__("flag", v))
    monkeypatch.setattr(lockdown, "get_lockdown", lambda: getattr(h, "flag", False))
    return h


def _seal(commands: list[str]) -> str:
    """The single command that carries the DROP. Exactly one may exist."""
    sealing = [c for c in commands if "-j DROP" in c and "-D " not in c and "while " not in c]
    assert len(sealing) == 1, f"the seal must be one command, got {len(sealing)}"
    return sealing[0]


async def test_seal_and_exemptions_travel_in_one_command(host):
    ok, _ = await lockdown.engage()
    assert ok

    seal = _seal(host.commands)
    assert "-j RETURN" in seal, "the exemptions must ride along with the DROP"
    assert "-I INPUT 1 -j SPEDA_LOCKDOWN" in seal, "the hook must ride along too"


async def test_exemptions_precede_the_drop_and_the_hook_is_last(host):
    await lockdown.engage()
    seal = _seal(host.commands)
    lines = seal.splitlines()

    first_drop = next(i for i, line in enumerate(lines) if "-j DROP" in line)
    last_return = max(i for i, line in enumerate(lines) if "-j RETURN" in line)
    hook = next(i for i, line in enumerate(lines) if "-I INPUT 1" in line)

    assert last_return < first_drop, "a half-built chain must fail open, not closed"
    assert hook > first_drop, "nothing may jump into the chain before it is built"
    assert hook == len(lines) - 1, "the hook is the last thing that happens"


async def test_the_live_bridge_address_is_exempted_first(host):
    """The Docker subnets are a guess; $SSH_CLIENT is the address actually in use."""
    await lockdown.engage()
    lines = _seal(host.commands).splitlines()

    client = next(i for i, line in enumerate(lines) if "$CLIENT" in line and "RETURN" in line)
    subnet = next(i for i, line in enumerate(lines) if "172.18.0.0/16" in line)
    assert client < subnet


async def test_no_host_command_is_sent_after_the_seal_that_the_seal_needs(host):
    """Anything issued after the DROP is live must be optional by construction."""
    await lockdown.engage()
    after = host.commands[host.commands.index(_seal(host.commands)) + 1:]
    assert all("DOCKER-USER" in c for c in after), (
        "only the second hook may follow the seal; it is a separate chain, and "
        "the SSH exemption is already live when it runs"
    )


async def test_unreadable_bridge_address_refuses_without_touching_the_host(host):
    host.results["-j DROP"] = (lockdown._NO_CLIENT, "", "source address could not be read")
    ok, report = await lockdown.engage()

    assert not ok
    assert "REFUSED" in report
    assert getattr(host, "flag", False) is False
    assert not any("-I DOCKER-USER" in c for c in host.commands)


async def test_a_failed_seal_says_nothing_went_live(host):
    host.results["-j DROP"] = (1, "", "iptables: Permission denied")
    ok, report = await lockdown.engage()

    assert not ok
    assert "NOT sealed" in report
    assert "Nothing went live" in report
    assert getattr(host, "flag", False) is False


async def test_the_flag_is_set_before_the_second_hook_is_attempted(host, monkeypatch):
    """A caller that gives up mid-engage must not leave a sealed host reading off."""
    seen = {}
    inner = host.run

    async def watching(command, timeout=30):
        if "DOCKER-USER -j" in command:
            seen["flag_at_second_hook"] = getattr(host, "flag", False)
        return await inner(command, timeout)

    monkeypatch.setattr(lockdown, "run", watching)
    await lockdown.engage()
    assert seen["flag_at_second_hook"] is True


async def test_disengage_clears_the_pre_chain_rules_too(host):
    await lockdown.disengage()
    teardown = next(c for c in host.commands if "-X SPEDA_LOCKDOWN" in c)

    assert "-D INPUT -j SPEDA_LOCKDOWN" in teardown
    assert "-D DOCKER-USER -j SPEDA_LOCKDOWN" in teardown
    # The flat DROP the first implementation stranded on the host.
    assert "-D INPUT -p tcp --dport 22 -j DROP" in teardown
    assert getattr(host, "flag", True) is False


async def test_disengage_names_the_console_escape_when_a_seal_survives(host):
    host.results["hook INPUT"] = (0, "hook INPUT=1\nhook DOCKER-USER=1\nCHAIN_BEGIN\n"
                                     "-A SPEDA_LOCKDOWN -p tcp --dport 22 -j DROP\n", "")
    _, report = await lockdown.disengage()

    assert "STILL sealed" in report
    assert "iptables -D INPUT -j SPEDA_LOCKDOWN" in report


async def test_disengage_never_claims_access_it_could_not_verify(host):
    """An unreachable host is usually unreachable BECAUSE it is sealed."""
    host.result = (255, "", "ssh: connect to host port 22: Connection timed out")
    ok, report = await lockdown.disengage()

    assert ok  # the flag is cleared regardless — the way out is never gated
    assert getattr(host, "flag", True) is False
    assert "normal access restored" not in report
    assert "Reopened" not in report
    assert "could NOT be reached" in report
    assert "iptables -D INPUT -j SPEDA_LOCKDOWN" in report


async def test_status_reports_nothing_rather_than_false_when_unreachable(host):
    host.result = (255, "", "connection timed out")
    host.flag = True
    state = await lockdown.status()

    assert state["engaged"] is True
    assert state["rules"] == {}, "unknown must not be rendered as 'not sealed'"


async def test_a_moved_ssh_port_is_sealed_alongside_22(host, monkeypatch):
    monkeypatch.setattr(lockdown.settings, "system_ops_ssh_port", 2222)
    await lockdown.engage()
    seal = _seal(host.commands)

    assert "--dport 2222 -j DROP" in seal
    assert "--dport 22 -j DROP" in seal


async def test_status_reports_flag_and_firewall_separately(host):
    host.results["hook INPUT"] = (
        0,
        "hook INPUT=0\nhook DOCKER-USER=0\n"
        "legacy INPUT:22=0\nlegacy DOCKER-USER:8000=0\nCHAIN_BEGIN\n",
        "",
    )
    host.flag = True
    state = await lockdown.status()

    assert state["engaged"] is True
    assert all(sealed is False for sealed in state["rules"].values()), (
        "drift — flag on, rules gone — is exactly what must stay visible"
    )
