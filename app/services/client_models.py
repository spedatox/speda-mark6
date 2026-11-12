"""Legacy presentation over the single revisioned native provider policy."""
from app.schemas.llm import ThinkingRequest
from app.services.model_policy import cyber_wire, parse_model_ref, thinking_wire


def present_thinking(request):
    if request is None or request.mode == "provider_default":
        return "default"
    if request.mode in {"off", "enabled"}:
        return request.mode
    return str(request.value) if request.mode == "effort" else f"budget:{request.value}"


def choices(spec):
    result = ["default"]
    if spec.control == "none":
        return result
    if spec.can_disable:
        result.append("off")
    if spec.control in {"effort", "adaptive"}:
        result.extend(spec.efforts)
    if spec.control in {"toggle", "adaptive"}:
        result.append("enabled")
    if spec.control == 'budget':
        result.extend(f'budget:{n}' for n in sorted({spec.budget_min, 2048, 4096, 8192, 16384, spec.budget_max})
                      if spec.budget_min <= n <= spec.budget_max)
    return list(dict.fromkeys(result))


async def catalog(policy, owner=1, discovery=None):
    revision, document = await policy.snapshot(owner)
    rows = (await discovery.available(policy, document) if discovery is not None else
            [{"id": spec.ref, "name": parse_model_ref(spec.ref)[1]} for spec in policy.specs(document).values()])
    return [{"id": spec.ref, "name": row['name'],
        "description": f"{spec.protocol}; native {spec.control} reasoning control",
        "provider": parse_model_ref(spec.ref)[0], "tags": ["vision"] if spec.vision else [],
        "thinking": present_thinking(document.models.get(spec.ref)),
        "thinking_pinned": spec.ref in document.models,
        "thinking_supported": spec.control != "none", "thinking_options": choices(spec),
        "thinking_control": spec.control, "thinking_budget_min": spec.budget_min,
        "thinking_budget_max": spec.budget_max, "policy_revision": revision,
        "daybreak_program": spec.daybreak_program,
        "daybreak_selection": document.cyber_programs.get(spec.ref),
        "daybreak_required": spec.daybreak_program == "daybreak_red"}
        for row in rows for spec in [policy.descriptor(row['id'], document)]]


async def set_thinking(policy, owner, model, level):
    revision, document = await policy.snapshot(owner)
    spec = policy.descriptor(model, document)
    if level is None:
        document.models.pop(model, None)
    else:
        if level in {"default", "provider_default"}:
            request = ThinkingRequest()
        elif level in {"off", "enabled"}:
            request = ThinkingRequest(mode=level)
        elif level.startswith("budget:"):
            request = ThinkingRequest(mode="budget", value=int(level.partition(":")[2]))
        else:
            request = ThinkingRequest(mode="effort", value=level)
        thinking_wire(spec, request)  # exact native mapping; never round one level to another
        document.models[model] = request
    await policy.save(owner, revision, document)
    return {"thinking": present_thinking(document.models.get(model)), "thinking_pinned": model in document.models,
            "policy_revision": revision + 1}


async def set_daybreak(policy, owner, model, enabled):
    revision, document = await policy.snapshot(owner)
    spec = policy.descriptor(model, document)
    if spec.daybreak_program is None:
        raise ValueError("This model has no declared Daybreak support")
    program = spec.daybreak_program if enabled else "standard"
    cyber_wire(spec, program)
    document.cyber_programs[model] = program
    await policy.save(owner, revision, document)
    return {"daybreak_program": spec.daybreak_program, "daybreak_selection": program,
            "daybreak_required": spec.daybreak_program == "daybreak_red", "policy_revision": revision + 1}
