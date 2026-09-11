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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

import psycopg
from fastapi import APIRouter, Request, Response
from starlette.responses import FileResponse, JSONResponse

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import report as onchain_report
from src.service.onchain.builder import JOB_BUILD
from src.service.onchain.config import get_onchain_database_url
from src.service.onchain.repository import OnchainRepository
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


# -- the dossier's read surfaces (design D6) ---------------------------------
#
# On the monitor's router rather than a second app: both read the same Postgres
# on the same machine for the same operator, and a second uvicorn on a second
# port would double the loopback middleware, the process management and the
# runbook for no separation that matters. The dependency direction is the one
# the design fixes -- the dashboard router imports the onchain package, and the
# onchain package imports nothing from the router.


@router.get('/onchain/runs')
async def onchain_runs(job: str = JOB_BUILD, limit: int = 30, test_mode: int = 0) -> Response:
    """The run ledger (P14): what each run did, what failed, what it cost.

    `limit` is clamped rather than trusted: this is a local page, but an
    unbounded `LIMIT` from a query string is how a read surface becomes a way to
    pull the whole table into one response.
    """
    try:
        with _onchain_repository(bool(test_mode)) as repository:
            runs = repository.get_runs(job=job or None, limit=max(1, min(int(limit), 200)))
        payload = onchain_report.render_runs(runs)
    except psycopg.Error as exc:
        logger.error('onchain store unavailable: %s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'error': type(exc).__name__})
    return Response(
        content=json.dumps(payload, sort_keys=True, default=str),
        media_type='application/json',
    )


@router.get('/onchain/dossier/{project}')
async def onchain_dossier(
    project: str, build: Optional[int] = None, test_mode: int = 0, format: str = 'html'
) -> Response:
    """One project's dossier: latest sections plus the diff.

    HTML by default and JSON on `?format=json`, both rendered from the SAME
    `load_dossier` call the report job uses -- the page and the CLI cannot
    disagree about a figure because there is one loader (DR1's rule, applied to
    the dossier).
    """
    try:
        with _onchain_repository(bool(test_mode)) as repository:
            dossier = onchain_report.load_dossier(repository, project, build_id=build)
            projects = (
                []
                if format == 'json'
                else [
                    onchain_report.project_key(row['key'])
                    for row in repository.get_projects()
                ]
            )
    except onchain_report.UnknownProjectError:
        # 404 and not 400: the project key is a path segment naming a resource,
        # and "no such project" is what the operator needs to read after a typo.
        return JSONResponse(status_code=404, content={'error': 'unknown project'})
    except onchain_report.UnknownBuildError:
        return JSONResponse(status_code=404, content={'error': 'unknown build'})
    except psycopg.Error as exc:
        logger.error('onchain store unavailable: %s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'error': type(exc).__name__})

    if format == 'json':
        return Response(
            content=json.dumps(dossier, sort_keys=True, default=str),
            media_type='application/json',
        )
    return Response(
        content=render_dossier_page(dossier, projects=projects, test_mode=bool(test_mode)),
        media_type='text/html',
        headers={'Cache-Control': 'no-store'},
    )


def _onchain_repository(test_mode: bool) -> OnchainRepository:
    return OnchainRepository(
        get_onchain_database_url(RuntimeMode.from_test_mode(test_mode)), read_only=True
    )


def render_dossier_page(
    dossier: Any, *, projects: Optional[List[str]] = None, test_mode: bool = False
) -> str:
    """The dossier as one self-contained HTML page.

    No stylesheet, no script, no font: the requirement's local-first constraint
    is that opening this page sends nothing anywhere, and the cheapest way to
    guarantee that is a page with no external reference in it at all. The lines
    are the report module's own, so the page and `report --project X` are the
    same words -- there is no second formatter to drift. The page adds only
    shape: what changed since the previous build first, then each section folded
    unless it changed, then links to the other builds and projects. That shape
    is the design's reading order (D6), and it is what a 95-line `<pre>` lost.
    """
    title = f"{dossier.get('display_name') or dossier.get('project')} dossier"
    # Every link carries the store it was read from: a bare `?build=17` would
    # drop `test_mode` and switch a test-store reader to production unnoticed.
    query = '&test_mode=1' if test_mode else ''
    project_query = '?test_mode=1' if test_mode else ''
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        f'<title>{_escape(title)}</title>',
        '<style>body{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;'
        'margin:2rem;max-width:120ch}pre{white-space:pre-wrap;margin:0.25rem 0 1rem}'
        'details{margin:0.5rem 0}summary{cursor:pointer;font-weight:bold}'
        'nav{margin-bottom:1rem}nav a{margin-right:1ch}'
        '.lead{border-left:3px solid #999;padding-left:1ch}</style>',
        f'</head><body><h1>{_escape(title)}</h1>',
    ]
    if projects:
        parts.append('<nav>projects: ' + ' '.join(
            f'<a href="{key}{project_query}">{key}</a>' if key != dossier.get('project')
            else f'<strong>{key}</strong>'
            for key in projects if _is_link_safe(key)
        ) + '</nav>')
    build = dossier.get('build')
    if build is None:
        parts.append('<pre>no build yet</pre></body></html>')
        return ''.join(parts)

    formatting = onchain_report.Formatting(dossier)
    parts.append('<pre>' + _escape('\n'.join(
        onchain_report.header_lines(dossier, formatting)
    )) + '</pre>')
    parts.append(_render_build_nav(dossier, formatting, query))

    blocks = onchain_report.render_blocks(dossier)
    changed = [block for block in blocks if block.has_changes]
    parts.append('<h2>Changes since the previous build</h2>')
    if changed:
        lead = []
        for block in changed:
            lead.extend([block.title, *block.flag_lines, *block.change_lines])
        parts.append(f'<pre class="lead">{_escape(chr(10).join(lead))}</pre>')
    else:
        parts.append('<pre class="lead">no change in any section</pre>')

    parts.append('<h2>Sections</h2>')
    for block in blocks:
        state = ' open' if block.has_changes else ''
        parts.append(
            f'<details{state}><summary>{_escape(block.title)}</summary>'
            f'<pre>{_escape(chr(10).join(block.lines()[1:]))}</pre></details>'
        )
    parts.append('</body></html>')
    return ''.join(parts)


def _is_link_safe(key: Any) -> bool:
    """A registry key is `[A-Za-z0-9._-]+`; anything else never becomes an
    href, where `_escape` would not stop a `javascript:` scheme."""
    return bool(re.fullmatch(r'[A-Za-z0-9._-]+', str(key)))


def _render_build_nav(dossier: Any, formatting: Any, query: str = '') -> str:
    """Links to the project's recent builds; `?build=N` is the existing query
    parameter, so stepping back through diffs needs no new route."""
    builds = dossier.get('builds') or []
    if len(builds) < 2:
        return ''
    current = dossier['build']['id']
    links = []
    for build in builds:
        when = build.get('block_timestamp')
        label = f"{build['id']}"
        if when:
            label += f" ({formatting.scalar('block_timestamp', when)[:10]})"
        if build['id'] == current:
            links.append(f'<strong>{_escape(label)}</strong>')
        else:
            links.append(f'<a href="?build={int(build["id"])}{query}">{_escape(label)}</a>')
    return '<nav>builds: ' + ' '.join(links) + '</nav>'


def _escape(text: str) -> str:
    """Escaped because the page renders CHAIN DATA.

    A token's `name()` is arbitrary bytes chosen by whoever deployed it, and it
    reaches this page through the dossier. An unescaped `<script>` in a token
    name would run on a loopback origin that can read every other route here.
    """
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )
