from abc import ABC, abstractmethod
from typing import Literal


class AgentProfile(ABC):
    """
    ABC for all agent identity profiles.
    Fork this for each of the Superior Six — swap the profile, the engine is untouched.
    """

    name: str
    system_prompt_template: str
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"

    @abstractmethod
    def build_system_prompt(self, context_vars: dict) -> str:
        """Build the full system prompt string from the template and runtime context vars."""
        ...

    def allocate_model(
        self,
        triggered_by: Literal["user", "n8n", "agent"],
        is_background: bool = False,
    ) -> str:
        """
        SPEDA governs model allocation — agents do not decide independently (D-C4).
        - User-facing interactive → Sonnet 4.6
        - Background / automated → Haiku 4.5
        """
        if is_background or triggered_by in ("n8n", "agent"):
            return self.haiku_model
        return self.sonnet_model
