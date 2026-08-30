"""The log plane: event specs, windowed fetch, and mint classification.

Which endpoint logs go to depends on the caller, not on this module. The live
job's small, frequent window stays on the public RPC: it is unmetered, and
measured to serve 1.5M-block windows and 20 hour-wide queries with no 429 --
cheaper than the keyed endpoint's free tier, which caps `eth_getLogs` at ten
blocks per query on this chain (measured at both edges: ten succeed, eleven
are rejected) and would cost ~3,600 queries per event type for an hourly
window.

The one-shot backfill sweeping full chain history no longer uses the public
RPC, because that endpoint cannot sustain one: a real run (2026-08-30)
narrowed its window from 750,000 down to 5,859 blocks under repeated 429s,
then after ~36 minutes stopped serving JSON at all and returned a Cloudflare
"Just a moment..." interstitial (HTTP 403) -- bot protection a sustained scan
trips that a bursty live window does not. The same night the keyed Alchemy
endpoint was verified to have full archive depth (`NET.totalSupply()` served
correctly at blocks 45M, 40M, 30M, 20,076,087 and 12,299,690, with `0x` only
below the contract's deployment, never an archive error). `backfill.py` routes
its log steps there instead. `fetch_window` and `MAX_LOG_WINDOW_BLOCKS` below
are shared by both callers and narrow on whichever endpoint's own refusal they
hit -- they do not assume which one that is.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from market_data_library.core.onchain.evm import EvmClient, EvmRpcError, LogFilter, abi

from .config import MAX_LOG_WINDOW_BLOCKS, ProjectConfig

logger = logging.getLogger('Project monitor logs')

ZERO_ADDRESS = '0x' + '0' * 40

TRANSFER = abi.EventSpec(
    'Transfer',
    [('from', 'address', True), ('to', 'address', True), ('value', 'uint256', False)],
)
BOND_CREATED = abi.EventSpec(
    'BondCreated',
    [
        ('depositor', 'address', True),
        ('marketId', 'uint256', True),
        ('amountIn', 'uint256', False),
        ('payout', 'uint256', False),
        ('priceWad', 'uint256', False),
    ],
)
# Extracted from the dapp bundle by string content on 2026-08-30. These are how
# the buyback's "repurchased" and "burned" figures are obtained: they are not
# getters, and the dapp itself sums these logs for both -- setting them to the
# same number, so a buyback burns exactly what it repurchases.
INVERSE_BONDED = abi.EventSpec(
    'InverseBonded',
    [
        ('seller', 'address', True),
        ('netBurned', 'uint256', False),
        ('usdgPaidWad', 'uint256', False),
    ],
)
PREMIUM_SOLD = abi.EventSpec(
    'PremiumSold',
    [('netSold', 'uint256', False), ('usdgSweptRaw', 'uint256', False)],
)

# Mint classes. `issuance` is its own class rather than falling into `other`
# because the premium sales desk demonstrably mints (seven mints to it in one
# 400k-block window), and the requirement calls that fact load-bearing -- an
# `other` bucket would have hidden it.
MINT_REBASE = 'rebase'
MINT_BOND = 'bond'
MINT_ISSUANCE = 'issuance'
MINT_OTHER = 'other'


def _topic_address(address: str) -> str:
    return '0x' + '0' * 24 + address.lower().removeprefix('0x')


@dataclass(frozen=True)
class LogQuery:
    """One named query in the sample's log window."""

    name: str
    addresses: Sequence[str]
    topics: Sequence[Any]
    spec: abi.EventSpec


