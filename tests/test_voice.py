import asyncio
import io
import json
import wave
import pytest
from sqlalchemy import select,func
from app.models.voice import SpeechAttempt
from app.services.global_budget import GlobalBudget,GlobalPolicy,GlobalCall
from app.services.voice import Voice
from app.schemas.voice import SpeakRequest,VoiceAgentPatch
from app.services.coordination_budget import CoordinationError
from app.schemas.llm import ProviderError
from tests.test_automations import build


async def service(memory,tmp_path):
    bits,automations,_=await build(memory)
    config=automations.config
    config.azure_speech_key='fixture-azure'; config.openai_api_key='fixture-openai'; config.elevenlabs_api_key='fixture-eleven'
    config.temp_outputs_dir=str(tmp_path/'audio')
    budget=GlobalBudget(memory.sessions)
    voice=Voice(memory.sessions,bits.profiles,budget,config)
    return bits,voice,budget


async def enable(budget):
    await budget.save(1,0,GlobalPolicy(paused=False,daily_calls=20,daily_microdollars=1000000,
        lifetime_microdollars=1000000,external_prices={'speech:tts:openai:gpt-4o-mini-tts':1,
            'speech:tts:azure:neural':1,'speech:tts:elevenlabs:eleven_v4':1,'speech:stt:azure:seconds':100}))


async def test_speech_reservation_cache_language_and_pins(memory,tmp_path,monkeypatch):
    bits,voice,budget=await service(memory,tmp_path)
    calls=[]
    async def transport(method,url,**kwargs): calls.append((method,url,kwargs)); return 200,b'ID3fixture-audio'
    monkeypatch.setattr('app.services.voice.request_bytes',transport)
    body=SpeakRequest(text='**Merhaba** <owner>',voice='openai:gpt-4o-mini-tts:nova',agent_id='sentinel')
    with pytest.raises(CoordinationError,match='paused'): await voice.speak(1,body)
    assert not calls
    await enable(budget)
    first=await voice.speak(1,body); second=await voice.speak(1,body)
    assert first==second and len(calls)==1
    assert calls[0][2]['json']['input']=='Merhaba <owner>'
    assert calls[0][2]['json']['instructions']=='Speak naturally in tr-TR.'
    await voice.update(1,'sentinel',VoiceAgentPatch(voice_id='azure:tr-TR-EmelNeural',expected_version=0))
    await voice.update(1,'sentinel',VoiceAgentPatch(language='en',voice_id='openai:gpt-4o-mini-tts:nova',expected_version=1))
    assert (await voice.resolve(1,'sentinel',locale='en-US'))['provider']=='openai'
    assert (await voice.resolve(1,'sentinel',locale='tr-TR'))['provider']=='azure'
    await voice.speak(1,SpeakRequest(text='Hello <break/> & owner',agent_id='sentinel'))
    assert b'&lt;break/&gt; &amp; owner' in calls[-1][2]['content']
    with pytest.raises(CoordinationError): await voice.update(1,'sentinel',VoiceAgentPatch(expected_version=1,style=.5))
    async with memory.sessions() as db:
        receipts=list(await db.scalars(select(GlobalCall)))
        assert len(receipts)==2 and all(r.tokens==0 and r.status=='complete' and r.microdollars>0 for r in receipts)
    await bits.coordinator.shutdown()


async def test_unknown_speech_not_replayed_even_with_new_request_id(memory,tmp_path,monkeypatch):
    bits,voice,budget=await service(memory,tmp_path); await enable(budget)
    count=0
    async def lost(*args,**kwargs):
        nonlocal count
        count+=1
        raise ProviderError('transport_unknown','Lost')
    monkeypatch.setattr('app.services.voice.request_bytes',lost)
    body=SpeakRequest(text='Hello owner',voice='openai:gpt-4o-mini-tts:nova')
    with pytest.raises(ProviderError): await voice.speak(1,body)
    with pytest.raises(CoordinationError,match='unknown'): await voice.speak(1,body.model_copy(update={'request_id':'fresh-id'}))
    assert count==1
    async with memory.sessions() as db:
        assert (await db.scalar(select(GlobalCall))).status=='unknown'
        assert (await db.scalar(select(SpeechAttempt))).status=='unknown'
    await bits.coordinator.shutdown()


async def test_transcription_silence_and_real_duration_validation(memory,tmp_path,monkeypatch):
    bits,voice,budget=await service(memory,tmp_path); await enable(budget)
    calls=[]
    async def recognize(*args,**kwargs): calls.append(kwargs); return 200,json.dumps({'RecognitionStatus':'NoMatch'}).encode()
    monkeypatch.setattr('app.services.voice.request_bytes',recognize)
    out=io.BytesIO()
    with wave.open(out,'wb') as recording:
        recording.setnchannels(1); recording.setsampwidth(2); recording.setframerate(16000); recording.writeframes(b'\0'*32000)
    result=await voice.listen(1,out.getvalue(),'en-US')
    assert result=={'text':'','locale':'en-US'} and len(calls)==1
    assert calls[0]['params']['language']=='en-US'
    assert await voice.listen(1,out.getvalue(),'en-US')==result
    with pytest.raises(CoordinationError): await voice.listen(1,out.getvalue()[:100])
    with pytest.raises(CoordinationError): await voice.listen(1,b'not wav')
    assert len(calls)==1
    await bits.coordinator.shutdown()
