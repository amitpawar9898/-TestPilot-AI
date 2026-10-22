from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger("testpilot.api")


def create_app(db: Any = None) -> FastAPI:
    # Import the domain stack only when an app is created. Importing api.main for tooling/tests
    # must not initialize the production database or import the analyzer/runner stack.
    from api.runtime import Runtime
    from api.routers.auth import router as auth_router
    from api.routers.runs import router as runs_router
    from api.routers.settings_learning import router as settings_router
    from config.settings import DB_PATH
    from database.db import Database

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.runtime.shutdown()

    app = FastAPI(title="TestPilot AI API", version="1.0.0", lifespan=lifespan)
    app.state.db = db or Database(DB_PATH)
    app.state.runtime = Runtime()
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        code = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
                413: "too_large", 422: "validation_error"}.get(exc.status_code, "http_error")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content={"detail": detail, "code": code}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Do not expose submitted values (which may contain source or credentials) in error output.
        return JSONResponse(status_code=422, content={"detail": "Request did not match the API schema.", "code": "validation_error"})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.exception("Unhandled API error: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error.", "code": "internal_error"})

    app.include_router(auth_router)
    app.include_router(runs_router)
    app.include_router(settings_router)

    web_root = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    index_file = web_root / "index.html"
    if index_file.is_file():
        @app.get("/", include_in_schema=False)
        async def frontend_index():
            return FileResponse(index_file)

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def frontend_files(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "Not found.")
            requested = (web_root / path).resolve()
            try:
                requested.relative_to(web_root.resolve())
            except ValueError:
                raise HTTPException(404, "Not found.") from None
            return FileResponse(requested if requested.is_file() else index_file)

    return app


class LazyASGI:
    """Uvicorn import target that creates FastAPI and opens SQLite on first ASGI scope."""

    def __init__(self):
        self._app: FastAPI | None = None
        self._lock = threading.Lock()

    def _get_app(self) -> FastAPI:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = create_app()
        return self._app

    async def __call__(self, scope, receive, send):
        await self._get_app()(scope, receive, send)


app = LazyASGI()