def build_log_queries(project: ProjectConfig) -> List[LogQuery]:
    """The seven queries a live window issues, plus the two buyback programs.

    Sleeve outbound transfers are collected in slice 1 although only scored in
    G5: it is one extra query now, and the series cannot be started later for a
    window that has already passed.
    """
    net = project.address('NET')
    usdg = project.address('USDG')
    treasury = project.address('treasury')
    pair = project.address('canonicalV2Pair')
    sleeve = project.address('managerSleeve')
    tax_collector = project.address('taxCollector')
    equities = [entry['erc20'] for entry in project.sleeve_equities.values()]

    return [
        LogQuery('net_mints', [net], [TRANSFER.topic0, _topic_address(ZERO_ADDRESS)], TRANSFER),
        LogQuery('usdg_in', [usdg], [TRANSFER.topic0, None, _topic_address(treasury)], TRANSFER),
        LogQuery('usdg_out', [usdg], [TRANSFER.topic0, _topic_address(treasury)], TRANSFER),
        LogQuery('bond_created', [project.address('bondDepository')], [BOND_CREATED.topic0], BOND_CREATED),
        LogQuery('net_from_pair', [net], [TRANSFER.topic0, _topic_address(pair)], TRANSFER),
        # Tax-path discovery (ticket step 3): the collector is the levy sink the
        # registry names, and where the 500 bps tax settles is still unverified.
        LogQuery('net_tax_collector_in', [net], [TRANSFER.topic0, None, _topic_address(tax_collector)], TRANSFER),
        LogQuery('sleeve_out', equities, [TRANSFER.topic0, _topic_address(sleeve)], TRANSFER),
        LogQuery('inverse_bonded', [project.address('inverseBond')], [INVERSE_BONDED.topic0], INVERSE_BONDED),
        LogQuery('premium_sold', [project.address('premiumSeller')], [PREMIUM_SOLD.topic0], PREMIUM_SOLD),
    ]


# Below this the window is not the problem, and halving further just multiplies
# requests against an endpoint that is already struggling.
#
# Unverified for Alchemy specifically: Alchemy's own docs (alchemy.com/docs/
# reference/eth-getlogs, read 2026-08-30) put the PAID-tier `eth_getLogs`
# block-range cap at 2,000-10,000 blocks depending on chain, or unlimited on
# their named major chains -- none of which say which bucket this chain's
# node falls into, and the FREE tier is 10 blocks, well below this floor. The
# floor does not need to guess right: `_is_window_too_wide` reads the
# endpoint's own error text ('block range', 'too many', 'limit exceeded'),
# which is the same vocabulary Alchemy's docs use for its refusal, so halving
# converges on whatever Alchemy's real cap turns out to be. The one case this
# does NOT cover is a free-tier key: a true 10-block cap sits below this floor,
# so the loop would stop halving at 1000 and raise on every call rather than
# serve anything. Confirm the backfill key is not on the free tier before a
# full sweep -- this raises loudly if it is, but every log step fails instantly
# rather than making any progress.
MIN_LOG_WINDOW_BLOCKS = 1000


def _is_window_too_wide(exc: EvmRpcError) -> bool:
    """Does this JSON-RPC error mean "ask for fewer blocks"?

    Matched on the message because the endpoint returns the same generic
    `-32000` for a timeout as for other server-side faults, and a range problem
    is the one worth narrowing for rather than failing on.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ('timed out', 'timeout', 'block range', 'too many', 'limit exceeded')
    )


async def fetch_window(
    client: EvmClient,
    query: LogQuery,
    from_block: int,
    to_block: int,
    *,
    max_window: int = MAX_LOG_WINDOW_BLOCKS,
) -> tuple[List[Dict[str, Any]], List[Any]]:
    """Every log for one query over `[from_block, to_block]`, narrowing the
    window when the endpoint says it is too wide.

    The 1.5M-block figure came from measuring NET `Transfer` near head. It does
    NOT hold everywhere: a topic-filtered query from genesis over 1.5M blocks
    returns `-32000 log query timed out` on this endpoint (measured 2026-08-30
    while backfilling epoch boundaries). So the width is a starting point that
    halves on refusal, rather than a constant the caller has to get right for
    every depth -- and it halves per sub-window, so one slow region does not
    slow the whole backfill.

    Returns the logs and every raw response, so the raw bodies are stored beside
    the decoded rows (R2).
    """
    logs: List[Dict[str, Any]] = []
    raws: List[Any] = []
    start = from_block
    window = max_window
    while start <= to_block:
        end = min(start + window - 1, to_block)
        try:
            window_logs, raw = await client.get_logs(
                LogFilter(
                    from_block=start,
                    to_block=end,
                    addresses=list(query.addresses),
                    topics=list(query.topics),
                )
            )
        except EvmRpcError as exc:
            if window > MIN_LOG_WINDOW_BLOCKS and _is_window_too_wide(exc):
                window = max(MIN_LOG_WINDOW_BLOCKS, window // 2)
                logger.info(
                    'narrowing %s log window to %s blocks at %s', query.name, window, start
                )
                continue
            raise
        logs.extend(window_logs)
        raws.append(raw)
        start = end + 1
    return logs, raws


def classify_mint(recipient: str, project: ProjectConfig) -> str:
    """A mint's class is decided by who received it, and nothing else."""
    target = recipient.lower()
    if target == project.address('staking').lower():
        return MINT_REBASE
    if target == project.address('bondDepository').lower():
        return MINT_BOND
    if target == project.address('premiumSeller').lower():
        return MINT_ISSUANCE
    return MINT_OTHER


