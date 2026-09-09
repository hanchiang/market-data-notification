"""The explorer client must be keyed wherever it is built.

An unkeyed client 403s on every read. The build still finishes, the sections
read `partial`, and six fields per project carry a `failed` marker -- which is
what a genuinely flaky explorer looks like too. So the regression is silent,
and the two tests here are what make it loud: the factory sends the configured
key, and no other module builds a client behind the factory's back.
"""
import ast
import pathlib

import pytest

from src.service.onchain.explorer import build_explorer_service

EXPLORER_API = 'https://robinhoodchain.blockscout.com/api/v2/'
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

# The factory itself, which is the one place allowed to call the constructor.
FACTORY_MODULE = REPO_ROOT / 'src' / 'service' / 'onchain' / 'explorer.py'


class TestTheFactorySendsTheKey:
    @pytest.mark.asyncio
    async def test_the_configured_key_reaches_the_client(self, monkeypatch) -> None:
        monkeypatch.setenv('BLOCKSCOUT_API_KEY', 'proapi_secret')
        service = build_explorer_service(EXPLORER_API)
        try:
            # `client.client` is the aiohttp session the library holds; the
            # headers on it are what actually go out on the wire.
            assert service.client.client.headers.get('x-api-key') == 'proapi_secret'
        finally:
            await service.cleanup()

    @pytest.mark.asyncio
    async def test_no_configured_key_sends_no_header(self, monkeypatch) -> None:
        monkeypatch.delenv('BLOCKSCOUT_API_KEY', raising=False)
        service = build_explorer_service(EXPLORER_API)
        try:
            assert 'x-api-key' not in service.client.client.headers
        finally:
            await service.cleanup()


class TestNothingBypassesTheFactory:
    """Pins the call sites, which no runtime test reaches.

    `run_build` needs a database, a pinned block and two RPC clients, so there
    is no unit test in which its construction line executes. This reads the
    source instead: it is the only thing that fails when someone writes
    `BlockscoutService(url)` again.
    """

    def test_only_the_factory_constructs_a_blockscout_service(self) -> None:
        offenders = []
        for path in [
            *(REPO_ROOT / 'src').rglob('*.py'),
            *(REPO_ROOT / 'scripts').rglob('*.py'),
        ]:
            if path == FACTORY_MODULE:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Attribute calls such as `module.BlockscoutService(...)` match
                # too, so an import-style change does not open a hole.
                name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
                if name == 'BlockscoutService':
                    offenders.append(f'{path.relative_to(REPO_ROOT)}:{node.lineno}')

        assert offenders == [], (
            'build the explorer through build_explorer_service, which supplies '
            f'the API key; direct construction is unkeyed: {offenders}'
        )
