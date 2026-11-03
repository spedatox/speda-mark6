"""Pooled native/compat transports with bounded deadlines and honest stream outcomes."""
import asyncio
from contextlib import asynccontextmanager
import json
from urllib.parse import quote

import httpx
import anthropic

from app.schemas.llm import ProviderError
from app.services.llm_protocol import decode, request_body
from app.services.model_policy import parse_model_ref


class LLMClient:
    def __init__(self, policy, settings, *, http_client=None, spending=None):
        self.policy, self.settings = policy, settings
        self.spending = spending
        self.http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_request_timeout_seconds, connect=10),
            follow_redirects=False, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        self._anthropic = None
        self._vertex_credentials = None
        self._vertex_lock = asyncio.Lock()

    def configured(self, model):
        provider, _ = parse_model_ref(model)
        if provider == "ollama":
            return True
        if provider == "vertex":
            return bool(self.settings.vertex_project_id)
        key = getattr(self.settings, f"{provider}_api_key", "")
        if provider == "kimi":
            key = key or self.settings.moonshot_api_key
        return bool(key and key != "not-set")

    async def close(self):
        await self.http.aclose()
        if self._anthropic:
            await self._anthropic.client.close()

    def anthropic_client(self):
        if self._anthropic is None:
            from app.services.anthropic_client import AnthropicClient
            self._anthropic = AnthropicClient()
        return self._anthropic

    async def vertex_token(self):
        async with self._vertex_lock:
            if self._vertex_credentials is None:
                def load():
                    import google.auth
                    from google.oauth2 import service_account
                    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
                    if self.settings.vertex_credentials_file:
                        return service_account.Credentials.from_service_account_file(self.settings.vertex_credentials_file, scopes=scopes)
                    return google.auth.default(scopes=scopes)[0]
                self._vertex_credentials = await asyncio.to_thread(load)
            if not self._vertex_credentials.valid:
                from google.auth.transport.requests import Request
                await asyncio.to_thread(self._vertex_credentials.refresh, Request())
            return self._vertex_credentials.token

    async def _target(self, policy):
        provider, model = parse_model_ref(policy.spec.ref)
        config = self.settings
        if not self.configured(policy.spec.ref):
            raise ProviderError("not_configured", f"Provider {provider} is not configured")
        headers = {"Content-Type": "application/json"}
        if provider in {"gemini", "vertex"}:
            if provider == "gemini":
                base = config.gemini_base_url
                headers["x-goog-api-key"] = config.gemini_api_key
            else:
                headers["Authorization"] = f"Bearer {await self.vertex_token()}"
                location = config.vertex_location
                host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
                base = f"https://{host}/v1/projects/{quote(config.vertex_project_id, safe='')}/locations/{quote(location, safe='')}/publishers/google"
            return f"{base.rstrip('/')}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse", headers
        base = getattr(config, f"{provider}_base_url")
        key = getattr(config, f"{provider}_api_key", "")
        if provider == "kimi":
            key = key or config.moonshot_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        path = "responses" if policy.spec.protocol == "responses" else "chat/completions"
        return f"{base.rstrip('/')}/{path}", headers

    @asynccontextmanager
    async def stream_message(self, *, model, system, messages, tools=None, max_tokens=16384,
                             policy=None, user_id=1, agent_id="speda", role="interactive", thinking=None, cache_key=None):
        policy = policy or await self.policy.resolve(model=model, user_id=user_id, agent_id=agent_id, role=role, request=thinking)
        candidates = (policy.spec.ref, *policy.fallbacks)
        stream = None
        # One application retry owner. No retries after opening or after tools ran.
        async with asyncio.timeout(policy.remaining()):
            for attempt, ref in enumerate(candidates, 1):
                selected = policy if attempt == 1 else await self.policy.resolve(model=ref,
                    user_id=user_id, agent_id=agent_id, role=role,
                    request=policy.requested if policy.source in {"turn", "parent"} else None,
                    snapshot=(policy.revision, policy.snapshot), deadline=policy.deadline)
                body = request_body(selected, system, messages, tools or [], max_tokens)
                if cache_key and selected.spec.protocol == "responses":
                    body["prompt_cache_key"] = cache_key
                selected_stream = ProviderStream(self, selected, body)
                reservation = None
                if self.spending:
                    if not self.configured(selected.spec.ref):
                        raise ProviderError("not_configured", "Selected provider is not configured")
                    reservation = await self.spending.reserve(user_id, selected, body, max_tokens, f"{agent_id}:{role}")
                try:
                    await selected_stream.open()
                except ProviderError as exc:
                    await selected_stream.close()
                    if reservation:
                        await asyncio.shield(self.spending.finish(reservation,
                            "rejected" if exc.code.startswith("http_") else "unknown"))
                    if not exc.retryable or attempt == len(candidates):
                        raise
                    continue
                except BaseException:
                    await selected_stream.close()
                    if reservation:
                        await asyncio.shield(self.spending.finish(reservation, "unknown"))
                    raise
                stream = selected_stream
                stream.attempts = attempt
                break
            try:
                yield stream
            finally:
                if stream:
                    try:
                        await stream.close()
                    finally:
                        if reservation:
                            await asyncio.shield(self.spending.finish(reservation, "complete" if stream.final else "unknown"))

    async def create_message(self, **kwargs):
        # Background/worker calls use the same resolver and protocol lifecycle.
        kwargs.setdefault("role", "background")
        async with self.stream_message(**kwargs) as stream:
            return await stream.get_final_message()


