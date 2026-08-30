"""AC6 and AC3, against the committed fixture of real JSON-RPC responses.

The fixture was captured from the public endpoint on 2026-08-30 at block
49,877,926 by `scripts/capture_project_monitor_fixture.py`, which is committed
beside it so it can be recaptured when the read plan changes.
"""
import asyncio
from decimal import Decimal

import pytest

from market_data_library.core.onchain.evm import abi

from src.service.project_monitor import logs as log_plane
from src.service.project_monitor import recorder
from src.service.project_monitor.attribution import LABEL_BOND, RULE_BOND_EVENT
from src.service.project_monitor.config import NETNET
from src.service.project_monitor.read_plan import build_read_plan


def _readings_by_name(expected_fixture):
    return {r['name']: r for r in expected_fixture['readings']}


def test_every_reading_re_derives_from_its_own_raw_responses(
    sample_fixture, expected_fixture
):
    """AC3: a stored record reproduces exactly from its stored raw responses.

    The decoders are re-run over the raw bodies and compared to the values
    written beside them. This is the criterion that makes the store independent
    of the provider: if it holds, a figure can be re-derived without a second
    network call and without trusting that the endpoint still answers the same.
    """
    plan = {read.name: read for read in build_read_plan(NETNET)}
    expected = _readings_by_name(expected_fixture)

    # Raw responses are in issue order, with the block read first; eth_call
    # bodies match the plan's own order.
    call_bodies = [
        raw for raw in sample_fixture['raw_responses'] if raw['method'] == 'eth_call'
    ]
    issued = [name for name in expected if expected[name]['state'] == 'ok']
    assert len(call_bodies) >= len(
        [n for n in issued if n in plan]
    ), 'fixture must hold one eth_call body per issued plan read'

    checked = 0
    for raw in call_bodies:
        calldata = raw['params'][0]['data']
        to = raw['params'][0]['to'].lower()
        matches = [
            read
            for read in plan.values()
            if read.calldata == calldata and read.to.lower() == to
        ]
        if not matches:
            continue
        read = matches[0]
        record = expected.get(read.name)
        if record is None or record['state'] != 'ok':
            continue
        value = abi.decode_single(read.result_type, raw['body']['result'])
        # The stored shape comes from the recorder's own `split_value`, not from
        # a second copy of the rule here. A local copy is what let an address be
        # stored as 42 one-character strings and still pass: both sides built
        # the same wrong shape and compared them to each other.
        value_int, value_json = recorder.split_value(value)
        if record['value_int'] is not None:
            assert value_int == int(record['value_int']), read.name
        else:
            assert value_json == record['value_json'], read.name
        checked += 1

    # Not vacuous: the whole core plan really was re-derived.
    assert checked >= 20, f'only {checked} readings re-derived'


def test_the_re_derivation_check_can_fail(sample_fixture, expected_fixture):
    """Corrupt one stored body and the AC3 comparison must go red.

    Without this, a green AC3 run is equally consistent with a comparison that
    never actually compared anything.
    """
    expected = _readings_by_name(expected_fixture)
    plan = {read.name: read for read in build_read_plan(NETNET)}
    read = plan['Treasury.rfv']
    body = next(
        raw
        for raw in sample_fixture['raw_responses']
        if raw['method'] == 'eth_call' and raw['params'][0]['data'] == read.calldata
    )
    corrupted = body['body']['result'][:-1] + ('0' if body['body']['result'][-1] != '0' else '1')
    assert abi.decode_single(read.result_type, corrupted) != int(
        expected['Treasury.rfv']['value_int']
    )


def test_the_fixture_holds_every_g4_getter_ac6_names(expected_fixture):
    expected = _readings_by_name(expected_fixture)
    required = [
        'NET.totalSupply', 'NET.decimals', 'USDG.decimals',
        'USDG.balanceOf(Treasury)', 'Treasury.rfv', 'Treasury.backingPerToken',
        'pair.token0', 'pair.token1', 'pair.getReserves', 'pair.totalSupply',
        'pair.balanceOf(Treasury)', 'Staking.epoch',
        'inverseBond.capacityRemaining', 'Morpho.market', 'IRM.borrowRateView',
    ]
    for symbol in ('NVDA', 'SPCX', 'AAPL', 'MSFT', 'GOOGL', 'COIN'):
        required += [
            f'Sleeve.{symbol}.balanceOf',
            f'Mark.{symbol}.latestRoundData',
            f'Mark.{symbol}.decimals',
        ]
    missing = [name for name in required if name not in expected]
    assert missing == []
    assert all(expected[name]['state'] == 'ok' for name in required)


