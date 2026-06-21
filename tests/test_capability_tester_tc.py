# Created by model-proxy on 2026/05/26
# Copyright © 2026

from src.config.capability_tester import CapabilityTester


class TestPromptInjectedTCProbe:
    def test_validate_get_current_time_probe_ok(self):
        ok, probe = CapabilityTester._validate_get_current_time_probe([
            {
                "function": {
                    "name": "get_current_time",
                    "arguments": '{"city": "北京"}',
                }
            }
        ])
        assert ok is True
        assert "get_current_time" in probe
        assert "北京" in probe

    def test_validate_get_current_time_probe_wrong_tool(self):
        ok, _ = CapabilityTester._validate_get_current_time_probe([
            {"function": {"name": "other_tool", "arguments": "{}"}}
        ])
        assert ok is False

    def test_extract_json_object_from_markdown(self):
        parsed = CapabilityTester._extract_json_object(
            '```json\n{"tool_calls":[{"function":{"name":"get_current_time","arguments":"{\\"city\\":\\"北京\\"}"}}]}\n```'
        )
        assert parsed is not None
        assert "tool_calls" in parsed

    def test_probe_result_from_tool_calls_prompt(self):
        result = CapabilityTester._probe_result_from_tool_calls(
            [{"function": {"name": "get_current_time", "arguments": '{"city": "北京"}'}}],
            tc_method="prompt",
        )
        assert result["tool_calling"] is True
        assert result["tc_method"] == "prompt"
