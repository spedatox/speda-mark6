"""Lossless provider boundary. Portable history remains text/tool content blocks."""
import json

from app.schemas.llm import Block, LLMMessage, ProviderError


def arguments(raw):
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise ProviderError("invalid_arguments", "The model returned incomplete tool arguments") from exc
    if not isinstance(result, dict):
        raise ProviderError("invalid_arguments", "Tool arguments must be a JSON object")
    return result


def blocks(message):
    content = message.get("content", [])
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def state(message, policy):
    return next((b["payload"] for b in blocks(message) if b.get("type") == "_provider_state"
        and b.get("origin") == policy.spec.ref and b.get("protocol") == policy.spec.protocol), None)


def validate_continuation(messages, policy):
    if not messages or not any(b.get("type") == "tool_result" for b in blocks(messages[-1])):
        return
    previous = messages[-2] if len(messages) > 1 else {}
    origins = [b for b in blocks(previous) if b.get("type") == "_provider_state"]
    if origins and state(previous, policy) is None:
        raise ProviderError("continuation_origin", "This tool exchange belongs to another model; automatic migration is unavailable")


def portable_content(message):
    return [b for b in blocks(message) if b.get("type") != "_provider_state"]


def image_url(block):
    source = block["source"]
    return f"data:{source['media_type']};base64,{source['data']}"


