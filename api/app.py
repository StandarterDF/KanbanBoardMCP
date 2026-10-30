import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router
from mcp_server.server import mcp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.lifespan():
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Kanban Board", lifespan=lifespan)
    app.include_router(api_router)

    # Mount MCP streamable-http endpoint at /mcp
    try:
        mcp_app = mcp.http_app(path="/")
        app.mount("/mcp", mcp_app)
    except Exception:
        pass

    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
