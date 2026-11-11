import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.models.client_compat import ClientProjectFile
from app.models.coordination import Room, ProjectTask, CoordinationRun
from app.schemas.project_workspace import WorkspaceCreate, WorkspacePatch, WorkspaceDirectory
from app.schemas.client_compat import ClientChat, DocumentAttachment
from app.schemas.coordination import CreateRoom, Checkpoint, IntegrateProject
from app.services.client_compat import ClientCompatibility
from app.services.project_workspace import ProjectWorkspace
from app.services.projects import Projects, inventory
from app.services.mission_control import MissionControl
from app.services.coordination_budget import CoordinationError
from app.routers.project_workspace import router
from app.middleware.auth import APIKeyMiddleware
from forge.runtime import ExecutionResult
from tests.test_projects import project_setup, delegate
from tests.test_coordination import reply, send


async def fixture(memory, tmp_path, script=()):
    async def executor(spec, **kwargs):
        (spec.workspace / 'example.txt').write_text('implemented once')
        return ExecutionResult(spec.job_id, 'succeeded', 'Updated existing example')
    bits = await project_setup(memory, tmp_path, list(script), executor)
    bits.workspace = ProjectWorkspace(bits.coordinator)
    bits.ui = await bits.workspace.create(1, WorkspaceCreate(agent_id='optimus', name='Example',
        path=str(bits.path), instructions='Keep the existing design and continue the saved work.'))
    bits.adapter = ClientCompatibility(bits.coordinator)
    return bits


async def chat(bits, **kwargs):
    result = await bits.adapter.start(1, 'optimus', ClientChat(request_id=str(uuid4()),
        message='Continue this project', **kwargs))
    if task := bits.coordinator.live.get(result['run_id']):
        await task
    return result


async def test_project_folder_chats_restart_private_instructions_and_originals(memory, tmp_path):
    bits = await fixture(memory, tmp_path, [reply('First chat'), reply('Second chat'), reply('Continue after restart')])
    ui = bits.ui
    document = DocumentAttachment(name='requirements.txt', data=base64.b64encode(b'Preserve existing accounts.').decode())
    uploaded = await bits.workspace.upload(1, 'optimus', ui['id'], document)
    first = await chat(bits, project_id=ui['id'])
    second = await chat(bits, project_id=ui['id'])
    assert first['session_id'] != second['session_id']
    rows = await bits.workspace.sessions_for(1, 'optimus', ui['id'])
    assert {row['id'] for row in rows} == {first['session_id'], second['session_id']}
    assert all(row['project_id'] == ui['id'] and row['workshop_project_id'] == ui['workshop_project_id'] for row in rows)
    assert all('Keep the existing design' in c['system'] and 'Preserve existing accounts' in c['system'] for c in bits.client.calls)
    assert 'First chat' not in json.dumps(bits.client.calls[1]['messages'])  # Separate conversations, shared project state.
    # Reconstruct services against the same database and Workshop state.
    bits.coordinator.projects = Projects(memory.sessions, bits.coordinator, bits.projects.settings)
    restored = ProjectWorkspace(bits.coordinator)
    assert (await restored.listing(1, 'optimus'))[0]['cwd'] == str(bits.path)
    assert len(await restored.sessions_for(1, 'optimus', ui['id'])) == 2
    # Existing session restores its native binding without a cwd/project request field.
    await chat(bits, session_id=first['session_id'])
    assert 'Keep the existing design' in bits.client.calls[-1]['system']
    for owner, agent in [(2,'optimus'), (1,'scourge')]:
        with pytest.raises(CoordinationError):
            await restored.get(owner, agent, ui['id'])
    assert await restored.context(1, 'scourge', ui['workshop_project_id']) == {
        'cwd':str(bits.path), 'host_id':'test-hisar', 'native_id':ui['workshop_project_id']}
    await restored.remove_file(1, 'optimus', ui['id'], uploaded['id'])
    assert not await restored.files(1, 'optimus', ui['id'])
    async with memory.sessions() as db:
        assert (await db.get(ClientProjectFile,uploaded['id'])).original == b'Preserve existing accounts.'
    await restored.remove(1, 'optimus', ui['id'])
    assert not await restored.listing(1, 'optimus', True)
    assert bits.path.joinpath('example.txt').read_text() == 'original'
    assert len(await restored.sessions_for(1, 'optimus', ui['id'])) == 2
    await bits.coordinator.shutdown()