def test_decimals_are_stored_beside_every_token_amount(expected_fixture):
    """R5. This is the assertion that caught the real bug: `NET.totalSupply` is
    issued before `NET.decimals` in the same batch, so a per-read lookup found
    nothing and every token amount was written with a null `decimals`."""
    expected = _readings_by_name(expected_fixture)
    amounts = {
        'NET.totalSupply': 9,
        'USDG.balanceOf(Treasury)': 6,
        'pair.totalSupply': 18,
        'pair.balanceOf(Treasury)': 18,
        'Treasury.rfv': 18,
        'Treasury.backingPerToken': 18,
        'premiumSeller.clipSize': 9,
        'Sleeve.NVDA.balanceOf': 18,
    }
    for name, decimals in amounts.items():
        assert expected[name]['decimals'] == decimals, name


def test_the_pair_price_is_above_backing(expected_fixture):
    """AC6 names this explicitly: the market pays a large premium to backing,
    which is the whole reason the treasury series is worth recording."""
    expected = _readings_by_name(expected_fixture)
    usdg_reserve, net_reserve, _ = expected['pair.getReserves']['value_json']
    # token0 is USDG, token1 is NET -- asserted against the addresses the sample
    # actually read, because reserve0/reserve1 carry no labels and swapping them
    # inverts the price silently. The previous version of this assertion checked
    # only that `value_int is None`, which is true of every non-integer reading
    # and therefore said nothing about which token came first.
    assert expected['pair.token0']['value_json'].lower() == NETNET.address('USDG').lower()
    assert expected['pair.token1']['value_json'].lower() == NETNET.address('NET').lower()
    usdg_dp = int(expected['USDG.decimals']['value_int'])
    net_dp = int(expected['NET.decimals']['value_int'])
    price = (Decimal(usdg_reserve) / Decimal(10) ** usdg_dp) / (
        Decimal(net_reserve) / Decimal(10) ** net_dp
    )
    backing = Decimal(expected['Treasury.backingPerToken']['value_int']) / Decimal(10) ** 18
    assert price > backing
    assert price / backing > 10  # ~19x at capture; the premium is the headline


def test_the_sleeve_total_is_absent_from_rfv(expected_fixture):
    """AC6. The project's own memo says the Sleeve is team-custodied and never
    summed into RFV; this asserts the recorded numbers agree, so a later reader
    cannot conclude rfv already includes it."""
    expected = _readings_by_name(expected_fixture)
    rfv = Decimal(expected['Treasury.rfv']['value_int']) / Decimal(10) ** 18

    sleeve_total = Decimal(0)
    for symbol in ('NVDA', 'SPCX', 'AAPL', 'MSFT', 'GOOGL', 'COIN'):
        balance = Decimal(expected[f'Sleeve.{symbol}.balanceOf']['value_int'])
        balance /= Decimal(10) ** expected[f'Sleeve.{symbol}.balanceOf']['decimals']
        answer = Decimal(expected[f'Mark.{symbol}.latestRoundData']['value_json'][1])
        feed_decimals = expected[f'Mark.{symbol}.decimals']['value_int']
        sleeve_total += balance * (answer / Decimal(10) ** int(feed_decimals))

    assert sleeve_total > 0
    liquid = Decimal(expected['Treasury.liquidUsdg']['value_int']) / Decimal(10) ** 18
    morpho = Decimal(expected['Treasury.morphoAssets']['value_int']) / Decimal(10) ** 18
    # If the Sleeve were inside rfv, rfv would have to exceed the two components
    # it does publish by roughly the Sleeve's size. It does not -- rfv is below
    # their sum, let alone their sum plus the Sleeve.
    assert rfv < liquid + morpho + sleeve_total


