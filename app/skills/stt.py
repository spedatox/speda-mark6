import asyncio
import json
from pathlib import Path
from app.skills.base import Skill


class STTSkill(Skill):
    name = "speech_to_text"
    deferred=True
    memoize=False
    description = (
        "Transcribe one owner-provided PCM WAV utterance through the budgeted speech service. "
        "Use for an existing audio artifact; the microphone UI uses this service directly. "
        "Only files inside the configured output folder are accepted; do not request host files. "
        "Returns audio evidence, which becomes an owner assertion only when the owner submits or confirms it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "audio_path": {
                "type": "string",
                "description": "Existing PCM WAV artifact in the outputs folder.",
                "maxLength":500,
            },
            "language": {
                "type": "string",
                "description": "ISO 639-1 language code. Defaults to auto-detect.",
                "default": "auto",
            },
        },
        "required": ["audio_path"],
    }

    async def execute(self,args,context):
        service=getattr(context.coordination,'voice',None)
        if not service: return '{"status":"unavailable"}'
        if context.task_role=='worker' or context.user_id!=1: return '{"status":"rejected"}'
        await context.coordination.ensure_active(context)
        path=Path(args['audio_path']).resolve(); root=Path(service.config.temp_outputs_dir).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>2*1024*1024:
            return '{"status":"rejected","detail":"Expected an existing audio artifact inside the outputs folder, up to 2 MiB."}'
        audio=await asyncio.to_thread(path.read_bytes)
        language=args.get('language')
        result=await service.listen(context.user_id,audio,None if language=='auto' else language)
        return json.dumps(result | {'authority':'audio_transcript_not_owner_assertion'},ensure_ascii=False)
