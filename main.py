# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _load_env(path: str = ".env") -> None:
    """加载 .env 文件中的环境变量（不覆盖已有值）"""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from src.api.routes import init_routes, router
from src.api.ui import get_ui_html
from src.config.capability_tester import CapabilityCache, CapabilityTester
from src.config.catalog import CatalogManager
from src.config.manager import ConfigManager
from src.providers.cloudflare import CloudflareProvider
from src.providers.cursor import CursorProvider
from src.providers.github import GitHubProvider
from src.providers.google import GoogleProvider
from src.providers.groq import GroqProvider
from src.providers.huggingface import HuggingFaceProvider
from src.providers.ollama import OllamaProvider
from src.providers.rapid_mlx import RapidMLXProvider
from src.providers.openai_compat import create_openai_provider
from src.scheduler.dispatcher import Dispatcher
from src.scheduler.history import RequestHistory
from src.scheduler.rate_limiter import RateLimiter
from src.providers.utils import configure_timeouts
from src.services.rapid_mlx_manager import RapidMLXManager

from src.api.log_buffer import install as install_log_buffer, preload_from_file as preload_logs

_LOG_DIR = Path("logs")
_LOG_FMT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"

_log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
try:
    _LOG_DIR.mkdir(exist_ok=True)
    _file_handler = TimedRotatingFileHandler(
        _LOG_DIR / "app.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(_LOG_FMT))
    _log_handlers.append(_file_handler)
except OSError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FMT,
    handlers=_log_handlers,
)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

install_log_buffer(max_records=2000)
try:
    _preloaded = preload_logs(_LOG_DIR / "app.log", max_lines=500)
    if _preloaded:
        logger.info("从日志文件预加载 %d 条历史记录到 UI 缓冲", _preloaded)
except OSError:
    pass


