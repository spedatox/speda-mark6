import json
import zipfile
import pytest
from app.config import settings
from app.schemas.client_compat import ClientChat
from app.schemas.llm import LLMMessage, Block
from app.services.client_compat import ClientCompatibility
from app.skills.documents import DocumentsSkill, _palette, _pdf_layout_css
from app.skills.tool_search import ToolSearchSkill
from tests.test_coordination import setup, reply


@pytest.mark.parametrize('fmt', ['docx', 'pptx','pdf'])
async def test_native_document_generation_delivery_and_reload(memory, tmp_path, monkeypatch, fmt):
    monkeypatch.setattr(settings, 'temp_outputs_dir', str(tmp_path / 'outputs'))
    script = [LLMMessage([Block(type='tool_use', id='find', name='tool_search', input={'query':'generate document'})], 'tool_use'),
        LLMMessage([Block(type='tool_use', id='doc', name='generate_document', input={
            'format':fmt, 'title':'Field Report', 'content':'## Evidence\n\nİstanbul\n\n- Verified finding'})], 'tool_use'),
        reply('Your document is ready.')]
    bits = await setup(memory, script)
    registry = bits.engine._registry
    await registry.register_skill(DocumentsSkill())
    await registry.register_skill(ToolSearchSkill(registry))
    adapter = ClientCompatibility(bits.coordinator)
    result = await adapter.start(1, 'friday', ClientChat(message='Make a document.'))
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    frames = [json.loads(f[6:]) async for f in adapter.stream(1, result['request_id']) if f.startswith('data: ')]
    cards = [e['data'] for e in frames if e['type'] == 'file']
    assert len(cards) == 1, frames
    generated = next((tmp_path / 'outputs').glob('*.' + fmt))
    if fmt=='pdf':
        from pypdf import PdfReader
        reader=PdfReader(generated)
        text='\n'.join(p.extract_text() for p in reader.pages)
        assert 'İstanbul' in text and 'Friday' in text and len(reader.pages)==1
    else:
        with zipfile.ZipFile(generated) as archive:
            text = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist() if name.endswith('.xml'))
            assert 'İstanbul' in text and 'Friday' in text
    assert (await adapter.messages(1, result['session_id']))[-1]['files'][0]['name'] == cards[0]['name']
    assert len(bits.client.calls) == 3
    await bits.coordinator.shutdown()


def test_inherited_pdf_layouts_are_distinct_and_bounded():
    from app.profiles.registry import ProfileRegistry
    profiles = ProfileRegistry()
    assert profiles.require('sentinel').snapshot()['document_theme']['pdf_layout'] == 'ledger'
    assert profiles.require('friday').snapshot()['document_theme']['pdf_layout'] == 'dossier'
    palette = _palette('#36abca')
    assert _pdf_layout_css('unknown', palette) == _pdf_layout_css('executive', palette)
    assert 'masthead' in _pdf_layout_css('dossier', palette)
