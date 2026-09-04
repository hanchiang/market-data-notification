"""The AC5 per-epoch table, computed only from what is stored.

One row per observed epoch number. The closing sample for an epoch is the last
committed sample that observed that number; a backfill sample counts, because a
late-filled row is still an observation of that epoch's state.

Two header notes are not decoration:

- **"fees unattributed"** stands until ticket step 3 identifies where the 500 bps
  trading tax settles. Measured: no NET moved from the pair to the Treasury and
  no NET was burned in 100k blocks, and no USDG sender is visibly a converter. A
  `fee` column would be an empty promise until that is answered.
- **"Sleeve total is not in rfv"** stands always. The project's own custody memo
  says the Sleeve is team-custodied, is not part of on-chain RFV, and is never
  summed into it. Presenting it as a column beside rfv without that line is how
  someone adds them.

A third note is a measurement, not a policy: the three components overshoot
`rfv()` by exactly 2% of `morphoAssets`, because `rfv()` credits the Morpho
vault position at 98%. That is a property of the contract, not a modelling gap
-- an earlier version of this note called the 56,490.24 overshoot at block
49,867,666 unexplained, and it is 2% of `morphoAssets` at that block to the
cent. `rfv_identity.py` owns the coefficient and the check on it.

The residual models the one consequence that is predictable ahead of time. A
deposit into the vault moves USDG from a component credited at 100% to one
credited at 98%, so `rfv()` rises by 2% less than the amount moved even though
nothing left the treasury; the residual subtracts that haircut from a booked
vault flow. It does NOT predict the other two terms the residual carries --
vault interest and `delta polRfv`. Neither is observable as a flow, so the only
way to predict them is to read the same epoch's realised component deltas, which
would drive the residual to zero in every epoch by construction and swallow
exactly what the column exists to surface: a polRfv drain or a vault re-mark
would land inside the predicted terms and disappear.
"""
import json
from bisect import bisect_right
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .attribution import (
    BOUNDARY_INTERNAL,
    LABEL_UNLABELLED,
    is_morpho_vault,
    rfv_boundary,
)
from .config import ProjectConfig
from .repository import ProjectMonitorRepository
from .rfv_identity import (
    IDENTITY_BROKEN,
    IDENTITY_EXPRESSION,
    IDENTITY_READINGS,
    MORPHO_DEPOSIT_HAIRCUT,
    check_rfv_identity,
)

MISSING = None
SECONDS_PER_YEAR = 365 * 24 * 3600
WAD = Decimal(10) ** 18

# The vault's per-epoch yield, in parts per million of the position it opened
# on, measured over the 69 quiet epochs of the backfill: min 23.5, median 28.5,
# max 42.3. Deposit epochs run wider (16.2 to 46.6) because the deposit lands
# mid-window, so the accrual is not earned on the opening position throughout.
INTEREST_PPM_OBSERVED = (Decimal('23.5'), Decimal('42.3'))
# The band a rate must leave before it is called signal. Deliberately about 1.6x
# below and 2x above the widest rate any of the 133 epochs has shown, because a
# yield that floats with the market will move within a factor of two and crying
# wolf on that would train the operator to ignore the column. What it still
# catches immediately is the case the check exists for: a vault re-mark or a
# halted position, which moves the rate by orders of magnitude or below zero.
INTEREST_PPM_TOLERANCE = (Decimal('10'), Decimal('95'))

INTEREST_OK = 'ok'
INTEREST_OUTSIDE_ENVELOPE = 'outside_envelope'
INTEREST_UNAVAILABLE = 'unavailable'

SIGN_AGREE = 'agree'
SIGN_DISAGREE = 'disagree'
SIGN_UNAVAILABLE = 'unavailable'

# The record is stale once two epochs could have been missed: an epoch closes
# every eight hours, so 16 h without a newer close is the first moment a stopped
# `record` job is distinguishable from a slow one. Frozen in the requirement
# (Constraints, Operational); changing it is an amendment there, not here.
STALE_AFTER_HOURS = 16

# The only event name the epoch table reads. `_EpochSources` prefetches exactly
# these, so a name added to a column without a matching entry here raises rather
# than summing an empty window into a confident zero.
EVENT_NAMES_READ = ('InverseBonded',)


