from unittest.mock import AsyncMock
import pytest
from forge.cell.base import CellPolicy
from forge.cell.docker_cell import DockerCell


@pytest.mark.parametrize('code,diagnostic,elapsed,expected',[
    (137,b"timeout: sending signal KILL to command 'sh'",1.1,True),
    (137,b'',1.1,False),
    (137,b"timeout: sending signal KILL to command 'sh'",.1,False),
    (124,b'',1.1,True),
    (0,b'',.1,False),
])
async def test_timeout_kill_receipt_does_not_misclassify_arbitrary_sigkill(monkeypatch,code,diagnostic,elapsed,expected):
    cell=DockerCell('fixture','fixture',CellPolicy())
    cell._started=cell._can_timeout=cell._verbose_timeout=True
    cell._docker=AsyncMock(return_value=(code,b'partial output',diagnostic))
    times=iter([0,elapsed])
    monkeypatch.setattr('forge.cell.docker_cell.monotonic',lambda:next(times))
    result=await cell.run('sleep 30',timeout=1)
    assert result.timed_out is expected
    assert result.stdout=='partial output' and result.exit_code==code
