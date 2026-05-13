# Created by model-proxy on 2026/05/14
# Copyright © 2026

"""parse_tool_calls 和 msg_to_dict 边界测试"""

from src.models.schemas import ChatMessage, FunctionCall, ToolCall
from src.providers.utils import msg_to_dict, parse_tool_calls


class TestMsgToDict:
    def test_basic_user_message(self):
        msg = ChatMessage(role="user", content="hello")
        d = msg_to_dict(msg)
        assert d == {"role": "user", "content": "hello"}

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="call_1", type="function", function=FunctionCall(name="get_weather", arguments='{"city":"北京"}'))
        msg = ChatMessage(role="assistant", content=None, tool_calls=[tc])
        d = msg_to_dict(msg)
        assert d["role"] == "assistant"
        assert "tool_calls" in d
        assert len(d["tool_calls"]) == 1

    def test_tool_response_message(self):
        msg = ChatMessage(role="tool", content='{"temp":20}', tool_call_id="call_1", name="get_weather")
        d = msg_to_dict(msg)
        assert d["tool_call_id"] == "call_1"
        assert d["name"] == "get_weather"

    def test_none_content_omitted(self):
        msg = ChatMessage(role="assistant", content=None)
        d = msg_to_dict(msg)
        assert "content" not in d

    def test_multimodal_content(self):
        msg = ChatMessage(role="user", content=[{"type": "text", "text": "describe this"}])
        d = msg_to_dict(msg)
        assert isinstance(d["content"], list)


class TestParseToolCalls:
    def test_normal_parse(self):
        raw = [{"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]
        result = parse_tool_calls(raw)
        assert result is not None
        assert len(result) == 1
        assert result[0].function.name == "test"

    def test_none_input(self):
        assert parse_tool_calls(None) is None

    def test_empty_list(self):
        assert parse_tool_calls([]) is None

    def test_malformed_entry_skipped(self):
        """畸形条目被跳过，不抛异常"""
        raw = [
            {"id": "ok", "function": {"name": "good", "arguments": "{}"}},
            {},
            {"broken": True},
        ]
        result = parse_tool_calls(raw)
        assert result is not None
        assert len(result) == 3
        assert result[0].function.name == "good"
        assert result[1].function.name == "unknown"

    def test_missing_function_key(self):
        """function 字段缺失时降级为默认值"""
        raw = [{"id": "x"}]
        result = parse_tool_calls(raw)
        assert result is not None
        assert result[0].function.name == "unknown"
        assert result[0].function.arguments == "{}"