def request_body(policy, system, messages, tools, max_tokens):
    validate_continuation(messages, policy)
    spec = policy.spec
    if not spec.vision and any(b.get("type") == "image" for m in messages for b in portable_content(m)):
        raise ValueError("This model has no declared vision capability")
    _, _, model = spec.ref.partition(":")
    if not model:
        model = spec.ref
    if isinstance(system, list):
        system = "\n\n".join(b["text"] for b in system)
    schema_tools = [{"type": "function", "function": {"name": t["name"],
        "description": t.get("description", ""), "parameters": t.get("input_schema", {"type": "object"})}} for t in tools]
    budget = policy.wire.get("thinking", {}).get("budget_tokens")
    if budget is not None and budget >= max_tokens:
        raise ValueError("Thinking budget must leave room below the output allowance")
    if spec.protocol == "anthropic":
        output = []
        for message in messages:
            payload = state(message, policy)
            content = payload if payload is not None else portable_content(message)
            # Provider-private blocks must never cross a model boundary.
            if payload is None:
                content = [b for b in content if b.get("type") in {"text", "image", "tool_use", "tool_result"}]
            output.append({"role": message["role"], "content": content})
        return {"model": model, "system": system, "messages": output,
                "max_tokens": max_tokens, **({"tools": tools} if tools else {}), **policy.wire}
    if spec.protocol == "responses":
        items = []
        for message in messages:
            payload = state(message, policy)
            if payload is not None:
                items.extend(payload)
                continue
            parts, calls, results = [], [], []
            for b in portable_content(message):
                kind = b["type"]
                if kind == "text":
                    parts.append({"type": "output_text" if message["role"] == "assistant" else "input_text", "text": b["text"]})
                elif kind == "image":
                    if not spec.vision:
                        raise ValueError("This model has no declared vision capability")
                    parts.append({"type": "input_image", "image_url": image_url(b)})
                elif kind == "tool_use":
                    calls.append({"type": "function_call", "call_id": b["id"], "name": b["name"], "arguments": json.dumps(b["input"])})
                elif kind == "tool_result":
                    results.append({"type": "function_call_output", "call_id": b["tool_use_id"], "output": b["content"]})
            items.extend(results)
            if parts:
                items.append({"role": message["role"], "content": parts})
            items.extend(calls)
        body = {"model": model, "instructions": system, "input": items, "store": False,
                "include": ["reasoning.encrypted_content"], "max_output_tokens": max_tokens, **policy.wire}
        if policy.show_thinking and spec.supports_summary:
            body["reasoning"] = {**body.get("reasoning", {}), "summary": "auto"}
        if tools:
            body["tools"] = [{"type": "function", **t["function"], "strict": False} for t in schema_tools]
        return body
    if spec.protocol == "google":
        contents = []
        for message in messages:
            payload = state(message, policy)
            parts = payload if payload is not None else []
            if payload is None:
                for b in portable_content(message):
                    if b["type"] == "text":
                        parts.append({"text": b["text"]})
                    elif b["type"] == "image":
                        if not spec.vision:
                            raise ValueError("This model has no declared vision capability")
                        parts.append({"inlineData": {"mimeType": b["source"]["media_type"], "data": b["source"]["data"]}})
                    elif b["type"] == "tool_use":
                        parts.append({"functionCall": {"id": b["id"], "name": b["name"], "args": b["input"]}})
                    elif b["type"] == "tool_result":
                        # Names are resolved from actual preceding calls, never guessed.
                        call = next((c for m in reversed(messages[:messages.index(message)]) for c in portable_content(m)
                                     if c["type"] == "tool_use" and c["id"] == b["tool_use_id"]), None)
                        if call is None:
                            raise ProviderError("orphan_result", "Tool output has no matching call")
                        parts.append({"functionResponse": {**({"id": call["id"]} if not call["id"].startswith("google_") else {}),
                            "name": call["name"], "response": {"result": b["content"]}}})
            contents.append({"role": "model" if message["role"] == "assistant" else "user", "parts": parts})
        config = {"maxOutputTokens": max_tokens, **policy.wire}
        # Display suppression does not remove signatures/continuation or alter compute.
        if "thinkingConfig" in config:
            config["thinkingConfig"] = {**config["thinkingConfig"], "includeThoughts": True}
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": config}
        if tools:
            body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t.get("description", ""),
                "parametersJsonSchema": t.get("input_schema", {"type": "object"})} for t in tools]}]
        return body
    output = [{"role": "system", "content": system}] if system else []
    for message in messages:
        payload = state(message, policy)
        if payload is not None:
            output.append(payload)
            continue
        parts, calls, results = [], [], []
        for b in portable_content(message):
            if b["type"] == "text":
                parts.append({"type": "text", "text": b["text"]})
            elif b["type"] == "image":
                if not spec.vision:
                    raise ValueError("This model has no declared vision capability")
                parts.append({"type": "image_url", "image_url": {"url": image_url(b)}})
            elif b["type"] == "tool_use":
                calls.append({"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}})
            elif b["type"] == "tool_result":
                results.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
        output.extend(results)
        if parts or calls:
            content = "\n\n".join(p["text"] for p in parts) if all(p["type"] == "text" for p in parts) else parts
            output.append({"role": message["role"], "content": content, **({"tool_calls": calls} if calls else {})})
    token_key = "max_completion_tokens" if spec.ref.startswith("openai:") else "max_tokens"
    return {"model": model, "messages": output, token_key: max_tokens,
            **({"tools": schema_tools} if tools else {}), **policy.wire}


def decode(policy, raw):
    """No tool dispatch from incomplete, failed or unknown terminal states."""
    protocol = policy.spec.protocol
    content = []
    usage = raw.get("usage", raw.get("usageMetadata", {})) or {}
    payload = None
    if protocol == "anthropic":
        status = {"end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens", "pause_turn": "pause_turn", "refusal": "refused"}.get(raw.get("stop_reason"), "interrupted")
        payload = raw.get("content", [])
        content = [Block(**b) for b in payload if b["type"] in {"text", "tool_use"}]
    elif protocol == "responses":
        status = {"completed": "end_turn", "failed": "failed", "cancelled": "cancelled", "incomplete": "incomplete"}.get(raw.get("status"), "interrupted")
        if status == "incomplete" and (raw.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
            status = "max_tokens"
        payload = raw.get("output", [])
        for item in payload:
            if item["type"] == "message":
                for part in item.get("content", []):
                    if part["type"] == "output_text":
                        content.append(Block(type="text", text=part["text"]))
                    elif part["type"] == "refusal":
                        status = "refused"
            elif item["type"] == "function_call" and status == "end_turn":
                if not item.get("call_id") or not item.get("name") or item.get("status", "completed") != "completed":
                    raise ProviderError("invalid_call", "Incomplete function call")
                content.append(Block(type="tool_use", id=item["call_id"], name=item["name"], input=arguments(item["arguments"])))
    elif protocol == "google":
        candidate = next(iter(raw.get("candidates", [])), {})
        finish = candidate.get("finishReason")
        status = {"STOP": "end_turn", "MAX_TOKENS": "max_tokens", "SAFETY": "refused", "RECITATION": "refused"}.get(finish, "interrupted")
        payload = candidate.get("content", {}).get("parts", [])
        for index, part in enumerate(payload):
            if "text" in part and not part.get("thought"):
                content.append(Block(type="text", text=part["text"]))
            if "functionCall" in part and status == "end_turn":
                call = part["functionCall"]
                # Google may omit ids; scope a stable generated id to this response.
                call_id = call.get("id") or f"google_{index}_{__import__('uuid').uuid4().hex}"
                content.append(Block(type="tool_use", id=call_id, name=call["name"], input=arguments(call.get("args", {}))))
                # Original signed parts remain byte-for-byte unchanged on replay.
    else:
        choice = next(iter(raw.get("choices", [])), {})
        finish = choice.get("finish_reason")
        status = {"stop": "end_turn", "tool_calls": "end_turn", "length": "max_tokens", "content_filter": "refused"}.get(finish, "interrupted")
        payload = choice.get("message", {})
        if payload.get("content"):
            content.append(Block(type="text", text=payload["content"]))
        for call in payload.get("tool_calls", []) if status == "end_turn" else []:
            if not call.get("id") or not call.get("function", {}).get("name"):
                raise ProviderError("invalid_call", "Incomplete tool call")
            content.append(Block(type="tool_use", id=call["id"], name=call["function"]["name"], input=arguments(call["function"]["arguments"])))
    calls = [b for b in content if b.type == "tool_use"]
    if status in {"end_turn", "tool_use"} and calls:
        ids = [b.id for b in calls]
        if len(ids) != len(set(ids)):
            raise ProviderError("duplicate_call_id", "The model repeated a tool-call identity")
        status = "tool_use"
    if status == "end_turn" and not any(b.type == "text" and b.text.strip() for b in content):
        status = "incomplete"
    envelope = {"type": "_provider_state", "origin": policy.spec.ref, "protocol": protocol, "payload": payload}
    return LLMMessage(content, status, usage, envelope, policy)


def has_tool_fragment(message):
    payload = (getattr(message, "continuation", None) or {}).get("payload")
    if isinstance(payload, dict):
        return bool(payload.get("tool_calls"))
    return any(item.get("type") in {"function_call", "tool_use"} or "functionCall" in item
               for item in (payload or []) if isinstance(item, dict))
def count_native_images(value):
    """Count provider-native image parts without interpreting quoted text."""
    if isinstance(value, list):
        return sum(count_native_images(item) for item in value)
    if not isinstance(value, dict):
        return 0
    if native_image(value):
        return 1
    return sum(count_native_images(item) for item in value.values() if isinstance(item, (list, dict)))


def native_image(value):
    if not isinstance(value, dict):
        return False
    kind, inline, source = value.get('type'), value.get('inlineData'), value.get('source')
    return ((kind == 'image' and isinstance(source, dict) and source.get('type') in ('base64','url'))
        or (kind in ('image_url', 'input_image') and isinstance(value.get('image_url'), (str, dict)))
        or (isinstance(inline, dict) and str(inline.get('mimeType', '')).startswith('image/')))


def budget_payload(value):
    """Native image tokens use verified route ceilings, not base64 text length.

    Tool results containing quoted JSON stay strings and keep their text bound.
    Only actual provider-native image parts are replaced in this calculation.
    The original request still owns the receipt hash and provider payload.
    """
    if native_image(value):
        return {'native_image_accounted_separately':True}
    if isinstance(value, dict):
        return {key:budget_payload(item) for key,item in value.items()}
    if isinstance(value, list):
        return [budget_payload(item) for item in value]
    return value
