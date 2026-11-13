"""Presentation choices derived from the same routing declarations as execution."""
from app.profiles.workers import WORKER_SPECS
from app.services.client_models import choices


def options(values):
    return [{'value': value, 'label': str(value).replace('_', ' ').capitalize()} for value in dict.fromkeys(values)]


async def decorate_policies(policies, policy, document, profiles, discovery=None):
    declared = policy.specs(document)
    rows = await discovery.available(policy, document) if discovery else []
    refs = list(dict.fromkeys([*(row['id'] for row in rows), *declared,
        *filter(None, [document.main_model, document.background_model,
            policy.settings.llm_main_model, policy.settings.llm_background_model]),
        *document.agent_models.values(), *document.telegram_models.values(), *document.worker_models.values(),
        *document.fallbacks, *document.models, *document.cyber_programs,
        *(ref for item in policies for ref in item['value'].get('prices', {}))]))
    names = {row['id']: row['name'] for row in rows}
    model_options = [{'value': ref, 'label': f'{names.get(ref, ref)}' if names.get(ref, ref) == ref
        else f'{names[ref]} · {ref}'} for ref in refs]
    agents = [{'value': p.agent_id, 'label': p.name} for p in profiles.profiles.values()]
    workers = options(WORKER_SPECS)
    for item in policies:
        schema = item['schema']
        fields = schema['properties']
        if 'prices' in fields:
            fields['prices']['x-key-options'] = model_options
        if item['id'] == 'models':
            schema['x-thinking-options'] = {ref: choices(policy.descriptor(ref, document)) for ref in refs}
            schema['x-route-defaults'] = {
                'main': policy.settings.llm_main_model or policy.profile.allocate_model('user'),
                'background': policy.settings.llm_background_model or policy.profile.allocate_model('user', is_background=True),
            }
            for name in ('main_model', 'background_model'):
                fields[name]['anyOf'][0]['x-options'] = model_options
            for name in ('agent_models', 'telegram_models', 'worker_models'):
                fields[name]['x-key-options'] = workers if name == 'worker_models' else agents
                fields[name]['additionalProperties']['x-options'] = model_options
            for name in ('models', 'cyber_programs', 'agents', 'workers', 'roles'):
                fields[name]['x-key-options'] = (agents if name == 'agents' else workers if name == 'workers'
                    else options(['interactive', 'background', 'worker']) if name == 'roles' else model_options)
            fields['fallbacks']['items']['x-options'] = model_options
            schema['$defs']['ModelSpec']['properties']['ref']['x-options'] = model_options
            schema['$defs']['ModelSpec']['properties']['efforts']['items']['x-options'] = options(
                effort for spec in declared.values() for effort in spec.efforts)
        if item['id'] == 'global':
            fields['external_prices']['x-key-options'] = options([
                'speech:tts:azure:neural', 'speech:tts:elevenlabs:eleven_v4',
                'speech:tts:elevenlabs:eleven_multilingual_v2',
                *(f'speech:tts:openai:{name}' for name in ('tts-1', 'tts-1-hd', 'gpt-4o-mini-tts', 'gpt-4o-mini-tts-2025-12-15')),
                'speech:stt:azure:seconds',
                *item['value']['external_prices'],
            ])
        if item['id'] == 'iacon':
            for name in ('quiet_start', 'quiet_end'):
                fields[name]['x-options'] = [{'value': hour, 'label': f'{hour:02d}:00'} for hour in range(24)]
    return policies
