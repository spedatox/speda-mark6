"""Original frugal-mode capability backed by the single policy authority."""
from app.skills.base import Skill
from app.schemas.llm import ProviderError


class BudgetModeSkill(Skill):
    name = 'set_budget_mode'
    deferred = True
    search_keywords = 'budget cost frugal spending saving concise'
    description = ('Changes the owner preference for concise answers, minimal searches and no worker delegation. '
        'Use only when the owner asks to enable or disable budget mode. '
        'It takes effect on the next turn and never changes hard spending limits or provider prices. '
        'Returns the acknowledged persisted setting.')
    input_schema = {'type':'object','properties':{'enabled':{'type':'boolean'}},'required':['enabled'],'additionalProperties':False}

    def __init__(self, policy=None):
        self.policy = policy

    async def execute(self, args, context):
        if self.policy is None:
            raise ProviderError('not_ready', 'Policy service is unavailable')
        if context.triggered_by != 'user':
            raise ProviderError('owner_preference', 'Only an owner conversation may change budget mode')
        revision, document = await self.policy.snapshot(context.user_id)
        document.budget_mode = args['enabled']
        await self.policy.save(context.user_id, revision, document)
        return f'Budget mode {"ON" if document.budget_mode else "OFF"}; applied from the next turn. Hard spending limits remain enforced.'
