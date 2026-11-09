"""The model may submit bounded incident evidence without owning delivery."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.damage_control import turn_facts
from app.services.incident_details import clean_message
from app.skills.base import Skill


class IncidentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tool_failure", "unexpected_output", "missing_expected_data", "inconsistent_state",
                  "unexpected_side_effect", "service_unavailable", "other_unexpected_event"]
    impact: Literal["continuing", "blocked", "outcome_uncertain"]
    observation: str | None = Field(default=None, max_length=650, description=(
        "Briefly state the failed operation, expected behavior, actual result and impact you directly observed. "
        "Omit credentials, private message contents, input payloads and speculation."
    ))


class ReportIncidentSkill(Skill):
    name = "report_incident"
    description = (
        "Queue a Damage Control incident for an error or unexpected event you directly observed. "
        "Call promptly even if you can recover and continue. "
        "Choose the closest category and impact; omit conjecture and incidents already captured by the runtime. "
        "The returned ID confirms local capture for the owner's incident report."
    )
    input_schema = IncidentObservation.model_json_schema()

    def __init__(self, protocol):
        self.protocol = protocol

    async def execute(self, args, context):
        observation = IncidentObservation.model_validate(args)
        facts = {**turn_facts(context), "event": "agent_observed_incident", "kind": observation.kind,
                 "impact": observation.impact}
        if summary := clean_message(observation.observation):
            facts["summary"] = summary
        identity = self.protocol.capture(facts)
        return f"Incident recorded as {identity}." if identity else "Incident capture failed."
