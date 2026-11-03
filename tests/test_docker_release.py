"""Opt-in real Docker checks; no model calls or production workspace access."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from forge.cell.base import CellPolicy
from forge.cell.docker_cell import DockerCell


@pytest.fixture
def cell_root():
    value=os.environ.get('SPEDA_TEST_CELL_ROOT')
    if not value:
        pytest.skip('Explicit disposable host/container-shared Cell root is not configured')
    root=Path(value).resolve(strict=True)
    workspace=root/('release-check-'+uuid4().hex)
    workspace.mkdir()
    return workspace


async def exists(name):
    process=await asyncio.create_subprocess_exec('docker','inspect',name,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,_=await process.communicate()
    return process.returncode==0, json.loads(out) if process.returncode==0 else None


async def test_real_cell_isolation_timeout_and_verified_teardown(cell_root):
    cell=DockerCell('release-check',os.environ['SPEDA_TEST_CELL_IMAGE'],
        CellPolicy(cpus=.5,memory_mb=256,pids_limit=64),workspace_mount=cell_root)
    try:
        await cell.start()
        present,state=await exists(cell.container)
        assert present and state[0]['HostConfig']['NetworkMode']=='none'
        assert state[0]['Config']['User']=='1000:1000'
        assert state[0]['HostConfig']['CapDrop']==['ALL']
        assert {m['Source'] for m in state[0]['Mounts']}=={str(cell_root)}
        await cell.write('proof.txt','Isolated artifact')
        assert await cell.read('proof.txt')=='Isolated artifact'
        assert (await cell.run('test ! -e /var/run/docker.sock && test ! -e /root/.speda/speda.db')).exit_code==0
        timed=await cell.run('sleep 30',timeout=1)
        assert timed.timed_out
        assert (await cell.run('printf recovered')).stdout=='recovered'
    finally:
        await cell.close()
    assert not (await exists(cell.container))[0]
    assert (cell_root/'proof.txt').read_text()=='Isolated artifact'


async def test_cancelled_cell_command_is_joined_and_container_removed(cell_root):
    cell=DockerCell('release-cancel',os.environ['SPEDA_TEST_CELL_IMAGE'],
        CellPolicy(cpus=.5,memory_mb=256,pids_limit=64),workspace_mount=cell_root)
    try:
        await cell.start()
        task=asyncio.create_task(cell.run('sleep 30',timeout=30))
        await asyncio.sleep(.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    finally:
        await cell.close()
    assert not (await exists(cell.container))[0]