async def sse_frames(response):
    """Incremental, bounded SSE framing; EOF is not a completion event."""
    data, size, total = [], 0, 0
    async for line in response.aiter_lines():
        total += len(line.encode("utf-8"))
        if total > 16_000_000:
            raise ProviderError("response_limit", "Provider response exceeded the bounded output limit")
        if not line:
            if data:
                yield "\n".join(data)
                data, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].lstrip(" ")
            size += len(value)
            if size > 2_000_000:
                raise ProviderError("frame_limit", "Provider event exceeded the bounded frame limit")
            data.append(value)
    if data:
        yield "\n".join(data)


class ProviderStream:
    def __init__(self, owner, policy, body):
        self.owner, self.policy, self.body = owner, policy, body
        self.response = self.cm = self.sdk = None
        self.final = None
        self.consumed = False
        self.attempts = 1
        self._events = None

    async def open(self):
        try:
            if self.policy.spec.protocol == "anthropic":
                if not self.owner.configured(self.policy.spec.ref):
                    raise ProviderError("not_configured", "Anthropic is not configured")
                self.cm = self.owner.anthropic_client().stream_message(**self.body)
                self.sdk = await self.cm.__aenter__()
                return
            url, headers = await self.owner._target(self.policy)
            body = self.body if self.policy.spec.protocol == "google" else {**self.body, "stream": True}
            if self.policy.spec.protocol == "chat":
                body["stream_options"] = {"include_usage": True}
            self.response = await self.owner.http.send(self.owner.http.build_request("POST", url, headers=headers, json=body), stream=True)
            if self.response.status_code >= 400:
                status = self.response.status_code
                # Never include raw provider errors/headers, which can contain secrets.
                raise ProviderError(f"http_{status}", f"Provider request failed (HTTP {status})", retryable=status == 429 or status >= 500)
            if "text/event-stream" not in self.response.headers.get("content-type", ""):
                raise ProviderError("invalid_stream", "Provider did not return an event stream")
        except httpx.TransportError as exc:
            raise ProviderError("transport", "Provider connection failed", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("transport", "Anthropic connection failed", retryable=True) from exc
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            status = getattr(exc, "status_code", None)
            if status:
                raise ProviderError(f"http_{status}", f"Provider request failed (HTTP {status})", retryable=status == 429 or status >= 500) from exc
            raise

    async def close(self):
        if self.response:
            await self.response.aclose()
        if self.cm and self.sdk:
            await self.cm.__aexit__(None, None, None)
            self.sdk = None

    def event_stream(self):
        if self._events is None:
            self._events = self._pump()
        return self._events

    @property
    def text_stream(self):
        return self._text_only()

    async def _text_only(self):
        async for kind, value in self.event_stream():
            if kind == "text":
                yield value

    async def _pump(self):
        try:
            async for event in self._consume():
                yield event
        except httpx.TransportError as exc:
            raise ProviderError("interrupted", "Provider connection ended while streaming") from exc
        except anthropic.APIError as exc:
            raise ProviderError("interrupted", "Anthropic stream failed before completion") from exc
        except (KeyError, TypeError, IndexError) as exc:
            raise ProviderError("invalid_event", "Provider returned an invalid event shape") from exc

    async def _consume(self):
        if self.consumed:
            return
        if self.sdk is not None:
            terminal = False
            async for event in self.sdk:
                if event.type == "message_stop":
                    terminal = True
                if event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield "text", delta.text
                    elif delta.type == "thinking_delta":
                        yield "thinking", delta.thinking
            if not terminal:
                raise ProviderError("interrupted", "Anthropic stream ended without completion")
            self.final = decode(self.policy, (await self.sdk.get_final_message()).model_dump())
        else:
            text, reasoning, calls, google_parts = [], [], {}, []
            finish, usage, final_raw = None, {}, None
            protocol = self.policy.spec.protocol
            async for frame in sse_frames(self.response):
                if frame == "[DONE]":
                    break
                try:
                    event = json.loads(frame)
                except ValueError as exc:
                    raise ProviderError("invalid_event", "Provider returned invalid JSON") from exc
                if event.get("error") or event.get("type") == "error":
                    raise ProviderError("provider_failed", "Provider reported an error while streaming")
                if protocol == "responses":
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        yield "text", event.get("delta", "")
                    elif kind == "response.reasoning_summary_text.delta":
                        yield "thinking", event.get("delta", "")
                    elif kind in {"response.completed", "response.incomplete", "response.failed", "response.cancelled"}:
                        final_raw = event.get("response")
                        if not isinstance(final_raw, dict):
                            raise ProviderError("invalid_terminal", "Missing final provider result")
                        if final_raw.get("status") != kind.removeprefix("response."):
                            raise ProviderError("invalid_terminal", "Conflicting provider completion status")
                        break
                elif protocol == "google":
                    if event.get("promptFeedback", {}).get("blockReason"):
                        raise ProviderError("refused", "The provider declined this input")
                    candidates = event.get("candidates", [])
                    if len(candidates) > 1:
                        raise ProviderError("multiple_candidates", "Expected one model candidate")
                    candidate = next(iter(candidates), {})
                    parts = candidate.get("content", {}).get("parts", [])
                    google_parts.extend(parts)
                    for part in parts:
                        if "text" in part:
                            yield ("thinking" if part.get("thought") else "text"), part["text"]
                    finish = candidate.get("finishReason", finish)
                    usage = event.get("usageMetadata", usage)
                else:
                    usage = event.get("usage", usage) or usage
                    choices = event.get("choices", [])
                    if len(choices) > 1:
                        raise ProviderError("multiple_candidates", "Expected one model candidate")
                    choice = next(iter(choices), {})
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                        yield "text", delta["content"]
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                        yield "thinking", delta["reasoning_content"]
                    for call in delta.get("tool_calls") or []:
                        index = call.get("index")
                        if not isinstance(index, int) or index < 0:
                            raise ProviderError("invalid_call_index", "Ambiguous streamed tool-call index")
                        acc = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            acc["id"] = call["id"]
                        for key in ("name", "arguments"):
                            if call.get("function", {}).get(key):
                                acc["function"][key] += call["function"][key]
            if protocol == "responses":
                if final_raw is None:
                    raise ProviderError("interrupted", "Responses stream ended without a terminal result")
            elif protocol == "google":
                final_raw = {"candidates": [{"finishReason": finish, "content": {"parts": google_parts}}], "usageMetadata": usage}
            else:
                payload = {"role": "assistant", "content": "".join(text)}
                # Empty reasoning_content is meaningful to some thinking protocols.
                if reasoning or parse_model_ref(self.policy.spec.ref)[0] in {"kimi", "deepseek"}:
                    payload["reasoning_content"] = "".join(reasoning)
                if calls:
                    payload["tool_calls"] = [calls[i] for i in sorted(calls)]
                final_raw = {"choices": [{"finish_reason": finish, "message": payload}], "usage": usage}
            self.final = decode(self.policy, final_raw)
        self.final.attempts = self.attempts
        if self.final.continuation:
            self.final.continuation["receipt"] = {**self.policy.receipt(), "attempts": self.attempts,
                "usage": self.final.usage, "outcome": self.final.stop_reason}
        self.consumed = True

    async def get_final_message(self):
        if not self.consumed:
            async for _ in self.event_stream():
                pass
        return self.final
