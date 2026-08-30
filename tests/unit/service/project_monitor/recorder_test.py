"""R6 attribution, the deploy-block gate, and AC2's loud failure."""
import asyncio
import json

import pytest
from aiohttp import web

from market_data_library.core.onchain.evm import (
    Endpoint,
    EvmClient,
    EvmTransportError,
    public_rpc_budget,
)

from src.service.project_monitor import recorder
from src.service.project_monitor.attribution import (
    LABEL_BOND,
    LABEL_ISSUANCE,
    LABEL_UNLABELLED,
    RULE_BOND_CORRELATION,
    RULE_BOND_EVENT,
    RULE_ISSUANCE_CORRELATION,
    RULE_LABELLED_SENDER,
    attribute_inflow,
    attribute_outflow,
    build_flow_rows,
    index_bond_events_by_transaction,
    index_mints_by_transaction,
)
from src.service.project_monitor.config import NETNET

BOND_TX = '0xBOND'
ISSUANCE_TX = '0xissuance'
PLAIN_TX = '0xplain'

MINTS = [
    {'tx_hash': BOND_TX, 'class': 'bond'},
    {'tx_hash': ISSUANCE_TX, 'class': 'issuance'},
]
BOND_EVENTS = [
    {'tx_hash': BOND_TX, 'name': 'BondCreated',
     'fields_json': {'depositor': '0xdep', 'marketId': '2'}},
]


def _attribute(tx_hash, sender, *, use_bond_event=True):
    return attribute_inflow(
        tx_hash=tx_hash,
        sender=sender,
        project=NETNET,
        mints_by_tx=index_mints_by_transaction(MINTS),
        bond_events_by_tx=index_bond_events_by_transaction(BOND_EVENTS),
        use_bond_event=use_bond_event,
    )


def test_a_bond_inflow_is_matched_by_transaction_not_by_sender():
    """Measured 2026-08-29: of 332 Treasury inflows, ZERO came from the Bond
    Depository, while 124 shared a transaction with a mint to it. A bond is paid
    from the bonder's own wallet, so sender labelling sees none of them."""
    result = _attribute(BOND_TX, sender='0xsomerandomwallet')
    assert result.label == LABEL_BOND
    assert result.rule == RULE_BOND_EVENT
    assert result.depositor == '0xdep'
    assert result.market_id == 2


def test_the_bond_event_path_and_the_correlation_fallback_agree():
    """Rule 1b exists for a deployment where `BondCreated` is absent from the
    bytecode. It is checked here to agree with rule 1 on the same input, because
    a fallback that has never been compared to the primary is a guess."""
    via_event = _attribute(BOND_TX, sender='0xwallet', use_bond_event=True)
    via_correlation = _attribute(BOND_TX, sender='0xwallet', use_bond_event=False)
    assert via_event.label == via_correlation.label == LABEL_BOND
    assert via_event.rule == RULE_BOND_EVENT
    assert via_correlation.rule == RULE_BOND_CORRELATION
    # Only the event path carries the depositor and route: that is what the
    # event buys over correlation, and why it is the primary.
    assert via_event.depositor is not None
    assert via_correlation.depositor is None


def test_an_issuance_inflow_is_matched_by_transaction_too():
    """Same reason as bonds: the USDG sender is the buyer's wallet, not the
    premium sales desk."""
    result = _attribute(ISSUANCE_TX, sender='0xbuyer')
    assert result.label == LABEL_ISSUANCE
    assert result.rule == RULE_ISSUANCE_CORRELATION


def test_a_labelled_sender_takes_its_label():
    result = _attribute(PLAIN_TX, sender=NETNET.address('taxCollector'))
    assert result.label == 'taxCollector'
    assert result.rule == RULE_LABELLED_SENDER


def test_an_unknown_sender_is_unlabelled_and_keeps_its_address():
    """No flow is dropped for lack of a label: an unattributed flow is a fact
    about our labelling, not about the chain."""
    result = _attribute(PLAIN_TX, sender='0xdeadbeef')
    assert result.label == LABEL_UNLABELLED
    assert result.rule == LABEL_UNLABELLED


def test_rule_order_bond_beats_a_labelled_sender():
    """If a bond were ever paid from a labelled address, it is still a bond.
    Getting the precedence backwards would file it under the label instead."""
    result = _attribute(BOND_TX, sender=NETNET.address('taxCollector'))
    assert result.label == LABEL_BOND


def test_outflows_are_attributed_by_recipient():
    assert attribute_outflow(NETNET.address('morphoUsdgVault'), NETNET).label == (
        'morphoUsdgVault'
    )
    assert attribute_outflow('0xnothing', NETNET).label == LABEL_UNLABELLED


def test_flow_rows_carry_the_rule_that_labelled_them():
    """Stored per flow so a later reader can tell WHY a flow is a bond, and
    re-audit it, rather than taking the label on trust."""
    rows = build_flow_rows(
        project_name='NETNET', project=NETNET,
        inflows=[{'block': 1, 'tx_hash': BOND_TX, 'log_index': 0,
                  'from': '0xw', 'value': 5}],
        outflows=[{'block': 2, 'tx_hash': PLAIN_TX, 'log_index': 1,
                   'to': '0xz', 'value': 3}],
        mint_rows=MINTS, bond_event_rows=BOND_EVENTS, usdg_decimals=6,
    )
    assert [r['direction'] for r in rows] == ['in', 'out']
    assert rows[0]['rule'] == RULE_BOND_EVENT
    assert rows[1]['label'] == LABEL_UNLABELLED


