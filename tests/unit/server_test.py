from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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

    assert response.status_code == 500
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_auth_check_does_not_use_localhost_host_bypass_in_prod(monkeypatch):
    call_next = AsyncMock()
    request = DummyRequest('GET', 'https://localhost/cryptoquant/price-ohlcv')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 500
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_auth_check_rejects_substring_match_on_public_route(monkeypatch):
    call_next = AsyncMock()
    request = DummyRequest('GET', 'https://example.com/internal/healthz/details')

    monkeypatch.setattr(server.config, 'get_env', lambda: 'prod')
    monkeypatch.setattr(server.config, 'get_api_auth_token', lambda: 'expected-token')

    response = await server.auth_check(request, call_next)

    assert response.status_code == 500
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

    assert response.status_code == 500
    call_next.assert_not_called()
