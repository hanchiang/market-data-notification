"""Serve the project monitor's read-only page on the operator's machine.

A second, lighter entrypoint rather than a route on `src/server.py`: that app's
startup calls `init_telegram_bots()`, which needs six bot tokens this path has
no business holding, and it binds `0.0.0.0:8080` behind an auth middleware that
skips itself outside `prod`. The router is written to be includable there later,
behind that auth, if the deferred online dashboard ever lands.

Usage:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/dashboard.py [--port 8765]
  then open http://127.0.0.1:8765/project-monitor/   (add ?test_mode=1 for the
  _test database)
"""
import argparse

import uvicorn
from fastapi import FastAPI

from src.router.project_monitor import dashboard

# Loopback only, and no `--host` flag to override it (DR10): a typo cannot open
# this to the network, and the port is the only thing worth moving. 8765 avoids
# the compose file's 8080, 55432, 4444, 5900 and 7900.
HOST = '127.0.0.1'
DEFAULT_PORT = 8765


def build_app() -> FastAPI:
    # No `/docs`, `/redoc` or `/openapi.json`. FastAPI mounts them by default,
    # and both pages load Swagger UI or ReDoc from cdn.jsdelivr.net -- ReDoc
    # also fonts from Google. The requirement's Local-first constraint is that
    # opening this server sends nothing anywhere, and it is about the server,
    # not about index.html: the operator opening `/docs` on a FastAPI app is a
    # normal thing to do. There is no API to document here anyway -- one JSON
    # route for one page.
    app = FastAPI(
        title='NETNET treasury dashboard',
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.middleware('http')(dashboard.loopback_only)
    app.include_router(dashboard.router)
    return app


app = build_app()


def main(port: int = DEFAULT_PORT) -> int:
    uvicorn.run(app, host=HOST, port=port)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    raise SystemExit(main(port=args.port))
