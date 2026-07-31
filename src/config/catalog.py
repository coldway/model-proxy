# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CATALOG_FILE = Path("conf/providers_catalog.yaml")
CATALOG_SAVE_DEBOUNCE = 2


class CatalogManager:
    """厂商模型目录管理器
    同时管理目录信息（模型列表、默认限速）和运行时状态（启用、优先级）
    """

    def __init__(self, catalog_path: Path | None = None):
        self._path = catalog_path or CATALOG_FILE
        self._data: dict[str, Any] = self._load()
        self._save_timer: threading.Timer | None = None
        self._dirty = False
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {"providers": {}}
                self._validate(data)
                return data
            except Exception as e:
                logger.error("加载模型目录失败: %s", e)
        return {"providers": {}}

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        """启动时校验 catalog 配置基本结构，异常则打警告但不阻断"""
        providers = data.get("providers")
        if not isinstance(providers, dict):
            logger.warning("catalog 校验: providers 不是 dict，将使用空配置")
            data["providers"] = {}
            return
        for prov_id, prov in list(providers.items()):
            if not isinstance(prov, dict):
                logger.warning("catalog 校验: providers.%s 不是 dict，跳过", prov_id)
                del providers[prov_id]
                continue
            priority = prov.get("priority")
            if priority is not None and not isinstance(priority, (int, float)):
                logger.warning("catalog 校验: %s.priority 类型错误(%s)，重置为 99", prov_id, type(priority).__name__)
                prov["priority"] = 99
            models = prov.get("models")
            if models is not None and not isinstance(models, list):
                logger.warning("catalog 校验: %s.models 不是 list，重置为空", prov_id)
                prov["models"] = []

    def save(self) -> None:
        """延迟写入：合并短时间内的多次变更为一次磁盘操作"""
        with self._lock:
            self._dirty = True
            if self._save_timer is None or not self._save_timer.is_alive():
                self._save_timer = threading.Timer(CATALOG_SAVE_DEBOUNCE, self._flush)
                self._save_timer.daemon = True
                self._save_timer.start()

    def _flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            try:
                import tempfile, os
                dir_name = os.path.dirname(self._path) or "."
                fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    os.replace(tmp_path, self._path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                self._dirty = False
                logger.info("模型目录已保存至 %s", self._path)
            except Exception as e:
                logger.warning("保存模型目录失败（将在下次重试）: %s", e)

    def flush(self) -> None:
        """立即写入磁盘（用于优雅关闭）"""
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
        self._flush()

    # --- 厂商信息查询 ---

    def get_all_providers(self) -> dict[str, Any]:
        with self._lock:
            return self._data.get("providers", {})

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get("providers", {}).get(provider_id)

    def get_models(self, provider_id: str) -> list[dict[str, Any]]:
        with self._lock:
            provider = self._data.get("providers", {}).get(provider_id)
            if not provider:
                return []
            return provider.get("models", [])

    def get_model(self, provider_id: str, model_id: str) -> dict[str, Any] | None:
        """按 id 查找目录中的单条模型记录（含 tool_calling 等人工标注）"""
        for m in self.get_models(provider_id):
            if m.get("id") == model_id:
                return m
        return None

    def search_models(self, query: str) -> list[dict[str, Any]]:
        """搜索所有厂商的模型（模糊匹配 id、name、description、category）"""
        results = []
        query_lower = query.lower()
        with self._lock:
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

    def set_model_type(self, provider_id: str, model_id: str, model_type: str) -> None:
        for model in self.get_models(provider_id):
            if model["id"] == model_id:
                model["type"] = model_type
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