def create_app() -> FastAPI:
    catalog = CatalogManager()
    config_manager = ConfigManager(catalog=catalog)
    settings = config_manager.settings

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    configure_timeouts(
        read_timeout=float(settings.http_read_timeout),
        connect_timeout=float(settings.http_connect_timeout),
    )
    logger.info(
        "HTTP 超时配置: connect=%ds, read=%ds",
        settings.http_connect_timeout,
        settings.http_read_timeout,
    )

    rate_limiter = RateLimiter()
    history = RequestHistory(
        persist=True,
        max_memory_records=settings.request_history_max_records,
    )
    capability_cache = CapabilityCache()
    dispatcher = Dispatcher(
        rate_limiter, capability_cache=capability_cache, history=history,
        catalog=catalog,
        route_cache_ttl=settings.route_cache_ttl,
        breaker_threshold=settings.breaker_threshold,
        breaker_cooldown=settings.breaker_cooldown,
        session_bind_ttl=settings.session_bind_ttl,
    )

    # 注册各厂商 Provider（根据 API Key 是否存在决定是否注册）
    # 独立适配器（有专门 Python 实现的厂商）
    provider_factories: dict[str, Any] = {
        "google": lambda key: GoogleProvider(key),
        "groq": lambda key: GroqProvider(key),
        "github": lambda key: GitHubProvider(key),
        "cloudflare": lambda key: CloudflareProvider(key),
        "huggingface": lambda key: HuggingFaceProvider(key),
        "ollama": lambda key: OllamaProvider(key),
        "rapid_mlx": lambda key: RapidMLXProvider(key),
    }

    # 自动发现 catalog 中 type=openai_compat 的厂商，无需手动逐个注册
    for prov_id, prov_cfg in catalog.get_providers_sorted():
        if prov_cfg.get("type") == "openai_compat" and prov_id not in provider_factories:
            base_url = prov_cfg.get("base_url")
            if base_url:
                _pid = prov_id  # 闭包变量捕获
                provider_factories[_pid] = lambda key, pid=_pid: create_openai_provider(pid, key)
            else:
                logger.warning("厂商 %s 声明 type=openai_compat 但缺少 base_url，跳过", prov_id)

    _NO_KEY_PROVIDERS = {"ollama", "rapid_mlx"}

    for name, factory in provider_factories.items():
        api_key = config_manager.get_api_key(name)
        if name in _NO_KEY_PROVIDERS:
            if catalog.get_provider(name) is None or catalog.is_provider_enabled(name):
                dispatcher.register_provider(name, factory(api_key))
                logger.info("已注册 %s 厂商（本地，无需 API Key）", name)
        elif api_key:
            dispatcher.register_provider(name, factory(api_key))
            logger.info("已注册 %s 厂商", name)

    if catalog.is_provider_enabled("cursor"):
        dispatcher.register_provider("cursor", CursorProvider())
        logger.info("已注册 Cursor Agent CLI 厂商")

    capability_tester = CapabilityTester(capability_cache)

    # 初始化 Rapid-MLX 多实例管理器
    rapid_mlx_mgr = RapidMLXManager()

    init_routes(
        config_manager, dispatcher, rate_limiter,
        history, catalog, provider_factories, capability_tester,
    )

    # 注入 manager 到 API 路由
    from src.api.routes_pkg.rapid_mlx import set_manager as set_rmlx_route_manager
    from src.api.routes_pkg.audio import set_audio_manager
    set_rmlx_route_manager(rapid_mlx_mgr)
    set_audio_manager(rapid_mlx_mgr)

    _PERIODIC_FLUSH_INTERVAL = 60

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        def _sync_flush_all():
            rate_limiter.flush()
            history.flush()
            catalog.flush()
            dispatcher._payload_tracker.flush()
            from src.api.routes import _deps
            if _deps.cost_tracker:
                _deps.cost_tracker.flush()

        async def _periodic_flush():
            while True:
                await asyncio.sleep(_PERIODIC_FLUSH_INTERVAL)
                try:
                    await asyncio.to_thread(_sync_flush_all)
                except Exception as exc:
                    logger.warning("周期性刷盘异常: %s", exc)

        async def _periodic_cache_purge():
            while True:
                await asyncio.sleep(dispatcher._route_cache_ttl)
                try:
                    await dispatcher.purge_expired_cache()
                except Exception as exc:
                    logger.warning("路由缓存清理异常: %s", exc)

        flush_task = asyncio.create_task(_periodic_flush())
        cache_purge_task = asyncio.create_task(_periodic_cache_purge())

        if capability_cache:
            all_caps = capability_cache.get_all()
            if all_caps:
                logger.info("已加载 %d 个模型的能力缓存（预热）", len(all_caps))

        ollama_prov = dispatcher.get_provider("ollama")
        if isinstance(ollama_prov, OllamaProvider):
            ollama_prov.set_breaker(dispatcher._breaker)

            async def _ollama_background_discover():
                try:
                    added = await ollama_prov.discover_and_register(catalog)
                    if added:
                        logger.info("Ollama 启动发现 %d 个本地模型", len(added))
                except Exception as exc:
                    logger.warning("Ollama 启动模型发现失败（服务可能未运行）: %s", exc)

            asyncio.create_task(_ollama_background_discover())

        rapid_mlx_prov = dispatcher.get_provider("rapid_mlx")
        if isinstance(rapid_mlx_prov, RapidMLXProvider):
            rapid_mlx_prov.set_breaker(dispatcher._breaker)
            rapid_mlx_prov.set_manager(rapid_mlx_mgr)

            async def _rapid_mlx_background_init():
                try:
                    # 发现外部已运行的实例
                    discovered = await rapid_mlx_mgr.discover_external()
                    if discovered:
                        logger.info("发现 %d 个外部 Rapid-MLX 实例", len(discovered))

                    # 启动所有配置为 auto_start 的实例
                    started = await rapid_mlx_mgr.start_all_enabled()
                    if started:
                        logger.info("自动启动 %d 个 Rapid-MLX 实例", len(started))

                    # 模型发现 → 注册到 catalog
                    added = await rapid_mlx_prov.discover_and_register(catalog)
                    if added:
                        logger.info("Rapid-MLX 启动发现 %d 个本地模型", len(added))
                except Exception as exc:
                    logger.warning("Rapid-MLX 初始化失败: %s", exc)

            asyncio.create_task(_rapid_mlx_background_init())

            # 启动周期性健康检查
            await rapid_mlx_mgr.start_health_loop()

        yield
        flush_task.cancel()
        cache_purge_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        try:
            await cache_purge_task
        except asyncio.CancelledError:
            pass
        logger.info("正在优雅关闭…")
        await rapid_mlx_mgr.close()
        rate_limiter.flush()
        history.flush()
        catalog.flush()
        dispatcher._payload_tracker.close()
        dispatcher.flush_session_bindings()
        try:
            from src.scheduler.memory_pkg._impl import _memory_manager
            if _memory_manager is not None:
                _memory_manager.close()
        except Exception as exc:
            logger.warning("关闭 MemoryManager 异常: %s", exc)
        await dispatcher.close_providers()
        logger.info("所有资源已释放")

    admin_token = settings.admin_token.strip()
    api_token = settings.api_token.strip()
    api_keys_cfg = settings.api_keys
    OPEN_PATHS = frozenset({"/", "/mp/ui", "/mp/health", "/mp/ready", "/docs", "/redoc", "/openapi.json", "/mp/favicon.ico"})

    # 构建有效 Key 集合: {key_value: ApiKeyConfig}
    from src.models.schemas import ApiKeyConfig
    _valid_api_keys: dict[str, ApiKeyConfig] = {}
    for kc in api_keys_cfg:
        if kc.enabled and kc.key.strip():
            _valid_api_keys[kc.key.strip()] = kc
    if api_token:
        _valid_api_keys.setdefault(api_token, ApiKeyConfig(key=api_token, name="default"))

    _auth_enabled = bool(admin_token or _valid_api_keys)

    app = FastAPI(
        title="Model Proxy",
        description="免费大模型推理代理服务。支持 OpenAI 和 Anthropic 两种协议接入，13+ 家厂商自动调度。",
        version="0.3.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "chat", "description": "OpenAI 兼容的聊天补全接口（/v1/chat/completions）"},
            {"name": "anthropic", "description": "Anthropic Messages API 兼容层（/v1/messages）"},
            {"name": "models", "description": "模型列表、厂商信息与用量统计"},
            {"name": "images", "description": "图像生成接口"},
            {"name": "videos", "description": "视频生成接口（异步任务）"},
            {"name": "config", "description": "配置管理（API Key、厂商启停、模型优先级）"},
            {"name": "history", "description": "请求历史记录与统计"},
            {"name": "system", "description": "健康探针与系统状态"},
        ],
    )

    _cors_origins = settings.cors_origins.strip()
    if _cors_origins == "*":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        logger.info("CORS 已启用: 允许任意来源 (*)")
    elif _cors_origins:
        _allowed = [o.strip() for o in _cors_origins.split(",") if o.strip()]
        if _allowed:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=_allowed,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            logger.info("CORS 已启用: %s", _allowed)

    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    if _auth_enabled:
        from collections import deque
        import time as _time

        _consumer_requests: dict[str, deque] = {}
        _global_rpm = settings.per_consumer_rpm
        _consumer_gc_last = _time.time()
        _CONSUMER_GC_INTERVAL = 300

        def _extract_bearer(auth_header: str) -> str:
            if auth_header.lower().startswith("bearer "):
                return auth_header[7:].strip()
            return ""

        def _token_match(given: str, expected: str) -> bool:
            if not given or not expected:
                return False
            return hmac.compare_digest(given.encode(), expected.encode())

        def _find_api_key(bearer: str) -> ApiKeyConfig | None:
            """在多 Key 集合中查找匹配的 Key 配置"""
            if not bearer:
                return None
            for key_val, cfg in _valid_api_keys.items():
                if hmac.compare_digest(bearer.encode(), key_val.encode()):
                    return cfg
            return None

        def _check_consumer_rate(bearer: str, key_cfg: ApiKeyConfig | None) -> bool:
            """Per-consumer RPM 限流检查（滑动窗口），支持 Key 级别独立限额"""
            nonlocal _consumer_gc_last
            # admin_token 不限流
            if admin_token and _token_match(bearer, admin_token):
                return True
            # 确定此 Key 的 RPM 限制
            rpm_limit = 0
            if key_cfg and key_cfg.rpm > 0:
                rpm_limit = key_cfg.rpm
            elif _global_rpm > 0:
                rpm_limit = _global_rpm
            if rpm_limit <= 0:
                return True

            now = _time.time()
            if now - _consumer_gc_last > _CONSUMER_GC_INTERVAL:
                _consumer_gc_last = now
                stale = [k for k, dq in _consumer_requests.items() if not dq or dq[-1] < now - 120]
                for k in stale:
                    del _consumer_requests[k]

            if bearer not in _consumer_requests:
                _consumer_requests[bearer] = deque()
            dq = _consumer_requests[bearer]
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) >= rpm_limit:
                return False
            dq.append(now)
            return True

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            path = request.url.path
            if path in OPEN_PATHS or path.startswith("/mp/static/"):
                return await call_next(request)

            bearer = _extract_bearer(request.headers.get("Authorization", ""))

            if path.startswith("/v1/"):
                if _valid_api_keys:
                    key_cfg = _find_api_key(bearer)
                    if key_cfg is None:
                        return JSONResponse(
                            status_code=401,
                            content={"error": {"message": "Invalid API key", "type": "auth_error"}},
                        )
                    if not _check_consumer_rate(bearer, key_cfg):
                        rpm = key_cfg.rpm if key_cfg.rpm > 0 else _global_rpm
                        return JSONResponse(
                            status_code=429,
                            content={"error": {"message": f"Rate limit: {rpm} requests/min exceeded", "type": "rate_limit_error"}},
                        )
                return await call_next(request)

            # 管理接口
            expected = admin_token or (api_token if api_token else "")
            if expected and not _token_match(bearer, expected):
                key_cfg = _find_api_key(bearer)
                if key_cfg is None:
                    return JSONResponse(status_code=401, content={"detail": "未授权：需要管理令牌"})
            return await call_next(request)

        _auth_parts = []
        if admin_token:
            _auth_parts.append("管理面板认证")
        if _valid_api_keys:
            _auth_parts.append(f"API Keys: {len(_valid_api_keys)} 个")
        if _global_rpm > 0:
            _auth_parts.append(f"全局 RPM={_global_rpm}")
        logger.info("认证已启用: %s", " + ".join(_auth_parts))
        for kv, cfg in _valid_api_keys.items():
            label = cfg.name or kv[:8] + "..."
            rpm_info = f"rpm={cfg.rpm}" if cfg.rpm > 0 else "rpm=全局"
            logger.info("  Key [%s]: %s", label, rpm_info)

    app.include_router(router)

    _static_dir = Path(__file__).parent / "src" / "api" / "static"
    app.mount("/mp/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/mp/ui", response_class=HTMLResponse)
    async def ui_panel():
        return get_ui_html()

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return '<meta http-equiv="refresh" content="0;url=/mp/ui">'

    @app.get("/mp/favicon.ico", include_in_schema=False)
    async def favicon():
        from fastapi.responses import Response
        # 1x1 transparent PNG
        PIXEL = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        return Response(content=PIXEL, media_type="image/png")

    logger.info("Model Proxy 启动于 http://%s:%s", settings.host, settings.port)
    logger.info("UI 面板: http://%s:%s/mp/ui", settings.host, settings.port)
    logger.info("Swagger API 文档: http://%s:%s/docs", settings.host, settings.port)
    logger.info("ReDoc API 文档: http://%s:%s/redoc", settings.host, settings.port)

    return app


# 单 Worker 进程设计：
# 所有状态（dispatcher、rate_limiter、history 等）以进程内单例持有，
# 不支持 uvicorn --workers N 多进程模式（会导致状态不一致）。
# 如需水平扩展，应改用外部存储（Redis 等）管理共享状态。
app = create_app()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Model Proxy")
    parser.add_argument("--validate", action="store_true", help="校验配置后退出（dry-run 模式）")
    args = parser.parse_args()

    _catalog = CatalogManager()
    _config = ConfigManager(catalog=_catalog)

    if args.validate:
        print("配置校验通过")
        print(f"  监听: {_config.settings.host}:{_config.settings.port}")
        providers = {n for n, p in _config.config.providers.items() if p.enabled}
        models = _config.get_enabled_models()
        print(f"  已启用厂商: {', '.join(sorted(providers)) or '无'}")
        print(f"  已启用模型: {len(models)} 个")
        if _config.settings.api_token:
            print("  /v1 认证: 已启用")
        if _config.settings.admin_token:
            print("  管理面板认证: 已启用")
        sys.exit(0)

    uvicorn.run(
        "main:app",
        host=_config.settings.host,
        port=_config.settings.port,
        reload=True,
        workers=1,
    )
