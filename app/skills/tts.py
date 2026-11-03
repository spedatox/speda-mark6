import json
from app.skills.base import Skill
from app.schemas.voice import SpeakRequest
from app.core.files import register_file


class TTSSkill(Skill):
    name = "text_to_speech"
    deferred=True
    memoize=False
    description = (
        "Generate an AI voice recording of owner-requested text with the selected agent voice. "
        "Use for an explicit spoken artifact; live voice mode already uses the same service. "
        "Do not call for automatic push or silent runs, or to translate or rewrite text. "
        "Returns a downloadable MP3; paid attempts are reserved and uncertain outcomes are never replayed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength":1,"maxLength":3000},
            "voice": {
                "type": "string",
                "description": "Optional provider:model:voice reference; omitted uses this agent's voice.",
                "maxLength":96,
            },
            "locale":{"type":"string","maxLength":16},
        },
        "required": ["text"],
    }

    async def execute(self,args,context):
        service=getattr(context.coordination,'voice',None)
        if not service: return '{"status":"unavailable"}'
        if context.task_role=='worker' or context.output_mode!='respond': return '{"status":"rejected","detail":"Voice artifacts require an owner-facing request."}'
        await context.coordination.ensure_active(context)
        result=await service.speak(context.user_id,SpeakRequest(**args,agent_id=context.agent_id))
        return json.dumps({'status':'complete','file':register_file(context,result['path'],'AI voice recording'),'spoken_text':result['spoken_text']},ensure_ascii=False)
