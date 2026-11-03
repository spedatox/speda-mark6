# SPDX-License-Identifier: AGPL-3.0-or-later
from pydantic import BaseModel,ConfigDict,Field


class ReminderOption(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=40)
    value:str=Field(default='',max_length=24)


class ReminderDefinitionIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=1,max_length=2000)
    agent:str=Field(default='speda',max_length=32)
    at:str=Field(default='',max_length=5)
    days:str=Field(default='*',max_length=32)
    options:list[ReminderOption|str]=Field(default_factory=list,max_length=6)
    every_minutes:int=Field(default=5,ge=1,le=1440)
    max_asks:int=Field(default=10,ge=1,le=200)
    enabled:bool=True
    expected_version:int|None=Field(default=None,ge=0)


class ReminderSpec(ReminderDefinitionIn):
    id:str=Field(min_length=1,max_length=64,pattern=r'^[\w-]+$')


class ReminderTickRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    agent:str=Field(default='speda',max_length=32)
    reminders:list[ReminderSpec]=Field(default_factory=list,max_length=50)


class ReminderAnswerRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cycle_id:int=Field(default=0,ge=0)
    reminder_id:str=Field(default='',max_length=64)
    answer:str=Field(default='done',min_length=1,max_length=64)
    expected_version:int|None=Field(default=None,ge=1)
