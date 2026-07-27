# Created by model-proxy on 2026/07/25
# Copyright © 2026

"""Rapid-MLX 多实例管理 API 路由

提供 rapid-mlx 实例的生命周期管理接口：
- 列出所有实例及状态
- 启动/停止/重启实例
- 发现外部运行的实例
- 配置持久化与重载
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rapid-mlx", tags=["rapid-mlx"])

_manager = None


def set_manager(manager) -> None:
    """由 main.py 在启动时注入 RapidMLXManager 实例"""
    global _manager
    _manager = manager


def _require_manager():
    if _manager is None:
        raise HTTPException(status_code=503, detail="Rapid-MLX 管理器未初始化")
    return _manager


class StartInstanceRequest(BaseModel):
    model: str = Field(..., description="模型名（HuggingFace 路径或别名）")
    port: int | None = Field(None, description="端口号（留空自动分配）")
    extra_args: list[str] = Field(default_factory=list, description="额外 CLI 参数")
    served_model_name: str = Field("", description="API 中显示的模型名（留空使用原始名）")
    auto_start: bool = Field(True, description="是否随 model-proxy 自动启动")


class StopInstanceRequest(BaseModel):
    model: str = Field(..., description="模型名")
    port: int = Field(..., description="端口号")


@router.get("/instances", summary="列出所有实例")
async def list_instances():
    """列出所有 rapid-mlx 实例的状态"""
    mgr = _require_manager()
    instances = mgr.list_instances()
    return {
        "status": "ok",
        "total": len(instances),
        "running": mgr.get_running_count(),
        "instances": instances,
    }


@router.post("/instances/start", summary="启动实例")
async def start_instance(body: StartInstanceRequest):
    """启动一个新的 rapid-mlx 实例"""
    mgr = _require_manager()
    try:
        state = await mgr.start_instance(
            model=body.model,
            port=body.port,
            extra_args=body.extra_args,
            served_model_name=body.served_model_name,
            auto_start=body.auto_start,
        )
        return {
            "status": "ok",
            "message": f"实例 {body.model} 已启动",
            "instance": state.to_info(),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/instances/stop", summary="停止实例")
async def stop_instance(body: StopInstanceRequest):
    """停止指定 rapid-mlx 实例"""
    mgr = _require_manager()
    success = await mgr.stop_instance(body.model, body.port)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到实例: {body.model}@{body.port}")
    return {"status": "ok", "message": f"实例 {body.model}@{body.port} 已停止"}


@router.post("/instances/restart", summary="重启实例")
async def restart_instance(body: StopInstanceRequest):
    """重启指定 rapid-mlx 实例"""
    mgr = _require_manager()
    try:
        state = await mgr.restart_instance(body.model, body.port)
        return {
            "status": "ok",
            "message": f"实例 {body.model} 已重启",
            "instance": state.to_info(),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/instances/uninstall/{model:path}", summary="卸载模型")
async def uninstall_model(model: str):
    """完全卸载模型：停止实例 + 移除配置 + 删除本地文件"""
    mgr = _require_manager()
    result = await mgr.uninstall_model(model)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "ok", **result}


@router.delete("/instances/{model:path}", summary="移除实例")
async def remove_instance(model: str, port: int):
    """停止并移除实例（从配置中删除）"""
    mgr = _require_manager()
    success = await mgr.remove_instance(model, port)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到实例: {model}@{port}")
    return {"status": "ok", "message": f"实例 {model}@{port} 已移除"}


@router.post("/instances/stop-all", summary="停止所有实例")
async def stop_all_instances():
    """停止所有运行中的实例"""
    mgr = _require_manager()
    await mgr.stop_all()
    return {"status": "ok", "message": "所有实例已停止"}


@router.post("/instances/start-all", summary="启动所有已配置实例")
async def start_all_instances():
    """启动所有 enabled + auto_start 的实例"""
    mgr = _require_manager()
    started = await mgr.start_all_enabled()
    return {
        "status": "ok",
        "message": f"已启动 {len(started)} 个实例",
        "started": started,
    }


@router.post("/discover", summary="发现外部实例")
async def discover_external():
    """发现非本管理器启动的外部 rapid-mlx 实例（通过 rapid-mlx ps）"""
    mgr = _require_manager()
    discovered = await mgr.discover_external()
    return {
        "status": "ok",
        "discovered": len(discovered),
        "instances": [s.to_info() for s in discovered],
    }


@router.get("/health", summary="实例健康检查")
async def health_check_all():
    """对所有实例执行健康检查"""
    mgr = _require_manager()
    results = await mgr.health_check_all()
    return {
        "status": "ok",
        "results": {k: v.value for k, v in results.items()},
        "instances": mgr.list_instances(),
    }


@router.get("/model-urls", summary="模型 URL 映射")
async def get_model_urls():
    """获取当前运行实例的 model → base_url 映射"""
    mgr = _require_manager()
    mapping = mgr.get_model_url_map()
    return {"status": "ok", "mapping": mapping}


@router.post("/reload", summary="重载配置")
async def reload_config():
    """重新加载 rapid_mlx_instances.yaml 配置"""
    mgr = _require_manager()
    mgr._load_config()
    return {
        "status": "ok",
        "message": "配置已重载",
        "total_instances": len(mgr.list_instances()),
    }


@router.get("/catalog", summary="官方模型目录")
async def list_catalog(type: str = ""):
    """获取 rapid-mlx 官方支持的所有可用模型

    可通过 ?type=text 或 ?type=audio 过滤
    """
    mgr = _require_manager()
    models = mgr.list_available_models()
    if type:
        models = [m for m in models if m.get("type") == type]
    return {
        "status": "ok",
        "total": len(models),
        "models": models,
    }


@router.get("/cached", summary="本地已缓存模型")
async def list_cached():
    """列出本地 HuggingFace 缓存中已下载的模型"""
    mgr = _require_manager()
    models = mgr.list_cached_models()
    return {
        "status": "ok",
        "total": len(models),
        "models": models,
    }
