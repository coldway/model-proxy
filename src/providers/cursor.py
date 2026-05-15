# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    UsageInfo,
)
from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class CursorProvider(BaseProvider):
    """Cursor Agent CLI 适配器"""

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        def _extract_text(msg: ChatMessage) -> str:
            if isinstance(msg.content, str):
                return msg.content
            if isinstance(msg.content, list):
                return " ".join(p.get("text", "") for p in msg.content if isinstance(p, dict) and p.get("type") == "text")
            return ""

        prompt = "\n".join(
            f"[{m.role}]: {_extract_text(m)}" for m in request.messages
        )

        logger.info("[cursor] CLI请求 msgs=%d prompt_len=%d", len(request.messages), len(prompt))
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "cursor", "agent", "--prompt", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            elapsed_ms = (time.monotonic() - t0) * 1000
            output = stdout.decode("utf-8", errors="replace").strip()
            logger.info("[cursor] CLI完成 耗时=%.0fms returncode=%d output_len=%d", elapsed_ms, proc.returncode, len(output))

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"Cursor CLI 退出码 {proc.returncode}: {error_msg}")

        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError("Cursor CLI 调用超时 (120s)")

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model="cursor-agent",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=output),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(),
        )

    async def list_models(self) -> list[str]:
        return ["cursor-agent"]

    async def health_check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "cursor", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False
