# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CATALOG_FILE = Path("conf/providers_catalog.yaml")


class CatalogManager:
    """厂商模型目录管理器
    同时管理目录信息（模型列表、默认限速）和运行时状态（启用、优先级）
    """

    def __init__(self, catalog_path: Path | None = None):
        self._path = catalog_path or CATALOG_FILE
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {"providers": {}}
            except Exception as e:
                logger.error(f"加载模型目录失败: {e}")
        return {"providers": {}}

    def save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"模型目录已保存至 {self._path}")

    # --- 厂商信息查询 ---

    def get_all_providers(self) -> dict[str, Any]:
        return self._data.get("providers", {})

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self._data.get("providers", {}).get(provider_id)

    def get_models(self, provider_id: str) -> list[dict[str, Any]]:
        provider = self.get_provider(provider_id)
        if not provider:
            return []
        return provider.get("models", [])

    def search_models(self, query: str) -> list[dict[str, Any]]:
        """搜索所有厂商的模型（模糊匹配 id、name、description、category）"""
        results = []
        query_lower = query.lower()
        for prov_id, prov in self._data.get("providers", {}).items():
            for model in prov.get("models", []):
                searchable = f"{model.get('id', '')} {model.get('name', '')} {model.get('description', '')} {model.get('category', '')}".lower()
                if query_lower in searchable:
                    results.append({
                        **model,
                        "provider_id": prov_id,
                        "provider_name": prov.get("name", prov_id),
                    })
        return results

    # --- 厂商运行时状态 ---

    def is_provider_enabled(self, provider_id: str) -> bool:
        prov = self.get_provider(provider_id)
        return bool(prov and prov.get("enabled", False))

    def get_provider_priority(self, provider_id: str) -> int:
        prov = self.get_provider(provider_id)
        return prov.get("priority", 99) if prov else 99

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        prov = self.get_provider(provider_id)
        if prov:
            prov["enabled"] = enabled
            self.save()

    def set_provider_priority(self, provider_id: str, priority: int) -> None:
        prov = self.get_provider(provider_id)
        if prov:
            prov["priority"] = priority
            self.save()

    def get_providers_sorted(self) -> list[tuple[str, dict[str, Any]]]:
        """获取所有厂商，按优先级排序"""
        providers = self._data.get("providers", {})
        items = list(providers.items())
        items.sort(key=lambda x: x[1].get("priority", 99))
        return items

    # --- 模型运行时状态 ---

    def get_active_models(self, provider_id: str) -> list[dict[str, Any]]:
        """获取厂商下已启用的模型"""
        models = self.get_models(provider_id)
        return [m for m in models if m.get("enabled")]

    def get_all_active_models_sorted(self) -> list[tuple[str, dict[str, Any]]]:
        """获取所有已启用厂商下已启用模型，按厂商优先级 + 模型优先级排序"""
        result: list[tuple[str, dict[str, Any], int]] = []
        for prov_id, prov in self._data.get("providers", {}).items():
            if not prov.get("enabled"):
                continue
            prov_priority = prov.get("priority", 99)
            for model in prov.get("models", []):
                if model.get("enabled"):
                    result.append((prov_id, model, prov_priority))
        result.sort(key=lambda x: (x[2], x[1].get("priority", 99)))
        return [(prov_id, model) for prov_id, model, _ in result]

    def set_model_enabled(self, provider_id: str, model_id: str, enabled: bool) -> None:
        for model in self.get_models(provider_id):
            if model["id"] == model_id:
                model["enabled"] = enabled
                self.save()
                return

    def set_model_priority(self, provider_id: str, model_id: str, priority: int) -> None:
        for model in self.get_models(provider_id):
            if model["id"] == model_id:
                model["priority"] = priority
                self.save()
                return

    def activate_model(self, provider_id: str, model_id: str, priority: int = 99) -> bool:
        """从目录中启用一个模型（设置 enabled=true 和 priority）"""
        for model in self.get_models(provider_id):
            if model["id"] == model_id:
                model["enabled"] = True
                model["priority"] = priority
                self.save()
                return True
        return False

    def deactivate_model(self, provider_id: str, model_id: str) -> bool:
        """停用模型（移除 enabled 和 priority 字段）"""
        for model in self.get_models(provider_id):
            if model["id"] == model_id:
                model.pop("enabled", None)
                model.pop("priority", None)
                self.save()
                return True
        return False

    # --- 目录编辑 ---

    def add_model(self, provider_id: str, model: dict[str, Any]) -> bool:
        """向目录中添加模型"""
        providers = self._data.setdefault("providers", {})
        if provider_id not in providers:
            return False
        models = providers[provider_id].setdefault("models", [])
        if any(m["id"] == model["id"] for m in models):
            return False
        models.append(model)
        self.save()
        return True

    def remove_model(self, provider_id: str, model_id: str) -> bool:
        """从目录中删除模型"""
        providers = self._data.get("providers", {})
        if provider_id not in providers:
            return False
        models = providers[provider_id].get("models", [])
        original_len = len(models)
        providers[provider_id]["models"] = [m for m in models if m["id"] != model_id]
        if len(providers[provider_id]["models"]) < original_len:
            self.save()
            return True
        return False

    def add_provider(self, provider_id: str, provider_info: dict[str, Any]) -> bool:
        """添加新厂商"""
        providers = self._data.setdefault("providers", {})
        if provider_id in providers:
            return False
        providers[provider_id] = provider_info
        self.save()
        return True

    def remove_provider(self, provider_id: str) -> bool:
        """删除厂商"""
        providers = self._data.get("providers", {})
        if provider_id in providers:
            del providers[provider_id]
            self.save()
            return True
        return False

    def reorder_providers(self, ordered_ids: list[str]) -> None:
        """按给定顺序重新设置厂商优先级"""
        for idx, prov_id in enumerate(ordered_ids, start=1):
            prov = self.get_provider(prov_id)
            if prov:
                prov["priority"] = idx
        self.save()