def test_rfv_components_do_not_sum_to_rfv(expected_fixture):
    """A measured fact the design assumed otherwise, pinned so it is noticed if
    it ever stops being true.

    The design called `liquidUsdg`, `morphoAssets` and `polRfv` "the three
    components rfv() publishes". They are real getters -- all three are in the
    Treasury's deployed bytecode -- but they do not add up to `rfv()`: measured
    -1.15% at block 49,867,666, and the fixture reproduces the same shape. So
    the components make a movement attributable to a component, which is why
    they are recorded, but the difference is a modelling gap and the report says
    so rather than presenting it as an unaccounted flow.
    """
    expected = _readings_by_name(expected_fixture)
    scale = Decimal(10) ** 18
    rfv = Decimal(expected['Treasury.rfv']['value_int']) / scale
    total = sum(
        Decimal(expected[f'Treasury.{name}']['value_int']) / scale
        for name in ('liquidUsdg', 'morphoAssets', 'polRfv')
    )
    assert total != rfv
    assert abs(total - rfv) / rfv < Decimal('0.05')  # a gap, not a unit error


def test_liquid_usdg_equals_the_treasurys_usdg_balance(expected_fixture):
    """Cross-check between two independent getters on two contracts.

    `Treasury.liquidUsdg()` is wad-denominated; `USDG.balanceOf(treasury)` is
    the token's own 6 dp. They agree exactly, which is what tells us the wad
    getter is a rescaling of the balance rather than a different quantity.
    """
    expected = _readings_by_name(expected_fixture)
    liquid = Decimal(expected['Treasury.liquidUsdg']['value_int']) / Decimal(10) ** 18
    balance = Decimal(expected['USDG.balanceOf(Treasury)']['value_int']) / Decimal(10) ** 6
    assert liquid == balance


def test_the_fixture_window_holds_a_bond_mint_and_its_paired_inflow(
    log_window_fixture,
):
    """AC6 requires both, and it is the case sender-labelling cannot see.

    Measured 2026-08-29: of 332 Treasury inflows, ZERO came from the Bond
    Depository -- a bond is paid from the bonder's own wallet. So an inflow
    attributed `bond` is proof the transaction-correlation path works, which
    sender matching would have got wrong for every one of them.
    """
    mints = log_window_fixture['mints']
    flows = log_window_fixture['flows']
    bond_mints = [m for m in mints if m['class'] == 'bond']
    bond_flows = [f for f in flows if f['label'] == LABEL_BOND]

    assert bond_mints, 'the fixture window must contain at least one bond mint'
    assert bond_flows, 'and its paired USDG inflow'
    assert all(f['rule'] == RULE_BOND_EVENT for f in bond_flows)

    # Each bond inflow shares its transaction with a bond mint -- the pairing
    # itself, not just the count.
    mint_txs = {m['tx_hash'].lower() for m in bond_mints}
    assert all(f['tx_hash'].lower() in mint_txs for f in bond_flows)

    # And the sender is never the depository, which is why rule 3 could not do
    # this job.
    depository = NETNET.address('bondDepository').lower()
    assert all(f['counterparty'].lower() != depository for f in bond_flows)


def test_the_window_holds_a_rebase_mint_marking_an_epoch_boundary(
    log_window_fixture,
):
    mints = log_window_fixture['mints']
    rebases = [m for m in mints if m['class'] == 'rebase']
    assert len(rebases) >= 1
    staking = NETNET.address('staking').lower()
    assert all(m['recipient'].lower() == staking for m in rebases)
    boundaries = log_plane.rebase_boundaries(NETNET, mints)
    assert len(boundaries) == len(rebases)


@pytest.mark.parametrize(
    'recipient_key,expected_class',
    [
        ('staking', 'rebase'),
        ('bondDepository', 'bond'),
        ('premiumSeller', 'issuance'),
        ('teamMultisig', 'other'),
    ],
)
def test_mint_classes_are_decided_by_recipient(recipient_key, expected_class):
    """`issuance` is its own class rather than falling into `other`: the premium
    sales desk demonstrably mints, and an `other` bucket would have hidden a
    fact the requirement calls load-bearing."""
    assert (
        log_plane.classify_mint(NETNET.address(recipient_key), NETNET)
        == expected_class
    )