def _scaled(value: Optional[Any], decimals: Optional[int]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(value) / (Decimal(10) ** int(decimals or 0))


def _pct_change(current: Optional[Decimal], previous: Optional[Decimal]) -> Optional[Decimal]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def load_epoch_rows(
    repository: ProjectMonitorRepository, project: ProjectConfig
) -> List[Dict[str, Any]]:
    """One row per epoch, from `sample`, `reading`, `mint`, `flow` and `event`.
    Nothing here re-reads the chain.

    `epoch_boundary` is deliberately NOT read: an epoch's closing sample is the
    last sample that observed that epoch number, which the sample rows already
    say. The boundary table exists for backfill's window planning, and reading
    it here would make the report depend on a derived table when the primary one
    answers the question.
    """
    closing_samples = repository.fetch_all(
        """
        SELECT DISTINCT ON (epoch_number)
               id, block, block_timestamp, epoch_number, kind, endpoint_kind, read_at
        FROM sample
        WHERE project = %s AND epoch_number IS NOT NULL
        ORDER BY epoch_number, block DESC, id DESC
        """,
        (project.name,),
    )
    if not closing_samples:
        return []

    rows: List[Dict[str, Any]] = []
    previous: Optional[Dict[str, Any]] = None
    epoch_numbers = [int(s['epoch_number']) for s in closing_samples]

    sources = _EpochSources(repository, project, [s['id'] for s in closing_samples])

    by_epoch = {int(s['epoch_number']): s for s in closing_samples}
    # Missing epochs render as a row of dashes with the epoch number, never
    # interpolated: an epoch with no sample is a gap in the record, and drawing
    # a line through it would invent a treasury figure nobody observed.
    for epoch in range(min(epoch_numbers), max(epoch_numbers) + 1):
        sample = by_epoch.get(epoch)
        if sample is None:
            rows.append({'epoch': epoch, 'present': False})
            continue
        row = _build_row(sources, project, sample, previous)
        rows.append(row)
        previous = row
    return rows


class _EpochSources:
    """Every store row the epoch table reads, fetched in five statements.

    Replaces a per-epoch query pattern: five statements per epoch is 690 round
    trips over the operator's 138 epochs, measured at 2.5-2.6 s and
    extrapolating to about 23 s at a year of history -- past the 6 s bound the
    requirement freezes. Whole-project reads plus a bisect per window answer the
    same questions once. The event read is filtered to `EVENT_NAMES_READ`
    because the table holds 76,151 rows of which 71,680 are pair and
    tax-collector transfers no column reads.

    Each list is ordered by block and carries a parallel list of blocks, so an
    epoch's `(previous_block, block]` window is two bisections.
    """

    def __init__(
        self,
        repository: ProjectMonitorRepository,
        project: ProjectConfig,
        sample_ids: Sequence[int],
    ) -> None:
        self.readings: Dict[int, Dict[str, Any]] = {
            int(sample_id): {} for sample_id in sample_ids
        }
        for reading in repository.fetch_all(
            'SELECT sample_id, name, value_int, value_json, decimals, state, '
            'error_class FROM reading WHERE sample_id = ANY(%s)',
            (list(sample_ids),),
        ):
            self.readings[int(reading['sample_id'])][reading['name']] = reading

        self.mints, self.mint_blocks = _by_block(
            repository.fetch_all(
                'SELECT block, class, amount, decimals FROM mint '
                'WHERE project = %s ORDER BY block',
                (project.name,),
            )
        )
        self.flows, self.flow_blocks = _by_block(
            repository.fetch_all(
                'SELECT block, direction, label, counterparty, amount, decimals '
                'FROM flow WHERE project = %s ORDER BY block',
                (project.name,),
            )
        )
        self.events: Dict[str, Tuple[List[Dict[str, Any]], List[int]]] = {
            name: _by_block(
                repository.fetch_all(
                    'SELECT block, fields_json FROM event '
                    'WHERE project = %s AND name = %s ORDER BY block',
                    (project.name, name),
                )
            )
            for name in EVENT_NAMES_READ
        }


def _by_block(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[int]]:
    ordered = sorted(rows, key=lambda row: int(row['block']))
    return ordered, [int(row['block']) for row in ordered]


def _window(
    rows: List[Dict[str, Any]],
    blocks: List[int],
    from_block: Optional[int],
    to_block: int,
) -> List[Dict[str, Any]]:
    """The rows of one epoch's window, `(from_block, to_block]`.

    Half-open at the low end exactly as the SQL was: a block belongs to the
    epoch that closed on or after it, and counting it twice would double an
    epoch's emission.
    """
    low = bisect_right(blocks, from_block if from_block is not None else -1)
    return rows[low:bisect_right(blocks, to_block)]


def _group_mints(rows: List[Dict[str, Any]]) -> Dict[str, Optional[Decimal]]:
    """`GROUP BY class` with `sum(amount)`, `max(decimals)`, in Python."""
    totals: Dict[str, Decimal] = {}
    decimals: Dict[str, int] = {}
    for row in rows:
        mint_class = row['class']
        totals[mint_class] = totals.get(mint_class, Decimal(0)) + Decimal(row['amount'])
        decimals[mint_class] = max(decimals.get(mint_class, 0), int(row['decimals']))
    return {
        mint_class: _scaled(total, decimals[mint_class])
        for mint_class, total in totals.items()
    }


def _group_flows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`GROUP BY direction, label, counterparty`, same aggregates as the SQL."""
    grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (row['direction'], row['label'], row['counterparty'])
        group = grouped.get(key)
        if group is None:
            grouped[key] = {
                'direction': row['direction'],
                'label': row['label'],
                'counterparty': row['counterparty'],
                'total': Decimal(row['amount']),
                'decimals': int(row['decimals']),
            }
            continue
        group['total'] += Decimal(row['amount'])
        group['decimals'] = max(group['decimals'], int(row['decimals']))
    return list(grouped.values())


def _flow_key(label: str, counterparty: str) -> str:
    """A labelled flow buckets by label; an unlabelled one keeps its address.

    Named buckets (`bond`, `issuance`, a project label) are aggregates on
    purpose. `unlabelled` is not a counterparty, so aggregating on it would
    merge unrelated addresses into one line.
    """
    return label if label != LABEL_UNLABELLED else f'{LABEL_UNLABELLED}:{counterparty}'


def _bucket_flows(
    flows: List[Dict[str, Any]], direction: str, project: ProjectConfig
) -> Tuple[Dict[str, Decimal], List[str]]:
    """Bucketed totals for one direction, and which of those buckets are internal.

    Internal buckets stay in the totals: `rfv()`'s boundary decides what the
    residual may count, not what the operator may see, and a treasury-to-vault
    deposit is a real transfer. The caller drops them from the residual's net
    and from nothing else.

    A key is marked internal when ANY row under it is, and the whole bucket then
    leaves the residual's net. Two kinds of key reach here and only one makes
    that safe. A registry label resolves to one address; a correlated label does
    not -- `attribution.py` assigns `bond` and `issuance` by transaction, not by
    counterparty, and `bond` aggregates 848 distinct senders in the operator's
    store. Correlated labels exist only on the inflow side: `attribute_outflow`
    applies no correlation rule, while on the inflow side correlation outranks
    the registry label rather than yielding to it. Nothing inside `rfv()` reaches
    a correlated bucket today because no such counterparty has ever sent USDG in
    -- all 71 vault rows are outflows. A vault withdrawal sharing a transaction
    with a bond or issuance mint is the case that ends that, and it would drop
    the epoch's whole bond inflow; classify per row before letting it happen.
    """
    buckets: Dict[str, Decimal] = {}
    internal: List[str] = []
    for row in flows:
        if row['direction'] != direction:
            continue
        key = _flow_key(row['label'], row['counterparty'])
        amount = _scaled(row['total'], row['decimals'])
        if amount is None:
            continue
        buckets[key] = buckets.get(key, Decimal(0)) + amount
        if (
            rfv_boundary(str(row['counterparty']), project) == BOUNDARY_INTERNAL
            and key not in internal
        ):
            internal.append(key)
    return buckets, sorted(internal)


def _vault_net_deposit(
    flows: List[Dict[str, Any]], project: ProjectConfig
) -> Decimal:
    """USDG booked into the Morpho vault this window, net of what came back out.

    Netted rather than counted one-way because both legs get the same 98%
    credit: an epoch that deposits 500 and withdraws 200 moves 300 of face value
    across the 100%/98% line, and haircutting the gross 500 would over-correct
    the residual by 2% of the round trip.
    """
    total = Decimal(0)
    for row in flows:
        if not is_morpho_vault(str(row['counterparty']), project):
            continue
        amount = _scaled(row['total'], row['decimals'])
        if amount is None:
            continue
        total += amount if row['direction'] == 'out' else -amount
    return total


def _vault_interest(
    row_components: Dict[str, Optional[Decimal]],
    previous: Optional[Dict[str, Any]],
    vault_deposit: Decimal,
    epochs_spanned: Optional[int],
) -> Dict[str, Any]:
    """Vault accrual this window, and its rate against the tolerance envelope.

    Attribution after the fact, never a prediction: this is the realised
    `morphoAssets` move minus the flow that was booked into it, and the residual
    does not subtract it. Modelling it would zero the residual by construction
    -- see the module docstring -- so what it earns is a rate to check, which is
    the only part of it that carries a signal ahead of a human looking.

    The rate is per epoch, divided by the number of epochs the window spans, so
    a gap in the samples does not read as a doubled yield.
    """
    interest = {'state': INTEREST_UNAVAILABLE, 'amount': None, 'rate_ppm': None}
    morpho = row_components.get('morpho_assets')
    previous_components = (previous or {}).get('rfv_components') or {}
    previous_morpho = previous_components.get('morpho_assets')
    if morpho is None or not previous_morpho or not epochs_spanned:
        return interest

    amount = morpho - previous_morpho - vault_deposit
    rate = amount / previous_morpho * Decimal(10) ** 6 / epochs_spanned
    low, high = INTEREST_PPM_TOLERANCE
    interest.update(
        {
            'state': INTEREST_OK if low <= rate <= high else INTEREST_OUTSIDE_ENVELOPE,
            'amount': amount,
            'rate_ppm': rate,
        }
    )
    return interest


def _price_and_premium(
    readings: Dict[str, Any],
    backing: Optional[Decimal],
    project: ProjectConfig,
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """Spot price from the pair's reserves, and its premium over backing.

    Reserve order is read from `token0()`, not assumed: a pair orders its tokens
    by address, so which reserve is USDG is a property of the deployed pair and
    would silently inverse the price if the pair were ever redeployed. Decimals
    come from the sample's own `decimals()` readings for the same reason -- the
    read plan issues them in every sample precisely so nothing downstream has to
    hardcode 6 and 9.
    """
    reserves = readings.get('pair.getReserves')
    if not reserves or reserves['state'] != 'ok' or not reserves['value_json']:
        return None, None
    token0 = readings.get('pair.token0')
    if not token0 or token0['state'] != 'ok' or not isinstance(token0['value_json'], str):
        return None, None

    usdg_first = token0['value_json'].lower() == project.address('USDG').lower()
    reserve0 = Decimal(reserves['value_json'][0])
    reserve1 = Decimal(reserves['value_json'][1])
    usdg_raw, net_raw = (reserve0, reserve1) if usdg_first else (reserve1, reserve0)

    usdg_dp = _decimals_reading(readings, 'USDG.decimals')
    net_dp = _decimals_reading(readings, 'NET.decimals')
    if usdg_dp is None or net_dp is None:
        return None, None

    net_reserve = net_raw / (Decimal(10) ** net_dp)
    if not net_reserve:
        return None, None
    price = (usdg_raw / (Decimal(10) ** usdg_dp)) / net_reserve
    return price, (price / backing) if backing else None


def _decimals_reading(readings: Dict[str, Any], name: str) -> Optional[int]:
    reading = readings.get(name)
    if not reading or reading['state'] != 'ok' or reading['value_int'] is None:
        return None
    return int(reading['value_int'])


def _loopback(readings: Dict[str, Any], state: Callable[[str], str]) -> Dict[str, Any]:
    """The Morpho market's supply, borrow, utilization and the two APRs."""
    loopback: Dict[str, Any] = {'state': state('Morpho.market')}
    market = readings.get('Morpho.market')
    if not market or market['state'] != 'ok' or not market['value_json']:
        return loopback

    supplied, _, borrowed, _, last_update, fee = (Decimal(v) for v in market['value_json'])
    utilization = (borrowed / supplied) if supplied else None
    borrow_apr = None
    supply_apr = None
    borrow_rate = readings.get('IRM.borrowRateView')
    if borrow_rate and borrow_rate['state'] == 'ok':
        # borrowRateView returns a per-second rate in wad. Annualised linearly,
        # not compounded, because that is what the dapp displays and what the
        # AC5 column is compared against.
        per_second = Decimal(borrow_rate['value_int']) / WAD
        borrow_apr = per_second * SECONDS_PER_YEAR * 100
        if utilization is not None:
            # No on-chain getter for supply APR; defined as borrow rate x
            # utilization x (1 - fee). The derivation is stated in the header so
            # a reader knows it is ours, not the protocol's.
            supply_apr = borrow_apr * utilization * (1 - fee / WAD)

    loopback.update(
        {
            'total_supplied': supplied / (Decimal(10) ** 6),
            'total_borrowed': borrowed / (Decimal(10) ** 6),
            'utilization_pct': (utilization * 100) if utilization is not None else None,
            'borrow_apr_pct': borrow_apr,
            'supply_apr_pct': supply_apr,
            'last_update': int(last_update),
        }
    )
    return loopback


def _sleeve(
    readings: Dict[str, Any],
    value: Callable[[str], Optional[Decimal]],
    sample: Dict[str, Any],
) -> Tuple[Decimal, bool, str]:
    """The Sleeve's marked value, whether any mark is stale, and completeness.

    A memo line, not treasury backing: these equities are the manager's, and
    scoring them is G5's job. Returns `partial` if any leg is missing rather
    than a total that silently omits one.
    """
    total = Decimal(0)
    stale = False
    sleeve_state = 'ok'
    for symbol in ('NVDA', 'SPCX', 'AAPL', 'MSFT', 'GOOGL', 'COIN'):
        balance = value(f'Sleeve.{symbol}.balanceOf')
        mark_reading = readings.get(f'Mark.{symbol}.latestRoundData')
        if balance is None or not mark_reading or mark_reading['state'] != 'ok':
            sleeve_state = 'partial'
            continue
        answer = Decimal(mark_reading['value_json'][1])
        updated_at = int(mark_reading['value_json'][3])
        feed_decimals = readings.get(f'Mark.{symbol}.decimals')
        scale = (
            Decimal(10) ** int(feed_decimals['value_int'] or 8)
            if feed_decimals
            else Decimal(10) ** 8
        )
        total += balance * (answer / scale)
        # A mark older than 24 h is stale. Without this rule a dead feed values
        # the Sleeve forever, at whatever the last price happened to be.
        if int(sample['block_timestamp']) - updated_at > 24 * 3600:
            stale = True
    return total, stale, sleeve_state


def _build_row(
    sources: '_EpochSources',
    project: ProjectConfig,
    sample: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    readings = sources.readings[int(sample['id'])]

    def value(name: str) -> Optional[Decimal]:
        reading = readings.get(name)
        if reading is None or reading['state'] != 'ok':
            return None
        return _scaled(reading['value_int'], reading['decimals'])

    def state(name: str) -> str:
        reading = readings.get(name)
        return reading['state'] if reading else 'absent'

    epoch = int(sample['epoch_number'])
    block = int(sample['block'])
    previous_block = int(previous['block']) if previous and previous.get('present') else None

    rfv = value('Treasury.rfv')
    backing = value('Treasury.backingPerToken')
    supply = value('NET.totalSupply')

    emission = _group_mints(
        _window(sources.mints, sources.mint_blocks, previous_block, block)
    )
    total_emission = sum((v for v in emission.values() if v is not None), Decimal(0))

    # Grouped by counterparty as well as label, because R6 says an unlabelled
    # flow is reported "with the address" and AC5 asks for outflows BY
    # RECIPIENT. Grouping on the label alone collapses every unknown recipient
    # into one `unlabelled` bucket -- which reads as a single counterparty and
    # is the one shape that makes an unrecognised drain look ordinary.
    flows = _group_flows(
        _window(sources.flows, sources.flow_blocks, previous_block, block)
    )
    # The internal/external split is derived here rather than stored on the
    # flow row, so it is a property of what `rfv()` counts TODAY. A stored
    # column would freeze each transfer's classification at the moment it was
    # swept, and `rfv()` gaining a component would leave every past epoch
    # classified against the old boundary with no way to notice.
    inflows, internal_inflow_keys = _bucket_flows(flows, 'in', project)
    outflows, internal_outflow_keys = _bucket_flows(flows, 'out', project)

    liquid = value('Treasury.liquidUsdg')
    morpho_assets = value('Treasury.morphoAssets')
    pol = value('Treasury.polRfv')
    component_sum = None
    if None not in (liquid, morpho_assets, pol):
        component_sum = liquid + morpho_assets + pol
    # Checked in wei on the values the chain returned, not on the scaled
    # Decimals above: the 98% credit is observed rather than read from the
    # contract, so this equality is the only warning we get that the protocol
    # re-rated its own vault -- and a comparison of two rounded quotients would
    # not notice a re-rating smaller than the rounding.
    identity = check_rfv_identity(
        {
            name: (
                readings[name]['value_int']
                if name in readings and readings[name]['state'] == 'ok'
                else None
            )
            for name in IDENTITY_READINGS
        }
    )

    previous_rfv = previous.get('rfv') if previous and previous.get('present') else None
    previous_supply = previous.get('supply') if previous and previous.get('present') else None
    # Internal buckets are left out of both nets. `out` in this sum has to mean
    # "value left `rfv()`", and a deposit into the Morpho vault leaves `rfv()`
    # unchanged -- `morphoAssets` rises by what `liquidUsdg` loses. Counting it
    # made the residual report the deposit back as unexplained: measured at
    # epoch 133, a 965,383 USDG deposit produced a residual of 946,213 against
    # the 7-185 of every epoch without one, burying the genuine signal the
    # column exists for.
    net_inflow = sum(
        (v for k, v in inflows.items() if v is not None and k not in internal_inflow_keys),
        Decimal(0),
    )
    net_outflow = sum(
        (v for k, v in outflows.items() if v is not None and k not in internal_outflow_keys),
        Decimal(0),
    )
    # A booked deposit into the Morpho vault costs `rfv()` 2% of itself: the
    # USDG leaves a component credited at 100% for one credited at 98%. That is
    # the whole of the predictable part, and subtracting it is what stops a
    # treasury-internal move reading as a six-figure outflow -- epoch 133's
    # 965,383 deposit reported a residual of -19,170 before this term.
    vault_deposit = _vault_net_deposit(flows, project)
    deposit_haircut = MORPHO_DEPOSIT_HAIRCUT * vault_deposit
    # The residual asks: how much of the rfv move is NOT explained by the USDG
    # that visibly entered and left across `rfv()`'s boundary, once the one
    # modelled contract behaviour is allowed for? What is left is vault interest
    # plus `delta polRfv` plus anything we have not labelled -- all three
    # deliberately unmodelled, because predicting them needs the realised
    # component deltas and that makes the number zero in every epoch forever.
    # It is a prompt to look, never a number to publish as a category.
    residual = None
    if rfv is not None and previous_rfv is not None:
        residual = (rfv - previous_rfv) - (net_inflow - net_outflow) + deposit_haircut

    pol_delta = (
        pol - previous['rfv_components']['pol_rfv']
        if pol is not None
        and previous
        and (previous.get('rfv_components') or {}).get('pol_rfv') is not None
        else None
    )
    vault_interest = _vault_interest(
        {'morpho_assets': morpho_assets},
        previous,
        vault_deposit,
        epoch - int(previous['epoch']) if previous and previous.get('present') else None,
    )

    price, premium = _price_and_premium(readings, backing, project)

    lp_total = value('pair.totalSupply')
    lp_treasury = value('pair.balanceOf(Treasury)')
    lp_share = (lp_treasury / lp_total * 100) if lp_total and lp_treasury else None

    loopback = _loopback(readings, state)

    sleeve_total, sleeve_stale, sleeve_state = _sleeve(readings, value, sample)

    buyback = {
        'capacity': value('inverseBond.capacityRemaining'),
        'capacity_state': state('inverseBond.capacityRemaining'),
        # No on-chain source: the dapp's own live path hardcodes 0 for this.
        # Reported as unavailable rather than derived from a rule we invented.
        'filled': None,
        'filled_state': 'no_onchain_source',
        'repurchased': _sum_event_field(sources, previous_block, block,
                                        'InverseBonded', 'netBurned', 9),
        # The dapp sets bought and burned to the same sum, so a buyback burns
        # exactly what it repurchases; both come from `InverseBonded.netBurned`.
        'burned': _sum_event_field(sources, previous_block, block,
                                   'InverseBonded', 'netBurned', 9),
    }

    backing_change_pct = _pct_change(
        backing, previous.get('backing_per_token') if previous else None
    )
    treasury_growth_pct = _pct_change(rfv, previous_rfv)
    dilution_pct = total_emission / previous_supply * 100 if previous_supply else None
    growth_minus_dilution_pct = _growth_minus_dilution(treasury_growth_pct, dilution_pct)

    return {
        'epoch': epoch,
        'present': True,
        'block': block,
        'block_timestamp': int(sample['block_timestamp']),
        'block_time_utc': _iso8601_utc(int(sample['block_timestamp'])),
        'kind': sample['kind'],
        'endpoint_kind': sample['endpoint_kind'],
        'backing_per_token': backing,
        'backing_change_pct': backing_change_pct,
        'rfv': rfv,
        'treasury_growth_pct': treasury_growth_pct,
        'supply': supply,
        'emission_rebase': emission.get('rebase'),
        'emission_bond': emission.get('bond'),
        'emission_issuance': emission.get('issuance'),
        'emission_other': emission.get('other'),
        'dilution_pct': dilution_pct,
        # DR14, additive: the D2 view compares growth against dilution, and the
        # difference plus its agreement with the backing move are derived here
        # rather than on the page so the CLI and the page cannot disagree about
        # a figure neither of them is the source of.
        'growth_minus_dilution_pct': growth_minus_dilution_pct,
        'sign_agreement': _sign_agreement(growth_minus_dilution_pct, backing_change_pct),
        'inflows': {k: str(v) for k, v in inflows.items()},
        'outflows': {k: str(v) for k, v in outflows.items()},
        # Which of those buckets the residual ignored, so a reader who sees a
        # six-figure outflow beside an unmoved residual is told why rather than
        # left to suspect the arithmetic.
        'internal_flows': {'in': internal_inflow_keys, 'out': internal_outflow_keys},
        # DR14, additive: the per-address `unlabelled:<addr>` keys above are 220
        # one-epoch series on the real data, which is unreadable as a stacked
        # bar. Folded into one bucket HERE, so the flow detail keeps the
        # per-address attribution and D3 draws one unlabelled series.
        'inflows_chart': _inflows_chart(inflows, internal_inflow_keys),
        'residual': residual,
        # The modelled part of the residual, reported beside it so a reader can
        # see how large a correction was applied rather than inferring it from
        # the flow detail.
        'deposit_haircut': deposit_haircut,
        'vault_net_deposit': vault_deposit,
        'rfv_components': {
            'liquid_usdg': liquid,
            'morpho_assets': morpho_assets,
            'pol_rfv': pol,
            # Left unmodelled on purpose and shown for that reason: POL drift is
            # the signal class the residual exists to surface, so it belongs in
            # front of the operator rather than inside a prediction.
            'pol_rfv_delta': pol_delta,
            'sum': component_sum,
            # The components overshoot rfv() by exactly 2% of morphoAssets --
            # the vault credit, not a gap. See the module docstring.
            'sum_minus_rfv': (component_sum - rfv) if component_sum and rfv else None,
        },
        'rfv_identity': {'state': identity.state, 'diff_wei': identity.diff_wei},
        'vault_interest': vault_interest,
        'pair_price': price,
        'premium_x': premium,
        'lp_total_supply': lp_total,
        'lp_treasury_share_pct': lp_share,
        'buyback': buyback,
        'sleeve_total_usd': sleeve_total,
        'sleeve_state': sleeve_state,
        'sleeve_mark_stale': sleeve_stale,
        'loopback': loopback,
        'reading_states': {name: r['state'] for name, r in readings.items()},
    }


def _growth_minus_dilution(
    treasury_growth_pct: Optional[Decimal], dilution_pct: Optional[Decimal]
) -> Optional[Decimal]:
    """DR14. Null if either side is, never a subtraction against an assumed 0."""
    if treasury_growth_pct is None or dilution_pct is None:
        return None
    return treasury_growth_pct - dilution_pct


def _sign(value: Decimal) -> int:
    return (value > 0) - (value < 0)


def _sign_agreement(
    growth_minus_dilution_pct: Optional[Decimal],
    backing_change_pct: Optional[Decimal],
) -> str:
    """Whether the two figures point the same way, three-way including zero.

    The identity G5 states is that backing rises exactly when treasury growth
    outruns emission, so a disagreement is a burn, a re-mark or a data gap and
    is the one thing on D2 worth marking. Compared as signs and not as
    magnitudes: the two are percentages of different denominators.
    """
    if growth_minus_dilution_pct is None or backing_change_pct is None:
        return SIGN_UNAVAILABLE
    same = _sign(growth_minus_dilution_pct) == _sign(backing_change_pct)
    return SIGN_AGREE if same else SIGN_DISAGREE


def _inflows_chart(
    inflows: Dict[str, Decimal], internal_keys: List[str]
) -> Dict[str, Any]:
    """DR14's chart aggregate: per-address unlabelled keys folded into one.

    A chart key is internal when ANY of the source keys folded into it is, so a
    single internal address inside `unlabelled` cannot lose its marker by being
    summed with external ones -- an internal flow shown as ordinary is the
    failure this marker exists to prevent, and over-marking is only noisier.
    """
    buckets: Dict[str, Decimal] = {}
    internal: List[str] = []
    for key, amount in inflows.items():
        chart_key = LABEL_UNLABELLED if key.startswith(f'{LABEL_UNLABELLED}:') else key
        buckets[chart_key] = buckets.get(chart_key, Decimal(0)) + amount
        if key in internal_keys and chart_key not in internal:
            internal.append(chart_key)
    return {
        'buckets': {k: str(v) for k, v in buckets.items()},
        'internal': sorted(internal),
    }


def _sum_event_field(
    sources: '_EpochSources',
    from_block: Optional[int],
    to_block: int,
    event_name: str,
    field: str,
    decimals: int,
) -> Optional[Decimal]:
    # KeyError, not an empty window, when a column asks for an event name the
    # prefetch did not read: a missing name would otherwise sum to a confident
    # zero that looks like "no buybacks happened".
    event_rows, event_blocks = sources.events[event_name]
    rows = _window(event_rows, event_blocks, from_block, to_block)
    if not rows:
        return Decimal(0)
    total = sum(Decimal(row['fields_json'][field]) for row in rows)
    return total / (Decimal(10) ** decimals)


HEADER_NOTES = (
    'fees unattributed (the 500 bps trading tax path is unidentified; ticket step 3)',
    'Sleeve total is NOT part of rfv() and is never summed into it',
    'rfv() credits the Morpho vault at 98% (rfv() = liquidUsdg + 0.98 x '
    'morphoAssets + polRfv, exact in wei in all 133 epochs), so the components '
    'overshoot rfv() by 2% of morphoAssets -- the credit, not an unaccounted flow',
    'the residual models that 2% on a booked vault deposit and nothing else; '
    'what remains in a quiet epoch is vault interest plus the polRfv move, '
    'neither of which is booked as a transfer (measured 7.2 to 185.2 USDG)',
    'Loopback supply APR is derived as borrow rate x utilization x (1 - fee); '
    'it has no on-chain getter',
    'buyback "filled" has no on-chain source and is reported as unavailable',
    'a flow marked [internal] moves value between two things rfv() already '
    'counts (today: the Morpho vault) and is excluded from the residual only; '
    'it is shown in full because the transfer is real',
)

# AC5's enumerated figures, in AC5's order. Every one of them that has a fixed
# arity is a column here; the two that do not -- inflows by bucket/label and
# outflows by recipient -- are unbounded in cardinality (a new counterparty
# appears whenever one transacts) and cannot be fixed columns at all. Those are
# rendered per epoch in the detail block below the table, which is why
# `render_table` emits both halves and neither is optional.
#
# A dotted key reaches into a nested dict on the row (`buyback.capacity`).
COLUMNS = (
    ('epoch', 'epoch'),
    ('block', 'close block'),
    ('block_time_utc', 'close time (UTC)'),
    ('backing_per_token', 'backing/token'),
    ('backing_change_pct', 'd backing %'),
    ('rfv', 'rfv()'),
    ('treasury_growth_pct', 'growth %'),
    ('emission_rebase', 'rebase'),
    ('emission_bond', 'bond'),
    ('emission_issuance', 'issuance'),
    ('emission_other', 'other'),
    ('dilution_pct', 'dilution %'),
    ('residual', 'residual'),
    ('rfv_components.pol_rfv_delta', 'd polRfv'),
    ('vault_interest.rate_ppm', 'vault ppm'),
    ('premium_x', 'premium x'),
    ('lp_total_supply', 'LP supply'),
    ('lp_treasury_share_pct', 'LP treasury %'),
    ('buyback.capacity', 'bb capacity'),
    ('buyback.filled', 'bb filled'),
    ('buyback.repurchased', 'bb repurchased'),
    ('buyback.burned', 'bb burned'),
    ('loopback.total_supplied', 'lb supplied'),
    ('loopback.total_borrowed', 'lb borrowed'),
    ('loopback.utilization_pct', 'lb util %'),
    ('loopback.borrow_apr_pct', 'lb borrow APR %'),
    ('sleeve_total_usd', 'Sleeve $ (memo)'),
)

# Columns whose absence is explained by a state field on the same nested dict,
# so a blank cell can say WHY it is blank instead of printing one dash for a
# not-yet-deployed contract and a failed read alike.
STATE_KEYS = {
    'buyback.capacity': ('buyback', 'capacity_state'),
    'buyback.filled': ('buyback', 'filled_state'),
    'loopback.total_supplied': ('loopback', 'state'),
    'loopback.total_borrowed': ('loopback', 'state'),
    'loopback.utilization_pct': ('loopback', 'state'),
    'loopback.borrow_apr_pct': ('loopback', 'state'),
    'vault_interest.rate_ppm': ('vault_interest', 'state'),
}

STATE_RENDERING = {
    'not_deployed': 'n/a (not deployed)',
    'no_onchain_source': 'n/a (no source)',
    'failed': 'failed',
    'absent': '-',
    'unavailable': '-',
}


def _lookup(row: Dict[str, Any], key: str) -> Any:
    """Resolve a plain or dotted column key against a row."""
    if '.' not in key:
        return row.get(key)
    outer, inner = key.split('.', 1)
    nested = row.get(outer)
    return nested.get(inner) if isinstance(nested, dict) else None


def _cell(row: Dict[str, Any], key: str) -> str:
    if not row.get('present'):
        # The epoch number still prints, so the reader can see WHICH epoch is
        # missing. A row of dashes with no identifier says only "something is
        # absent", which is the one thing the gap row exists to make specific.
        return str(row['epoch']) if key == 'epoch' else '-'
    value = _lookup(row, key)
    if value is None:
        # A not-deployed contract is a different fact from a failed read, and
        # the table says which rather than printing one dash for both. The state
        # consulted is THIS column's own, not "any reading anywhere was
        # not_deployed" -- which would stamp the label onto unrelated blanks.
        state_path = STATE_KEYS.get(key)
        if state_path:
            outer, inner = state_path
            nested = row.get(outer)
            state = nested.get(inner) if isinstance(nested, dict) else None
            if state in STATE_RENDERING:
                return STATE_RENDERING[state]
        return '-'
    if isinstance(value, Decimal):
        # The pair's LP token is 18 dp with a total supply around 2.7e-7, so a
        # fixed 4-decimal format renders the whole LP column as 0.0000 -- a real
        # figure displayed as a zero. Small non-zero magnitudes switch to
        # significant figures rather than being rounded away.
        if value != 0 and abs(value) < Decimal('0.001'):
            return f'{value:.4g}'
        return f'{value:,.4f}'
    return str(value)


def _iso8601_utc(block_timestamp: int) -> str:
    """The chain's own block timestamp as ISO 8601 UTC.

    Derived from `block_timestamp`, never from the sample's `read_at`: one says
    when the epoch closed on chain, the other when we happened to read it, and
    for a backfilled sample those differ by weeks. Rendered with a `Z` and no
    offset so two runs on machines in different timezones produce byte-identical
    output -- the report is diffed between runs.
    """
    return datetime.fromtimestamp(block_timestamp, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )


def identity_breaks(rows: List[Dict[str, Any]]) -> List[int]:
    """Epochs whose sample contradicts the 98% credit.

    The caller decides how loud to be; the report prints them above the table
    and the job exits non-zero on them, because a broken identity invalidates
    the deposit haircut for every epoch after it, not just the one it appears in.
    """
    return [
        row['epoch']
        for row in rows
        if (row.get('rfv_identity') or {}).get('state') == IDENTITY_BROKEN
    ]


def _interest_excursions(rows: List[Dict[str, Any]]) -> List[Tuple[int, Decimal]]:
    return [
        (row['epoch'], row['vault_interest']['rate_ppm'])
        for row in rows
        if (row.get('vault_interest') or {}).get('state') == INTEREST_OUTSIDE_ENVELOPE
    ]


def render_alerts(rows: List[Dict[str, Any]]) -> List[str]:
    """The two checks that have something to say only when they fail.

    Printed above the table rather than as a column: over 133 rows a per-row
    "ok" is scrolled past, and both of these are conditions the operator must
    not be able to miss on a run they only glanced at.
    """
    lines = []
    broken = identity_breaks(rows)
    if broken:
        lines.append(
            f'  !! rfv() IDENTITY BROKEN at epoch(s) {broken}: {IDENTITY_EXPRESSION} '
            'no longer holds. The 98% credit is observed, not read from the '
            'contract, so treat this as a possible re-rating of the vault '
            'position -- every residual here models the deposit haircut with a '
            'coefficient the chain has stopped honouring.'
        )
    low, high = INTEREST_PPM_TOLERANCE
    for epoch, rate in _interest_excursions(rows):
        lines.append(
            f'  !! epoch {epoch}: vault interest ran at {rate:,.1f} ppm of the '
            f'position, outside the {low}-{high} ppm tolerance (observed range '
            f'{INTEREST_PPM_OBSERVED[0]}-{INTEREST_PPM_OBSERVED[1]} ppm over the '
            'backfill). A rate this far off is a re-mark or a halted position, '
            'not a yield move.'
        )
    return lines


def render_table(rows: List[Dict[str, Any]]) -> str:
    lines = ['NETNET treasury vs emission, by epoch']
    lines += [f'  note: {note}' for note in HEADER_NOTES]
    lines += render_alerts(rows)
    lines.append('')
    headers = [label for _, label in COLUMNS]
    table = [headers] + [[_cell(row, key) for key, _ in COLUMNS] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    for index, record in enumerate(table):
        lines.append('  '.join(cell.ljust(widths[i]) for i, cell in enumerate(record)))
        if index == 0:
            lines.append('  '.join('-' * w for w in widths))
    lines.extend(_render_flow_detail(rows))
    return '\n'.join(lines)


def _render_flow_detail(rows: List[Dict[str, Any]]) -> List[str]:
    """AC5's inflows and outflows, per epoch, beneath the table.

    Not columns, because neither is fixed-arity: a bucket appears the first time
    a counterparty transacts, so the column set would change between two runs of
    the same report. Rendered per epoch instead, always emitted -- an epoch with
    no flows prints "none", which is a different statement from an epoch the
    report skipped.
    """
    lines = ['', 'flows by epoch (AC5: inflows by bucket, outflows by recipient)']
    for row in rows:
        if not row.get('present'):
            lines.append(f'  epoch {row["epoch"]}: no sample')
            continue
        lines.append(f'  epoch {row["epoch"]} (close block {row["block"]})')
        internal = row.get('internal_flows') or {}
        for direction, key in (('in', 'inflows'), ('out', 'outflows')):
            entries = row.get(key) or {}
            if not entries:
                lines.append(f'    {direction:3} none')
                continue
            internal_keys = internal.get(direction) or []
            for name, amount in sorted(entries.items()):
                marker = ' [internal]' if name in internal_keys else ''
                lines.append(
                    f'    {direction:3} {name:<52} {_format_amount(amount)}{marker}'
                )
    return lines


def _format_amount(amount: Any) -> str:
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if value != 0 and abs(value) < Decimal('0.001'):
        return f'{value:.4g}'
    return f'{value:,.4f}'


def render_json(rows: List[Dict[str, Any]]) -> str:
    """The same rows as JSON, so the later local dashboard consumes `report`'s
    output rather than re-deriving the epoch table from rows -- one derivation
    to be wrong, not two.

    `null` where the table prints a dash, plus the reading state that produced
    it, so a consumer can tell a missing epoch from a failed read.
    """
    return json.dumps([_jsonable(row) for row in rows], indent=2, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def freshness(rows: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """D5/DR9: how old the newest close is, and whether that is stale.

    `now` is an argument rather than read here so a test can pin it and so two
    views rendered from one request agree on the time. An empty store is stale:
    no record is the stalest record, and reporting it as fresh would let a store
    that was never written look like a market that never moved.
    """
    present = [row for row in rows if row.get('present')]
    if not present:
        return {
            'latest_epoch': None,
            'latest_block_time_utc': None,
            'age_hours': None,
            'stale': True,
            'threshold_hours': STALE_AFTER_HOURS,
        }
    latest = present[-1]
    age_seconds = Decimal(int(now.timestamp()) - int(latest['block_timestamp']))
    age_hours = (age_seconds / 3600).quantize(Decimal('0.01'))
    return {
        'latest_epoch': latest['epoch'],
        'latest_block_time_utc': latest['block_time_utc'],
        'age_hours': age_hours,
        'stale': age_hours > STALE_AFTER_HOURS,
        'threshold_hours': STALE_AFTER_HOURS,
    }


# The row keys each view draws, in the order the page stacks them. Kept as data
# so the charts <-> rows invariant test can walk them instead of restating the
# mapping a second time.
D1_KEYS = ('backing_per_token', 'pair_price')
D2_KEYS = (
    'treasury_growth_pct',
    'dilution_pct',
    'backing_change_pct',
    'growth_minus_dilution_pct',
    'sign_agreement',
)


def chart_series(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The same values as `rows`, transposed into per-series arrays.

    Reshaping only -- no arithmetic, no rounding, no filtering (DR3). A gap row
    contributes `null` at its index in every series rather than being dropped,
    which is what makes `spanGaps: false` break the line at exactly the epoch
    that has no sample instead of drawing through it.

    Done here rather than on the page because the CI image has no JS runtime, so
    a mapping written in the page could not be tested at all.
    """
    epochs = [row['epoch'] for row in rows]
    bucket_keys = sorted(
        {
            key
            for row in rows
            for key in ((row.get('inflows_chart') or {}).get('buckets') or {})
        }
    )
    return {
        'd1': {
            'epochs': epochs,
            **{key: [_series_value(row, key) for row in rows] for key in D1_KEYS},
        },
        'd2': {
            'epochs': epochs,
            **{key: [_series_value(row, key) for row in rows] for key in D2_KEYS},
        },
        'd3': {
            'epochs': epochs,
            'buckets': {
                key: [
                    ((row.get('inflows_chart') or {}).get('buckets') or {}).get(key)
                    for row in rows
                ]
                for key in bucket_keys
            },
            'internal': [
                list((row.get('inflows_chart') or {}).get('internal') or [])
                for row in rows
            ],
        },
        'points': [
            {
                'epoch': row['epoch'],
                'block': row.get('block'),
                'block_time_utc': row.get('block_time_utc'),
            }
            for row in rows
        ],
    }


def _series_value(row: Dict[str, Any], key: str) -> Any:
    return row.get(key) if row.get('present') else None


def report_payload(
    rows: List[Dict[str, Any]],
    project: ProjectConfig,
    *,
    now: datetime,
) -> Dict[str, Any]:
    """The whole page's input: the CLI's rows plus what the CLI prints around them.

    `rows` goes through the same `_jsonable` as `render_json`, so a value the
    route serves is the string the CLI prints (DR1, AC-D1). `charts` is applied
    to the same objects before that conversion, so the two representations of a
    figure in one body cannot differ in the last decimal place.
    """
    return {
        'project': project.name,
        'generated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'rows': [_jsonable(row) for row in rows],
        'notes': list(HEADER_NOTES),
        'alerts': render_alerts(rows),
        'identity_broken_epochs': identity_breaks(rows),
        'freshness': _jsonable(freshness(rows, now)),
        'charts': _jsonable(chart_series(rows)),
    }