# ------------------------------------------------------- deploy gate and AC2

class _Server:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def handle(self, request):
        payload = await request.json()
        self.requests.append(payload)
        return await self.handler(payload)


async def _start(handler):
    server = _Server(handler)
    app = web.Application()
    app.router.add_post('/rpc', server.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]
    return server, f'http://127.0.0.1:{port}/rpc', runner


def test_a_rejecting_endpoint_makes_the_read_fail_loudly(event_loop=None):
    """AC2, and the constraint a fetch failure must never become a finding.

    Both public RPCs answer 403 to a non-browser user agent, and the first
    crawler written for this chain swallowed that and reported a live contract
    as an externally-owned account. The read must raise, and no sample may exist.
    """

    async def run():
        async def handler(_payload):
            return web.Response(status=403, text='forbidden')

        server, url, runner = await _start(handler)
        client = EvmClient(
            Endpoint(kind='public', url=url),
            public_rpc_budget(min_request_interval_seconds=0.0),
        )
        try:
            with pytest.raises(EvmTransportError):
                await recorder.read_state(client, NETNET, 100)
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(run())


def test_a_read_below_a_contracts_deploy_block_is_not_issued():
    """A contract that did not exist at the pinned block is `not_deployed` -- a
    defined observation, distinct from a failure -- and no call is sent for it.

    The count of issued calls is asserted, not just the recorded state: the
    point is to spend nothing on a read whose answer is already known.
    """

    async def run():
        async def handler(payload):
            entries = payload if isinstance(payload, list) else [payload]
            body = []
            for entry in entries:
                if entry['method'] == 'eth_getBlockByNumber':
                    body.append({'jsonrpc': '2.0', 'id': entry['id'],
                                 'result': {'number': '0x64', 'timestamp': '0x1'}})
                else:
                    # Eight words, not one: the read plan holds tuple returns
                    # of up to six (`Morpho.market`), and a one-word stub makes
                    # the decoder raise for the right reason but the wrong test.
                    body.append({'jsonrpc': '2.0', 'id': entry['id'],
                                 'result': '0x' + f'{7:064x}' * 8})
            return web.Response(
                text=json.dumps(body if isinstance(payload, list) else body[0]),
                content_type='application/json',
            )

        server, url, runner = await _start(handler)
        client = EvmClient(
            Endpoint(kind='public', url=url),
            public_rpc_budget(min_request_interval_seconds=0.0),
        )
        try:
            # Every inverseBond read is gated out; nothing else is.
            result = await recorder.read_state(
                client, NETNET, 100, deploy_blocks={'inverseBond': 500}
            )
        finally:
            await client.close()
            await runner.cleanup()
        return server, result

    server, result = asyncio.run(run())

    gated = [r for r in result.readings if r['contract'] == 'inverseBond']
    assert gated and all(r['state'] == recorder.STATE_NOT_DEPLOYED for r in gated)
    assert all(r['raw_hex'] is None for r in gated)

    sent_calldata = []
    for payload in server.requests:
        for entry in payload if isinstance(payload, list) else [payload]:
            if entry['method'] == 'eth_call':
                sent_calldata.append(entry['params'][0]['to'].lower())
    assert NETNET.address('inverseBond').lower() not in sent_calldata
    # And the gate did not swallow the rest of the plan.
    assert NETNET.address('treasury').lower() in sent_calldata


def test_the_deploy_gate_does_not_fire_without_a_deploy_block():
    """A contract with no recorded deploy block is read normally: the gate must
    not turn "we have not searched yet" into "it does not exist"."""

    async def run():
        async def handler(payload):
            entries = payload if isinstance(payload, list) else [payload]
            body = []
            for entry in entries:
                if entry['method'] == 'eth_getBlockByNumber':
                    body.append({'jsonrpc': '2.0', 'id': entry['id'],
                                 'result': {'number': '0x64', 'timestamp': '0x1'}})
                else:
                    # Eight words, not one: the read plan holds tuple returns
                    # of up to six (`Morpho.market`), and a one-word stub makes
                    # the decoder raise for the right reason but the wrong test.
                    body.append({'jsonrpc': '2.0', 'id': entry['id'],
                                 'result': '0x' + f'{7:064x}' * 8})
            return web.Response(
                text=json.dumps(body if isinstance(payload, list) else body[0]),
                content_type='application/json',
            )

        _, url, runner = await _start(handler)
        client = EvmClient(
            Endpoint(kind='public', url=url),
            public_rpc_budget(min_request_interval_seconds=0.0),
        )
        try:
            return await recorder.read_state(client, NETNET, 100, deploy_blocks={})
        finally:
            await client.close()
            await runner.cleanup()

    result = asyncio.run(run())
    assert not any(
        r['state'] == recorder.STATE_NOT_DEPLOYED for r in result.readings
    )
