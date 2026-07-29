"""ExternalAgentProxy tool_result normalization.

The peer (Forge) emits tool_result payloads in the Anthropic-native shape
({tool_use_id, is_error, content}), while every consumer — both clients' live
renderers and the turn runner that persists the turn for history — reads the
orchestrator's shape ({id, result}). These tests pin the bridge so a proxied
tool result renders and saves exactly like an in-process one.
"""

from app.core.external_proxy import (
    _RESULT_PREVIEW_CHARS,
    _normalize_tool_result,
    _stringify_content,
)


def test_peer_shape_is_mapped_to_canonical_keys():
    out = _normalize_tool_result(
        {"tool_use_id": "call_1", "is_error": False, "content": "hello world"}
    )
    assert out == {"id": "call_1", "result": "hello world"}


def test_content_block_list_is_flattened_to_text():
    out = _normalize_tool_result(
        {
            "tool_use_id": "call_2",
            "content": [
                {"type": "text", "text": "line one"},
                {"type": "text", "text": "line two"},
            ],
        }
    )
    assert out == {"id": "call_2", "result": "line one\nline two"}


def test_error_result_still_carries_its_text():
    out = _normalize_tool_result(
        {"tool_use_id": "call_3", "is_error": True, "content": "boom: nonzero exit"}
    )
    assert out["id"] == "call_3"
    assert out["result"] == "boom: nonzero exit"


def test_result_is_truncated_to_the_preview_cap():
    out = _normalize_tool_result(
        {"tool_use_id": "call_4", "content": "x" * (_RESULT_PREVIEW_CHARS + 500)}
    )
    assert len(out["result"]) == _RESULT_PREVIEW_CHARS


def test_already_canonical_payload_passes_through():
    # No-op if the peer is ever updated to emit {id, result} directly.
    out = _normalize_tool_result({"id": "call_5", "result": "done"})
    assert out == {"id": "call_5", "result": "done"}


def test_missing_id_yields_none_not_crash():
    out = _normalize_tool_result({"content": "orphan output"})
    assert out == {"id": None, "result": "orphan output"}


def test_non_dict_payload_is_stringified():
    assert _normalize_tool_result("raw string") == {"id": None, "result": "raw string"}


def test_stringify_handles_none_string_and_blocks():
    assert _stringify_content(None) == ""
    assert _stringify_content("plain") == "plain"
    assert _stringify_content([{"type": "text", "text": "a"}, {"text": "b"}]) == "a\nb"
