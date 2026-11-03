# SPDX-License-Identifier: AGPL-3.0-or-later
from pydantic import BaseModel,ConfigDict,Field


class MailScanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    automation_key:str=Field(pattern=r'^[a-f0-9]{32}$')
    automation_revision:int=Field(ge=1)
    domain:str=Field(default='',max_length=253)
    recipient:str=Field(default='',max_length=320)
    max_results:int=Field(default=10,ge=1,le=10)
    newer_than_days:int=Field(default=2,ge=1,le=30)
    include_body:bool=True
    body_chars:int=Field(default=2000,ge=200,le=2000)
    label:str=Field(default='SPEDA-Seen',max_length=64)


class MailSeenRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    automation_key:str=Field(pattern=r'^[a-f0-9]{32}$')
    automation_revision:int=Field(ge=1)
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    message_ids:list[str]=Field(default_factory=list,max_length=10)
    label:str=Field(default='SPEDA-Seen',max_length=64)
