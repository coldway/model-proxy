# Created by yuanrui on 2026/08/01
# Copyright © 2026

"""Config Platform 薄 HTTP 客户端（model-proxy 双读接入）"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/.config-cache")


class PlatformClient:
    """从 Config Platform 读取合并配置，Platform 不可达时返回 None。"""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        service: str | None = None,
        env: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or os.getenv("CONFIG_PLATFORM_URL", "")).rstrip("/")
        self.token = token or os.getenv("CONFIG_PLATFORM_TOKEN", "")
        self.service = service or os.getenv("CONFIG_SERVICE", "model-proxy")
        self.env = env or os.getenv("CONFIG_ENV", "local")
        self.timeout = timeout
        self.enabled = bool(self.base_url and self.token)

    @classmethod
    def from_env(cls) -> PlatformClient | None:
        client = cls()
        if not client.enabled:
            return None
        return client

    def get_merged(self, namespace: str) -> dict[str, Any] | None:
        cached = self._read_cache(namespace)
        try:
            data = self._fetch(namespace)
            self._write_cache(namespace, data)
            logger.info("已从 Config Platform 加载 %s/%s/%s", self.service, self.env, namespace)
            return data
        except Exception as e:
            logger.warning("Config Platform 不可用，降级本地配置: %s", e)
            return cached

    def patch_runtime(self, namespace: str, content: dict[str, Any], merge: bool = True) -> bool:
        if not self.enabled:
            return False
        url = f"{self.base_url}/v1/config/{self.service}/{self.env}/{namespace}/runtime"
        body = json.dumps({"content": content, "merge": merge}).encode()
        req = Request(url, data=body, method="PUT")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning("回写 Config Platform runtime 失败: %s", e)
            return False

    def _fetch(self, namespace: str) -> dict[str, Any]:
        url = f"{self.base_url}/v1/config/{self.service}/{self.env}/{namespace}"
        req = Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")
        with urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode())
        content = payload.get("content")
        if not isinstance(content, dict):
            raise ValueError("Platform 返回 content 不是 dict")
        return content

    def _cache_path(self, namespace: str) -> Path:
        return CACHE_DIR / f"{self.service}_{self.env}_{namespace.replace('/', '_')}.json"

    def _read_cache(self, namespace: str) -> dict[str, Any] | None:
        path = self._cache_path(namespace)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取配置缓存失败 %s: %s", path, e)
            return None

    def _write_cache(self, namespace: str, data: dict[str, Any]) -> None:
        path = self._cache_path(namespace)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("写入配置缓存失败 %s: %s", path, e)


def extract_catalog_runtime(catalog: dict[str, Any]) -> dict[str, Any]:
    """从完整 catalog 提取 runtime 层字段，用于 Platform 回写。"""
    out: dict[str, Any] = {"providers": {}}
    for prov_id, prov in (catalog.get("providers") or {}).items():
        if not isinstance(prov, dict):
            continue
        rp: dict[str, Any] = {}
        for k in ("enabled", "priority"):
            if k in prov:
                rp[k] = prov[k]
        models_out = []
        for m in prov.get("models") or []:
            if not isinstance(m, dict) or "id" not in m:
                continue
            rm: dict[str, Any] = {"id": m["id"]}
            for k in ("enabled", "priority", "type"):
                if k in m:
                    rm[k] = m[k]
            if len(rm) > 1:
                models_out.append(rm)
        if models_out:
            rp["models"] = models_out
        if rp:
            out["providers"][prov_id] = rp
    return out
