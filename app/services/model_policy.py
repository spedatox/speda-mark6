"""Revisioned owner policy and one deterministic thinking resolver for every caller."""
from time import monotonic

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.model_policy import ModelPolicyState
from app.schemas.llm import EffectivePolicy, ModelSpec, PolicyDocument, ThinkingRequest


PROVIDERS = frozenset({"anthropic", "openai", "gemini", "vertex", "zai", "deepseek", "nvidia", "ollama", "kimi"})


def parse_model_ref(ref: str):
    provider, sep, model = ref.partition(":")
    if not sep:
        if not ref.strip():
            raise ValueError("Empty model route")
        return "anthropic", ref
    if provider not in PROVIDERS or not model:
        raise ValueError("Unsupported or empty provider:model route")
    return provider, model


def thinking_wire(spec: ModelSpec, request: ThinkingRequest):
    """No silent substitutions: advertised controls must have an exact mapping."""
    if request.mode == "provider_default":
        if request.value is not None:
            raise ValueError("Provider default cannot contain a value")
        return {}
    if request.mode == "inherit":
        raise ValueError("Inherit requires a parent effective policy")
    if request.mode not in {"effort", "budget"} and request.value is not None:
        raise ValueError("This thinking mode does not accept a value")
    provider, _ = parse_model_ref(spec.ref)
    if spec.control == "none":
        raise ValueError("This model has no declared thinking control")
    if request.mode == "off":
        if not spec.can_disable:
            raise ValueError("This model cannot disable thinking; select a supported effort")
        if provider == "openai":
            return {"reasoning": {"effort": "none"}} if spec.protocol == "responses" else {"reasoning_effort": "none"}
        if spec.protocol == "google":
            return {"thinkingConfig": {"thinkingBudget": 0}}
        return {"thinking": {"type": "disabled"}}
    if request.mode == "enabled":
        if spec.control == "toggle":
            return {"thinking": {"type": "enabled"}}
        if spec.control == "adaptive":
            return {"thinking": {"type": "adaptive"}}
        raise ValueError("Choose a native effort or token budget for this model")
    if request.mode == "budget":
        if spec.control != "budget" or type(request.value) is not int or not spec.budget_min <= request.value <= spec.budget_max:
            raise ValueError("Thinking budget is outside this model's supported range")
        if spec.protocol == "google":
            return {"thinkingConfig": {"thinkingBudget": request.value}}
        return {"thinking": {"type": "enabled", "budget_tokens": request.value}}
    if spec.control not in {"effort", "adaptive"} or request.value not in spec.efforts:
        raise ValueError("Unsupported native effort for this model")
    if spec.protocol == "google":
        return {"thinkingConfig": {"thinkingLevel": request.value}}
    if spec.protocol == "responses":
        return {"reasoning": {"effort": request.value}}
    if spec.protocol == "anthropic":
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": request.value}}
    if provider in {"deepseek", "zai"}:
        return {"thinking": {"type": "enabled"}, "reasoning_effort": request.value}
    return {"reasoning_effort": request.value}