ISSUANCE_TX = '0x0fe14bcf821343c228921e995b085b5c1f76e1eaae1301397fedcad8c6337b19'


def test_the_premium_sale_mints_new_net_in_the_same_transaction(issuance_fixture):
    """Settles the claim the requirement held pending this decode.

    The research note read the dapp's UI copy and inferred that the desk sells
    NET at a premium; whether it MINTS that NET in the same act was unverified,
    and only the stronger claim depends on it -- that a premium sale moves
    supply and reserves together, corrupting both terms of the
    emission-against-growth comparison the whole report is built on.

    Decoded from the one execution the trace names: a NET mint of 0.813691617
    from the zero address to `premiumSeller`, and a transfer of that exact
    amount on to the pair, both in transaction 0x0fe14bcf...7b19. It mints.
    """
    mints = issuance_fixture['mints']
    assert [m['class'] for m in mints] == ['issuance']
    mint = mints[0]
    assert mint['tx_hash'].lower() == ISSUANCE_TX
    assert Decimal(mint['amount']) / Decimal(10) ** 9 == Decimal('0.813691617')

    # The paired USDG inflow is attributed to `issuance` by the same-transaction
    # mint correlation -- rule 2, not a labelled sender. That is what makes the
    # issuance bucket land on the flow as well as on the supply.
    inflows = [f for f in issuance_fixture['flows'] if f['direction'] == 'in']
    assert len(inflows) == 1
    assert inflows[0]['label'] == 'issuance'
    assert inflows[0]['rule'] == 'issuance:same-transaction-mint'
    assert Decimal(inflows[0]['amount']) / Decimal(10) ** 6 == Decimal('952.809405')


def test_the_issuance_window_carries_the_premium_sold_event(issuance_fixture):
    """The event decode, cross-checked against a source outside the chain: the
    dapp's own activity table rendered this execution as "0.81 NET | 952.81
    USDG". The decoded figures round to exactly that, which is independent
    evidence that the ABI extracted from the minified bundle is the right one --
    a wrong field order would still decode, just to different numbers.
    """
    events = [e for e in issuance_fixture['events'] if e['name'] == 'PremiumSold']
    assert len(events) == 1
    fields = events[0]['fields_json']
    assert Decimal(fields['netSold']) / Decimal(10) ** 9 == Decimal('0.813691617')
    assert Decimal(fields['usdgSweptRaw']) / Decimal(10) ** 6 == Decimal('952.809405')


# ------------------------------------------- AC6's log half, actually decoded
#
# Round-1 test review, finding 2: the tests above read the PRE-DECODED
# `mints`/`flows`/`events` arrays that the capture script stored, so they
# assert properties of their own stored output and the decoder never runs.
# Demonstrated: swapping `from`/`to` in `decode_transfer` corrupts every mint
# recipient, every mint class and every flow counterparty, and the whole suite
# stayed green. AC6 says "when the decoder and the attribution rules run on it,
# then the expected values written beside the fixture reproduce exactly" -- for
# the log half they were not running at all.
#
# The route below replays the fixture's own nine raw `eth_getLogs` bodies
# through the REAL `recorder.read_log_window`, which is what makes this the
# analogue of `test_every_reading_re_derives_from_its_own_raw_responses` for
# state: every decoder (`decode_transfer`, `build_mint_rows`,
# `extract_event_rows`, `classify_mint`) and the R6 attribution rules run for
# real, and so does the composition that wires them together.


class _ReplayingLogClient:
    """Serves the fixture's stored `eth_getLogs` bodies back to the real code.

    Matched on the query's own address+topics filter rather than on call order:
    order matching would still pass if `build_log_queries` swapped two queries,
    which would file one event type's logs under another's decoder.
    """

    def __init__(self, raw_responses):
        self.endpoint = type('E', (), {'kind': 'public'})()
        self.by_filter = {}
        for raw in raw_responses:
            self.by_filter[self._key(raw['params'][0])] = raw
        self.served = []

    @staticmethod
    def _key(params):
        addresses = tuple(sorted(a.lower() for a in (params.get('address') or [])))
        topics = tuple(
            tuple(sorted(t)) if isinstance(t, list) else t
            for t in (params.get('topics') or [])
        )
        return (addresses, topics)

    async def get_logs(self, log_filter):
        params = log_filter.to_params()
        key = self._key(params)
        raw = self.by_filter.get(key)
        assert raw is not None, f'no fixture response for query filter {key}'
        self.served.append(key)
        result = raw['body'].get('result') or []
        return list(result), raw


