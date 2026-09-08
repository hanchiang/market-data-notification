"""A1 re-run offline, against committed provider, explorer and chain bodies.

The requirement's Validation Expectations ask for exactly this: "the identity
collector's resolution of the four seed pool references is committed as a
fixture ... so the configuration criterion (A1) re-runs offline". A1 is the one
criterion that must keep holding for projects nobody is building today, and a
live-network check cannot give that -- the explorer this chain runs answers 500
most nights, so a networked A1 test would be red for reasons that have nothing
to do with A1.

What is replayed is every read the identity collector makes: the DEX provider's
two bodies, the pinned-block `eth_call`/`eth_getCode` responses keyed by method
and params, the creation log, and the explorer's three routes. Recapture with

    PYTHONPATH="$(pwd)" poetry run python scripts/capture_onchain_fixtures.py \
        --project touch-grass --project not-a-website \
        --project predict-fwa --project zzz --test_mode 0

Nothing here reaches the network; a fixture that stops matching the collector's
reads fails loudly rather than falling back to a live call.
"""
import json
from pathlib import Path

import pytest

from src.service.onchain.collectors import identity
from src.service.onchain.collectors.base import BuildContext
from src.service.onchain.chain import PinnedBlock
from src.service.onchain.config import get_registry_path
from src.service.onchain.explorer import ExplorerUnit
from src.service.onchain.observability import run_context
from src.service.onchain.registry import load_registry

FIXTURES = Path(__file__).parent / 'fixtures'
SEED_PROJECTS = ('touch-grass', 'not-a-website', 'predict-fwa', 'zzz')


def _load(project: str, name: str):
    path = FIXTURES / f'{project}.{name}.json'
    if not path.exists():
        pytest.fail(
            f'fixture {path.name} is missing; recapture with '
            'scripts/capture_onchain_fixtures.py (see this module docstring)'
        )
    return json.loads(path.read_text())


class ReplayEndpoint:
    def __init__(self, kind):
        self.kind = kind


class ReplayStateClient:
    """`eth_call`/`eth_getCode` answered from the captured bodies.

    Keyed by (method, params) so a collector asking a question the capture never
    asked raises rather than silently receiving another call's answer -- which
    would make the fixture agree with a collector that had changed.
    """

    def __init__(self, captured):
        self.endpoint = ReplayEndpoint('alchemy')
        self.by_key = {}
        for entry in captured:
            self.by_key[(entry['method'], json.dumps(entry['params'], sort_keys=True))] = entry

    def _raw(self, method, params):
        entry = self.by_key.get((method, json.dumps(params, sort_keys=True)))
        if entry is None:
            raise KeyError(f'no captured response for {method} {params}')
        return _Raw(entry)

    async def batch_call(self, calls, block, *, expect_value=True):
        out = []
        for to, data in calls:
            raw = self._raw('eth_call', [{'to': to, 'data': data}, hex(block)])
            out.append((raw.body['result'], raw))
        return out

    async def call(self, to, data, block, *, expect_value=True):
        return (await self.batch_call([(to, data)], block, expect_value=expect_value))[0]

    async def get_code(self, address, block):
        raw = self._raw('eth_getCode', [address, hex(block)])
        return raw.body['result'], raw

    async def get_block_by_number(self, block):
        """Matched on the block number rather than on the whole params list.

        The collector bounds its creation-log query by binary-searching headers,
        and the capture records those reads; matching on the number alone keeps
        the replay working if the client ever changes the second argument.
        """
        wanted = hex(int(block))
        for (method, _params), entry in self.by_key.items():
            if method == 'eth_getBlockByNumber' and entry['params'][0] == wanted:
                raw = _Raw(entry)
                return raw.body['result'], raw
        raise KeyError(f'no captured header for block {wanted}')


class _Raw:
    def __init__(self, entry):
        self.method = entry['method']
        self.params = entry['params']
        self.endpoint_kind = entry['endpoint_kind']
        self.body = entry['body']


class ReplayDexscreener:
    """The two provider bodies, parsed by the SERVICE's own parser.

    Hand-building the models would let a fixture pass while the real client's
    parsing of that same body failed -- which is the one thing a recorded body
    exists to catch.
    """

    def __init__(self, pair_body, token_pairs_body):
        self.pair_body = pair_body
        self.token_pairs_body = token_pairs_body

    @staticmethod
    def _parse(payload, endpoint):
        from market_data_library.core.crypto.dexscreener import DexscreenerService

        return DexscreenerService._parse_pairs(payload=payload, endpoint=endpoint)

    async def get_pairs_raw(self, *, chain_id, pair_addresses):
        return self._parse(self.pair_body, 'latest/dex/pairs'), self.pair_body

    async def get_token_pairs_raw(self, *, chain_id, token_address):
        return self._parse(self.token_pairs_body, 'token-pairs/v1'), self.token_pairs_body


