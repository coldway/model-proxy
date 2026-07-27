# Created by model-proxy on 2026/07/25
# Copyright © 2026

"""Rapid-MLX 多实例进程管理器

职责：
- 管理多个 rapid-mlx serve 进程（每个模型一个独立进程/端口）
- 配置持久化（YAML）：记录期望运行的实例列表
- 健康检查：定期探测各实例存活状态
- 自动恢复：启动时根据配置恢复实例
- 端口分配：自动为新实例选择可用端口
- 与 RapidMLXProvider 协同：提供 model→base_url 映射
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

_CONF_PATH = Path("conf/rapid_mlx_instances.yaml")
_DEFAULT_PORT_RANGE_START = 8001
_DEFAULT_PORT_RANGE_END = 8099
_HEALTH_CHECK_TIMEOUT = 3
_STARTUP_WAIT_SECS = 30
_HEALTH_CHECK_INTERVAL = 30
_AUTO_RESTART_MAX_ATTEMPTS = 3
_AUTO_RESTART_COOLDOWN = 300  # 5 分钟内最多重启 _MAX_ATTEMPTS 次


def _find_rapid_mlx_bin() -> str | None:
    """查找 rapid-mlx 可执行文件
    
    优先级: venv 内 → pyenv shims → 系统 PATH
    """
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "rapid-mlx"
    if venv_bin.exists():
        return str(venv_bin)
    pyenv_shim = Path.home() / ".pyenv" / "shims" / "rapid-mlx"
    if pyenv_shim.exists():
        return str(pyenv_shim)
    return shutil.which("rapid-mlx")


def _get_process_memory_mb(pid: int) -> float:
    """通过 ps 获取进程 RSS 内存（MB），失败返回 0"""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            rss_kb = int(result.stdout.strip())
            return round(rss_kb / 1024, 1)
    except Exception:
        pass
    return 0


class InstanceStatus(str, Enum):
    RUNNING = "running"
    STARTING = "starting"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class InstanceConfig:
    """单个 rapid-mlx 实例的配置"""
    model: str
    port: int
    enabled: bool = True
    extra_args: list[str] = field(default_factory=list)
    served_model_name: str = ""
    auto_start: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "port": self.port,
            "enabled": self.enabled,
            "auto_start": self.auto_start,
        }
        if self.extra_args:
            d["extra_args"] = self.extra_args
        if self.served_model_name:
            d["served_model_name"] = self.served_model_name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstanceConfig":
        return cls(
            model=data["model"],
            port=data["port"],
            enabled=data.get("enabled", True),
            extra_args=data.get("extra_args", []),
            served_model_name=data.get("served_model_name", ""),
            auto_start=data.get("auto_start", True),
        )


@dataclass
class InstanceState:
    """运行时实例状态"""
    config: InstanceConfig
    status: InstanceStatus = InstanceStatus.STOPPED
    pid: int | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)
    started_at: float | None = None
    last_health_check: float | None = None
    error_message: str = ""
    restart_count: int = 0
    restart_timestamps: list[float] = field(default_factory=list)
    # 流量统计
    request_count: int = 0
    total_latency_ms: float = 0
    last_request_at: float | None = None

    def to_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "model": self.config.model,
            "port": self.config.port,
            "status": self.status.value,
            "enabled": self.config.enabled,
            "auto_start": self.config.auto_start,
            "base_url": f"http://127.0.0.1:{self.config.port}",
        }
        if self.config.served_model_name:
            info["served_model_name"] = self.config.served_model_name
        if self.config.extra_args:
            info["extra_args"] = self.config.extra_args
        if self.pid:
            info["pid"] = self.pid
        if self.started_at:
            info["uptime_seconds"] = int(time.time() - self.started_at)
        if self.error_message:
            info["error"] = self.error_message
        if self.restart_count > 0:
            info["restart_count"] = self.restart_count
        if self.pid and self.status == InstanceStatus.RUNNING:
            mem_mb = _get_process_memory_mb(self.pid)
            if mem_mb > 0:
                info["memory_mb"] = mem_mb
        if self.request_count > 0:
            info["request_count"] = self.request_count
            info["avg_latency_ms"] = round(self.total_latency_ms / self.request_count, 1)
            if self.last_request_at:
                info["last_request_ago_s"] = int(time.time() - self.last_request_at)
        return info


class RapidMLXManager:
    """Rapid-MLX 多实例进程管理器"""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or _CONF_PATH
        self._instances: dict[str, InstanceState] = {}
        self._health_task: asyncio.Task | None = None
        self._http_client = httpx.AsyncClient(timeout=_HEALTH_CHECK_TIMEOUT)
        self._port_range = (_DEFAULT_PORT_RANGE_START, _DEFAULT_PORT_RANGE_END)
        self._load_config()

    def _load_config(self) -> None:
        """从 YAML 文件加载实例配置"""
        if not self._config_path.exists():
            logger.info("Rapid-MLX 实例配置文件不存在，将创建默认配置")
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if "port_range" in data:
                pr = data["port_range"]
                self._port_range = (pr.get("start", _DEFAULT_PORT_RANGE_START),
                                    pr.get("end", _DEFAULT_PORT_RANGE_END))

            for inst_data in data.get("instances", []):
                try:
                    cfg = InstanceConfig.from_dict(inst_data)
                    key = self._instance_key(cfg.model, cfg.port)
                    self._instances[key] = InstanceState(config=cfg)
                except (KeyError, TypeError) as e:
                    logger.warning("跳过无效实例配置: %s — %s", inst_data, e)

            logger.info("已加载 %d 个 Rapid-MLX 实例配置", len(self._instances))
        except Exception as e:
            logger.error("加载 Rapid-MLX 配置失败: %s", e)

    def save_config(self) -> None:
        """持久化实例配置到 YAML"""
        data: dict[str, Any] = {
            "port_range": {
                "start": self._port_range[0],
                "end": self._port_range[1],
            },
            "instances": [
                state.config.to_dict() for state in self._instances.values()
            ],
        }
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info("Rapid-MLX 实例配置已保存（%d 个实例）", len(self._instances))

    @staticmethod
    def _instance_key(model: str, port: int) -> str:
        return f"{model}@{port}"

    _AUDIO_MODEL_KEYWORDS = ("kokoro", "parler", "bark", "speecht5", "mms-tts", "whisper")

    def _is_audio_model(self, model: str) -> bool:
        """判断模型是否为音频模型（需要 --enable-audio 启动）"""
        model_lower = model.lower()
        if any(kw in model_lower for kw in self._AUDIO_MODEL_KEYWORDS):
            return True
        catalog = self.list_available_models()
        for m in catalog:
            if m.get("type") == "audio" and (
                m.get("alias", "").lower() in model_lower
                or model_lower in m.get("hf_id", "").lower()
            ):
                return True
        return False

    def _find_free_port(self) -> int:
        """在端口范围内找到一个可用端口"""
        used_ports = {s.config.port for s in self._instances.values()}
        for port in range(self._port_range[0], self._port_range[1] + 1):
            if port in used_ports:
                continue
            if self._is_port_available(port):
                return port
        raise RuntimeError(
            f"端口范围 {self._port_range[0]}-{self._port_range[1]} 内无可用端口"
        )

    @staticmethod
    def _is_port_available(port: int) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    async def start_instance(
        self,
        model: str,
        port: int | None = None,
        extra_args: list[str] | None = None,
        served_model_name: str = "",
        auto_start: bool = True,
    ) -> InstanceState:
        """启动一个新的 rapid-mlx 实例"""
        rapid_mlx_bin = _find_rapid_mlx_bin()
        if not rapid_mlx_bin:
            raise RuntimeError("未找到 rapid-mlx 命令，请确认已安装")

        if port is None:
            port = self._find_free_port()
        elif not self._is_port_available(port):
            existing = self._find_instance_by_port(port)
            if existing and existing.status == InstanceStatus.RUNNING:
                raise RuntimeError(f"端口 {port} 已被实例 {existing.config.model} 占用")

        key = self._instance_key(model, port)
        if key in self._instances and self._instances[key].status == InstanceStatus.RUNNING:
            logger.info("实例 %s 已在运行中", key)
            return self._instances[key]

        cfg = InstanceConfig(
            model=model,
            port=port,
            enabled=True,
            extra_args=extra_args or [],
            served_model_name=served_model_name,
            auto_start=auto_start,
        )

        cmd = [rapid_mlx_bin, "serve", model, "--port", str(port)]
        if served_model_name:
            cmd.extend(["--served-model-name", served_model_name])
        if extra_args:
            cmd.extend(extra_args)

        if self._is_audio_model(model) and "--enable-audio" not in (extra_args or []):
            cmd.append("--enable-audio")

        logger.info("启动 Rapid-MLX: %s", " ".join(cmd))
        state = InstanceState(config=cfg, status=InstanceStatus.STARTING)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            state.process = proc
            state.pid = proc.pid
            state.started_at = time.time()
        except Exception as e:
            state.status = InstanceStatus.ERROR
            state.error_message = str(e)
            self._instances[key] = state
            raise RuntimeError(f"启动 rapid-mlx 失败: {e}") from e

        self._instances[key] = state
        self.save_config()

        healthy = await self._wait_for_healthy(state)
        if healthy:
            state.status = InstanceStatus.RUNNING
            logger.info("Rapid-MLX 实例已就绪: %s (port=%d, pid=%d)", model, port, proc.pid)
        else:
            state.status = InstanceStatus.ERROR
            stderr_output = ""
            if proc.stderr:
                try:
                    stderr_output = proc.stderr.read(2048).decode(errors="replace")
                except Exception:
                    pass
            state.error_message = f"启动超时（{_STARTUP_WAIT_SECS}s）" + (
                f": {stderr_output[:200]}" if stderr_output else ""
            )
            logger.warning("Rapid-MLX 实例启动超时: %s (port=%d)", model, port)

        return state

    async def _wait_for_healthy(self, state: InstanceState, timeout: float = _STARTUP_WAIT_SECS) -> bool:
        """等待实例健康就绪"""
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{state.config.port}/v1/models"
        while time.time() < deadline:
            if state.process and state.process.poll() is not None:
                return False
            try:
                resp = await self._http_client.get(url)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(1)
        return False

    async def stop_instance(self, model: str, port: int) -> bool:
        """停止指定实例"""
        key = self._instance_key(model, port)
        state = self._instances.get(key)
        if not state:
            logger.warning("未找到实例: %s", key)
            return False

        if state.process and state.process.poll() is None:
            try:
                state.process.terminate()
                try:
                    state.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    state.process.kill()
                    state.process.wait(timeout=3)
            except Exception as e:
                logger.error("停止实例 %s 失败: %s", key, e)
                return False
        elif state.pid:
            try:
                import os
                os.kill(state.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("向 PID %d 发送 SIGTERM 失败: %s", state.pid, e)

        state.status = InstanceStatus.STOPPED
        state.process = None
        state.pid = None
        state.started_at = None
        state.error_message = ""
        logger.info("已停止实例: %s", key)
        return True

    async def remove_instance(self, model: str, port: int) -> bool:
        """停止并移除实例配置"""
        await self.stop_instance(model, port)
        key = self._instance_key(model, port)
        if key in self._instances:
            del self._instances[key]
            self.save_config()
            return True
        return False

    async def uninstall_model(self, model: str) -> dict:
        """完全卸载模型：停止所有相关实例 + 移除配置 + 删除本地缓存文件"""
        stopped_instances = []
        keys_to_remove = []
        for key, state in list(self._instances.items()):
            if state.config.model == model:
                await self.stop_instance(state.config.model, state.config.port)
                keys_to_remove.append(key)
                stopped_instances.append(key)

        for key in keys_to_remove:
            del self._instances[key]
        if keys_to_remove:
            self.save_config()

        rapid_mlx_bin = _find_rapid_mlx_bin()
        rm_output = ""
        rm_success = False
        if rapid_mlx_bin:
            try:
                proc = subprocess.run(
                    [rapid_mlx_bin, "rm", "-y", model],
                    capture_output=True, text=True, timeout=60,
                )
                rm_output = (proc.stdout + proc.stderr).strip()
                rm_success = proc.returncode == 0
            except subprocess.TimeoutExpired:
                rm_output = "删除超时（60s）"
            except Exception as e:
                rm_output = str(e)
        else:
            rm_output = "未找到 rapid-mlx 命令"

        if not rm_success:
            hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
            dir_name = f"models--{model.replace('/', '--')}"
            model_dir = hf_cache / dir_name
            if model_dir.exists():
                shutil.rmtree(model_dir, ignore_errors=True)
                rm_success = not model_dir.exists()
                rm_output = f"已清理 HF 缓存目录: {model_dir}" if rm_success else rm_output

        return {
            "model": model,
            "stopped_instances": stopped_instances,
            "files_deleted": rm_success,
            "rm_output": rm_output,
        }

    async def restart_instance(self, model: str, port: int) -> InstanceState:
        """重启实例"""
        key = self._instance_key(model, port)
        state = self._instances.get(key)
        if not state:
            raise RuntimeError(f"未找到实例: {key}")

        await self.stop_instance(model, port)
        await asyncio.sleep(1)
        return await self.start_instance(
            model=state.config.model,
            port=state.config.port,
            extra_args=state.config.extra_args,
            served_model_name=state.config.served_model_name,
            auto_start=state.config.auto_start,
        )

    async def start_all_enabled(self) -> list[str]:
        """启动所有 enabled + auto_start 的实例（用于服务启动时恢复）"""
        started: list[str] = []
        for key, state in list(self._instances.items()):
            if state.config.enabled and state.config.auto_start:
                if state.status in (InstanceStatus.STOPPED, InstanceStatus.ERROR, InstanceStatus.UNKNOWN):
                    try:
                        await self.start_instance(
                            model=state.config.model,
                            port=state.config.port,
                            extra_args=state.config.extra_args,
                            served_model_name=state.config.served_model_name,
                            auto_start=state.config.auto_start,
                        )
                        started.append(key)
                    except Exception as e:
                        logger.error("自动启动实例 %s 失败: %s", key, e)
        return started

    async def stop_all(self) -> None:
        """停止所有运行中的实例"""
        for state in list(self._instances.values()):
            if state.status == InstanceStatus.RUNNING:
                await self.stop_instance(state.config.model, state.config.port)

    async def discover_external(self) -> list[InstanceState]:
        """发现非本管理器启动的外部 rapid-mlx 实例（通过 rapid-mlx ps）
        
        同时更新已配置但状态为 stopped 的实例——如果端口上有进程在跑，则标记为 running。
        """
        rapid_mlx_bin = _find_rapid_mlx_bin()
        if not rapid_mlx_bin:
            return []

        try:
            result = subprocess.run(
                [rapid_mlx_bin, "ps"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
        except Exception as e:
            logger.warning("执行 rapid-mlx ps 失败: %s", e)
            return []

        discovered: list[InstanceState] = []
        managed_ports = {s.config.port for s in self._instances.values()}
        running_by_port: dict[int, tuple[int, str]] = {}

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("PID") or line.startswith("--"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                port = int(parts[1])
                model = parts[2]
            except (ValueError, IndexError):
                continue
            running_by_port[port] = (pid, model)

        for state in self._instances.values():
            port_info = running_by_port.get(state.config.port)
            if port_info and state.status in (InstanceStatus.STOPPED, InstanceStatus.ERROR, InstanceStatus.UNKNOWN):
                state.status = InstanceStatus.RUNNING
                state.pid = port_info[0]
                state.started_at = state.started_at or time.time()
                state.error_message = ""

        for port, (pid, model) in running_by_port.items():
            if port in managed_ports:
                continue
            key = self._instance_key(model, port)
            cfg = InstanceConfig(model=model, port=port, enabled=True, auto_start=False)
            state = InstanceState(
                config=cfg,
                status=InstanceStatus.RUNNING,
                pid=pid,
                started_at=time.time(),
            )
            self._instances[key] = state
            discovered.append(state)

        if discovered:
            self.save_config()
            logger.info("发现 %d 个外部 Rapid-MLX 实例", len(discovered))

        return discovered

    async def health_check_all(self) -> dict[str, InstanceStatus]:
        """对所有实例执行健康检查"""
        results: dict[str, InstanceStatus] = {}
        for key, state in list(self._instances.items()):
            if state.status not in (InstanceStatus.RUNNING, InstanceStatus.STARTING):
                results[key] = state.status
                continue

            if state.process and state.process.poll() is not None:
                state.status = InstanceStatus.ERROR
                state.error_message = f"进程退出 (exit_code={state.process.returncode})"
                results[key] = state.status
                continue

            url = f"http://127.0.0.1:{state.config.port}/v1/models"
            try:
                resp = await self._http_client.get(url)
                if resp.status_code == 200:
                    state.status = InstanceStatus.RUNNING
                    state.error_message = ""
                else:
                    state.status = InstanceStatus.ERROR
                    state.error_message = f"HTTP {resp.status_code}"
            except (httpx.ConnectError, httpx.ConnectTimeout):
                state.status = InstanceStatus.ERROR
                state.error_message = "连接失败"
            except Exception as e:
                state.status = InstanceStatus.ERROR
                state.error_message = str(e)[:100]

            state.last_health_check = time.time()
            results[key] = state.status

        return results

    async def start_health_loop(self) -> None:
        """启动周期性健康检查循环（含自动重启）"""
        async def _loop():
            while True:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
                try:
                    results = await self.health_check_all()
                    await self._auto_restart_crashed(results)
                except Exception as e:
                    logger.warning("Rapid-MLX 健康检查异常: %s", e)

        self._health_task = asyncio.create_task(_loop())

    async def _auto_restart_crashed(self, results: dict[str, InstanceStatus]) -> None:
        """对宕掉的实例尝试自动重启"""
        now = time.time()
        for key, status in results.items():
            if status != InstanceStatus.ERROR:
                continue
            state = self._instances.get(key)
            if not state or not state.config.enabled:
                continue
            if not state.config.auto_start:
                continue

            state.restart_timestamps = [
                ts for ts in state.restart_timestamps
                if now - ts < _AUTO_RESTART_COOLDOWN
            ]
            if len(state.restart_timestamps) >= _AUTO_RESTART_MAX_ATTEMPTS:
                if not state.error_message.startswith("[自动重启已暂停]"):
                    state.error_message = (
                        f"[自动重启已暂停] {_AUTO_RESTART_COOLDOWN}s 内已重启 "
                        f"{_AUTO_RESTART_MAX_ATTEMPTS} 次，等待冷却。原因: {state.error_message}"
                    )
                    logger.warning(
                        "实例 %s 自动重启次数超限 (%d/%ds)，暂停重启",
                        key, _AUTO_RESTART_MAX_ATTEMPTS, _AUTO_RESTART_COOLDOWN,
                    )
                continue

            logger.info("检测到实例 %s 宕机，尝试自动重启 (#%d)", key, state.restart_count + 1)
            try:
                await self._do_restart(state)
                state.restart_count += 1
                state.restart_timestamps.append(now)
            except Exception as e:
                logger.error("自动重启实例 %s 失败: %s", key, e)
                state.restart_count += 1
                state.restart_timestamps.append(now)

    async def _do_restart(self, state: InstanceState) -> None:
        """执行单个实例的重启流程"""
        if state.process and state.process.poll() is None:
            try:
                state.process.terminate()
                state.process.wait(timeout=5)
            except Exception:
                state.process.kill()

        rapid_mlx_bin = _find_rapid_mlx_bin()
        if not rapid_mlx_bin:
            raise RuntimeError("未找到 rapid-mlx 命令")

        cfg = state.config
        cmd = [rapid_mlx_bin, "serve", cfg.model, "--port", str(cfg.port)]
        if cfg.served_model_name:
            cmd.extend(["--served-model-name", cfg.served_model_name])
        if cfg.extra_args:
            cmd.extend(cfg.extra_args)

        if self._is_audio_model(cfg.model) and "--enable-audio" not in (cfg.extra_args or []):
            cmd.append("--enable-audio")

        logger.info("重启 Rapid-MLX: %s", " ".join(cmd))
        state.status = InstanceStatus.STARTING
        state.error_message = ""

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        state.process = proc
        state.pid = proc.pid
        state.started_at = time.time()

        healthy = await self._wait_for_healthy(state)
        if healthy:
            state.status = InstanceStatus.RUNNING
            logger.info("实例已恢复: %s (port=%d, pid=%d)", cfg.model, cfg.port, proc.pid)
        else:
            state.status = InstanceStatus.ERROR
            stderr_output = ""
            if proc.stderr:
                try:
                    stderr_output = proc.stderr.read(2048).decode(errors="replace")
                except Exception:
                    pass
            state.error_message = f"重启后启动超时（{_STARTUP_WAIT_SECS}s）" + (
                f": {stderr_output[:200]}" if stderr_output else ""
            )

    async def close(self) -> None:
        """关闭管理器：取消健康检查、关闭 HTTP 客户端"""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        await self._http_client.aclose()

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def list_instances(self) -> list[dict[str, Any]]:
        """列出所有实例信息"""
        return [state.to_info() for state in self._instances.values()]

    def get_model_url_map(self) -> dict[str, str]:
        """获取 model_name → base_url 映射（仅 RUNNING 的实例）"""
        mapping: dict[str, str] = {}
        for state in self._instances.values():
            if state.status == InstanceStatus.RUNNING:
                model_name = state.config.served_model_name or state.config.model
                mapping[model_name] = f"http://127.0.0.1:{state.config.port}"
                mapping[state.config.model] = f"http://127.0.0.1:{state.config.port}"
        return mapping

    def get_instance(self, model: str, port: int) -> InstanceState | None:
        key = self._instance_key(model, port)
        return self._instances.get(key)

    def _find_instance_by_port(self, port: int) -> InstanceState | None:
        for state in self._instances.values():
            if state.config.port == port:
                return state
        return None

    def get_running_count(self) -> int:
        return sum(1 for s in self._instances.values() if s.status == InstanceStatus.RUNNING)

    def record_request(self, model: str, latency_ms: float) -> None:
        """记录一次请求（由 Provider 在完成请求后调用）"""
        model_lower = model.lower()
        for state in self._instances.values():
            cfg_model = state.config.model.lower()
            served_name = (state.config.served_model_name or "").lower()
            if cfg_model == model_lower or served_name == model_lower \
                    or cfg_model in model_lower or model_lower in cfg_model:
                state.request_count += 1
                state.total_latency_ms += latency_ms
                state.last_request_at = time.time()
                break

    # ------------------------------------------------------------------
    # 模型目录（远程 + 本地缓存）
    # ------------------------------------------------------------------

    def list_available_models(self) -> list[dict[str, Any]]:
        """获取 rapid-mlx 官方支持的所有模型列表"""
        rapid_mlx_bin = _find_rapid_mlx_bin()
        if not rapid_mlx_bin:
            return []

        try:
            result = subprocess.run(
                [rapid_mlx_bin, "models"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return []
        except Exception as e:
            logger.warning("执行 rapid-mlx models 失败: %s", e)
            return []

        return self._parse_models_output(result.stdout)

    def list_cached_models(self) -> list[dict[str, Any]]:
        """获取本地已下载的模型"""
        rapid_mlx_bin = _find_rapid_mlx_bin()
        if not rapid_mlx_bin:
            return []

        try:
            result = subprocess.run(
                [rapid_mlx_bin, "ls"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return []
        except Exception as e:
            logger.warning("执行 rapid-mlx ls 失败: %s", e)
            return []

        return self._parse_cached_output(result.stdout)

    @staticmethod
    def _parse_models_output(output: str) -> list[dict[str, Any]]:
        """解析 `rapid-mlx models` 的表格输出"""
        models: list[dict[str, Any]] = []
        current_section = ""

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("─"):
                continue
            if "Available models" in stripped:
                current_section = "text"
                continue
            if "Audio models" in stripped:
                current_section = "audio"
                continue
            if stripped.startswith("Alias") or stripped.startswith("Tip:"):
                continue

            parts = stripped.split()
            if not parts:
                continue

            alias = parts[0]
            if current_section == "text":
                tools = parts[1] if len(parts) > 1 and parts[1] != "—" else ""
                reasoning = parts[2] if len(parts) > 2 and parts[2] != "—" else ""
                spec_decode = "✓" in stripped
                models.append({
                    "alias": alias,
                    "type": "text",
                    "tools": tools,
                    "reasoning": reasoning,
                    "spec_decode": spec_decode,
                })
            elif current_section == "audio":
                kind = ""
                family = ""
                hf_id = ""
                for i, p in enumerate(parts[1:], 1):
                    if p.startswith("[audio:"):
                        kind = p.strip("[]")
                        continue
                    if "/" in p:
                        hf_id = p
                        continue
                    if not family and p not in ("—",):
                        family = p
                models.append({
                    "alias": alias,
                    "type": "audio",
                    "kind": kind,
                    "family": family,
                    "hf_id": hf_id,
                })

        return models

    @staticmethod
    def _parse_cached_output(output: str) -> list[dict[str, Any]]:
        """解析 `rapid-mlx ls` 的表格输出"""
        models: list[dict[str, Any]] = []

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("─") or stripped.startswith("Cached") \
                    or stripped.startswith("Alias") or stripped.startswith("Total:") \
                    or stripped.startswith("Tip:"):
                continue

            parts = stripped.split()
            if len(parts) < 3:
                continue

            alias = parts[0]
            hf_repo = ""
            size = ""

            for i, p in enumerate(parts[1:], 1):
                if "/" in p:
                    hf_repo = p
                elif p in ("MiB", "GiB", "KiB") and i > 1:
                    size = f"{parts[i-1]} {p}"

            models.append({
                "alias": alias,
                "hf_repo": hf_repo,
                "size": size,
            })

        return models