def _replay(fixture):
    client = _ReplayingLogClient(fixture['raw_responses'])
    window = asyncio.run(
        recorder.read_log_window(
            client,
            NETNET,
            'NETNET',
            fixture['from_block'],
            fixture['to_block'],
            net_decimals=9,
            usdg_decimals=6,
        )
    )
    return window, client


def _comparable(rows, keys):
    """Rows reduced to the named fields, with every value stringified.

    The fixture stores uint256 amounts as decimal STRINGS (they exceed what a
    JSON number holds exactly) while the decoders return ints, so the two sides
    are normalised before comparison. Stringifying is not a weakening: a wrong
    amount, recipient or label still differs as a string.
    """
    return sorted(
        tuple((k, str(row[k])) for k in keys) for row in rows
    )


def test_the_log_window_re_derives_from_its_own_raw_responses(log_window_fixture):
    """AC6's log half, run for real. Every mint, flow and event stored beside
    the fixture must reproduce from the fixture's own raw `eth_getLogs` bodies
    through the live decoders and the R6 attribution rules."""
    window, client = _replay(log_window_fixture)

    assert len(client.served) == 9, 'every one of the nine queries was served'

    assert _comparable(
        window['mints'], ('block', 'tx_hash', 'log_index', 'recipient', 'amount', 'class')
    ) == _comparable(
        log_window_fixture['mints'],
        ('block', 'tx_hash', 'log_index', 'recipient', 'amount', 'class'),
    )

    assert _comparable(
        window['flows'],
        ('block', 'tx_hash', 'log_index', 'direction', 'counterparty', 'amount',
         'label', 'rule'),
    ) == _comparable(
        log_window_fixture['flows'],
        ('block', 'tx_hash', 'log_index', 'direction', 'counterparty', 'amount',
         'label', 'rule'),
    )

    assert len(window['events']) == len(log_window_fixture['events'])
    assert _comparable(
        window['events'], ('block', 'tx_hash', 'log_index', 'contract', 'name')
    ) == _comparable(
        log_window_fixture['events'],
        ('block', 'tx_hash', 'log_index', 'contract', 'name'),
    )

    # Decoded event FIELDS too, not just their identity: a wrong ABI field
    # order still produces the right number of events at the right blocks.
    derived_fields = {
        (e['tx_hash'].lower(), e['log_index'], e['name']): e['fields_json']
        for e in window['events']
    }
    for stored in log_window_fixture['events']:
        key = (stored['tx_hash'].lower(), stored['log_index'], stored['name'])
        assert derived_fields[key] == stored['fields_json'], key


def test_the_issuance_window_re_derives_from_its_own_raw_responses(issuance_fixture):
    """The same, for the single-block window holding the settling premium sale.
    This one carries the `issuance` mint class and the transaction-correlated
    `issuance` inflow, so it exercises an R6 rule the near-head window does
    not."""
    window, _ = _replay(issuance_fixture)

    assert [m['class'] for m in window['mints']] == ['issuance']
    assert _comparable(
        window['mints'], ('block', 'tx_hash', 'recipient', 'amount', 'class')
    ) == _comparable(
        issuance_fixture['mints'], ('block', 'tx_hash', 'recipient', 'amount', 'class')
    )
    assert _comparable(
        window['flows'], ('direction', 'counterparty', 'amount', 'label', 'rule')
    ) == _comparable(
        issuance_fixture['flows'],
        ('direction', 'counterparty', 'amount', 'label', 'rule'),
    )
    premium = [e for e in window['events'] if e['name'] == 'PremiumSold']
    assert len(premium) == 1
    assert premium[0]['fields_json'] == next(
        e['fields_json'] for e in issuance_fixture['events'] if e['name'] == 'PremiumSold'
    )