class ReplayExplorer:
    """The explorer bodies as captured, `not_found` included.

    Wrapped in the real `ExplorerUnit` so the fixture exercises the same
    404-versus-failure classification the build uses.
    """

    def __init__(self, bodies):
        self.bodies = bodies

    async def get_address(self, address):
        return self._answer('address')

    async def get_smart_contract(self, address):
        return self._answer('smart_contract')

    async def get_transaction(self, tx_hash):
        return self._answer('transaction')

    def _answer(self, key):
        """Parsed by the SERVICE's own parser, not by hand.

        A hand-rolled model would let a fixture pass while the real client's
        parsing of that same body failed -- which is the one thing a recorded
        body is supposed to catch.
        """
        from market_data_library.core.crypto.blockscout import (
            BlockscoutNotFound,
            BlockscoutService,
        )
        from market_data_library.types import blockscout_type

        body = self.bodies.get(key)
        if body == 'api_error':
            from market_data_library.util.exception import BlockscoutApiError

            # Replayed as the failure it was, so the section comes back `partial`
            # with the error class -- the state this explorer is in most nights.
            raise BlockscoutApiError(message='captured 500', endpoint=key)
        if body is None or body == 'not_found':
            return BlockscoutNotFound(endpoint=key)
        model = {
            'address': blockscout_type.BlockscoutAddress,
            'smart_contract': blockscout_type.BlockscoutContract,
            'transaction': blockscout_type.BlockscoutTransaction,
        }[key]
        if key == 'transaction':
            renamed = dict(body)
            renamed['from_address'] = renamed.pop('from', None)
            renamed['to_address'] = renamed.pop('to', None)
            return BlockscoutService._parse(renamed, model, key, raw=body)
        return BlockscoutService._parse(body, model, key)


def _replay_context(seed_project, onchain_repository, monkeypatch):
    """The identity collector wired to one seed's committed bodies."""
    manifest = _load(seed_project, 'manifest')
    logs = _load(seed_project, 'logs')

    async def replay_fetch_window(client, query, from_block, to_block, **kwargs):
        return logs.get(query.name, []), []

    monkeypatch.setattr(
        'src.service.project_monitor.logs.fetch_window', replay_fetch_window
    )

    registry = load_registry(get_registry_path())
    project = registry.projects[seed_project]
    chain_entry = registry.chain_for(project)
    entity = onchain_repository.upsert_entity(
        level='project', key=f'project:{seed_project}'
    )
    context = BuildContext(
        repository=onchain_repository,
        registry=registry,
        chain=chain_entry,
        project=project,
        chain_entity_id=entity,
        project_entity_id=entity,
        pinned=PinnedBlock(
            block=int(manifest['block']),
            timestamp=int(manifest['block_timestamp']),
            window_start_block=int(manifest['block']) - 1,
            window_start_timestamp=int(manifest['block_timestamp']) - 1,
            header_reads=1,
        ),
        state_client=ReplayStateClient(_load(seed_project, 'jsonrpc')),
        log_client=ReplayStateClient([]),
        dexscreener=ReplayDexscreener(
            _load(seed_project, 'dexscreener_pair'),
            _load(seed_project, 'dexscreener_token_pairs'),
        ),
        explorer=ExplorerUnit(ReplayExplorer(_load(seed_project, 'blockscout'))),
    )
    return context, registry.projects[seed_project]


@pytest.fixture(params=SEED_PROJECTS)
def seed_project(request):
    return request.param


class TestIdentityResolvesOffline:
    @pytest.mark.asyncio
    async def test_a_previously_unresolved_immutable_is_read_again_not_carried(
        self, seed_project, onchain_repository, monkeypatch
    ):
        """`unavailable` is not a fact, so it must not be carried forward.

        predict-fwa's creation block failed once and every later build inherited
        the failure, which also sent its health section to walk from block 0 --
        the real cost of treating an unread field as immutable.
        """
        context, _ = _replay_context(seed_project, onchain_repository, monkeypatch)
        previous = {name: 'unavailable' for name in identity.IMMUTABLE_FIELDS}
        run_id = onchain_repository.start_run('onchain.build')
        with run_context(run_id, 'onchain.build'):
            section = await identity.collect(context, previous)
        assert isinstance(section.fields['creation_block'], int)

    @pytest.mark.asyncio
    async def test_each_seed_pool_reference_resolves_from_committed_bodies(
        self, seed_project, onchain_repository, monkeypatch
    ):
        """A1: given the four seed projects, the identity collector resolves each
        one's pool reference into a pool the chain agrees exists."""
        context, project = _replay_context(
            seed_project, onchain_repository, monkeypatch
        )
        run_id = onchain_repository.start_run('onchain.build')
        with run_context(run_id, 'onchain.build'):
            section = await identity.collect(context)

        fields = section.fields
        assert fields['version'] in ('v3', 'v4')
        assert fields['token_address'].startswith('0x')
        assert fields['token_symbol']
        assert isinstance(fields['decimals'], int)
        if fields['version'] == 'v4':
            assert fields['pool_id'] == project.pool_ref
            # `is True`, not truthiness: an unreadable `Initialize` log fills
            # every key field with the string 'unavailable', which is truthy, so
            # `assert fields['currency0']` passed on a fixture that resolved
            # nothing. The recomputed-id check is the whole of A1 for a v4 pool.
            assert fields['key_matches'] is True
            assert fields['currency0'].startswith('0x')
            assert fields['currency1'].startswith('0x')
        else:
            assert fields['pool_address'] == project.pool_ref
            assert fields['factory_matches'] is True
        # A1 names the creation block among what must resolve, and it is what
        # bounds every later transfer fetch. `unavailable` is a string; an int is
        # the only value that means the log was actually found.
        assert isinstance(fields['creation_block'], int)
        # The criterion is resolution, not a green explorer: this chain's
        # explorer answers 500 most nights and those units are allowed to fail.
        assert section.status in ('ok', 'partial')
