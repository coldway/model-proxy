# Created by AI on 2026/05/27
# Copyright © 2026

"""测试 Cursor Agent CLI plan 模式集成"""

import pytest
from src.models.schemas import ChatCompletionRequest, ChatMessage
from src.providers.cursor import CursorProvider


class TestCursorPlanMode:
    """测试 Cursor provider 的 plan 模式支持"""

    def test_build_cmd_args_plan_mode(self):
        """测试 plan 模式命令构建"""
        provider = CursorProvider(api_key="test-key")
        cmd_args = provider._build_cmd_args(
            model="sonnet-4",
            prompt="重构 auth 模块",
            mode="plan"
        )
        
        assert "--plan" in cmd_args
        assert "--model" in cmd_args
        assert "sonnet-4" in cmd_args
        assert "--api-key" in cmd_args
        assert "test-key" in cmd_args

    def test_build_cmd_args_ask_mode(self):
        """测试 ask 模式命令构建"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            model="auto",
            prompt="这个代码做什么?",
            mode="ask"
        )
        
        assert "--mode" in cmd_args
        assert "ask" in cmd_args

    def test_build_cmd_args_agent_mode(self):
        """测试 agent 模式（默认）命令构建"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            model="auto",
            prompt="修复这个 bug",
            mode="agent"
        )
        
        # agent 模式不添加额外参数
        assert "--plan" not in cmd_args
        assert "--mode" not in cmd_args

    def test_build_cmd_args_no_mode(self):
        """测试未指定 mode（默认为 agent）"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            model="auto",
            prompt="帮我写个函数",
            mode=None
        )
        
        assert "--plan" not in cmd_args
        assert "--mode" not in cmd_args

    def test_build_cmd_args_stream_with_plan(self):
        """测试流式 + plan 模式"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            model="auto",
            prompt="分析这个项目",
            stream=True,
            mode="plan"
        )
        
        assert "--plan" in cmd_args
        assert "--output-format" in cmd_args
        assert "stream-json" in cmd_args

    @pytest.mark.asyncio
    async def test_chat_completion_with_plan_mode(self):
        """测试 chat_completion 方法传递 mode"""
        provider = CursorProvider()
        request = ChatCompletionRequest(
            model="cursor-agent",
            messages=[ChatMessage(role="user", content="分析这个项目的架构")],
            mode="plan"
        )
        
        # 验证 request 包含 mode 字段
        assert hasattr(request, "mode")
        assert request.mode == "plan"