def test_the_log_re_derivation_check_can_fail(log_window_fixture):
    """The vacuity control for the two tests above, matching the one the state
    half already has. Corrupt one stored log's `to` topic and the re-derived
    mint recipient must stop matching -- without this, a green re-derivation is
    equally consistent with a comparison that compared nothing."""
    import copy

    corrupted = copy.deepcopy(log_window_fixture)
    mint_raw = next(
        raw for raw in corrupted['raw_responses']
        if (raw['body'].get('result') or [])
        and raw['params'][0]['topics'][1] == '0x' + '0' * 64
    )
    entry = mint_raw['body']['result'][0]
    # topics[2] of a Transfer is the recipient; flip its last nibble.
    original = entry['topics'][2]
    entry['topics'][2] = original[:-1] + ('0' if original[-1] != '0' else '1')

    window, _ = _replay(corrupted)
    keys = ('block', 'tx_hash', 'log_index', 'recipient', 'amount', 'class')
    assert _comparable(window['mints'], keys) != _comparable(
        log_window_fixture['mints'], keys
    )


# The one InverseBonded execution in chain history. Values decoded from the raw
# log independently of this codebase before the fixture was captured:
# data word 1 = 0x519891a2 = 1,368,953,250 (9 dp NET), word 2 =
# 0x6a3941b6705665b0 = 7,654,221,293,517,432,240 (18 dp USDG).
BUYBACK_TX = '0x6029006837baf9236f8565a4b0ed6512f70e35b2c22e84c8a7618b8db6e96dc4'
BUYBACK_NET_BURNED = Decimal('1.36895325')
BUYBACK_USDG_PAID = Decimal('7.65422129351743224')


def test_the_buyback_event_decodes_against_a_real_execution(buyback_fixture):
    """Round-2 test review, finding 7: `InverseBonded` had no ground-truth
    event anywhere, so scrambling `INVERSE_BONDED`'s field order left AC5's
    `repurchased` and `burned` rendering 0.00 forever -- indistinguishable
    from an epoch with no buybacks, which is the silent-zero shape the
    requirement itself warns about.

    The program has executed exactly once. That window is now committed, and
    the decode is pinned to values read off the raw log before any of this
    code touched them.
    """
    window, _ = _replay(buyback_fixture)
    events = [e for e in window['events'] if e['name'] == 'InverseBonded']
    assert len(events) == 1
    fields = events[0]['fields_json']

    assert events[0]['tx_hash'].lower() == BUYBACK_TX
    assert Decimal(fields['netBurned']) / Decimal(10) ** 9 == BUYBACK_NET_BURNED
    assert Decimal(fields['usdgPaidWad']) / Decimal(10) ** 18 == BUYBACK_USDG_PAID
    # `seller` is the indexed field, so it comes from topics[1] rather than the
    # data words -- a different decode path from the two amounts above.
    assert fields['seller'].lower().startswith('0x')
    assert len(fields['seller']) == 42

    # Independent corroboration that the field ORDER is right, not merely that
    # two numbers decoded: the same transaction's USDG outflow from the
    # Treasury is 7.654221 at 6 dp, which is `usdgPaidWad` rescaled. A swapped
    # field order would put 1.37 here and the two would stop agreeing.
    outflow = next(f for f in window['flows'] if f['direction'] == 'out')
    assert Decimal(outflow['amount']) / Decimal(10) ** 6 == (
        BUYBACK_USDG_PAID.quantize(Decimal('0.000001'))
    )


def test_the_buyback_window_re_derives_from_its_own_raw_responses(buyback_fixture):
    """The same re-derivation contract the other two windows carry."""
    window, client = _replay(buyback_fixture)
    assert len(client.served) == 9
    assert _comparable(
        window['events'], ('block', 'tx_hash', 'log_index', 'contract', 'name')
    ) == _comparable(
        buyback_fixture['events'],
        ('block', 'tx_hash', 'log_index', 'contract', 'name'),
    )
    derived = {e['name']: e['fields_json'] for e in window['events']}
    for stored in buyback_fixture['events']:
        assert derived[stored['name']] == stored['fields_json'], stored['name']
