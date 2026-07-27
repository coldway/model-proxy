# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""实时日志路由"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.api.log_buffer import get_instance as _get_log_buffer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/logs", summary="获取日志")
async def get_logs(after: int = 0, limit: int = 200):
    """获取最近的日志条目（支持增量拉取）"""
    handler = _get_log_buffer()
    if not handler:
        return {"entries": [], "latest_seq": 0}
    limit = min(max(limit, 1), 500)
    entries, latest_seq = handler.get_logs(after_seq=after, limit=limit)
    return {"entries": entries, "latest_seq": latest_seq}


@router.delete("/api/logs/clear", summary="清空日志")
async def clear_logs():
    """清空日志缓冲"""
    handler = _get_log_buffer()
    if handler:
        handler.clear()
    return {"status": "ok"}


def _tail_file(path, n: int, chunk_size: int = 8192) -> list[str]:
    """从文件末尾高效读取最后 n 行"""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []
        buf = b""
        pos = size
        lines_found = 0
        while pos > 0 and lines_found <= n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            lines_found = buf.count(b"\n")
        return buf.decode("utf-8", errors="replace").splitlines()[-n:]


@router.get("/api/logs/history", summary="查询历史日志")
async def get_log_history(date: str = "", tail: int = 500):
    """查询历史日志文件"""
    log_dir = Path("logs")
    if not log_dir.is_dir():
        return {"dates": [], "lines": []}

    if not date:
        dates = []
        for f in sorted(log_dir.iterdir()):
            if f.is_file() and f.name.startswith("app.log"):
                stat = f.stat()
                size_kb = round(stat.st_size / 1024, 1)
                if f.name == "app.log":
                    dates.append({"name": "app.log", "label": "当前", "size_kb": size_kb})
                else:
                    suffix = f.name.replace("app.log.", "")
                    dates.append({"name": f.name, "label": suffix, "size_kb": size_kb})
        return {"dates": dates}

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) and date != "current":
        return JSONResponse(status_code=400, content={"detail": "日期格式无效"})

    target = log_dir / ("app.log" if date == "current" else f"app.log.{date}")
    if not target.resolve().parent.samefile(log_dir.resolve()):
        return JSONResponse(status_code=400, content={"detail": "非法路径"})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"detail": f"日志文件不存在: {target.name}"})

    tail = min(max(tail, 1), 5000)
    try:
        lines = await asyncio.to_thread(_tail_file, target, tail)
        return {"file": target.name, "returned_lines": len(lines), "lines": lines}
    except Exception as e:
        logger.warning("读取日志文件失败: %s", e)
        return JSONResponse(status_code=500, content={"detail": "读取日志文件失败"})


@router.get("/api/logs/level", summary="获取日志级别")
async def get_log_level():
    """获取当前日志级别"""
    level = logging.getLogger().level
    return {"level": logging.getLevelName(level)}


@router.post("/api/logs/level", summary="切换日志级别")
async def set_log_level(level: str):
    """动态切换日志级别"""
    level_upper = level.upper()
    numeric = getattr(logging, level_upper, None)
    if numeric is None:
        raise HTTPException(status_code=400, detail=f"无效的日志级别: {level}")
    logging.getLogger().setLevel(numeric)
    logger.info("日志级别已切换为 %s", level_upper)
    return {"status": "ok", "level": level_upper}
