"""FastAPI application factory and process wiring.

``uv run uvicorn lkap_api.main:app`` serves the module-level :data:`app`.
Tests build isolated instances with :func:`create_app` after setting env vars.

Every error leaves the process in the CONTRACTS §7 envelope
``{"error": {"code", "message", "details"}}``; the 422 handler strips the
offending ``input`` value so a malformed credential body can never echo a
secret back to the caller or into a log line.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from lkap_contracts.api_models import ErrorBody, ErrorResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from lkap_api import __version__
from lkap_api.db.session import Database
from lkap_api.errors import ApiError
from lkap_api.logging import configure_logging, get_logger
from lkap_api.packs import router as packs_router
from lkap_api.routers import (
    agents,
    connect,
    credentials,
    health,
    internal,
    providers,
    sessions,
    tools,
)
from lkap_api.routers.credentials import seed_bootstrap_credentials
from lkap_api.sessions_sweep import sweep_loop
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

log = get_logger(__name__)

DESCRIPTION = """
Control plane for the LiveKit Agent Platform: providers, credentials, agents,
tools, knowledge bases, sessions and the browser `connect` endpoint.

* `/v1/*` — admin surface (`X-Admin-Token`) plus the public `connect` and `health` routes.
* `/internal/v1/*` — worker surface (`X-Service-Token`); the only place decrypted
  credentials leave the api.
"""


def _error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _sanitised_validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Drop the echoed ``input`` and ``ctx`` values from validation errors.

    A 422 on ``POST /v1/credentials`` would otherwise return the rejected secret
    verbatim. Only the location, the error type and the message survive.
    """
    return [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "")),
            "msg": str(error.get("msg", "")),
        }
        for error in exc.errors()
    ]


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            422,
            "unprocessable_entity",
            "request body failed validation",
            {"errors": _sanitised_validation_details(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
        return _error_response(500, "internal_error", "internal server error")


def _include_routers(app: FastAPI) -> None:
    app.include_router(providers.router)
    app.include_router(packs_router)
    app.include_router(credentials.router)
    app.include_router(tools.router)
    app.include_router(agents.router)
    app.include_router(connect.router)
    app.include_router(sessions.router)
    app.include_router(internal.router)
    app.include_router(health.router)
    _include_knowledge_router(app)


def _include_knowledge_router(app: FastAPI) -> None:
    """Include W2's knowledge router when it exists, without editing this file later."""
    try:
        knowledge = importlib.import_module("lkap_api.routers.knowledge")
    except ModuleNotFoundError as exc:
        if exc.name not in {"lkap_api.routers.knowledge", "knowledge"}:
            raise
        log.debug("knowledge_router_absent")
        return
    app.include_router(knowledge.router)


async def _startup(app: FastAPI, settings: Settings) -> None:
    await asyncio.to_thread(Path(settings.data_dir).mkdir, parents=True, exist_ok=True)
    app.state.db = Database(settings.resolved_database_url)
    if settings.bootstrap_credentials_json:
        try:
            async with app.state.db.session() as session:
                created = await seed_bootstrap_credentials(
                    session, Vault(settings.master_key), settings.bootstrap_credentials_json
                )
            log.info("bootstrap_credentials_seeded", created=created)
        except Exception as exc:  # noqa: BLE001 - never block startup on a dev convenience
            log.warning("bootstrap_credentials_failed", error_type=type(exc).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Args:
        settings: Explicit settings; by default the process-wide cached instance.

    Returns:
        The application, with routers, CORS, error handlers and lifespan wired.
    """
    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, json_output=resolved.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _startup(app, resolved)
        sweep_task: asyncio.Task[None] = asyncio.create_task(sweep_loop(app.state.db, resolved))
        app.state.sweep_task = sweep_task
        log.info("api_started", version=__version__, agent_name=resolved.agent_name)
        try:
            yield
        finally:
            sweep_task.cancel()
            with suppress(asyncio.CancelledError):
                await sweep_task
            database: Database | None = getattr(app.state, "db", None)
            if database is not None:
                await database.dispose()
            log.info("api_stopped")

    app = FastAPI(
        title="LKAP api",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_exception_handlers(app)
    _include_routers(app)
    return app


app = create_app()