async def test_project_reads_text_outside_the_reference_and_repository_previews(memory, tmp_path):
    from app.skills.read_chat_attachment import ReadChatAttachmentSkill

    bits = await fixture(memory, tmp_path, [reply("Ready to review.")])
    try:
        for name, text in [("long.md", "Design " * 6000), ("decision.txt", "Pick the existing adapter.")]:
            uploaded = await bits.workspace.upload(1, "optimus", bits.ui["id"],
                DocumentAttachment(name=name, data=base64.b64encode(text.encode()).decode()))
        run = await chat(bits, project_id=bits.ui["id"])
        async with memory.sessions() as db:
            room_id = (await db.get(CoordinationRun, run["run_id"])).room_id
        project_id = bits.ui["workshop_project_id"]
        ctx = memory.context(agent_id="optimus", room_id=room_id, run_id=run["run_id"],
                             project_id=project_id, coordination=bits.coordinator)
        ctx.extra["project_context"] = await bits.workspace.context(1, "optimus", project_id)
        document = ctx.extra["project_context"]["documents"][1]
        assert document["name"] == "decision.txt" and document["text"] == ""
        skill = ReadChatAttachmentSkill()
        result = json.loads(await skill.execute({"id": document["document_id"]}, ctx))
        assert result["text"] == "Pick the existing adapter."
        await bits.workspace.remove_file(1, "optimus", bits.ui["id"], uploaded["id"])
        assert json.loads(await skill.execute({"id": document["document_id"]}, ctx))["status"] == "unavailable"

        line = "ö" * 280000 + " End of generated source."
        (bits.path / "generated.txt").write_text(line + "\nnext line\n", encoding="utf-8")
        first = await bits.workspace.read(ctx, "generated.txt")
        assert len(first["content"].encode()) <= 32000 and first["next_line"] == 1
        cursor = first["next_column"]
        assert first["content"] == "1: " + line[:cursor - 1]
        tail = await bits.workspace.read(ctx, "generated.txt", start_column=len(line) - 25)
        assert tail["content"].endswith("End of generated source.\n2: next line")
        assert tail["next_line"] is None and tail["total_lines"] == 2
    finally:
        await bits.coordinator.shutdown()


async def test_forge_can_continue_inside_a_long_line(monkeypatch):
    from forge.tools.files import ReadFile, ReadFileArgs
    from forge.warden.filestate import FileStateCache

    async def read(path):
        return "0123456789" * 5 + "\nThe second line."

    monkeypatch.setattr("forge.tools.files.MAX_READ_CHARS", 32)
    context = SimpleNamespace(cell=SimpleNamespace(read=read), files=FileStateCache())
    tool = ReadFile()
    first = await tool.call(ReadFileArgs(path="generated.txt"), context)
    assert not first.is_error and "offset=1, column=30" in first.content
    assert not context.files.get("generated.txt").shown_fully
    second = await tool.call(ReadFileArgs(path="generated.txt", offset=1, column=30), context)
    assert second.content.startswith("1→ 901234567890123456789")
    assert "2→ The " in second.content and "offset=2, column=5" in second.content
    third = await tool.call(ReadFileArgs(path="generated.txt", offset=2, column=5), context)
    assert third.content == "2→ second line."


async def test_project_pdf_page_read_is_agent_and_project_scoped(memory, tmp_path, monkeypatch):
    import io

    from pypdf import PdfWriter

    from app.schemas.llm import Block, LLMMessage
    from app.skills.read_chat_attachment import ReadChatAttachmentSkill

    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=612, height=792)
    data = io.BytesIO()
    writer.write(data)

    bits = await fixture(memory, tmp_path)
    skill = ReadChatAttachmentSkill()
    await bits.engine._registry.register_skill(skill)
    try:
        uploaded = await bits.workspace.upload(1, "optimus", bits.ui["id"],
            DocumentAttachment(name="scanned.pdf", data=base64.b64encode(data.getvalue()).decode()))
        arguments = {"id": f"project:{uploaded['id']}", "page": 21}
        bits.client.script.extend([LLMMessage([Block(type="tool_use", id="project-pdf-page",
            name=skill.name, input=arguments)], "tool_use"), reply("Page 21 has no extractable text.")])
        run = await chat(bits, project_id=bits.ui["id"])
        assert "pdf_id" in bits.client.calls[0]["system"]
        results = [json.loads(block["content"])
            for message in bits.client.calls[1]["messages"] if isinstance(message["content"], list)
            for block in message["content"] if block.get("tool_use_id") == "project-pdf-page"]
        assert results[0]["status"] == "no_extractable_text" and results[0]["total_pages"] == 21

        project = await bits.workspace.context(1, "optimus", bits.ui["workshop_project_id"])
        async with memory.sessions() as db:
            room_id = (await db.get(CoordinationRun, run["run_id"])).room_id
        ctx = memory.context(agent_id="scourge", room_id=room_id, run_id=run["run_id"], coordination=bits.coordinator)
        ctx.extra["project_context"] = project
        assert json.loads(await skill.execute(arguments, ctx))["status"] == "unavailable"
        ctx.agent_id = "optimus"
        from app.services import pdf_navigation
        search = {"id": arguments["id"], "query": "test"}
        with monkeypatch.context() as patch:
            patch.setattr(pdf_navigation, "INDEX_SLICE_SECONDS", 0)
            partial = json.loads(await skill.execute(search, ctx))
            assert not partial["index_complete"] and partial["indexed_through_page"] == 1
        complete = json.loads(await skill.execute(search, ctx))
        assert complete["index_complete"] and complete["pages_without_indexed_text"] == 21
        await bits.workspace.remove_file(1, "optimus", bits.ui["id"], uploaded["id"])
        assert json.loads(await skill.execute(arguments, ctx))["status"] == "unavailable"
    finally:
        await bits.coordinator.shutdown()


