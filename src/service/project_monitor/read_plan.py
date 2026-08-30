"""The ordered list of state reads a sample issues, in batch order.

One place, so the fixture capture script, the recorder and backfill all read the
same plan (AC6). Each entry carries the contract it reads, the calldata, the
type to decode the result as, the decimals to record beside the value, and
whether a failure there fails the whole sample (R8's core/peripheral split).

Every read, `decimals()` included, is issued at the pinned block in every
sample: R5 says the sample stores the decimals it read, and AC3 says the sample
reproduces from its own responses. A cache would break both to save 364 CU.
"""
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from market_data_library.core.onchain.evm import abi

from .config import LOOPBACK_MARKET_PARAMS, ProjectConfig

# Batch 1-2 plus the log window are CORE: a failure there rolls back the sample.
# Batches 3-5 are PERIPHERAL: a failed read records `state = 'failed'` with its
# error class and the sample commits without it. The reason for the split is
# that a dead third-party price feed must not blank the treasury series.
CORE = 'core'
PERIPHERAL = 'peripheral'

MARKET_PARAMS_TYPE = ('address', 'address', 'address', 'address', 'uint256')
MARKET_TYPE = ('uint128',) * 6
EPOCH_TYPE = ('uint64', 'uint64', 'uint64', 'uint256')
RESERVES_TYPE = ('uint112', 'uint112', 'uint32')
ROUND_DATA_TYPE = ('uint80', 'int256', 'uint256', 'uint256', 'uint80')


@dataclass(frozen=True)
class Read:
    """One `eth_call` in a sample.

    `contract` is the registry name, not the address, so a read is attributable
    to a contract whose address may change between manifest snapshots.
    """

    name: str
    contract: str
    to: str
    calldata: str
    result_type: Any
    tier: str
    batch: int
    # Which reading holds this value's decimals, where the value is a token
    # amount. `None` means the value is not denominated in a token.
    decimals_from: Optional[str] = None


def _erc20_reads(
    name_prefix: str, contract: str, address: str, tier: str, batch: int
) -> List[Read]:
    return [
        Read(
            f'{name_prefix}.totalSupply',
            contract,
            address,
            abi.encode_call('totalSupply'),
            'uint256',
            tier,
            batch,
            decimals_from=f'{name_prefix}.decimals',
        ),
        Read(
            f'{name_prefix}.decimals',
            contract,
            address,
            abi.encode_call('decimals'),
            'uint8',
            tier,
            batch,
        ),
    ]


