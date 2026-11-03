# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class VoiceAgentPatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int|None=Field(default=None,ge=0)
    language:Literal['tr','en']|None=None
    voice_id:str|None=Field(default=None,max_length=96)
    stability:float|None=Field(default=None,ge=0,le=1)
    similarity_boost:float|None=Field(default=None,ge=0,le=1)
    style:float|None=Field(default=None,ge=0,le=1)
    speed:float|None=Field(default=None,ge=0.7,le=1.2)
    use_speaker_boost:bool|None=None


class SpeakRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=1,max_length=3000)
    agent_id:str|None=Field(default=None,max_length=48)
    voice:str|None=Field(default=None,max_length=96)
    locale:str|None=Field(default=None,max_length=16)
    request_id:str|None=Field(default=None,max_length=64,pattern=r'^[\w-]+$')
