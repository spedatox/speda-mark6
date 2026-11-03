"""Read-only evaluation evidence from native runtime authorities.

A running backend is evidence of configuration, never proof of product parity
or production acceptance. This snapshot makes that distinction explicit.
"""
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.context import AgentContext
from app.profiles.workers import WORKER_SPECS
from app.services.capability_acceptance import fingerprint


PUBLIC_SETTINGS = (
    'agent_language', 'owner_timezone', 'chat_max_output_tokens',
    'llm_request_timeout_seconds', 'memory_capture_enabled',
    'orchestration_profiles_enabled', 'orchestration_rooms_enabled',
    'project_execution_enabled', 'external_integrations_enabled',
    'library_max_bytes', 'library_max_pages', 'browser_max_chars',
    'browser_max_images', 'news_poll_enabled', 'tts_locale',
)


class BehaviorConfiguration:
    def __init__(self, coordinator, registry, policy_configuration, configuration):
        self.coordinator = coordinator
        self.registry = registry
        self.policies = policy_configuration
        self.configuration = configuration

    @staticmethod
    def source_hashes():
        root = Path(__file__).resolve().parents[1]
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in {'.py', '.md', '.json'}}

    async def snapshot(self, owner):
        coordinator = self.coordinator
        revision, policy = await coordinator.policy.snapshot(owner)
        agents, definitions, indexes, apis = {}, {}, {}, {}
        for profile in coordinator.profiles.roster():
            aid = profile['agent_id']
            persona = await coordinator.preference(owner, aid)
            ctx = AgentContext(user_id=owner, session_id=0, request_id=uuid4().hex,
                triggered_by='user', trigger_payload={}, output_mode='respond', model='',
                system_prompt='', conversation_history=[], db=None, agent_id=aid,
                allowed_tools=frozenset(persona['tools']), coordination=coordinator)
            routes = {}
            try:
                effective = await coordinator.policy.resolve(user_id=owner, agent_id=aid,
                    role='interactive', snapshot=(revision, policy))
                ctx.effective_policy = effective
                routes['model'] = effective.spec.ref
                routes['protocol'] = effective.spec.protocol
                routes['thinking'] = effective.requested.model_dump()
                background = await coordinator.policy.resolve(user_id=owner, agent_id=aid,
                    role='background', snapshot=(revision, policy))
                routes['background_model'] = background.spec.ref
            except ValueError:
                routes['configuration_error'] = 'A model route or native control is unavailable.'
            agents[aid] = {**persona, **routes}
            definitions[aid] = self.registry.list_tools(ctx, include_deferred=True)
            indexes[aid] = self.registry.discovery_index(ctx)
            apis[aid] = routes.get('protocol', 'unconfigured')
        policies = await self.policies.snapshot(owner)
        after, _ = await coordinator.policy.snapshot(owner)
        policy_revision = next(p['revision'] for p in policies['policies'] if p['id'] == 'models')
        hashes = await asyncio.to_thread(self.source_hashes)
        return {
            'version': 3, 'production_verified': False,
            'verification': 'runtime_snapshot_only',
            'source': 'Authenticated native runtime snapshot ' + datetime.now(timezone.utc).isoformat(),
            'consistent_model_revision': revision == after == policy_revision,
            'model_policy_revision': revision,
            'settings': {key: self.configuration.running[key] for key in PUBLIC_SETTINGS},
            'settings_activation': 'Running startup values; saved changes may await restart.',
            'saved_configuration_revision': self.configuration.store.read()['revision'],
            'agents': agents, 'aliases': dict(coordinator.profiles.aliases),
            'tool_definitions_by_agent': definitions, 'tool_index_by_agent': indexes,
            'tool_scope': 'Unbound interactive owner context. Room/project-specific tools require a real binding and are excluded; persona grants and the registered catalog are also provided.',
            'tool_catalog': self.registry.list_tools(include_deferred=True),
            'api_by_agent': apis, 'policies': policies['policies'],
            'worker_roles': [{
                'id': key, 'backend': spec.backend, 'read_only': spec.read_only,
                'team': spec.team, 'team_lead': spec.team_lead,
                'effort': spec.effort, 'max_tool_iterations': spec.max_iterations,
            } for key, spec in WORKER_SPECS.items()],
            'authorities': {'memory': 'Iacon', 'rooms_and_admission': 'Coordinator',
                'execution_claims': 'Forge', 'scheduler': 'n8n'},
            'app_file_hashes': hashes,
            'native_tree_sha256': fingerprint(hashes),
            'acceptance': {
                'target': 'Superior Mark VI', 'complete': False,
                'requirement': 'Every original useful outcome preserved and behaviorally verified; improvements demonstrated.',
                'scope': 'This endpoint reports runtime configuration. It does not certify behavioral parity or live acceptance.',
                'deferred_for_owner_review': ['projects', 'canvas'],
            },
        }
