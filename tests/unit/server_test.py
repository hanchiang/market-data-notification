import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import URL

from src import server


class DummyRequest:
    def __init__(self, method: str, url: str, headers=None):
        self.method = method
        self.url = URL(url)
        self.headers = headers or {}
        self.client = SimpleNamespace(host='203.0.113.10')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('GET', '/healthz'),
        ('POST', '/tradingview/daily-stocks'),
    ],
)
async def test_auth_check_allows_exact_public_routes_without_token(
    monkeypatch,
    method,
    path,
):
    expected_response = SimpleNamespace(status_code=200)
    call_next = AsyncMock(return_value=expected_response)
    request = DummyRequest(method, f'https://example.com{path}')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')

    response = await server.auth_check(request, call_next)

    assert response is expected_response
    call_next.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_auth_check_rejects_protected_route_when_exclusion_env_is_blank(
    monkeypatch,
):
    call_next = AsyncMock()
    request = DummyRequest('GET', 'https://example.com/cryptoquant/price-ohlcv')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 404
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_auth_check_does_not_use_localhost_host_bypass_in_prod(monkeypatch):
    call_next = AsyncMock()
    request = DummyRequest('GET', 'https://localhost/cryptoquant/price-ohlcv')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 404
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_auth_check_rejects_substring_match_on_public_route(monkeypatch):
    call_next = AsyncMock()
    request = DummyRequest('GET', 'https://example.com/internal/healthz/details')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 404
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_auth_check_allows_private_route_with_valid_token(monkeypatch):
    expected_response = SimpleNamespace(status_code=200)
    call_next = AsyncMock(return_value=expected_response)
    request = DummyRequest(
        'GET',
        'https://example.com/cryptoquant/price-ohlcv',
        headers={'X-Api-Auth': 'expected-token'},
    )

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response is expected_response
    call_next.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/docs', '/redoc', '/openapi.json'])
async def test_auth_check_protects_fastapi_docs_routes_in_prod(monkeypatch, path):
    call_next = AsyncMock()
    request = DummyRequest('GET', f'https://example.com{path}')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 404
    call_next.assert_not_called()


def real_missing_route_response():
    """The 404 payload this app's exception handlers render, on an empty router.

    Deliberately NOT "what server.app returns for a missing route" -- it has no
    routes and no middleware, so a real 404 additionally carries `X-Process-Time`
    from the request logger. What it does track is the drift this canned body is
    exposed to: a framework upgrade changing the default payload, and an app-wide
    exception handler added to `server.app`, which is the next queued step.

    The security invariant itself -- that an unauthenticated caller cannot tell a
    protected route from an absent one -- is pinned by
    `test_unauthenticated_responses_are_identical_across_every_path` instead, which
    drives the real stack.
    """
    probe = FastAPI(exception_handlers=dict(server.app.exception_handlers))
    response = TestClient(probe).get('/definitely-not-a-route')
    return response.status_code, response.content, dict(response.headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('label', 'headers'),
    [
        ('absent header', None),
        ('empty header', {'X-Api-Auth': ''}),
        ('wrong token', {'X-Api-Auth': 'guessed-token'}),
        # A prefix of the real token. Without this case a comparison degraded to
        # `startswith` passes, which is a brute-forceable oracle one char at a time.
        ('prefix of the token', {'X-Api-Auth': 'expected'}),
    ],
)
async def test_auth_check_rejection_is_indistinguishable_from_a_missing_route(
    monkeypatch,
    label,
    headers,
):
    call_next = AsyncMock()
    request = DummyRequest(
        'GET',
        'https://example.com/cryptoquant/price-ohlcv',
        headers=headers,
    )

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    expected_status, expected_body, expected_headers = real_missing_route_response()
    assert response.status_code == expected_status
    assert response.body == expected_body
    # The whole header set, not a chosen subset: an added header is exactly the
    # kind of tell that makes a protected route distinguishable again.
    assert dict(response.headers) == expected_headers
    call_next.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [None, {'X-Api-Auth': ''}])
