import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router
from mcp_server.server import mcp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def create_app() -> FastAPI:
    try:
        # Stateless Streamable HTTP: each request is independent, so restarting
        # the web server does not leave clients with an invalid MCP session id.
        # OpenCode/Cursor can reconnect without "Session not found" errors.
        mcp_app = mcp.http_app(path="/", stateless_http=True)
    except Exception:
        mcp_app = None

    if mcp_app is not None:
        app = FastAPI(title="Kanban Board", lifespan=mcp_app.lifespan)
    else:
        app = FastAPI(title="Kanban Board")

    app.include_router(api_router)

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
