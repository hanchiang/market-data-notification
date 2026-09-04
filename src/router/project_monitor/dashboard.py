"""The local read surface over the project monitor's store.

One JSON route and two static files. Everything it serves comes from
`src/service/project_monitor/report.py` -- the same functions the CLI calls --
so the page and `report --json` cannot disagree about a figure (DR1).

Nothing here writes: the repository is opened `read_only=True`, which makes the
SERVER refuse a write on the connection rather than leaving it to review
discipline (DR12).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import psycopg
from fastapi import APIRouter, Request, Response
from starlette.responses import FileResponse, JSONResponse

from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor import report
from src.service.project_monitor.config import NETNET, get_project_monitor_database_url
from src.service.project_monitor.repository import ProjectMonitorRepository

logger = logging.getLogger('Project monitor dashboard')

router = APIRouter(prefix='/project-monitor')

STATIC_DIR = Path(__file__).resolve().parents[2] / 'static' / 'project_monitor'

LOOPBACK_HOSTS = frozenset({'127.0.0.1', '::1'})
# The names a local browser puts in `Host` for this server. Checked as well as
# the client address because the two catch different attacks: the address stops
# a remote client, and this stops DNS rebinding, where a page on an attacker's
# domain resolves that domain to 127.0.0.1 and the operator's OWN browser makes
# the request -- loopback client address and all -- letting the attacker's
# origin read the body back.
LOOPBACK_HOST_NAMES = frozenset({'127.0.0.1', 'localhost', '::1'})


def _host_name(header: Optional[str]) -> Optional[str]:
    """The host out of a `Host` header, port and IPv6 brackets removed.

    Returns None for anything it cannot parse, so the caller fails closed: an
    unrecognised Host is refused rather than waved through.
    """
    if not header:
        return None
    if header.startswith('['):
        return header[1:header.index(']')] if ']' in header else None
    return header.rsplit(':', 1)[0] if ':' in header else header


async def loopback_only(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Refuse anything that did not come from this machine (DR10).

    Redundant with the loopback bind, and deliberately so: the bind is what
    makes the port unreachable, this is what still holds if the router is ever
    mounted on an app that binds elsewhere. A missing `client` or an
    unparseable `Host` is refused rather than trusted -- an unknown origin is
    not a local one.
    """
    client = request.client
    if client is None or client.host not in LOOPBACK_HOSTS:
        return JSONResponse(status_code=403, content={'error': 'local requests only'})
    if _host_name(request.headers.get('host')) not in LOOPBACK_HOST_NAMES:
        return JSONResponse(status_code=403, content={'error': 'local requests only'})
    return await call_next(request)


@router.get('/')
async def page() -> FileResponse:
    # `no-store` so an edited page is what the browser shows: this is a local
    # dev surface whose file changes under a running server.
    return FileResponse(
        STATIC_DIR / 'index.html',
        media_type='text/html',
        headers={'Cache-Control': 'no-store'},
    )


@router.get('/static/chart.umd.js')
async def chart_library() -> FileResponse:
    return FileResponse(STATIC_DIR / 'chart.umd.js', media_type='text/javascript')


@router.get('/netnet/report')
async def netnet_report(test_mode: int = 0) -> Response:
    try:
        payload = _load_payload(bool(test_mode))
    except psycopg.Error as exc:
        # The class name only. A connection string in a log line or a response
        # body is a credential leak, and psycopg puts the DSN in some messages.
        logger.error('project monitor store unavailable: %s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'error': type(exc).__name__})
    # Serialised with report's own encoder rather than FastAPI's: `jsonable_encoder`
    # turns a Decimal into a float, which would make the route's backing figure
    # differ from the CLI's in the last places and break AC-D1 on the first row.
    return Response(
        content=json.dumps(payload, sort_keys=True),
        media_type='application/json',
    )


def _load_payload(test_mode: bool) -> Any:
    database_url = get_project_monitor_database_url(RuntimeMode.from_test_mode(test_mode))
    # A connection per request. On loopback that costs a few milliseconds and
    # keeps this entrypoint free of pool lifecycle for one operator's page.
    with ProjectMonitorRepository(database_url, read_only=True) as repository:
        rows = report.load_epoch_rows(repository, NETNET)
    return report.report_payload(rows, NETNET, now=datetime.now(timezone.utc))
