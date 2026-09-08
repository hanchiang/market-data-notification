"""Run-level chain work: which endpoint serves what, the pinned block, and the
24-hour boundary every paired metric is measured over.

Three things live here rather than in a collector, because all four collectors
share them and a second copy would be a second answer:

* **Endpoint roles.** State reads go to the keyed archive endpoint under its
  compute-unit budget; log windows go to the public RPC under its request
  budget. `--test_mode 1` routes state to the public endpoint too, so a manual
  run spends nothing on the metered account (the monitor's rule, same reasoning).
* **One pinned block per run**, not per project. Sections of different projects
  are only comparable if they were read at the same height, and a build that
  pinned per project would silently compare a project read at head with one read
  a minute later.
* **The 24-hour boundary by binary search.** Logs carry no timestamp, so the
  trailing-24h window is a BLOCK INTERVAL, found once per run by searching block
  headers. ~25 header reads for the whole run, against ~855k blocks a day on
  this chain; reading a timestamp per transfer instead was measured at ~17M CU a
  month for one project (design review round 2).
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from market_data_library.core.onchain.evm import (
    EvmClient,
    alchemy_budget,
    public_rpc_budget,
)

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain.config import (
    ChainConstants,
    get_archive_endpoint,
    get_public_endpoint,
)

logger = logging.getLogger('Onchain chain')

SECONDS_PER_DAY = 24 * 60 * 60

# How far the binary search may wander before it gives up. A chain producing ~10
# blocks a second puts a day at ~855k blocks; 64 halvings covers any interval a
# 64-bit block number can express, so hitting this bound means the timestamps are
# not monotonic and the answer would be meaningless anyway.
MAX_SEARCH_STEPS = 64


@dataclass(frozen=True)
class EndpointRole:
    """One endpoint with the budget it must be driven under."""

    endpoint: Any
    budget: Any

    def client(self) -> EvmClient:
        return EvmClient(self.endpoint, self.budget)


def state_role(runtime_mode: RuntimeMode) -> EndpointRole:
    """Where state reads go: the archive endpoint, unless this is a test run or
    no archive key is configured."""
    archive = get_archive_endpoint()
    if archive is None or runtime_mode.is_test_mode:
        # `supports_batch=False`: the public node is sent unbatched until
        # batching on it is measured (the monitor's finding, not re-tested here).
        return EndpointRole(get_public_endpoint(supports_batch=False), public_rpc_budget())
    return EndpointRole(archive, alchemy_budget())


def log_role() -> EndpointRole:
    """Where log windows go: always the public RPC. The free archive tier
    refuses `eth_getLogs` beyond a ten-block range, two orders below the
    narrowest window the fetcher will ask for."""
    return EndpointRole(get_public_endpoint(supports_batch=True), public_rpc_budget())


@dataclass(frozen=True)
class PinnedBlock:
    """The height every read in this run is taken at, and the window start."""

    block: int
    timestamp: int
    window_start_block: int
    window_start_timestamp: int
    header_reads: int


async def pin_run_block(client: EvmClient) -> Tuple[int, int, Any]:
    """Head and its timestamp, from the SAME endpoint that will serve the reads.

    Pinning on one endpoint and reading on another can name a block the second
    has not seen yet, which reads as an empty result rather than as an error.
    """
    head, _ = await client.block_number()
    header, raw = await client.get_block_by_number(head)
    return head, int(header['timestamp'], 16), raw


async def find_block_at_or_before(
    client: EvmClient,
    target_timestamp: int,
    *,
    high_block: int,
    high_timestamp: int,
    constants: ChainConstants,
    low_block: int = 0,
) -> Tuple[int, int, int]:
    """The highest block whose timestamp is <= `target_timestamp`.

    Returns `(block, timestamp, header_reads)`. Seeded from the chain's measured
    block rate so the search starts near the answer instead of at genesis: on a
    ~10 blocks/second chain a day is ~855k blocks, and bisecting the whole
    40M-block history for that would cost twice as many header reads for the
    same answer.

    Clamped at `low_block` (genesis by default, or a token's creation block when
    the caller knows one): a chain younger than the window has no such block, and
    returning its earliest is the honest answer -- the window is then "everything
    so far", which is what a token minted yesterday should measure over.
    """
    reads = 0
    delta_seconds = max(0, high_timestamp - target_timestamp)
    estimate = high_block - int(delta_seconds * constants.blocks_per_second)
    low = max(low_block, 0)
    high = high_block

    if estimate <= low:
        header, _ = await client.get_block_by_number(low)
        return low, int(header['timestamp'], 16), reads + 1

    # Bracket around the estimate rather than trusting it, because the recorded
    # block rate is a measurement and a chain that has drifted would otherwise
    # put the answer outside the range being searched. The bracket is bounded on
    # BOTH sides by the estimate: an exact rate makes the probe the answer and
    # the search collapses, and a rate wrong by up to 2x is still bracketed
    # inside twice the estimated span -- so the search costs ~20 header reads
    # over a day's blocks and never bisects the whole chain history.
    span = max(1, high_block - estimate)
    probe = max(low, min(high, estimate))
    header, _ = await client.get_block_by_number(probe)
    reads += 1
    probe_timestamp = int(header['timestamp'], 16)
    if probe_timestamp <= target_timestamp:
        low = probe
    else:
        high = probe
        low = max(low, probe - 2 * span)

    steps = 0
    best_block, best_timestamp = low, None
    while low <= high and steps < MAX_SEARCH_STEPS:
        steps += 1
        middle = (low + high) // 2
        header, _ = await client.get_block_by_number(middle)
        reads += 1
        middle_timestamp = int(header['timestamp'], 16)
        if middle_timestamp <= target_timestamp:
            best_block, best_timestamp = middle, middle_timestamp
            low = middle + 1
        else:
            high = middle - 1

    if best_timestamp is None:
        header, _ = await client.get_block_by_number(best_block)
        reads += 1
        best_timestamp = int(header['timestamp'], 16)
    return best_block, best_timestamp, reads


async def pin_block_and_window(
    client: EvmClient, constants: ChainConstants
) -> PinnedBlock:
    """The run's pinned block plus the block 24 hours before it."""
    head, head_timestamp, _ = await pin_run_block(client)
    start_block, start_timestamp, reads = await find_block_at_or_before(
        client,
        head_timestamp - SECONDS_PER_DAY,
        high_block=head,
        high_timestamp=head_timestamp,
        constants=constants,
    )
    logger.info(
        'pinned block %s (ts %s); 24h window starts at block %s (ts %s) '
        'after %s header reads',
        head,
        head_timestamp,
        start_block,
        start_timestamp,
        reads,
    )
    return PinnedBlock(
        block=head,
        timestamp=head_timestamp,
        window_start_block=start_block,
        window_start_timestamp=start_timestamp,
        header_reads=reads + 1,
    )


