# Created by model-proxy on 2026/05/11
# Copyright © 2026

import shutil
from pathlib import Path

import yaml

from src.config.catalog import CatalogManager
from src.config.manager import ConfigManager
from src.models.schemas import ModelConfig, RateLimit

CATALOG_FILE = Path("conf/providers_catalog.yaml")


def _make_catalog(tmp_path: Path) -> CatalogManager:
    """将项目 providers_catalog.yaml 复制到临时目录，返回 CatalogManager"""
    dst = tmp_path / "providers_catalog.yaml"
    shutil.copy(CATALOG_FILE, dst)
    return CatalogManager(catalog_path=dst)


class TestConfigManager:
    def test_default_config_has_google_models(self, tmp_path):
        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=tmp_path / "config.yaml", catalog=catalog)
        config = manager.config
        assert "google" in config.providers
        enabled_google = [m for m in catalog.get_models("google") if m.get("enabled")]
        assert len(config.providers["google"].models) == len(enabled_google)
        assert config.providers["google"].models[0].name == enabled_google[0]["id"]

    def test_load_from_file(self, tmp_path):
        config_data = {
            "providers": {"google": {"api_key": "test-key"}},
            "settings": {"host": "0.0.0.0", "port": 9000},
        }
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=config_file, catalog=catalog)
        assert manager.get_api_key("google") == "test-key"
        assert manager.settings.port == 9000
        assert len(manager.config.providers["google"].models) >= 4

    def test_save_and_reload(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=config_file, catalog=catalog)
        manager.update_api_key("google", "new-key-123")

        manager2 = ConfigManager(config_path=config_file, catalog=catalog)
        assert manager2.get_api_key("google") == "new-key-123"

    def test_get_enabled_models_sorted_by_priority(self, tmp_path):
        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=tmp_path / "config.yaml", catalog=catalog)
        enabled = manager.get_enabled_models()
        prov_priorities = {
            pid: catalog.get_provider_priority(pid)
            for pid, _ in enabled
        }
        sort_keys = [(prov_priorities[pid], m.priority) for pid, m in enabled]
        assert sort_keys == sorted(sort_keys)

    def test_toggle_model(self, tmp_path):
        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=tmp_path / "config.yaml", catalog=catalog)
        manager.toggle_model("google", "gemini-2.5-pro", False)
        for m in manager.config.providers["google"].models:
            if m.name == "gemini-2.5-pro":
                assert not m.enabled
                break

    def test_add_model(self, tmp_path):
        catalog = _make_catalog(tmp_path)
        manager = ConfigManager(config_path=tmp_path / "config.yaml", catalog=catalog)
        new_model = ModelConfig(
            name="new-test-model", enabled=True, priority=5,
            rate_limit=RateLimit(rpd=100, rpm=10),
        )
        manager.add_model("google", new_model)
        names = [m.name for m in manager.config.providers["google"].models]
        assert "new-test-model" in names
