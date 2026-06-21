# Created by model-proxy on 2026/05/21
# Copyright © 2026

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.config.capability_tester import merge_catalog_capabilities, SKIP_TEST_PROVIDERS
from src.models.schemas import ChatCompletionRequest, ModelConfig
from src.scheduler.payload_tracker import estimate_payload_bytes

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class FilteringMixin:
    """能力排序与可用性过滤相关方法"""

    @staticmethod
    def _get_user_hint(request: ChatCompletionRequest) -> str:
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                return msg.content[:80]
        return ""

    def _can_skip_routing(
        self,
        sorted_models: list[tuple[str, ModelConfig]],
        request: ChatCompletionRequest,
    ) -> bool:
        if len(sorted_models) <= 1:
            return True
        if not self._capability_cache:
            return True
        top_model_name = sorted_models[0][1].name
        if self._is_hot_model(top_model_name):
            logger.debug("热点模型跳过 LLM 路由: %s (命中 %d 次)", top_model_name, self._route_hit_counter.get(top_model_name, 0))
            return True
        needs_tc = bool(request.tools)
        has_chinese = self._detect_chinese(request)
        if len(sorted_models) <= 3 and not needs_tc and not has_chinese:
            return True

        top_prov, top_model = sorted_models[0]
        top_caps = self._get_caps(top_prov, top_model.name)
        second_prov, second_model = sorted_models[1]
        second_caps = self._get_caps(second_prov, second_model.name)

        needs_tc = bool(request.tools)
        has_chinese = self._detect_chinese(request)

        top_tc = top_caps.get("tool_calling") or top_model.tool_calling
        top_mt = top_caps.get("multi_turn_tc", False)
        top_cn = top_caps.get("chinese", False)

        sec_tc = second_caps.get("tool_calling") or second_model.tool_calling
        sec_mt = second_caps.get("multi_turn_tc", False)

        if needs_tc and top_tc and top_mt and (not has_chinese or top_cn):
            if not sec_tc or not sec_mt:
                return True

        if not needs_tc and not has_chinese:
            top_lat = top_caps.get("latency_ms", 99999)
            sec_lat = second_caps.get("latency_ms", 99999)
            if top_lat < sec_lat * 0.5:
                return True

        return False

    @staticmethod
    def _unpack_rate_limit(model_cfg: ModelConfig) -> tuple[int, int, int, int]:
        rl = model_cfg.rate_limit
        if not rl:
            return 0, 0, 0, 0
        return rl.rpd, rl.rpm, getattr(rl, "tpm", 0), getattr(rl, "tpd", 0)

    @staticmethod
    def _rate_limits_for(
        enabled_models: list[tuple[str, ModelConfig]],
        provider: str,
        model: str,
    ) -> tuple[int, int, int, int]:
        for p, m in enabled_models:
            if p == provider and m.name == model:
                return FilteringMixin._unpack_rate_limit(m)
        return 0, 0, 0, 0

    def _filter_available(
        self, models: list[tuple[str, ModelConfig]],
        payload_bytes: int = 0,
        exclude_manual_only: bool = True,
    ) -> list[tuple[str, ModelConfig]]:
        non_broken = []
        for prov_name, model_cfg in models:
            if self._breaker.is_open(prov_name, model_cfg.name):
                logger.info("跳过 %s:%s — 处于熔断状态", prov_name, model_cfg.name)
                continue
            # 排除仅限指定调用的厂商（dashscope等），不参与 auto 路由
            if exclude_manual_only and prov_name in SKIP_TEST_PROVIDERS:
                continue
            non_broken.append((prov_name, model_cfg))

        if not non_broken:
            return []

        checks = [
            (prov_name, model_cfg.name, *self._unpack_rate_limit(model_cfg))
            for prov_name, model_cfg in non_broken
        ]
        can_flags = self._rate_limiter.batch_can_request(checks)

        result = []
        for i, (prov_name, model_cfg) in enumerate(non_broken):
            if not can_flags[i]:
                continue
            if payload_bytes and not self._payload_tracker.can_accept(prov_name, model_cfg.name, payload_bytes):
                limit = self._payload_tracker.get_limit(prov_name, model_cfg.name)
                logger.info(
                    "跳过 %s:%s — payload %d bytes ≥ 已知上限 %s bytes",
                    prov_name, model_cfg.name, payload_bytes, limit,
                )
                continue
            result.append((prov_name, model_cfg))
        return result

    def _model_supports_tool_calling(self, provider: str, model_cfg: ModelConfig) -> bool:
        if model_cfg.tool_calling:
            return True
        caps = self._get_caps(provider, model_cfg.name)
        return bool(caps.get("tool_calling"))

    def _model_supports_vision(self, provider: str, model_cfg: ModelConfig) -> bool:
        caps = self._get_caps(provider, model_cfg.name)
        return bool(caps.get("vision"))

    def _sort_by_capability(
        self,
        models: list[tuple[str, ModelConfig]],
        request: ChatCompletionRequest,
    ) -> list[tuple[str, ModelConfig]]:
        needs_stream = bool(request.stream)
        has_chinese = self._detect_chinese(request)

        health_map = {}
        if self._history and hasattr(self._history, "get_model_health"):
            health_map = self._history.get_model_health(window_minutes=60)

        def score(item: tuple[str, ModelConfig]) -> tuple:
            prov_name, m = item
            caps = self._get_caps(prov_name, m.name)

            stream_ok = 1 if not needs_stream or caps.get("streaming") else 0

            has_tc = bool(caps.get("tool_calling") or m.tool_calling)
            has_mt = bool(caps.get("multi_turn_tc"))
            has_reason = bool(caps.get("reasoning"))

            if has_tc and has_mt and has_reason:
                cap_combo = 6
            elif has_tc and has_mt:
                cap_combo = 5
            elif has_tc and has_reason:
                cap_combo = 4
            elif has_mt and has_reason:
                cap_combo = 3
            elif has_tc:
                cap_combo = 2
            elif has_mt or has_reason:
                cap_combo = 1
            else:
                cap_combo = 0

            cn = 1 if caps.get("chinese") and has_chinese else 0
            latency = caps.get("latency_ms", 99999)

            health_key = f"{prov_name}:{m.name}"
            health = health_map.get(health_key, {})
            success_rate = health.get("success_rate", 1.0)
            health_penalty = 0 if success_rate >= 0.8 else (-1 if success_rate >= 0.5 else -2)

            return (health_penalty, stream_ok, cap_combo, cn, max(0, 30000 - latency))

        _CAP_LABELS = {6: "TC+MT+R", 5: "TC+MT", 4: "TC+R", 3: "MT+R", 2: "TC", 1: "MT|R", 0: "-"}
        scored = [(item, score(item)) for item in models]
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [item for item, _ in scored]
        if len(result) > 1:
            ranking = ", ".join(
                f"{m.name}({_CAP_LABELS.get(s[2], '?')})" for (_, m), s in scored[:5]
            )
            logger.info(
                "[排序] stream=%s cn=%s | 前5: %s",
                needs_stream, has_chinese, ranking,
            )
        return result

    def _get_caps(self, provider: str, model: str) -> dict[str, Any]:
        cached: dict[str, Any] = {}
        if self._capability_cache:
            entry = self._capability_cache.get(provider, model)
            if entry:
                cached = dict(entry)
        catalog_model = self._catalog.get_model(provider, model) if self._catalog else None
        return merge_catalog_capabilities(cached, catalog_model)

    _RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")

    @staticmethod
    def _detect_chinese(request: ChatCompletionRequest) -> bool:
        for msg in request.messages:
            if isinstance(msg.content, str) and FilteringMixin._RE_CHINESE.search(msg.content):
                return True
        return False

    _tools_sig_cache: dict[int, str] = {}

    def _compute_feature_hash(
        self,
        request: ChatCompletionRequest,
        available: list[tuple[str, ModelConfig]],
    ) -> str:
        has_tools = bool(request.tools)
        tool_names = sorted(t.function.name for t in request.tools) if request.tools else []
        if request.tools:
            cache_key = id(request.tools)
            tools_sig = self._tools_sig_cache.get(cache_key)
            if tools_sig is None:
                tools_dump = json.dumps(
                    [t.model_dump() for t in request.tools],
                    ensure_ascii=False, sort_keys=True,
                )
                tools_sig = (
                    f"n={len(request.tools)}|h={hashlib.blake2b(tools_dump.encode(), digest_size=6).hexdigest()}"
                    f"|b={len(tools_dump.encode())}"
                )
                self._tools_sig_cache[cache_key] = tools_sig
                if len(self._tools_sig_cache) > 64:
                    oldest = next(iter(self._tools_sig_cache))
                    del self._tools_sig_cache[oldest]
        else:
            tools_sig = "n=0"
        has_chinese = self._detect_chinese(request)
        img_parts = 0
        img_url_len = 0
        has_image = False
        for m in request.messages:
            if isinstance(m.content, list):
                for p in m.content:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        has_image = True
                        img_parts += 1
                        iu = p.get("image_url")
                        if isinstance(iu, dict):
                            url = str(iu.get("url") or "")
                        else:
                            url = str(iu or "")
                        img_url_len += len(url)
        total_len = sum(len(m.content) for m in request.messages if isinstance(m.content, str))
        if total_len < 1000:
            len_bucket = "S"
        elif total_len < 4000:
            len_bucket = "M"
        elif total_len < 16000:
            len_bucket = "L"
        else:
            len_bucket = "XL"
        model_set = sorted(m.name for _, m in available)

        feature_str = (
            f"tc={has_tools}|tools={','.join(tool_names[:5])}|tss={tools_sig}"
            f"|cn={has_chinese}|img={has_image}|imgn={img_parts}|imgul={img_url_len}"
            f"|stream={request.stream}|len={len_bucket}"
            f"|models={','.join(model_set)}"
        )
        return hashlib.blake2b(feature_str.encode(), digest_size=8).hexdigest()
