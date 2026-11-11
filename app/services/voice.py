"""App-owned paid speech transport with immutable attempt identity and no replay."""
import asyncio
import copy
import io
import json
import math
import re
import wave
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
from sqlalchemy import select,update
from app.models.voice import VoicePreference,SpeechAttempt
from app.models.user import User
from app.services.speech_text import strip_for_speech,build_ssml,parse_voice_ref,MAX_CHARS
from app.services.coordination_budget import CoordinationError,digest
from app.services.remote_tools import request_bytes

DEFAULT_VOICES={
    'speda':'pNInz6obpgDQGcFmaJgB','ultron':'TxGEqnHWrfWFTfGW9XjX','optimus':'AZnzlk1XvdvUeBnXmlld',
    'scourge':'yoZ06aMxZJJ28mfd3POQ','friday':'VR6AewLTigWG4xSOukaG','orion':'MF3mGyEYCl7XYWbV9V6O',
    'sentinel':'ErXwobaYiN019PkySvjV','atomix':'EXAVITQu4vr4xnSDxMaL'}
TUNING={'stability','similarity_boost','style','speed','use_speaker_boost'}
OPENAI_VOICES={'alloy','ash','ballad','cedar','coral','echo','fable','marin','nova','onyx','sage','shimmer','verse'}


