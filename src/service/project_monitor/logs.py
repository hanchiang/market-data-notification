"""The log plane: event specs, windowed fetch, and mint classification.

Every log query in this service -- live job and one-shot backfill alike --
goes to the PUBLIC RPC. The keyed Alchemy endpoint cannot serve one: the
operator's key is on the free tier, which refuses `eth_getLogs` for any range
wider than ten blocks. Measured 2026-08-31 with four probes at spans
1,000,000 / 100,000 / 10,000 / 2,000, all anchored at block 1: every one came
back HTTP 400, JSON-RPC -32600, "Under the Free tier plan, you can make
eth_getLogs requests with up to a 10 block range." Ten blocks is two orders
below `MIN_LOG_WINDOW_BLOCKS`, so a log step routed there fails on its first
call and every call after it. The keyed endpoint's ARCHIVE DEPTH is real and
unaffected -- `backfill.py` still reads historical state through it -- but
depth and log service are separate capabilities on this key.

The public endpoint's own refusal is NOT a block cap. It is driven by how much
the node must scan, so the serviceable window depends on where you are in the
chain, not on a number. Measured near head 2026-08-31 with the NET address
filter: 750,000 / 200,000 / 100,000 / 50,000 blocks all refused with -32000
"log query timed out" in a flat ~2.3s (a canned refusal, not a real timeout),
while 25,000 returned 1,357 logs in 0.6s, 10,000 returned 610, 5,000 returned
153 and 1,000 returned 32. Sparse early history accepts far wider windows than
the busy head does.

That is why `fetch_window` below both narrows AND widens. A window that only
ever narrowed would let one dense region near the head permanently cap the
sparse 40M blocks behind it -- which is what the 2026-08-30 sweep did, walking
750,000 down to 5,859 and staying there.
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
# requests against an endpoint that is already struggling. 1,000 blocks was
# measured to return in well under a second even at the busy head (32 logs), so
# a refusal at this width is an endpoint fault, not a width fault.
MIN_LOG_WINDOW_BLOCKS = 1000

# --- Widening after a run of successes -------------------------------------
#
# WIDEN_AFTER_SUCCESSES: a widening probe that guesses wrong costs exactly one
# refused call, and a refusal is cheap (~2.3s, no work done). The worst-case
# cycle is four successes plus one refused probe, so probes are at most one call
# in five -- 20% of calls -- and only in a region sitting exactly at its serving
# limit with the ceiling below expired. It is far cheaper than that in practice,
# because CEILING_HOLD suppresses repeat probes. Recovery is still quick:
# climbing the 2026-08-30 run's ratchet back, 5,859 -> 750,000, is seven
# doublings, so 28 successful calls.
WIDEN_AFTER_SUCCESSES = 4

# WIDEN_GROWTH exactly inverts the halving, which keeps every window size on
# the same power-of-two lattice the narrowing produces. That is what makes
# "never retry a size already refused" an exact property rather than an
# approximate one: a 1.5x growth would land just *under* a refused size, re-trip
# the same refusal, and look like a new measurement while being the old one.
WIDEN_GROWTH = 2

# CEILING_HOLD_MULTIPLE: how far the sweep must advance past a refusal before
# that refusal stops constraining the window. Ticket scope item 3 -- a size
# refused in this sweep is not retried "without evidence the density changed" --
# and the only evidence available mid-sweep is position: a refusal describes the
# blocks it was issued over, and says nothing about blocks far beyond them.
#
# ONLY THE MOST RECENT REFUSAL'S HOLD BINDS. There is one ceiling, not one per
# refused width, so each refusal in a halving cascade overwrites the one before
# it -- and the narrowest refusal, which is the last, carries the SHORTEST hold.
# A wider width from earlier in the same cascade can therefore be re-probed
# before its own nominal hold would have expired. That is deliberate rather than
# overlooked: the earlier refusals in a cascade were all issued at the same
# block, so they are one measurement of one region, and re-probing costs a
# single ~2.3s refused call which immediately reinstalls the ceiling. Tracking a
# hold per width would buy a fraction of one call per region and a second piece
# of state to keep correct.
#
# UNMEASURED. We have point measurements near the head, none about how far a
# dense region extends. 8x the refused width is a judgement, and here is its
# arithmetic at the one measurement we do have (50,000 refused, 25,000 served
# near head): the ceiling holds for 400,000 blocks, which at 25,000 a call is
# 16 calls, so a persistently dense region pays about one wasted probe per 16
# calls (~6%). A region that has genuinely thinned waits at most those 400,000
# blocks -- under 1% of the ~50M range -- before the window starts climbing.
CEILING_HOLD_MULTIPLE = 8


# --- Silent truncation ------------------------------------------------------
#
# An endpoint that caps a result set and returns 200 with a short list carries
# no truncation flag, so the narrowing above -- which fires only on an ERROR --
# never sees it, and `backfill.py`'s sweep then commits its per-segment
# watermark past a range it only partly read. Nothing revisits it.
#
# An exactly-round count is the only signal available, and the asymmetry sets
# the policy: a false positive costs re-reading that span in two or more
# narrower calls, while a false negative loses those logs for good. These are
# the common provider caps, not values observed on this endpoint -- a guard,
# not a measurement.
SUSPECTED_RESULT_CAPS = frozenset({1_000, 2_000, 5_000, 10_000})


class TruncatedLogResponseError(RuntimeError):
    """A window hit a suspected cap at MIN_LOG_WINDOW_BLOCKS, where it cannot
    be re-read any narrower. Raised rather than accepted: near the busy head
    1,000 blocks measured 32 logs, so a round 1,000 at that width is far more
    likely a silent cap than a real count."""


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
    window where the endpoint refuses it and widening it back where it does not.

    `max_window` is a starting point, never a constant the caller has to get
    right: the serviceable width varies by an order of magnitude between sparse
    early history and the busy head (see the module docstring's measurements),
    so the loop finds it per region instead. Halving on refusal is the old
    behaviour and is unchanged. Widening is the new half, and without it the
    narrowing is a one-way ratchet: the 2026-08-30 sweep hit one dense region,
    walked 750,000 down to 5,859, and then paid that width for the remaining
    40M sparse blocks.

    Widening is bounded by a ceiling so it cannot re-trip the same refusal in a
    loop. The ceiling is positional rather than permanent -- a refusal is
    evidence about the blocks it was issued over -- because a permanent one
    would simply be the ratchet again, one level shallower. It is also singular:
    the latest refusal's hold is the one that binds.

    Returns the logs and every raw response, so the raw bodies are stored beside
    the decoded rows (R2).
    """
    logs: List[Dict[str, Any]] = []
    raws: List[Any] = []
    start = from_block
    window = max_window
    # The largest width we are currently willing to ask for. It drops to the
    # post-halving width on every refusal -- strictly below the width just
    # refused -- so while a ceiling stands, nothing at or above the width that
    # installed it is re-issued. One ceiling, not one per refused width: see
    # CEILING_HOLD_MULTIPLE for why an earlier, wider refusal in the same
    # cascade can be re-probed before its own nominal hold elapses.
    ceiling = max_window
    # The block past which the standing ceiling no longer describes where we
    # are. Initialised to `from_block` so the very first widening (no refusal
    # has happened yet) is not held back by a ceiling that does not exist.
    ceiling_holds_until = from_block
    successes = 0
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
                refused_width = end - start + 1
                window = max(MIN_LOG_WINDOW_BLOCKS, window // 2)
                ceiling = window
                ceiling_holds_until = start + CEILING_HOLD_MULTIPLE * refused_width
                successes = 0
                logger.info(
                    'narrowing %s log window to %s blocks at %s', query.name, window, start
                )
                continue
            raise
        if len(window_logs) in SUSPECTED_RESULT_CAPS:
            # Treated exactly like a refusal at this width, ceiling included:
            # the call did return, but not provably all of it. The body is
            # DROPPED rather than stored -- the only `eth_getLogs` body
            # discarded while the sweep CONTINUES, against the 2026-08-30
            # ruling that the backfill persists each response verbatim
            # (`kb/decisions.md`). Scoped to `eth_getLogs` deliberately:
            # `get_code` and `block_number` bodies are discarded at their call
            # sites too, and the ruling never covered them. A truncated
            # body under a span-keyed table would poison exactly the
            # re-derivation R2 wants it for. The info line below is then the
            # only record that the cap event happened.
            # Halved off the ISSUED width, not off `window`. `end` is clipped
            # to `to_block` on the first call of every backfill segment, so
            # halving `window` would re-issue a byte-identical request and get
            # the identical count back, twice, before the width actually moved.
            # The refusal path above still halves `window` and still pays that
            # cost -- measured at 12 identical refused calls on a 1-block span.
            # It costs only cheap canned refusals there, never a duplicated
            # body, which is why this side was fixed first and not both.
            capped_width = end - start + 1
            if capped_width <= MIN_LOG_WINDOW_BLOCKS:
                raise TruncatedLogResponseError(
                    f'{query.name} returned exactly {len(window_logs)} logs over '
                    f'blocks {start}-{end} at the narrowest window'
                )
            window = max(MIN_LOG_WINDOW_BLOCKS, capped_width // 2)
            ceiling = window
            ceiling_holds_until = start + CEILING_HOLD_MULTIPLE * capped_width
            successes = 0
            logger.info(
                'narrowing %s log window to %s blocks at %s: exactly %s logs '
                'looks like a silent result cap',
                query.name,
                window,
                start,
                len(window_logs),
            )
            continue
        logs.extend(window_logs)
        raws.append(raw)
        start = end + 1
        successes += 1
        if successes >= WIDEN_AFTER_SUCCESSES:
            successes = 0
            if start > ceiling_holds_until:
                # We are past the blocks the refusal was issued over, so it is
                # evidence about a region we have left rather than about this
                # one. Release the ceiling and let the window climb again.
                ceiling = max_window
            widened = min(max_window, ceiling, window * WIDEN_GROWTH)
            if widened > window:
                window = widened
                logger.info(
                    'widening %s log window to %s blocks at %s', query.name, window, start
                )
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
