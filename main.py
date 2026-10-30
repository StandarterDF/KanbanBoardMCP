import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from db.database import init_db
from utils.git_init import init_git_repo


def bootstrap():
    init_git_repo(BASE_DIR)
    init_db()


def run_mcp():
    bootstrap()
    from mcp_server.server import mcp

    mcp.run(transport="stdio")


def run_web(host: str = "0.0.0.0", port: int = 8000):
    bootstrap()
    import uvicorn
    from api.app import create_app

    app = create_app()
    uvicorn.run(app, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description="Kanban Board with MCP")
    parser.add_argument("--mcp", action="store_true", help="Run as MCP server (stdio)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.mcp:
        run_mcp()
    else:
        run_web(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
