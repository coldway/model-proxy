# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""成本追踪与预算控制模块

为每个模型维护 token 用量 → 成本映射，支持月度预算上限。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_COST_FILE = Path("data/cost_tracking.yaml")
_BUDGET_FILE = Path("conf/cost_budget.yaml")


@dataclass
class ModelCost:
    """每千 token 的成本（美元）"""
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


@dataclass
class MonthlyUsage:
    """月度累计用量"""
    month: str = ""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)


_DEFAULT_COST_MAP: dict[str, ModelCost] = {
    "gpt-4o": ModelCost(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelCost(input_per_1k=0.00015, output_per_1k=0.0006),
    "claude-sonnet-4-20250514": ModelCost(input_per_1k=0.003, output_per_1k=0.015),
    "gemini-2.5-pro": ModelCost(input_per_1k=0.0, output_per_1k=0.0),
    "gemini-2.5-flash": ModelCost(input_per_1k=0.0, output_per_1k=0.0),
    "deepseek-chat": ModelCost(input_per_1k=0.00014, output_per_1k=0.00028),
    "llama-3.3-70b-versatile": ModelCost(input_per_1k=0.0, output_per_1k=0.0),
}


class CostTracker:
    """成本追踪器：记录每次调用的 token 消耗并计算费用"""

    def __init__(self, budget_limit_usd: float = 0.0):
        self._lock = threading.Lock()
        self._cost_map: dict[str, ModelCost] = dict(_DEFAULT_COST_MAP)
        self._budget_limit = budget_limit_usd
        self._current_month = self._get_month()
        self._usage = MonthlyUsage(month=self._current_month)
        self._dirty = False
        self._load()
        self._load_budget()

    @staticmethod
    def _get_month() -> str:
        return time.strftime("%Y-%m")

    def _load(self) -> None:
        if not _COST_FILE.exists():
            return
        try:
            data = yaml.safe_load(_COST_FILE.read_text(encoding="utf-8")) or {}
            if data.get("month") == self._current_month:
                self._usage.total_cost_usd = data.get("total_cost_usd", 0.0)
                self._usage.total_input_tokens = data.get("total_input_tokens", 0)
                self._usage.total_output_tokens = data.get("total_output_tokens", 0)
                self._usage.by_model = data.get("by_model", {})
        except Exception as e:
            logger.warning("加载成本追踪数据失败: %s", e)

    def _load_budget(self) -> None:
        if not _BUDGET_FILE.exists():
            return
        try:
            data = yaml.safe_load(_BUDGET_FILE.read_text(encoding="utf-8")) or {}
            self._budget_limit = data.get("monthly_budget_usd", 0.0)
            custom_costs = data.get("model_costs", {})
            for model_name, costs in custom_costs.items():
                self._cost_map[model_name] = ModelCost(
                    input_per_1k=costs.get("input_per_1k", 0.0),
                    output_per_1k=costs.get("output_per_1k", 0.0),
                )
        except Exception as e:
            logger.warning("加载预算配置失败: %s", e)

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """记录一次调用的 token 消耗，返回本次费用（美元）"""
        cost_info = self._cost_map.get(model, ModelCost())
        cost = (input_tokens / 1000 * cost_info.input_per_1k +
                output_tokens / 1000 * cost_info.output_per_1k)

        with self._lock:
            current_month = self._get_month()
            if current_month != self._current_month:
                self._flush_unlocked()
                self._current_month = current_month
                self._usage = MonthlyUsage(month=current_month)

            self._usage.total_cost_usd += cost
            self._usage.total_input_tokens += input_tokens
            self._usage.total_output_tokens += output_tokens

            model_stats = self._usage.by_model.setdefault(model, {
                "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0,
            })
            model_stats["cost_usd"] += cost
            model_stats["input_tokens"] += input_tokens
            model_stats["output_tokens"] += output_tokens
            model_stats["requests"] += 1
            self._dirty = True

        return cost

    def is_over_budget(self) -> bool:
        """检查是否超出月度预算"""
        if self._budget_limit <= 0:
            return False
        with self._lock:
            return self._usage.total_cost_usd >= self._budget_limit

    def get_budget_remaining(self) -> float:
        """获取月度剩余预算（美元），0 表示无限制"""
        if self._budget_limit <= 0:
            return 0.0
        with self._lock:
            return max(0.0, self._budget_limit - self._usage.total_cost_usd)

    def get_stats(self) -> dict[str, Any]:
        """获取当月统计"""
        with self._lock:
            return {
                "month": self._usage.month,
                "total_cost_usd": round(self._usage.total_cost_usd, 6),
                "total_input_tokens": self._usage.total_input_tokens,
                "total_output_tokens": self._usage.total_output_tokens,
                "budget_limit_usd": self._budget_limit,
                "budget_remaining_usd": round(self.get_budget_remaining(), 6) if self._budget_limit > 0 else None,
                "by_model": {
                    k: {**v, "cost_usd": round(v["cost_usd"], 6)}
                    for k, v in self._usage.by_model.items()
                },
            }

    def get_model_cost(self, model: str) -> ModelCost:
        """获取模型的单价配置"""
        return self._cost_map.get(model, ModelCost())

    def set_model_cost(self, model: str, input_per_1k: float, output_per_1k: float) -> None:
        """动态设置模型单价"""
        self._cost_map[model] = ModelCost(input_per_1k=input_per_1k, output_per_1k=output_per_1k)

    def flush(self) -> None:
        """持久化当前数据"""
        with self._lock:
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if not self._dirty:
            return
        try:
            _COST_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "month": self._usage.month,
                "total_cost_usd": self._usage.total_cost_usd,
                "total_input_tokens": self._usage.total_input_tokens,
                "total_output_tokens": self._usage.total_output_tokens,
                "by_model": self._usage.by_model,
            }
            _COST_FILE.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            self._dirty = False
        except Exception as e:
            logger.warning("保存成本追踪数据失败: %s", e)