async def test_auth_check_logs_a_supplied_no_token_at_info(monkeypatch, caplog, headers):
    # An empty header value is the same event as no header: a caller that sent no
    # credential. It must not be logged as a wrong token, which is the signal
    # reserved for someone who sent one.
    call_next = AsyncMock()
    request = DummyRequest(
        'GET',
        'https://example.com/cryptoquant/price-ohlcv',
        headers=headers,
    )

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    with caplog.at_level(logging.INFO, logger='Server'):
        await server.auth_check(request, call_next)

    records = [r for r in caplog.records if 'X-Api-Auth' in r.getMessage()]
    assert [r.levelno for r in records] == [logging.INFO]


@pytest.mark.asyncio
async def test_auth_check_logs_wrong_token_at_warning_without_the_value(
    monkeypatch,
    caplog,
):
    call_next = AsyncMock()
    request = DummyRequest(
        'GET',
        'https://example.com/cryptoquant/price-ohlcv',
        headers={'X-Api-Auth': 'guessed-token'},
    )

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    with caplog.at_level(logging.INFO, logger='Server'):
        await server.auth_check(request, call_next)

    records = [r for r in caplog.records if 'X-Api-Auth' in r.getMessage()]
    assert [r.levelno for r in records] == [logging.WARNING]
    # auth_check is the outermost middleware, so a rejected request never reaches
    # the request/response logger and uvicorn's access log records no headers.
    # This line is therefore the only place the value could leak. Keep it out.
    assert 'guessed-token' not in caplog.text


def test_auth_check_is_the_outermost_registered_middleware():
    # Every other test in this file calls `auth_check` directly, so deleting the
    # `@app.middleware("http")` decorator would leave all of them green while every
    # protected route went unauthenticated in prod. Order is pinned too: `auth_check`
    # must run before the request logger, or a rejected request gets logged with the
    # header it presented.
    # Name every entry, not only the function-style ones: filtering to middleware
    # with a `dispatch` option would let a class-based `add_middleware` registered
    # outside auth_check sit in front of it unnoticed.
    registered = [
        getattr(m, 'options', {}).get('dispatch', m.cls).__name__
        for m in server.app.user_middleware
    ]
    assert registered[0] == 'auth_check'
    assert 'log_request_and_time_taken' in registered


@pytest.mark.asyncio
async def test_auth_check_still_skips_auth_outside_prod(monkeypatch):
    # The dangerous direction (a bypass firing in prod) is covered above. This pins
    # the other one: local and dev must keep working without a token.
    expected_response = SimpleNamespace(status_code=200)
    call_next = AsyncMock(return_value=expected_response)
    request = DummyRequest('GET', 'https://example.com/cryptoquant/price-ohlcv')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'dev')

    response = await server.auth_check(request, call_next)

    assert response is expected_response
    call_next.assert_awaited_once_with(request)


@pytest.mark.parametrize(
    'path',
    [
        '/cryptoquant/price-ohlcv',   # a real protected route
        '/cryptoquant/price-ohlcv/',  # its trailing-slash form: a 307 with a Location
                                      # header if anything routed before auth
        '/docs',
        '/openapi.json',
        '/definitely-not-a-route',
        '/',
    ],
)
def test_unauthenticated_responses_are_identical_across_every_path(monkeypatch, path):
    # The invariant, driven through the real stack rather than a stand-in: in prod an
    # unauthenticated request must produce the same bytes and the same headers
    # whatever it asks for, so nothing distinguishes a protected route from an absent
    # one. It holds because auth_check returns before routing -- in dev these same
    # paths give a 307 with a Location header, a 200 for /docs, and X-Process-Time
    # throughout. TestClient runs no lifespan outside a `with` block, so the startup
    # event never fires and no Telegram bot is constructed.
    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    client = TestClient(server.app)
    baseline = client.get('/definitely-not-a-route')
    response = client.get(path)

    assert (response.status_code, response.content, dict(response.headers)) == (
        baseline.status_code,
        baseline.content,
        dict(baseline.headers),
    )