class Voice:
    def __init__(self,sessions,profiles,budget,config):
        self.sessions,self.profiles,self.budget,self.config=sessions,profiles,budget,config

    def providers(self):
        return [provider for provider,key in [('azure',self.config.azure_speech_key),('openai',self.config.openai_api_key),('elevenlabs',self.config.elevenlabs_api_key)] if key]

    def status(self):
        providers=self.providers()
        return {'configured':bool(providers),'speak':bool(providers),'listen':bool(self.config.azure_speech_key),
            'providers':providers,'default_voice':self.config.tts_default_voice,'streaming':False,
            'budget_required':True,'canvas':{'enabled':self.config.voice_canvas_enabled,
                'max_panels':self.config.voice_canvas_max_panels}}

    def locale(self,value=None):
        value=value or self.config.tts_locale
        if not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}',value):
            raise CoordinationError('voice_locale','Use a language tag such as tr-TR or en-US',422)
        return value

    def validate_ref(self,ref):
        try: provider,model,name=parse_voice_ref(ref)
        except ValueError as exc: raise CoordinationError('voice_ref',str(exc),422) from exc
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',model) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}',name):
            raise CoordinationError('voice_ref','Invalid model or voice identity',422)
        if provider=='openai' and (model not in {'tts-1','tts-1-hd','gpt-4o-mini-tts','gpt-4o-mini-tts-2025-12-15'} or name not in OPENAI_VOICES):
            raise CoordinationError('voice_ref','Unsupported OpenAI speech model or voice',422)
        if provider=='openai' and model in {'tts-1','tts-1-hd'} and name not in {'alloy','echo','fable','onyx','nova','shimmer'}:
            raise CoordinationError('voice_ref','This older speech model does not support that voice',422)
        return provider,model,name

    async def resolve(self,owner,agent=None,explicit=None,locale=None):
        aid=self.profiles.canonical(agent or 'speda'); locale=self.locale(locale)
        async with self.sessions() as db: row=await db.get(VoicePreference,(owner,aid))
        saved=copy.deepcopy(row.settings) if row else {}
        scoped=saved.pop('languages',{}).get(locale.split('-')[0],{})
        merged={**saved,**scoped}
        default='elevenlabs:eleven_v4:'+DEFAULT_VOICES[aid] if aid in DEFAULT_VOICES else self.config.tts_default_voice
        ref=explicit or merged.get('voice_id') or default
        provider,model,name=self.validate_ref(ref)
        return {'agent_id':aid,'voice':ref,'provider':provider,'model':model,'name':name,'locale':locale,
            'settings':{k:v for k,v in merged.items() if k in TUNING},'version':row.version if row else 0,'default_voice':default}

    async def agents(self,owner):
        agents=[]
        for p in self.profiles.roster():
            aid=p['agent_id']
            resolved=await self.resolve(owner,aid)
            async with self.sessions() as db: row=await db.get(VoicePreference,(owner,aid))
            saved=copy.deepcopy(row.settings) if row else {}
            languages={}
            for code in ('tr','en'):
                scoped=await self.resolve(owner,aid,locale=code)
                pin=saved.get('languages',{}).get(code,{})
                languages[code]={'voice_id':pin.get('voice_id'),'effective_voice':scoped['voice'],
                    'voice_settings':{k:v for k,v in pin.items() if k in TUNING} or None,'effective_voice_settings':scoped['settings'] or None}
            agents.append({'agent_id':aid,'name':p['name'],'domain':p['domain'],'version':resolved['version'],'default_voice':resolved['default_voice'],
                'voice_id':saved.get('voice_id'),'effective_voice':resolved['voice'],'voice_settings':{k:v for k,v in saved.items() if k in TUNING} or None,
                'effective_voice_settings':resolved['settings'] or None,'languages':languages})
        return {'agents':agents}

    async def update(self,owner,agent,patch):
        aid=self.profiles.canonical(agent)
        values=patch.model_dump(exclude_unset=True)
        expected=values.pop('expected_version',None); language=values.pop('language',None)
        if values.get('voice_id'): self.validate_ref(values['voice_id'])
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            row=await db.get(VoicePreference,(owner,aid))
            if expected is not None and expected!=(row.version if row else 0): raise CoordinationError('voice_version','Voice preferences changed; reread them')
            if not row: row=VoicePreference(user_id=owner,agent_id=aid,settings={},version=0); db.add(row)
            settings=copy.deepcopy(row.settings)
            scope=settings.setdefault('languages',{}).setdefault(language,{}) if language else settings
            if not values:
                for key in list(scope):
                    if key!='languages': scope.pop(key)
            else:
                for key,value in values.items():
                    if value is None or value=='': scope.pop(key,None)
                    else: scope[key]=value
            row.settings=settings; row.version+=1
        return next(a for a in (await self.agents(owner))['agents'] if a['agent_id']==aid)

    async def execute(self,owner,request_hash,request_id,route,units,operation):
        identity=request_id or request_hash
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            prior=await db.get(SpeechAttempt,(owner,identity))
            if prior and prior.request_hash!=request_hash: raise CoordinationError('speech_conflict','Speech identity belongs to different input')
            if not prior:
                prior=await db.scalar(select(SpeechAttempt).where(SpeechAttempt.user_id==owner,SpeechAttempt.request_hash==request_hash,
                    SpeechAttempt.status.in_(['started','unknown','complete','admitted'])).limit(1))
            if prior:
                if prior.status=='complete': return prior.result
                if prior.status=='rejected' and not prior.budget_id: prior.status='admitted'
                else: raise CoordinationError('speech_held','Speech attempt is '+prior.status+'; no paid request was replayed')
            else: db.add(SpeechAttempt(user_id=owner,request_id=identity,request_hash=request_hash,status='admitted'))
        budget_id=None
        try:
            budget_id=await self.budget.reserve_external(owner,route,units,'speech:'+identity[:64])
            async with self.sessions() as db,db.begin():
                await db.execute(update(SpeechAttempt).where(SpeechAttempt.user_id==owner,SpeechAttempt.request_id==identity).values(status='started',budget_id=budget_id))
            result=await operation()
            async with self.sessions() as db,db.begin():
                await db.execute(update(SpeechAttempt).where(SpeechAttempt.user_id==owner,SpeechAttempt.request_id==identity).values(status='complete',result=result))
            await self.budget.finish(budget_id,'complete')
            return result
        except BaseException as exc:
            async def settle():
                async with self.sessions() as db,db.begin():
                    await db.execute(update(SpeechAttempt).where(SpeechAttempt.user_id==owner,SpeechAttempt.request_id==identity,
                        SpeechAttempt.status!='complete').values(status='unknown' if budget_id else 'rejected'))
                if budget_id: await self.budget.finish(budget_id,'unknown')
            await asyncio.shield(settle())
            raise

    async def speak(self,owner,body,*,cache_scope=None):
        resolved=await self.resolve(owner,body.agent_id,body.voice,body.locale)
        p,m,n=resolved['provider'],resolved['model'],resolved['name']
        if p not in self.providers(): raise CoordinationError('voice_unconfigured','The selected voice provider is not configured',503)
        spoken=strip_for_speech(body.text,resolved['locale'])
        if not spoken or len(spoken)>MAX_CHARS: raise CoordinationError('voice_text','Speech text must contain 1 to 3000 characters',422)
        if p=='azure':
            region=self.config.azure_speech_region
            if not re.fullmatch(r'[a-z0-9-]{1,40}',region): raise CoordinationError('voice_region','Invalid Azure Speech region',422)
            wire=build_ssml(spoken,n,resolved['locale'])
            url=f'https://{region}.tts.speech.microsoft.com/cognitiveservices/v1'
            kwargs={'headers':{'Ocp-Apim-Subscription-Key':self.config.azure_speech_key,'Content-Type':'application/ssml+xml',
                'X-Microsoft-OutputFormat':'audio-24khz-48kbitrate-mono-mp3','User-Agent':'speda-mark-vi'},'content':wire.encode()}
            units=len(wire.encode())
        elif p=='openai':
            url='https://api.openai.com/v1/audio/speech'
            payload={'model':m,'voice':n,'input':spoken,'response_format':'mp3'}
            if m.startswith('gpt-4o-mini-tts'): payload['instructions']='Speak naturally in '+resolved['locale']+'.'
            kwargs={'headers':{'Authorization':'Bearer '+self.config.openai_api_key},'json':payload}
            units=len(json.dumps(payload,ensure_ascii=False).encode())*8  # Conservative input plus generated-audio ceiling per character.
        else:
            url='https://api.elevenlabs.io/v1/text-to-speech/'+quote(n,safe='')
            payload={'text':spoken,'model_id':m}
            if resolved['settings']: payload['voice_settings']=resolved['settings']
            if m!='eleven_multilingual_v2': payload['language_code']=resolved['locale'].split('-')[0]
            kwargs={'headers':{'xi-api-key':self.config.elevenlabs_api_key},'params':{'output_format':'mp3_44100_128'},'json':payload}
            units=len(spoken.encode())
        speech_input={'kind':'tts','resolved':resolved,'spoken':spoken}
        if cache_scope is not None:
            # A later automation firing has its own receipt, even if its reply
            # repeats earlier text whose cached audio has since expired.
            speech_input['scope']=cache_scope
        fingerprint=digest(speech_input)
        async def generate():
            status,audio=await request_bytes('POST',url,maximum=12*1024*1024,timeout=45,**kwargs)
            if status>=300 or not audio or not (audio.startswith(b'ID3') or (len(audio)>1 and audio[0]==255 and audio[1]&224==224)):
                raise CoordinationError('voice_response','Speech provider did not return verifiable MP3 audio',503)
            folder=Path(self.config.temp_outputs_dir); folder.mkdir(parents=True,exist_ok=True)
            path=folder/(uuid4().hex+'.mp3')
            await asyncio.to_thread(path.write_bytes,audio)
            return {'path':str(path.resolve()),'content_type':'audio/mpeg','voice':resolved['voice'],'spoken_text':spoken}
        result=await self.execute(owner,fingerprint,body.request_id,'speech:tts:'+p+':'+m,units,generate)
        path=Path(result['path'])
        if not path.is_relative_to(Path(self.config.temp_outputs_dir).resolve()) or not path.is_file():
            raise CoordinationError('speech_result_missing','The recorded audio artifact is unavailable; no paid request was replayed',409)
        return result

    async def listen(self,owner,audio,locale=None,request_id=None):
        locale=self.locale(locale)
        if not self.config.azure_speech_key: raise CoordinationError('voice_unconfigured','Azure voice recognition is not configured',503)
        if not audio or len(audio)>2*1024*1024: raise CoordinationError('voice_audio','Use one PCM WAV utterance up to 60 seconds',422)
        try:
            with wave.open(io.BytesIO(audio),'rb') as recording:
                if (recording.getnchannels(),recording.getsampwidth(),recording.getframerate(),recording.getcomptype())!=(1,2,16000,'NONE'): raise ValueError()
                frames=recording.getnframes(); duration=frames/16000
                if not 0<duration<=60 or len(recording.readframes(frames))!=frames*2: raise ValueError()
        except (wave.Error,EOFError,ValueError): raise CoordinationError('voice_audio','Expected complete 16 kHz mono 16-bit PCM WAV, up to 60 seconds',422)
        region=self.config.azure_speech_region
        if not re.fullmatch(r'[a-z0-9-]{1,40}',region): raise CoordinationError('voice_region','Invalid Azure Speech region',422)
        async def recognize():
            status,raw=await request_bytes('POST',f'https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1',
                maximum=64000,timeout=45,content=audio,params={'language':locale,'profanity':'raw'},
                headers={'Ocp-Apim-Subscription-Key':self.config.azure_speech_key,'Content-Type':'audio/wav; codecs=audio/pcm; samplerate=16000','User-Agent':'speda-mark-vi'})
            if status>=300: raise CoordinationError('voice_response','Azure did not confirm recognition',503)
            try: data=json.loads(raw)
            except ValueError: raise CoordinationError('voice_response','Recognition response was not valid JSON',503)
            state=data.get('RecognitionStatus') if isinstance(data,dict) else None
            if state in {'NoMatch','InitialSilenceTimeout','BabbleTimeout'}: return {'text':'','locale':locale}
            if state!='Success' or not isinstance(data.get('DisplayText'),str) or len(data['DisplayText'])>16000:
                raise CoordinationError('voice_response','Recognition response was not a bounded transcript',503)
            return {'text':data['DisplayText'].strip(),'locale':locale}
        import hashlib
        return await self.execute(owner,digest({'kind':'stt','audio':hashlib.sha256(audio).hexdigest(),'locale':locale}),
            request_id,'speech:stt:azure:seconds',math.ceil(duration),recognize)

    async def voices(self):
        rows=[]; errors=[]
        for model in ('gpt-4o-mini-tts','gpt-4o-mini-tts-2025-12-15','tts-1','tts-1-hd'):
            if 'openai' not in self.providers(): break
            names=OPENAI_VOICES if model.startswith('gpt-4o') else {'alloy','echo','fable','onyx','nova','shimmer'}
            rows.extend({'id':f'openai:{model}:{name}','name':name,'provider':'openai','model':model,'locale':'','gender':'','display':name+' · '+model} for name in sorted(names))
        for provider in self.providers():
            if provider=='openai': continue
            url='https://api.elevenlabs.io/v1/voices' if provider=='elevenlabs' else f'https://{self.config.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/voices/list'
            if provider=='azure' and not re.fullmatch(r'[a-z0-9-]{1,40}',self.config.azure_speech_region): errors.append(provider); continue
            headers={'xi-api-key':self.config.elevenlabs_api_key} if provider=='elevenlabs' else {'Ocp-Apim-Subscription-Key':self.config.azure_speech_key}
            try:
                status,raw=await request_bytes('GET',url,headers=headers,maximum=1024*1024,timeout=5)
                if status>=300: raise ValueError()
                data=json.loads(raw)
                values=data.get('voices',[]) if provider=='elevenlabs' and isinstance(data,dict) else data
                if not isinstance(values,list): raise ValueError()
                for value in values[:300]:
                    if not isinstance(value,dict): continue
                    name=value.get('voice_id') if provider=='elevenlabs' else value.get('ShortName')
                    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}',name): continue
                    model='eleven_v4' if provider=='elevenlabs' else 'neural'
                    display=value.get('name') or value.get('DisplayName') or name
                    rows.append({'id':f'{provider}:{model}:{name}','name':str(display)[:120],'provider':provider,'model':model,
                        'locale':value.get('Locale',''),'gender':value.get('Gender',''),'display':str(display)[:120]})
            except Exception: errors.append(provider)
        return {'voices':rows,'unavailable_providers':errors}