async def test_new_chat_inherits_checkpoint_tasks_and_exact_artifact_dependency(memory, tmp_path):
    bits = await fixture(memory, tmp_path, [delegate(), reply('Worker started')])
    await bits.projects.checkpoint(1,bits.ui['workshop_project_id'],Checkpoint(expected_revision=0,
        objective='Finish the existing application', progress='Database already implemented', next_steps=['Build UI']))
    first = await chat(bits, project_id=bits.ui['id'])
    async with memory.sessions() as db:
        first_task = await db.scalar(select(ProjectTask))
    bits.client.script.extend([delegate(dependencies=[first_task.id]),reply('I see the previous result')])
    second = await chat(bits, project_id=bits.ui['id'])
    assert 'Database already implemented' in json.dumps(bits.client.calls[-1]['messages'])
    async with memory.sessions() as db:
        prior = await db.get(ProjectTask,first_task.id)
        run = await db.get(CoordinationRun,second['run_id'])
        room = await db.get(Room,run.room_id)
        step = (await bits.coordinator.inspect_run(1,run.id))['steps'][0]
    context = SimpleNamespace(user_id=1, agent_id='optimus', room_id=room.id, project_id=room.project_id)
    packet = await MissionControl(bits.coordinator).snapshot(context)
    assert prior.id in {m['execution_id'] for m in packet['missions']}
    assert packet['checkpoint']['objective'] == 'Finish the existing application'
    artifact = await bits.workspace.read(context, 'example.txt', task_id=prior.id)
    assert 'implemented once' in artifact['content']
    source = await bits.workspace.read(context, 'example.txt')
    assert 'original' in source['content']
    # The project remains canonical; new task dependency scope includes earlier project chats.
    assert prior.project_id == room.project_id and first['session_id'] != second['session_id']
    new_task = next(t for t in (await bits.workspace.state(1,'optimus',bits.ui['id']))['tasks'] if t['id']!=prior.id)
    assert new_task['status']=='succeeded' and new_task['base_revision']==prior.result['revision']
    diff = await bits.workspace.changes(1,'optimus',bits.ui['id'],new_task['id'])
    assert not diff['source_changed_since_execution']
    await bits.workspace.integrate(1,'optimus',bits.ui['id'],IntegrateProject(
        task_id=new_task['id'],expected_source_revision=diff['expected_source_revision'],
        expected_artifact_revision=diff['expected_artifact_revision']))
    assert bits.path.joinpath('example.txt').read_text()=='implemented once'
    await bits.coordinator.shutdown()


async def test_repeated_and_concurrent_folder_registration_is_one_project(memory,tmp_path):
    bits = await fixture(memory,tmp_path)
    body = WorkspaceCreate(agent_id='optimus',name='Again',path=str(bits.path))
    rows = await asyncio.gather(*(bits.workspace.create(1,body) for _ in range(3)))
    assert {r['id'] for r in rows}=={bits.ui['id']}
    assert len(await bits.workspace.listing(1,'optimus'))==1
    private = await bits.workspace.create(1,body.model_copy(update={'agent_id':'scourge','instructions':'Private security rules'}))
    assert private['workshop_project_id']==bits.ui['workshop_project_id'] and private['id']!=bits.ui['id']
    assert 'Private security rules' not in json.dumps(await bits.workspace.context(1,'optimus',bits.ui['workshop_project_id']))
    with pytest.raises(CoordinationError):
        await bits.workspace.create(2,body)
    await bits.coordinator.shutdown()


