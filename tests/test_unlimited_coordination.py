from types import SimpleNamespace

from app.schemas.coordination import CreateRoom, RoomInput, SpendingPolicy
from app.services.coordination_budget import call_deadline, run_expired, run_timeout
from tests.test_coordination import setup, reply


async def test_zero_seconds_run_finishes_without_aggregate_deadline(memory):
    bits = await setup(memory, [reply("Finished the assigned work.")])
    version, policy = await bits.budget.policy(1)
    policy.run.seconds = 0
    await bits.budget.save(1, version, policy)
    room = await bits.coordinator.create(1, CreateRoom(lead="scourge"))
    started = await bits.coordinator.start(
        1, room["id"], RoomInput(input_id="unlimited", message="Finish the mission")
    )
    await bits.coordinator.live[started["run_id"]]
    result = await bits.coordinator.inspect_run(1, started["run_id"])
    assert result["status"] == "succeeded"
    assert result["deadline"] == 0
    assert len(bits.client.calls) == 1
    await bits.coordinator.shutdown()


def test_zero_deadline_disables_only_aggregate_timer():
    run = SimpleNamespace(deadline=0)
    assert SpendingPolicy(run={"seconds": 0}).run.seconds == 0
    assert not run_expired(run)
    assert run_timeout(run) is None
    assert call_deadline(run) > 0