def decode_string(data: str) -> Optional[str]:
    """An ABI-encoded `string` return value, or a `bytes32` one.

    The library's decoder handles static types only, and `name()`/`symbol()` are
    dynamic. Both encodings are in the wild: OpenZeppelin returns a real string,
    and some older tokens return a right-padded `bytes32`. Returns None when the
    payload is neither, so the caller records `unavailable` rather than a
    mojibake symbol.
    """
    raw = bytes.fromhex((data or '').removeprefix('0x'))
    if not raw:
        return None
    if len(raw) >= 64:
        try:
            offset = int.from_bytes(raw[0:32], 'big')
            if offset == 32 and len(raw) >= 64:
                length = int.from_bytes(raw[32:64], 'big')
                if 0 <= length <= len(raw) - 64:
                    return raw[64:64 + length].decode('utf-8', errors='replace')
        except ValueError:  # pragma: no cover - int.from_bytes cannot raise here
            pass
    if len(raw) == 32:
        return raw.rstrip(b'\x00').decode('utf-8', errors='replace') or None
    return None


def spend_counters() -> Dict[str, Dict[str, int]]:
    """The empty `spend_json` shape a run starts from.

    Counted per endpoint kind rather than per call site: the ledger's job is to
    say what the run cost on each metered account, and a per-call-site breakdown
    would be a profiler, not a budget.
    """
    return {'requests': {}, 'compute_units': {}}


def add_spend(spend: Dict[str, Dict[str, int]], kind: str, requests: int, units: int = 0) -> None:
    spend.setdefault('requests', {})
    spend.setdefault('compute_units', {})
    spend['requests'][kind] = spend['requests'].get(kind, 0) + requests
    if units:
        spend['compute_units'][kind] = spend['compute_units'].get(kind, 0) + units