async def test_diff_apply_stale_refusal_and_rollback_preserve_current_files(memory, tmp_path):
    bits = await fixture(memory, tmp_path, [delegate(), reply('Saved artifact')])
    await chat(bits,project_id=bits.ui['id'])
    state = await bits.workspace.state(1,'optimus',bits.ui['id'])
    task = state['tasks'][0]
    changes = await bits.workspace.changes(1,'optimus',bits.ui['id'],task['id'])
    assert '-original' in changes['diff'] and '+implemented once' in changes['diff']
    body = IntegrateProject(task_id=task['id'], expected_source_revision=changes['expected_source_revision'],
        expected_artifact_revision=changes['expected_artifact_revision'])
    bits.path.joinpath('owner-note.txt').write_text('New owner work')
    with pytest.raises(CoordinationError,match='Folder changed'):
        await bits.workspace.integrate(1,'optimus',bits.ui['id'],body)
    assert bits.path.joinpath('owner-note.txt').read_text() == 'New owner work'
    bits.path.joinpath('owner-note.txt').unlink()  # Only the test-owned temporary file.
    applied = await bits.workspace.integrate(1,'optimus',bits.ui['id'],body)
    assert bits.path.joinpath('example.txt').read_text() == 'implemented once'
    restored = ProjectWorkspace(bits.coordinator)
    result = await restored.rollback(1,'optimus',bits.ui['id'],applied['id'],changes['expected_artifact_revision'])
    assert result['status']=='complete' and bits.path.joinpath('example.txt').read_text()=='original'
    assert Path(applied['backup']).joinpath('example.txt').read_text() == 'original'
    with pytest.raises(CoordinationError):
        await restored.changes(1,'scourge',bits.ui['id'],task['id'])
    await bits.coordinator.shutdown()


async def test_folder_picker_file_reads_archives_and_binding_fail_closed(memory,tmp_path):
    bits = await fixture(memory,tmp_path,[reply('Existing chat')])
    listing = await bits.workspace.folders()
    assert listing['directories'][0]['path']==str(bits.path)
    made = await bits.workspace.mkdir(WorkspaceDirectory(parent=listing['path'],name='new-app'))
    assert Path(made['path']).is_dir()
    for name in ['../escape','.forge','bad/name','trailing.','bad:name']:
        with pytest.raises(CoordinationError):
            await bits.workspace.mkdir(WorkspaceDirectory(parent=listing['path'],name=name))
    with pytest.raises(CoordinationError):
        await bits.workspace.folders(str(tmp_path))
    (bits.path/'.env').write_text('SECRET=never-in-preview')
    ctx=SimpleNamespace(user_id=1,agent_id='optimus',project_id=bits.ui['workshop_project_id'])
    for path in ['../example.txt','.env','.git/config',str(bits.path/'example.txt')]:
        with pytest.raises(CoordinationError):
            await bits.workspace.read(ctx,path)
    first=await chat(bits,project_id=bits.ui['id'])
    other=await bits.workspace.create(1,WorkspaceCreate(agent_id='optimus',name='Other',path=made['path']))
    with pytest.raises(CoordinationError,match='bound to another'):
        await chat(bits,session_id=first['session_id'],project_id=other['id'])
    version=(await bits.workspace.get(1,'optimus',bits.ui['id']))['version']
    await bits.workspace.patch(1,'optimus',bits.ui['id'],WorkspacePatch(expected_version=version,archived=True))
    with pytest.raises(CoordinationError,match='changed'):
        await bits.workspace.patch(1,'optimus',bits.ui['id'],WorkspacePatch(expected_version=version,name='Stale'))
    with pytest.raises(CoordinationError,match='Restore'):
        await chat(bits,project_id=bits.ui['id'])
    assert not (await bits.workspace.listing(1,'optimus'))[0]['archived']
    await bits.coordinator.shutdown()


async def test_authenticated_http_project_workflow(memory,tmp_path):
    bits=await fixture(memory,tmp_path)
    app=FastAPI()
    app.state.coordinator=bits.coordinator
    app.include_router(router)
    app.add_middleware(APIKeyMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/projects')).status_code==401
        headers={'X-API-Key':'test-only-key'}
        res=await client.get('/projects?agent_id=optimus',headers=headers)
        assert res.status_code==200 and res.json()[0]['id']==bits.ui['id']
        assert (await client.get('/projects/folders',headers=headers)).json()['path']==str(bits.projects.root)
        res=await client.get(f"/projects/{bits.ui['id']}/workspace/file?path=example.txt&agent_id=optimus",headers=headers)
        assert res.status_code==200 and 'original' in res.json()['content']
    await bits.coordinator.shutdown()
