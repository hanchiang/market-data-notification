"""The token's `Transfer` history: fetch, cursor, holder set, window counts.

The health section's base. Everything about holders, activity and burn is
derived from these rows rather than read from a page somewhere, which is what
makes the numbers reproducible from the store months later -- and what makes the
derivation checkable, since a derived balance must equal `balanceOf` on chain.

Three properties this module has to keep:

* **Windows are block intervals, never timestamps.** Logs carry no timestamp and
  none is stored: a per-transfer header read was priced at ~17M CU a month for
  one project (design review round 2). The build finds two boundary blocks by
  binary search and every window here is expressed between them.
* **The cursor is committed per fetched window**, so a section that fails after
  three of five windows resumes from the third rather than from the token's
  creation block. That matters once: the first build of a month-old token walks
  ~30M blocks.
* **The derivation is exact only for a non-rebasing supply.** That is the
  `launchpad-fixed-supply` archetype's promise, and it is spot-checked every
  build against `balanceOf` for the top holders. A mismatch fails the section
  with `holder_derivation_mismatch` rather than reporting a number nothing
  supports (design DG-5).
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from market_data_library.core.onchain.evm import EvmClient

from src.service.project_monitor.logs import LogQuery, fetch_window
from src.service.onchain.repository import OnchainRepository

logger = logging.getLogger('Onchain transfers')

ZERO_ADDRESS = '0x' + '0' * 40
# The other conventional burn sink. Not a real account -- nobody holds its key --
# so tokens sent there are gone in exactly the way a `0x0` transfer makes them
# gone, and a burn figure that counted only one of the two would understate.
DEAD_ADDRESS = '0x000000000000000000000000000000000000dead'

STREAM_TRANSFER = 'transfer'
# Bumped from 'position' on 2026-09-08 so the next build re-walks the position
# stream from the pool's creation block. The rows themselves are fine; their
# derived `owner` and `nft_token_id` were not, and the re-walk plus the upsert in
# `insert_position_events` repairs them in place. A cursor left at head would
# fetch nothing and repair nothing.
STREAM_POSITION = 'position:v2'

# How much of the history is banked at a time. Two million blocks is about two
# days on this chain (~9.9 blocks a second), so a first build banks progress
# roughly every couple of minutes and an interruption costs at most that.
RESUME_CHUNK_BLOCKS = 2_000_000

# Re-exported from the monitor's log plane rather than redefined: the ERC-20
# `Transfer` spec has one home, and a second copy is how two consumers end up
# decoding the same log differently.
from src.service.project_monitor.logs import TRANSFER  # noqa: E402


def _topic_address(address: str) -> str:
    return '0x' + '0' * 24 + address.lower().removeprefix('0x')


@dataclass
class FetchOutcome:
    """What one advance actually did, for the run notes and the ledger."""

    from_block: int
    to_block: int
    fetched: int
    inserted: int
    windows: int
    resumed: bool


async def advance_transfers(
    repository: OnchainRepository,
    client: EvmClient,
    *,
    token_entity_id: int,
    token_address: str,
    creation_block: int,
    to_block: int,
    max_window: Optional[int] = None,
) -> FetchOutcome:
    """Fetch `Transfer` logs from the cursor (or creation) to the pinned block.

    Each chunk inserts its rows and advances the cursor inside ONE transaction,
    which this function then commits itself -- the caller does not, despite what
    this said until 2026-09-08. The commit boundary is what makes the ordering
    safe: a crash anywhere before it re-fetches the whole chunk, which
    insert-or-ignore makes free, and no cursor can name blocks whose rows are
    not committed beside it.
    """
    cursor = repository.get_fetch_cursor(token_entity_id, STREAM_TRANSFER)
    from_block = creation_block if cursor is None else cursor + 1
    resumed = cursor is not None
    if from_block > to_block:
        return FetchOutcome(from_block, to_block, 0, 0, 0, resumed)

    query = LogQuery(
        name=f'transfer:{token_address[:10]}',
        addresses=[token_address],
        topics=[TRANSFER.topic0],
        spec=TRANSFER,
    )
    kwargs: Dict[str, Any] = {}
    if max_window is not None:
        kwargs['max_window'] = max_window

    fetched = inserted = windows = 0
    # Fetched, stored and COMMITTED in chunks rather than as one 30-million-block
    # call. Two things depend on this. The first build of a month-old token walks
    # its whole history, which takes tens of minutes on the public RPC; without a
    # commit inside that walk, an interruption anywhere throws all of it away and
    # the next run starts again from the creation block. And the whole history's
    # logs would otherwise be held in memory at once, which for a ZZZ-like
    # project is ~1.8M rows a month.
    #
    # The chunk is much wider than the fetcher's own window, so the fetcher keeps
    # finding the serviceable width per region (it both narrows and widens) and
    # this only decides how often the work is banked.
    for chunk_start in range(from_block, to_block + 1, RESUME_CHUNK_BLOCKS):
        chunk_end = min(chunk_start + RESUME_CHUNK_BLOCKS - 1, to_block)
        logs, raws = await fetch_window(client, query, chunk_start, chunk_end, **kwargs)
        rows = [decode_transfer_row(log) for log in logs]
        inserted += repository.insert_transfers(token_entity_id, rows)
        repository.set_fetch_cursor(token_entity_id, STREAM_TRANSFER, chunk_end)
        repository.commit()
        fetched += len(logs)
        windows += len(raws)
        logger.info(
            'transfer history for %s banked %s-%s: %s logs, %s windows',
            token_address,
            chunk_start,
            chunk_end,
            len(logs),
            len(raws),
        )

    logger.info(
        'transfer history for %s advanced %s->%s: %s logs, %s new rows, %s windows',
        token_address,
        from_block,
        to_block,
        fetched,
        inserted,
        windows,
    )
    return FetchOutcome(from_block, to_block, fetched, inserted, windows, resumed)


def decode_transfer_row(log: Dict[str, Any]) -> Dict[str, Any]:
    from market_data_library.core.onchain.evm import abi

    fields = abi.decode_log(TRANSFER, log)
    return {
        'block': int(log['blockNumber'], 16),
        'tx_hash': log['transactionHash'],
        'log_index': int(log['logIndex'], 16),
        'from_addr': str(fields['from']).lower(),
        'to_addr': str(fields['to']).lower(),
        'amount': int(fields['value']),
    }


def burn_addresses() -> Tuple[str, str]:
    return ZERO_ADDRESS, DEAD_ADDRESS


def holder_summary(
    repository: OnchainRepository, token_entity_id: int, *, top: int = 10
) -> Dict[str, Any]:
    """Holder count, the top holders by derived balance, and their share.

    Burn sinks and the zero address are excluded from the holder set: they are
    where supply goes to stop existing, and counting them as holders would put
    the largest "holder" of a token with a burn at an address nobody controls.
    """
    balances = repository.holder_balances(token_entity_id, exclude=list(burn_addresses()))
    circulating = sum(amount for _, amount in balances)
    top_rows = balances[:top]
    return {
        'holder_count': len(balances),
        'circulating_from_transfers': str(circulating),
        'top_holders': [
            {
                'address': address,
                'balance': str(amount),
                'share': None if not circulating else round(amount / circulating, 6),
            }
            for address, amount in top_rows
        ],
        'top_ten_share': (
            None
            if not circulating
            else round(sum(amount for _, amount in top_rows) / circulating, 6)
        ),
    }


async def check_derivation(
    client: EvmClient,
    *,
    token_address: str,
    block: int,
    holders: Sequence[Dict[str, Any]],
) -> Tuple[bool, List[Dict[str, Any]]]:
    """`balanceOf` at the pinned block must equal every top holder's derived
    balance.

    The one check standing between the whole health section and a silently wrong
    holder table. It is run every build, not once: the assumption it guards
    ("this token does not rebase and emits a `Transfer` for every balance
    change") can stop being true without any code changing.
    """
    from market_data_library.core.onchain.evm import abi

    if not holders:
        return True, []
    calls = [
        (token_address, abi.encode_call('balanceOf', ['address'], [holder['address']]))
        for holder in holders
    ]
    results = await client.batch_call(calls, block)
    mismatches: List[Dict[str, Any]] = []
    for holder, (data, _) in zip(holders, results):
        on_chain = int(abi.decode_single('uint256', data))
        if str(on_chain) != str(holder['balance']):
            mismatches.append(
                {
                    'address': holder['address'],
                    'derived': str(holder['balance']),
                    'on_chain': str(on_chain),
                }
            )
    return not mismatches, mismatches
