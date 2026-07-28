"""Wire schemas for the n8n-facing reminder tick. See services/reminders.py."""

from pydantic import BaseModel, Field


class ReminderOption(BaseModel):
    """One answer button. `label` is what the owner sees and can be reworded
    freely; `value` is what gets recorded, so it stays short and stable."""

    label: str = Field(max_length=40)
    value: str = Field(default="", max_length=24)


class ReminderSpec(BaseModel):
    """One reminder definition, as written in the n8n config node."""

    id: str = Field(max_length=64, description="Stable key, e.g. 'medicine_morning'.")
    text: str = Field(max_length=2000, description="The question, sent verbatim.")
    # "HH:MM" wall clock. Empty = due whenever the tick runs.
    at: str = Field(default="", max_length=5)
    # "*" or cron-style weekday numbers, 1=Monday … 7=Sunday.
    days: str = Field(default="*", max_length=32)
    options: list[ReminderOption | str] = Field(default_factory=list, max_length=6)
    every_minutes: int = Field(default=5, ge=1, le=1440)
    max_asks: int = Field(default=10, ge=1, le=200)


class ReminderTickRequest(BaseModel):
    # Which agent asks — its Telegram bot sends, in its voice.
    agent: str = Field(default="speda", max_length=32)
    reminders: list[ReminderSpec] = Field(default_factory=list, max_length=50)


class ReminderTickResponse(BaseModel):
    status: str
    agent: str = ""
    sent: list[dict] = Field(default_factory=list)
    gave_up: list[str] = Field(default_factory=list)
    waiting: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class ReminderAnswerRequest(BaseModel):
    """Manual close — used by the desktop UI and for testing. The normal paths
    are a button tap (gateway) and the agent's `reminders` tool."""

    cycle_id: int = 0
    reminder_id: str = Field(default="", max_length=64)
    answer: str = Field(default="done", max_length=64)
