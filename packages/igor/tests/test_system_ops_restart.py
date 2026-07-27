"""Orion restarting Igor: the failure that reported itself as a success.

The self-restart backgrounds a shell (`setsid ... &`) so the turn can finish
before the container dies. That shell exits 0 the instant it forks, which says
nothing about whether the restart happened — and in the containerized deployment
it never could, because the backend image ships no docker CLI and mounts no
/var/run/docker.sock. `docker restart` died with 'docker: not found' into
/tmp/speda_restart.log while the tool told the agent the restart was scheduled
and to stop working. RestartCount on the production container stayed 0.

What these pin down: an unreachable Docker is reported as a failure, the failure
says so in words the agent cannot read as success, and nothing is scheduled.
"""

import pytest

from app.skills.system_ops import SystemOpsSkill


class _Ctx:
    """Minimal stand-in for AgentContext — _log_op is stubbed out."""

    request_id = "test-request"
    agent_id = "orion"
    user_id = 1


@pytest.fixture
def skill(monkeypatch):
    s = SystemOpsSkill()

    async def _noop_log(context, message):
        s.logged.append(message)

    s.logged = []
    monkeypatch.setattr(s, "_log_op", _noop_log)
    return s


@pytest.fixture
def never_exec(monkeypatch):
    """Trips if anything reaches the shell — the point is that nothing runs."""
    calls = []

    async def _exec(self, args, context):
        calls.append(args.get("command", ""))
        return "exit_code: 0"

    monkeypatch.setattr(SystemOpsSkill, "_exec", _exec)
    return calls


async def _unreachable(self):
    return False, "sh: 1: docker: not found"


async def _reachable(self):
    return True, "28.1.1"


@pytest.mark.asyncio
async def test_self_restart_is_refused_when_docker_is_unreachable(skill, never_exec, monkeypatch):
    monkeypatch.setattr(SystemOpsSkill, "_docker_reachable", _unreachable)

    out = await skill._restart_service({"service": "app"}, _Ctx())

    assert "could not restart" in out.lower()
    assert "docker: not found" in out
    assert never_exec == [], "nothing may be scheduled when Docker is unreachable"


@pytest.mark.asyncio
async def test_the_refusal_cannot_be_mistaken_for_a_pending_restart(skill, never_exec, monkeypatch):
    """The old success text said 'restart SCHEDULED ... Do NOT run any further
    commands', which is precisely what made the silent failure invisible."""
    monkeypatch.setattr(SystemOpsSkill, "_docker_reachable", _unreachable)

    out = await skill._restart_service({"service": "app"}, _Ctx())

    assert "SCHEDULED" not in out
    assert "FAILED" in out
    assert "nothing restarted" in out.lower()


@pytest.mark.asyncio
async def test_non_self_restart_is_gated_too(skill, never_exec, monkeypatch):
    """A sandbox/n8n restart hits the same missing CLI — it must not run either."""
    monkeypatch.setattr(SystemOpsSkill, "_docker_reachable", _unreachable)

    out = await skill._restart_service({"service": "sandbox"}, _Ctx())

    assert "could not restart" in out.lower()
    assert never_exec == []


@pytest.mark.asyncio
async def test_self_restart_still_schedules_when_docker_is_reachable(skill, never_exec, monkeypatch):
    monkeypatch.setattr(SystemOpsSkill, "_docker_reachable", _reachable)

    out = await skill._restart_service({"service": "app", "delay": 10}, _Ctx())

    assert "SCHEDULED" in out
    assert len(never_exec) == 1
    cmd = never_exec[0]
    assert "setsid" in cmd and "sleep 10" in cmd and "docker restart" in cmd


@pytest.mark.asyncio
async def test_non_self_restart_runs_synchronously_when_docker_is_reachable(skill, never_exec, monkeypatch):
    monkeypatch.setattr(SystemOpsSkill, "_docker_reachable", _reachable)

    await skill._restart_service({"service": "sandbox"}, _Ctx())

    assert len(never_exec) == 1
    cmd = never_exec[0]
    assert "setsid" not in cmd, "only the SELF service defers"
    assert "com.docker.compose.service=sandbox" in cmd