class ModelPolicy:
    def __init__(self, sessions, profile, settings):
        self.sessions, self.profile, self.settings = sessions, profile, settings

    def specs(self, document=None):
        specs = {s.ref: s for s in self.profile.model_specs}
        for spec in (document or PolicyDocument()).descriptors:
            parse_model_ref(spec.ref)
            specs[spec.ref] = spec
        return specs

    def descriptor(self, ref, document=None):
        specs = self.specs(document)
        if ref in specs:
            return specs[ref]
        provider, _ = parse_model_ref(ref)
        if provider == "anthropic" and ref.startswith("anthropic:") and ref.partition(":")[2] in specs:
            return specs[ref.partition(":")[2]].model_copy(update={"ref": ref})
        protocol = {"anthropic": "anthropic", "openai": "responses", "gemini": "google", "vertex": "google"}.get(provider, "chat")
        # Unknown models are usable at provider default; never fabricate their knobs.
        return ModelSpec(ref=ref, protocol=protocol)

    async def snapshot(self, user_id=1):
        async with self.sessions() as db:
            state = await db.get(ModelPolicyState, user_id)
            return (state.revision, PolicyDocument.model_validate(state.document)) if state else (0, PolicyDocument())

    def validate(self, document):
        from app.profiles.workers import normalize_worker_mapping
        # Also cover programmatic updates to mutable PolicyDocument dictionaries.
        document.worker_models = normalize_worker_mapping(document.worker_models)
        document.workers = normalize_worker_mapping(document.workers)
        if document.main_model:
            parse_model_ref(document.main_model)
        if document.background_model:
            parse_model_ref(document.background_model)
        for ref in [*document.agent_models.values(),*document.telegram_models.values()]:
            parse_model_ref(ref)
        for ref in document.worker_models.values():
            parse_model_ref(ref)
        for spec in document.descriptors:
            provider, _ = parse_model_ref(spec.ref)
            expected = {"anthropic": "anthropic", "openai": "responses", "gemini": "google", "vertex": "google"}.get(provider, "chat")
            if spec.protocol != expected and not (provider == "openai" and spec.protocol == "chat"):
                raise ValueError("Model protocol does not match its provider connection")
        for ref, request in document.models.items():
            thinking_wire(self.descriptor(ref, document), request)
        for role, request in document.roles.items():
            if role == "worker" and request.mode == "inherit":
                continue
            routes = (set(document.agent_models.values()) | set(document.telegram_models.values())) or {document.main_model or self.settings.llm_main_model or self.profile.allocate_model("user")}
            if role == 'worker':
                routes |= set(document.worker_models.values())
            if role == "background" and document.background_model:
                routes = {document.background_model}
            for ref in routes:
                thinking_wire(self.descriptor(ref, document), request)
        for agent, request in document.agents.items():
            ref = document.agent_models.get(agent) or document.main_model or self.settings.llm_main_model or self.profile.allocate_model("user")
            thinking_wire(self.descriptor(ref, document), request)
            if agent in document.telegram_models: thinking_wire(self.descriptor(document.telegram_models[agent],document),request)
        parent_routes = set(document.agent_models.values()) | set(document.telegram_models.values()) | {document.main_model or self.settings.llm_main_model or self.profile.allocate_model('user')}
        for worker, request in document.workers.items():
            if request.mode == 'inherit':
                continue  # The admitted parent is required for exact validation.
            routes = {document.worker_models[worker]} if worker in document.worker_models else parent_routes
            for ref in routes:
                thinking_wire(self.descriptor(ref, document), request)
        for ref in document.fallbacks:
            parse_model_ref(ref)

    async def save(self, user_id, expected_revision, document):
        self.validate(document)
        try:
            async with self.sessions() as db, db.begin():
                from app.models.user import User
                if await db.get(User, user_id) is None:
                    db.add(User(id=user_id, name="Owner", timezone="Europe/Istanbul"))
                    await db.flush()
                if expected_revision == 0:
                    if await db.get(ModelPolicyState, user_id):
                        raise ValueError("Policy revision conflict")
                    db.add(ModelPolicyState(user_id=user_id, revision=1, document=document.model_dump(mode="json")))
                else:
                    result = await db.execute(update(ModelPolicyState).where(
                        ModelPolicyState.user_id == user_id, ModelPolicyState.revision == expected_revision
                    ).values(revision=expected_revision+1, document=document.model_dump(mode="json")))
                    if result.rowcount != 1:
                        raise ValueError("Policy revision conflict")
        except IntegrityError as exc:
            raise ValueError("Policy revision conflict or missing owner") from exc
        return {"revision": expected_revision+1, "policy": document.model_dump(mode="json")}

    async def resolve(self, *, model=None, user_id=1, role="interactive", agent_id="speda",
                      request=None, parent=None, snapshot=None, deadline=None):
        revision, doc = snapshot if snapshot is not None else await self.snapshot(user_id)
        model = model or (doc.background_model if role == "background" else None) or doc.agent_models.get(agent_id)
        model = model or (self.settings.llm_background_model if role == "background" else None)
        model = model or doc.main_model or self.settings.llm_main_model
        model = model or (self.profile.allocate_model("user", is_background=True) if role == "background" else self.profile.allocate_model("user"))
        spec = self.descriptor(model, doc)
        source = "provider_default"
        if request is not None:
            selected, source = request, "turn"
        elif agent_id in doc.agents:
            selected, source = doc.agents[agent_id], "agent"
        elif role in doc.roles:
            selected, source = doc.roles[role], "role"
        elif model in doc.models:
            selected, source = doc.models[model], "model"
        else:
            selected = ThinkingRequest()
        if selected.mode == "inherit":
            if parent is None:
                raise ValueError("Inherit requires a parent policy")
            selected, source = parent.requested, "parent"
        wire = thinking_wire(spec, selected)
        return EffectivePolicy(spec, selected, wire, source, revision, doc.show_thinking,
            deadline or monotonic()+self.settings.llm_request_timeout_seconds,
            tuple(doc.fallbacks), doc)

    async def catalog(self, user_id=1):
        revision, document = await self.snapshot(user_id)
        return {"revision": revision, "models": [s.model_dump(mode="json") for s in self.specs(document).values()]}

    async def resolve_worker_policy(self, worker_id, *, parent=None, model=None, user_id=1,
                                    agent_id='speda', snapshot=None, deadline=None, request=None):
        """Resolve only from the admitted snapshot; no provider calls or budget grants.

        The explicit per-worker model wins over the parent/model fallback. An
        owner-pinned worker thinking setting wins over a requested child setting.
        When routes change, an absent worker setting uses the target model's
        native setting/default, rather than translating the parent's effort.
        """
        from app.profiles.workers import canonical_worker_id
        worker = canonical_worker_id(worker_id)
        revision, doc = snapshot if snapshot is not None else (
            (parent.revision, parent.snapshot) if parent is not None else await self.snapshot(user_id))
        route = doc.worker_models.get(worker) or model or (parent.spec.ref if parent else None)
        if route is None:
            base = await self.resolve(user_id=user_id, agent_id=agent_id,
                snapshot=(revision, doc), deadline=deadline)
            route = base.spec.ref
        else:
            base = parent
        spec = self.descriptor(route, doc)
        inherited_route = model or (parent.spec.ref if parent is not None else None)
        changed_route = inherited_route is not None and route != inherited_route
        if worker in doc.workers:
            selected, source = doc.workers[worker], 'worker'
        elif request is not None:
            selected, source = request, 'worker_request'
        elif 'worker' in doc.roles and not (changed_route and doc.roles['worker'].mode == 'inherit'):
            selected, source = doc.roles['worker'], 'worker_role'
        elif base is not None and not changed_route:
            selected, source = base.requested, 'parent'
        elif route in doc.models:
            selected, source = doc.models[route], 'model'
        else:
            selected, source = ThinkingRequest(), 'provider_default'
        if selected.mode == 'inherit':
            if parent is None:
                base = await self.resolve(model=model or route, user_id=user_id, agent_id=agent_id,
                    snapshot=(revision, doc), deadline=deadline)
            else:
                base = parent
            selected, source = base.requested, 'parent'
        applied = thinking_wire(spec, selected)
        return EffectivePolicy(spec, selected, applied, source, revision, doc.show_thinking,
            deadline or (parent.deadline if parent is not None else monotonic()+self.settings.llm_request_timeout_seconds),
            (), doc)
