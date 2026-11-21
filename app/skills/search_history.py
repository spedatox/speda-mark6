"""Legacy literal archive tool as a view over the single native lookup budget."""
from app.skills.base import Skill


class SearchHistorySkill(Skill):
    name = 'search_history'
    read_only = True
    deferred = True
    search_keywords = 'history past conversation literal keyword date archive transcript episode recall'
    description = (
        'Find original messages and episodes in this agent\'s conversations by semantic topic, literal keyword, or owner-local date range. '
        'Use episode=true for thematic or relative recall across past sessions, or literal query/date range for exact archival wording. '
        'It shares the same one lookup batch and byte/time allowance as memory_lookup; do not call both for overlapping evidence. '
        'Returns bounded attributed excerpts, original message/source IDs and a cursor. Assistant statements are not owner assertions and partial coverage does not establish absence.')
    input_schema = {'type':'object','properties':{'query':{'type':'string','maxLength':2000},
        'after':{'type':'string','maxLength':10},'before':{'type':'string','maxLength':10},
        'episode':{'type':'boolean','description':'Rank past conversation episodes by hybrid semantic and lexical relevance.'},
        'limit':{'type':'integer','minimum':1,'maximum':8},'before_message_id':{'type':'integer','minimum':1}},
        'additionalProperties':False}

    def __init__(self, lookup): self.lookup=lookup
    def offered(self, context): return self.lookup.offered(context)
    async def execute(self, args, context):
        return await self.lookup.execute({**args,'query':args.get('query',''),'corpus':'conversation_history'},context)