def build_read_plan(project: ProjectConfig) -> List[Read]:
    """Every state read in a sample, in the order it is issued."""
    net = project.address('NET')
    usdg = project.address('USDG')
    treasury = project.address('treasury')
    pair = project.address('canonicalV2Pair')
    staking = project.address('staking')
    sleeve = project.address('managerSleeve')
    inverse_bond = project.address('inverseBond')
    premium_seller = project.address('premiumSeller')
    morpho = project.address('morphoBlue')

    reads: List[Read] = []

    # -- Batch 1: core token and treasury state -------------------------
    reads += _erc20_reads('NET', 'NET', net, CORE, 1)
    reads += [
        Read(
            'Treasury.rfv', 'treasury', treasury,
            abi.encode_call('rfv'), 'uint256', CORE, 1, decimals_from='const:18',
        ),
        Read(
            'Treasury.backingPerToken', 'treasury', treasury,
            abi.encode_call('backingPerToken'), 'uint256', CORE, 1,
            decimals_from='const:18',
        ),
        # The three components rfv() publishes, recorded so a movement is
        # attributable to a component rather than to "rfv moved". Measured
        # 2026-08-30: they do NOT sum to rfv (see report.py's residual note).
        Read(
            'Treasury.liquidUsdg', 'treasury', treasury,
            abi.encode_call('liquidUsdg'), 'uint256', CORE, 1,
            decimals_from='const:18',
        ),
        Read(
            'Treasury.morphoAssets', 'treasury', treasury,
            abi.encode_call('morphoAssets'), 'uint256', CORE, 1,
            decimals_from='const:18',
        ),
        Read(
            'Treasury.polRfv', 'treasury', treasury,
            abi.encode_call('polRfv'), 'uint256', CORE, 1, decimals_from='const:18',
        ),
        Read(
            'USDG.balanceOf(Treasury)', 'USDG', usdg,
            abi.encode_call('balanceOf', ['address'], [treasury]), 'uint256', CORE, 1,
            decimals_from='USDG.decimals',
        ),
        Read('USDG.decimals', 'USDG', usdg, abi.encode_call('decimals'), 'uint8', CORE, 1),
        Read(
            'Staking.epoch', 'staking', staking,
            abi.encode_call('epoch'), EPOCH_TYPE, CORE, 1,
        ),
    ]

    # -- Batch 2: pair and LP custody -----------------------------------
    reads += [
        Read('pair.getReserves', 'canonicalV2Pair', pair,
             abi.encode_call('getReserves'), RESERVES_TYPE, CORE, 2),
        Read('pair.token0', 'canonicalV2Pair', pair,
             abi.encode_call('token0'), 'address', CORE, 2),
        Read('pair.token1', 'canonicalV2Pair', pair,
             abi.encode_call('token1'), 'address', CORE, 2),
        Read('pair.totalSupply', 'canonicalV2Pair', pair,
             abi.encode_call('totalSupply'), 'uint256', CORE, 2,
             decimals_from='pair.decimals'),
        Read('pair.balanceOf(Treasury)', 'canonicalV2Pair', pair,
             abi.encode_call('balanceOf', ['address'], [treasury]), 'uint256', CORE, 2,
             decimals_from='pair.decimals'),
        Read('pair.decimals', 'canonicalV2Pair', pair,
             abi.encode_call('decimals'), 'uint8', CORE, 2),
    ]

    # -- Batch 3: buyback, premium desk, Loopback (peripheral) ----------
    #
    # The four figures AC5 names for the buyback -- capacity, filled,
    # repurchased, burned -- do not all exist as getters. Settled 2026-08-30 by
    # extracting the two ABIs from the bundle by string content:
    #   capacity   -> `capacityRemaining()`, a real getter (and measured equal
    #                 to liquidUsdg x 100bps, which is the rule the dapp's own
    #                 mock derives it by)
    #   repurchased/burned -> NOT getters. The dapp sums `InverseBonded` logs
    #                 for both, and sets them to the same figure, so a buyback
    #                 burns exactly what it repurchases. Collected on the log
    #                 plane instead (see logs.py).
    #   filled     -> no on-chain source at all; the dapp hardcodes 0 in its
    #                 live path. Reported as unavailable, not derived.
    reads += [
        Read('inverseBond.active', 'inverseBond', inverse_bond,
             abi.encode_call('active'), 'bool', PERIPHERAL, 3),
        Read('inverseBond.capacityRemaining', 'inverseBond', inverse_bond,
             abi.encode_call('capacityRemaining'), 'uint256', PERIPHERAL, 3,
             decimals_from='const:18'),
        Read('inverseBond.price', 'inverseBond', inverse_bond,
             abi.encode_call('price'), 'uint256', PERIPHERAL, 3,
             decimals_from='const:18'),
        Read('inverseBond.spreadBps', 'inverseBond', inverse_bond,
             abi.encode_call('spreadBps'), 'uint256', PERIPHERAL, 3),
        Read('premiumSeller.active', 'premiumSeller', premium_seller,
             abi.encode_call('active'), 'bool', PERIPHERAL, 3),
        Read('premiumSeller.clipSize', 'premiumSeller', premium_seller,
             abi.encode_call('clipSize'), 'uint256', PERIPHERAL, 3,
             decimals_from='NET.decimals'),
        Read('premiumSeller.lastExecuteAt', 'premiumSeller', premium_seller,
             abi.encode_call('lastExecuteAt'), 'uint64', PERIPHERAL, 3),
        Read('premiumSeller.premiumThresholdWad', 'premiumSeller', premium_seller,
             abi.encode_call('premiumThresholdWad'), 'uint256', PERIPHERAL, 3,
             decimals_from='const:18'),
        Read('Morpho.market', 'morphoBlue', morpho,
             abi.encode_call('market', ['bytes32'], [loopback_market_id()]),
             MARKET_TYPE, PERIPHERAL, 3),
    ]

    # -- Batch 4: Sleeve balances (peripheral) --------------------------
    for symbol, entry in project.sleeve_equities.items():
        reads += [
            Read(f'Sleeve.{symbol}.balanceOf', f'{symbol}_token', entry['erc20'],
                 abi.encode_call('balanceOf', ['address'], [sleeve]), 'uint256',
                 PERIPHERAL, 4, decimals_from=f'Sleeve.{symbol}.decimals'),
            Read(f'Sleeve.{symbol}.decimals', f'{symbol}_token', entry['erc20'],
                 abi.encode_call('decimals'), 'uint8', PERIPHERAL, 4),
        ]

    # -- Batch 5: Chainlink marks (peripheral) --------------------------
    for symbol, entry in project.sleeve_equities.items():
        reads += [
            Read(f'Mark.{symbol}.latestRoundData', f'{symbol}_feed', entry['feed'],
                 abi.encode_call('latestRoundData'), ROUND_DATA_TYPE, PERIPHERAL, 5),
            Read(f'Mark.{symbol}.decimals', f'{symbol}_feed', entry['feed'],
                 abi.encode_call('decimals'), 'uint8', PERIPHERAL, 5),
        ]

    # `IIrm.borrowRateView` needs the Market struct this same sample reads, so
    # it cannot be encoded until `Morpho.market` has come back. It is issued as
    # a follow-up read by the recorder rather than sitting in this static plan.
    return reads


