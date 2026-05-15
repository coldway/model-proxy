# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from src.models.schemas import AppConfig, AppSettings, ModelConfig, ProviderConfig, RateLimit

if TYPE_CHECKING:
    from src.config.catalog import CatalogManager

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("conf/config.yaml")


class ConfigManager:
    """配置管理器
    config.yaml 仅保存 API Key 和服务设置
    厂商/模型的启用状态、优先级等由 CatalogManager 管理
    """

    def __init__(self, config_path: Path | None = None, catalog: "CatalogManager | None" = None):
        self._path = config_path or CONFIG_FILE
        self._catalog = catalog
        self._api_keys: dict[str, str] = {}
        self._settings = AppSettings()
        self._load()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def catalog(self) -> "CatalogManager | None":
        return self._catalog

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                for name, prov_data in raw.get("providers", {}).items():
                    if isinstance(prov_data, dict):
                        self._api_keys[name] = prov_data.get("api_key", "")
                settings_data = raw.get("settings", {})
                if settings_data:
                    self._settings = AppSettings(**settings_data)
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}，使用默认设置")

    def save(self) -> None:
        """仅保存 API Key 和 settings 到 config.yaml"""
        providers = {}
        all_provider_ids = set(self._api_keys.keys())
        if self._catalog:
            all_provider_ids |= set(self._catalog.get_all_providers().keys())
        for name in sorted(all_provider_ids):
            providers[name] = {"api_key": self._api_keys.get(name, "")}
        data = {
            "providers": providers,
            "settings": self._settings.model_dump(),
        }
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"配置已保存至 {self._path}")

    # --- API Key 管理 ---

    def get_api_key(self, provider: str) -> str:
        return self._api_keys.get(provider, "")

    def update_api_key(self, provider: str, api_key: str) -> None:
        self._api_keys[provider] = api_key
        self.save()

    def has_api_key(self, provider: str) -> bool:
        return bool(self._api_keys.get(provider, "").strip())

    # --- 兼容旧接口：通过 catalog 提供统一视图 ---

    @property
    def config(self) -> AppConfig:
        """兼容旧接口：构造 AppConfig 对象"""
        providers: dict[str, ProviderConfig] = {}
        if self._catalog:
            for prov_id, prov_data in self._catalog.get_all_providers().items():
                models = []
                for m in prov_data.get("models", []):
                    if m.get("enabled"):
                        rl = None
                        rpd = m.get("default_rpd", 0)
                        rpm = m.get("default_rpm", 0)
                        tpm = m.get("default_tpm", 0)
                        tpd = m.get("default_tpd", 0)
                        if rpd or rpm or tpm or tpd:
                            rl = RateLimit(rpd=rpd, rpm=rpm, tpm=tpm, tpd=tpd)
                        models.append(ModelConfig(
                            name=m["id"],
                            enabled=True,
                            priority=m.get("priority", 99),
                            rate_limit=rl,
                            tool_calling=m.get("tool_calling", False),
                        ))
                providers[prov_id] = ProviderConfig(
                    api_key=self._api_keys.get(prov_id, ""),
                    enabled=prov_data.get("enabled", False),
                    priority=prov_data.get("priority", 99),
                    models=models,
                )
        return AppConfig(providers=providers, settings=self._settings)

    def get_enabled_models(self) -> list[tuple[str, ModelConfig]]:
        """获取所有已启用的模型，按厂商优先级 + 模型优先级排序"""
        if not self._catalog:
            return []
        result: list[tuple[str, ModelConfig]] = []
        for prov_id, model_data in self._catalog.get_all_active_models_sorted():
            rl = None
            rpd = model_data.get("default_rpd", 0)
            rpm = model_data.get("default_rpm", 0)
            tpm = model_data.get("default_tpm", 0)
            tpd = model_data.get("default_tpd", 0)
            if rpd or rpm or tpm or tpd:
                rl = RateLimit(rpd=rpd, rpm=rpm, tpm=tpm, tpd=tpd)
            mc = ModelConfig(
                name=model_data["id"],
                enabled=True,
                priority=model_data.get("priority", 99),
                rate_limit=rl,
                tool_calling=model_data.get("tool_calling", False),
            )
            result.append((prov_id, mc))
        return result

    def get_providers_sorted(self) -> list[tuple[str, ProviderConfig]]:
        """获取所有厂商，按优先级排序"""
        if not self._catalog:
            return []
        result = []
        for prov_id, prov_data in self._catalog.get_providers_sorted():
            models = []
            for m in prov_data.get("models", []):
                if m.get("enabled"):
                    rl = None
                    rpd = m.get("default_rpd", 0)
                    rpm = m.get("default_rpm", 0)
                    tpm = m.get("default_tpm", 0)
                    tpd = m.get("default_tpd", 0)
                    if rpd or rpm or tpm or tpd:
                        rl = RateLimit(rpd=rpd, rpm=rpm, tpm=tpm, tpd=tpd)
                    models.append(ModelConfig(
                        name=m["id"],
                        enabled=True,
                        priority=m.get("priority", 99),
                        rate_limit=rl,
                        tool_calling=m.get("tool_calling", False),
                    ))
            pc = ProviderConfig(
                api_key=self._api_keys.get(prov_id, ""),
                enabled=prov_data.get("enabled", False),
                priority=prov_data.get("priority", 99),
                models=models,
            )
            result.append((prov_id, pc))
        return result

    # --- 委托到 catalog 的操作 ---

    def toggle_model(self, provider: str, model_name: str, enabled: bool) -> None:
        if self._catalog:
            self._catalog.set_model_enabled(provider, model_name, enabled)

    def update_model_priority(self, provider: str, model_name: str, priority: int) -> None:
        if self._catalog:
            self._catalog.set_model_priority(provider, model_name, priority)

    def add_model(self, provider: str, model: ModelConfig) -> None:
        if self._catalog:
            existing = self._catalog.get_models(provider)
            if not any(m["id"] == model.name for m in existing):
                rpd = model.rate_limit.rpd if model.rate_limit else 0
                rpm = model.rate_limit.rpm if model.rate_limit else 0
                self._catalog.add_model(provider, {
                    "id": model.name,
                    "name": model.name,
                    "default_rpd": rpd,
                    "default_rpm": rpm,
                    "category": "通用",
                })
            self._catalog.activate_model(provider, model.name, model.priority)

    def update_provider_priority(self, provider: str, priority: int) -> None:
        if self._catalog:
            self._catalog.set_provider_priority(provider, priority)
