from pathlib import Path
import pytest
from pydantic import ValidationError
from app.config import settings
from app.skills.atomix_reports import GenerateDailyTrainingProgramSkill,DailyProgramInput,_render_html


def program():
    return {'day_name':'Pazartesi · A günü','week_label':'1. Hafta','date':'2026-10-26','date_human':'26 Ekim 2026',
        'goal':'Kontrollü yük ve temiz teknik.','warmup':['5 dakika rahat yürüyüş','Omuz ve kalça mobilitesi'],
        'exercises':[{'name':'Goblet squat','load':'3 × 10 · 12 kg','note':'Kontrollü tempo'},
            {'name':'Dumbbell row','load':'3 × 12 · 10 kg','note':'Her iki taraf'},
            {'name':'Incline push-up','load':'3 × 8','note':'Temiz tekrar'}],
        'finisher':'5 dakika rahat yürüyüş.','rules':['Keskin ağrıda dur.','Setler arasında 90 saniye dinlen.']}


def test_locked_template_escapes_content_and_rejects_filename_paths():
    data=program(); data['goal']='<script>not markup</script>'
    html=_render_html(DailyProgramInput(**data))
    assert '&lt;script&gt;not markup&lt;/script&gt;' in html and '<script>' not in html
    assert 'data:font/' in html and '[[GOAL]]' not in html
    data['date']='../../not-a-date'
    with pytest.raises(ValidationError): DailyProgramInput(**data)


async def test_real_atomix_pdf_has_original_template_and_turkish_text(memory,tmp_path,monkeypatch):
    # This is a real renderer check. Pango/WeasyPrint are release dependencies.
    from pypdf import PdfReader
    monkeypatch.setattr(settings,'temp_outputs_dir',str(tmp_path))
    ctx=memory.context(agent_id='atomix')
    result=await GenerateDailyTrainingProgramSkill().execute(program(),ctx)
    assert 'Generated the daily training program PDF' in result,result
    card=ctx.extra['produced_files'][0]
    path=tmp_path/card['name']; reader=PdfReader(str(path))
    text='\n'.join(p.extract_text() for p in reader.pages)
    assert len(reader.pages)==1 and 'Goblet squat' in text and 'Kontrollü' in text and 'ATOMIX' in text
    assert path.stat().st_size>10000
