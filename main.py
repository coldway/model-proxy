# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
from src.providers.openai_compat import create_openai_provider
from src.scheduler.dispatcher import Dispatcher
from src.scheduler.history import RequestHistory
from src.scheduler.rate_limiter import RateLimiter

from src.api.log_buffer import install as install_log_buffer

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FMT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"

_file_handler = TimedRotatingFileHandler(
    _LOG_DIR / "app.log",
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FMT))

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FMT,
    handlers=[logging.StreamHandler(sys.stdout), _file_handler],
)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        logger.info("正在优雅关闭…")
        rate_limiter.flush()
        history.flush()
        catalog.flush()
        await dispatcher.close_providers()
        logger.info("所有资源已释放")

    admin_token = settings.admin_token.strip()
    OPEN_PATHS = frozenset({"/", "/ui"})

    app = FastAPI(
        title="Model Proxy",
        description="免费大模型推理代理服务",
        version="0.3.0",
        lifespan=lifespan,
    )

    if admin_token:
        @app.middleware("http")
        async def admin_auth_middleware(request: Request, call_next):
            path = request.url.path
            if path in OPEN_PATHS or path.startswith("/v1/"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {admin_token}":
                return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的管理令牌"})
            return await call_next(request)
        logger.info("管理面板认证已启用（/api/* 路由需要 Bearer Token）")

    app.include_router(router)

    @app.get("/ui", response_class=HTMLResponse)
    async def ui_panel():
        return get_ui_html()

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
    _catalog = CatalogManager()
    _config = ConfigManager(catalog=_catalog)
    uvicorn.run(
        "main:app",
        host=_config.settings.host,
        port=_config.settings.port,
        reload=True,
        workers=1,
    )
