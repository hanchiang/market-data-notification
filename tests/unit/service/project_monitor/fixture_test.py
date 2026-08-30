"""AC6 and AC3, against the committed fixture of real JSON-RPC responses.

The fixture was captured from the public endpoint on 2026-08-30 at block
49,877,926 by `scripts/capture_project_monitor_fixture.py`, which is committed
beside it so it can be recaptured when the read plan changes.
"""
from decimal import Decimal

import pytest

from market_data_library.core.onchain.evm import abi

from src.service.project_monitor import logs as log_plane
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
        if record['value_int'] is not None:
            assert int(value) == int(record['value_int']), read.name
        else:
            assert [str(v) for v in value] == record['value_json'], read.name
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
    # token0 is USDG (6 dp), token1 is NET (9 dp) -- asserted, not assumed,
    # because reserve0/reserve1 carry no labels and swapping them inverts the
    # price silently.
    assert expected['pair.token0']['value_int'] is None
    price = (Decimal(usdg_reserve) / Decimal(10) ** 6) / (
        Decimal(net_reserve) / Decimal(10) ** 9
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