def decode_transfer(log: Dict[str, Any]) -> Dict[str, Any]:
    fields = abi.decode_log(TRANSFER, log)
    return {
        'from': fields['from'],
        'to': fields['to'],
        'value': fields['value'],
        'block': int(log['blockNumber'], 16),
        'tx_hash': log['transactionHash'],
        'log_index': int(log['logIndex'], 16),
    }


def build_mint_rows(
    project_name: str,
    project: ProjectConfig,
    mint_logs: Sequence[Dict[str, Any]],
    net_decimals: int,
) -> List[Dict[str, Any]]:
    rows = []
    for log in mint_logs:
        transfer = decode_transfer(log)
        rows.append(
            {
                'project': project_name,
                'block': transfer['block'],
                'tx_hash': transfer['tx_hash'],
                'log_index': transfer['log_index'],
                'recipient': transfer['to'],
                'amount': transfer['value'],
                'decimals': net_decimals,
                'class': classify_mint(transfer['to'], project),
            }
        )
    return rows


def rebase_boundaries(
    project: ProjectConfig, mint_rows: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Each rebase mint is one epoch transition, at an exact block.

    This is why backfill derives epoch boundaries from logs rather than binary
    searching block timestamps against `epoch().end`: the search costs ~25 block
    reads per epoch and lands *near* the boundary, where a rebase log *is* the
    boundary. Measured at the 126->127 transition: the mint landed within
    seconds of `end` and the new `end` was exactly +28,800.
    """
    return [
        {'first_block': row['block'], 'rebase_tx': row['tx_hash']}
        for row in mint_rows
        if row['class'] == MINT_REBASE
    ]


def extract_event_rows(
    project_name: str,
    contract: str,
    name: str,
    spec: abi.EventSpec,
    logs: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    for log in logs:
        fields = abi.decode_log(spec, log)
        rows.append(
            {
                'project': project_name,
                'block': int(log['blockNumber'], 16),
                'tx_hash': log['transactionHash'],
                'log_index': int(log['logIndex'], 16),
                'contract': contract,
                'name': name,
                'fields_json': {k: str(v) for k, v in fields.items()},
            }
        )
    return rows


async def bond_created_in_bytecode(
    client: EvmClient, bond_depository: str, block: int
) -> bool:
    """Is `BondCreated` actually emitted by the deployed contract?

    Deterministic and cheap: the compiler emits an event's topic0 as a `PUSH32`
    constant before the `LOG` opcode, so its presence in the deployed bytecode
    settles whether R6's rule-1 path exists or the same-transaction correlation
    fallback (rule 1b) has to carry bond attribution.

    Measured 2026-08-30 on the live deployment: present. Two control signatures
    checked the same way came back absent, so the check is not trivially true of
    any 32-byte string.
    """
    code, _ = await client.get_code(bond_depository, block)
    return BOND_CREATED.topic0.removeprefix('0x').lower() in code.lower()
