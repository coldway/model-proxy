# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.api.routes import init_routes, router
from src.api.ui import UI_HTML
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    catalog = CatalogManager()
    config_manager = ConfigManager(catalog=catalog)
    settings = config_manager.settings

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    rate_limiter = RateLimiter()
    history = RequestHistory(persist=True)
    dispatcher = Dispatcher(rate_limiter)

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

    init_routes(config_manager, dispatcher, rate_limiter, history, catalog, provider_factories)

    app = FastAPI(
        title="Model Proxy",
        description="免费大模型推理代理服务",
        version="0.1.0",
    )
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


app = create_app()

if __name__ == "__main__":
    _catalog = CatalogManager()
    _config = ConfigManager(catalog=_catalog)
    uvicorn.run(
        "main:app",
        host=_config.settings.host,
        port=_config.settings.port,
        reload=True,
    )
