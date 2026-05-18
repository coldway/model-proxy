# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import hmac
import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.api.routes import init_routes, router
from src.api.ui import UI_HTML
from src.config.capability_tester import CapabilityCache, CapabilityTester
from src.config.catalog import CatalogManager
from src.config.manager import ConfigManager
from src.providers.cloudflare import CloudflareProvider
from src.providers.cursor import CursorProvider
from src.providers.github import GitHubProvider
from src.providers.google import GoogleProvider
from src.providers.groq import GroqProvider
from src.providers.huggingface import HuggingFaceProvider
from src.providers.openai_compat import create_openai_provider
from src.scheduler.dispatcher import Dispatcher
from src.scheduler.history import RequestHistory
from src.scheduler.rate_limiter import RateLimiter

from src.api.log_buffer import install as install_log_buffer

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FMT = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_LOG_FMT)

_log_handlers: list[logging.Handler] = [_console_handler]
try:
    _LOG_DIR.mkdir(exist_ok=True)
    _file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "model-proxy.log",
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(_LOG_FMT)
    _log_handlers.append(_file_handler)
except OSError:
    pass

logging.basicConfig(
    level=logging.INFO,
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

install_log_buffer(max_records=2000)


def create_app() -> FastAPI:
    catalog = CatalogManager()
    config_manager = ConfigManager(catalog=catalog)
    settings = config_manager.settings

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    rate_limiter = RateLimiter()
    history = RequestHistory(persist=True)
    capability_cache = CapabilityCache()
    dispatcher = Dispatcher(
        rate_limiter, capability_cache=capability_cache, history=history,
        route_cache_ttl=settings.route_cache_ttl,
        breaker_threshold=settings.breaker_threshold,
        breaker_cooldown=settings.breaker_cooldown,
    )

    # 注册各厂商 Provider（根据 API Key 是否存在决定是否注册）
    provider_factories = {
        "google": lambda key: GoogleProvider(key),
        "groq": lambda key: GroqProvider(key),
        "github": lambda key: GitHubProvider(key),
        "cloudflare": lambda key: CloudflareProvider(key),
        "huggingface": lambda key: HuggingFaceProvider(key),
        "cerebras": lambda key: create_openai_provider("cerebras", key),
        "sambanova": lambda key: create_openai_provider("sambanova", key),
        "openrouter": lambda key: create_openai_provider("openrouter", key),
        "mistral": lambda key: create_openai_provider("mistral", key),
    }

    for name, factory in provider_factories.items():
        api_key = config_manager.get_api_key(name)
        if api_key:
            dispatcher.register_provider(name, factory(api_key))
            logger.info(f"已注册 {name} 厂商")

    if catalog.is_provider_enabled("cursor"):
        dispatcher.register_provider("cursor", CursorProvider())
        logger.info("已注册 Cursor Agent CLI 厂商")

    capability_tester = CapabilityTester(capability_cache)

    init_routes(
        config_manager, dispatcher, rate_limiter,
        history, catalog, provider_factories, capability_tester,
    )

    _PERIODIC_FLUSH_INTERVAL = 60

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def _periodic_flush():
            while True:
                await asyncio.sleep(_PERIODIC_FLUSH_INTERVAL)
                try:
                    rate_limiter.flush()
                    history.flush()
                    catalog.flush()
                except Exception as exc:
                    logger.warning("周期性刷盘异常: %s", exc)

        flush_task = asyncio.create_task(_periodic_flush())

        if capability_cache:
            all_caps = capability_cache.get_all()
            if all_caps:
                logger.info("已加载 %d 个模型的能力缓存（预热）", len(all_caps))

        yield
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        logger.info("正在优雅关闭…")
        rate_limiter.flush()
        history.flush()
        catalog.flush()
        await dispatcher.close_providers()
        logger.info("所有资源已释放")

    admin_token = settings.admin_token.strip()
    proxy_api_key = settings.proxy_api_key.strip()
    OPEN_PATHS = frozenset({"/", "/ui", "/health", "/health/ready"})

    app = FastAPI(
        title="Model Proxy",
        description="免费大模型推理代理服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    if admin_token or proxy_api_key:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            path = request.url.path
            if path in OPEN_PATHS:
                return await call_next(request)

            auth = request.headers.get("Authorization", "")
            bearer_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

            if path.startswith("/v1/"):
                if proxy_api_key and not hmac.compare_digest(bearer_token, proxy_api_key):
                    return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的 API Key"})
                return await call_next(request)

            if admin_token and not hmac.compare_digest(bearer_token, admin_token):
                return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的管理令牌"})
            return await call_next(request)

        if proxy_api_key:
            logger.info("/v1 推理接口认证已启用（需要 Bearer Token）")
        if admin_token:
            logger.info("管理面板认证已启用（/api/* 路由需要 Bearer Token）")

    app.include_router(router)

    @app.get("/ui", response_class=HTMLResponse)
    async def ui_panel():
        return UI_HTML

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return '<meta http-equiv="refresh" content="0;url=/ui">'

    logger.info(f"Model Proxy 启动于 http://{settings.host}:{settings.port}")
    logger.info(f"UI 面板: http://{settings.host}:{settings.port}/ui")

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
        print("配置校验通过 ✓")
        print(f"  监听: {_config.settings.host}:{_config.settings.port}")
        providers = {n for n, p in _config.config.providers.items() if p.enabled}
        models = _config.get_enabled_models()
        print(f"  已启用厂商: {', '.join(sorted(providers)) or '无'}")
        print(f"  已启用模型: {len(models)} 个")
        if _config.settings.proxy_api_key:
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
