# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""Provider 自动发现注册表

通过 @register_provider 装饰器注册厂商，main.py 中可用
discover_providers() 自动获取全部已注册的 Provider 工厂。
"""

from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, dict] = {}


def register_provider(
    name: str,
    *,
    requires_key: bool = True,
):
    """装饰器：将 Provider 类注册到全局注册表

    用法:
        @register_provider("google")
        class GoogleProvider(BaseProvider): ...
    """
    def decorator(cls):
        _REGISTRY[name] = {
            "class": cls,
            "requires_key": requires_key,
        }
        return cls
    return decorator


def discover_providers() -> dict[str, dict]:
    """返回全部已注册的 Provider 信息（延迟导入触发注册）"""
    import importlib
    import pkgutil
    import src.providers as pkg

    for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
        if module_name in ("base", "utils", "__init__"):
            continue
        try:
            importlib.import_module(f"src.providers.{module_name}")
        except Exception as e:
            logger.warning("自动发现 Provider %s 失败: %s", module_name, e)

    return dict(_REGISTRY)


def get_provider_factory(name: str) -> Callable[[str], "BaseProvider"] | None:
    """获取已注册 Provider 的工厂函数"""
    entry = _REGISTRY.get(name)
    if not entry:
        return None
    cls = entry["class"]
    return lambda key: cls(key)
