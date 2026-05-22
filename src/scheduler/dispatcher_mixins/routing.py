# Created by model-proxy on 2026/05/21
# Copyright © 2026

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from src.models.schemas import ChatCompletionRequest, ChatMessage, ModelConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RoutingMixin:
    """路由缓存与 LLM 路由相关方法"""

    _RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")

    def _get_cached_route(self, feature_hash: str) -> str | None:
        with self._route_cache_lock:
            entry = self._route_cache.get(feature_hash)
            if entry is None:
                return None
            model_name, ts = entry
            if time.time() - ts < self._route_cache_ttl:
                self._route_cache.move_to_end(feature_hash)
                logger.info("路由缓存命中: %s → %s", feature_hash, model_name)
                return model_name
            del self._route_cache[feature_hash]
            return None

    def _set_cached_route(self, feature_hash: str, model_name: str) -> None:
        with self._route_cache_lock:
            from src.scheduler.dispatcher import ROUTE_CACHE_MAX
            if feature_hash in self._route_cache:
                self._route_cache.move_to_end(feature_hash)
            elif len(self._route_cache) >= ROUTE_CACHE_MAX:
                self._route_cache.popitem(last=False)
            self._route_cache[feature_hash] = (model_name, time.time())
            self._route_hit_counter[model_name] = self._route_hit_counter.get(model_name, 0) + 1

    def _is_hot_model(self, model_name: str) -> bool:
        return self._route_hit_counter.get(model_name, 0) >= self._route_hit_threshold

    async def purge_expired_cache(self) -> int:
        with self._route_cache_lock:
            now = time.time()
            expired = [k for k, (_, ts) in self._route_cache.items() if now - ts >= self._route_cache_ttl]
            for k in expired:
                del self._route_cache[k]
            if expired:
                logger.debug("路由缓存过期清理: 删除 %d 条，剩余 %d 条", len(expired), len(self._route_cache))
            self._route_hit_counter.clear()
            return len(expired)

    async def _route_with_llm(
        self,
        request: ChatCompletionRequest,
        available: list[tuple[str, ModelConfig]],
    ) -> str | None:
        if len(available) <= 1:
            return available[0][1].name if available else None

        feature_hash = self._compute_feature_hash(request, available)
        cached = self._get_cached_route(feature_hash)
        if cached:
            valid_names = {m.name for _, m in available}
            if cached in valid_names:
                return cached

        router_prov, router_model = self._pick_router_model(available)
        if not router_prov:
            return None

        model_descriptions = self._build_model_descriptions(available)
        user_summary = self._summarize_request(request)

        routing_prompt = f"""你是一个模型路由器。根据用户的请求特征，从可用模型中选择最合适的一个。
只输出模型名称，不要输出任何其他内容。

## 可用模型及能力
{model_descriptions}

## 用户请求特征
{user_summary}

## 输出
直接输出推荐的模型名称（仅模型名，不含厂商前缀）："""

        try:
            route_request = ChatCompletionRequest(
                model=router_model,
                messages=[ChatMessage(role="user", content=routing_prompt)],
                temperature=0,
                max_tokens=50,
            )
            result = await self._call_provider(
                router_prov, router_model, route_request, timeout=self._route_llm_timeout, is_routing=True,
            )
            recommended = result.choices[0].message.content.strip()
            recommended = recommended.strip("`\"'").split("\n")[0].strip()

            valid_names = {m.name for _, m in available}
            if recommended in valid_names:
                logger.info(
                    "LLM 路由推荐: %s（路由器: %s:%s）",
                    recommended, router_prov, router_model,
                )
                self._set_cached_route(feature_hash, recommended)
                return recommended

            for name in valid_names:
                if recommended in name or name in recommended:
                    logger.info(
                        "LLM 路由推荐（模糊匹配）: %s → %s", recommended, name,
                    )
                    self._set_cached_route(feature_hash, name)
                    return name

            logger.warning("LLM 路由返回无效模型名: %s", recommended)
            return None

        except Exception as e:
            logger.warning("LLM 路由调用失败: %s，回退到规则排序", e)
            if hasattr(self, '_history') and self._history:
                self._history.record(
                    provider=router_prov, model=router_model,
                    success=False, latency_ms=0, error=f"route_llm_fail: {e}",
                    route_strategy="LLM路由失败",
                )
            return None

    def _pick_router_model(
        self, available: list[tuple[str, ModelConfig]]
    ) -> tuple[str, str]:
        candidates = []
        for prov_name, model_cfg in available:
            caps = self._get_caps(prov_name, model_cfg.name)
            latency = caps.get("latency_ms") or 99999
            has_error = bool(caps.get("error"))
            candidates.append((has_error, latency, prov_name, model_cfg.name))

        candidates.sort(key=lambda x: (x[0], x[1]))
        if candidates:
            _, lat, prov, model = candidates[0]
            logger.debug("选择路由器模型: %s:%s（延迟 %dms）", prov, model, lat)
            return prov, model
        return "", ""

    def _build_model_descriptions(
        self, available: list[tuple[str, ModelConfig]]
    ) -> str:
        lines = []
        for prov_name, model_cfg in available:
            caps = self._get_caps(prov_name, model_cfg.name)
            features = []
            if caps.get("tool_calling") or model_cfg.tool_calling:
                features.append("工具调用")
            if caps.get("multi_turn_tc"):
                features.append("多轮对话稳定")
            if caps.get("chinese"):
                features.append("中文友好")
            if caps.get("vision"):
                features.append("图像理解")
            if caps.get("json_mode"):
                features.append("结构化JSON")
            if caps.get("streaming"):
                features.append("流式输出")
            if caps.get("reasoning"):
                features.append("逻辑推理强")
            latency = caps.get("latency_ms")
            lat_str = f"，延迟{latency}ms" if latency else ""
            lines.append(f"- {model_cfg.name}（{prov_name}）：{', '.join(features) or '基础能力'}{lat_str}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_request(request: ChatCompletionRequest) -> str:
        features = []
        if request.tools:
            tool_names = [t.function.name for t in request.tools]
            features.append(f"需要工具调用（{', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}）")
        if request.stream:
            features.append("需要流式输出")

        has_chinese = False
        has_image = False
        msg_count = len(request.messages)
        total_len = 0
        for msg in request.messages:
            if isinstance(msg.content, str):
                total_len += len(msg.content)
                if RoutingMixin._RE_CHINESE.search(msg.content):
                    has_chinese = True
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        has_image = True

        if has_chinese:
            features.append("包含中文内容")
        if has_image:
            features.append("包含图像")
        features.append(f"消息数: {msg_count}")
        features.append(f"总文本长度: ~{total_len}字符")

        last_user = ""
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                last_user = msg.content[:200]
                break
        if last_user:
            features.append(f"最新用户消息: \"{last_user}\"")

        return "\n".join(f"- {f}" for f in features)