def loopback_market_id() -> str:
    """`keccak256(abi.encode(marketParams))`, per Morpho Blue.

    Single-sourced from the dapp bundle and not cross-checked against the
    facility's own `marketId()`, because slice 1 never found that contract's
    address. See the note on `LOOPBACK_MARKET_PARAMS` in `config.py` for what
    that leaves unverified.
    """
    encoded = abi.encode(
        MARKET_PARAMS_TYPE,
        [
            LOOPBACK_MARKET_PARAMS['loanToken'],
            LOOPBACK_MARKET_PARAMS['collateralToken'],
            LOOPBACK_MARKET_PARAMS['oracle'],
            LOOPBACK_MARKET_PARAMS['irm'],
            LOOPBACK_MARKET_PARAMS['lltv'],
        ],
    )
    from market_data_library.core.onchain.evm import keccak256

    return '0x' + keccak256(encoded).hex()


def borrow_rate_view_calldata(market_values: Sequence[int]) -> str:
    """Calldata for `borrowRateView(MarketParams, Market)` given this sample's
    Market tuple. Both structs are static, so both encode inline."""
    return abi.encode_call(
        'borrowRateView',
        [MARKET_PARAMS_TYPE, MARKET_TYPE],
        [
            (
                LOOPBACK_MARKET_PARAMS['loanToken'],
                LOOPBACK_MARKET_PARAMS['collateralToken'],
                LOOPBACK_MARKET_PARAMS['oracle'],
                LOOPBACK_MARKET_PARAMS['irm'],
                LOOPBACK_MARKET_PARAMS['lltv'],
            ),
            tuple(market_values),
        ],
    )


def split_by_tier(reads: Sequence[Read]) -> Tuple[List[Read], List[Read]]:
    core = [r for r in reads if r.tier == CORE]
    peripheral = [r for r in reads if r.tier == PERIPHERAL]
    return core, peripheral
