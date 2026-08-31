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

A third note is new to this implementation and is a measurement, not a policy:
`rfv()` does NOT equal `liquidUsdg + morphoAssets + polRfv`. Read live at block
49,867,666 on 2026-08-30 the three components summed to 4,987,116.39 against an
`rfv()` of 4,930,626.16 -- the components overshoot by 56,490.24, or 1.15% of
rfv. So the components make a movement attributable to a component, which is why
they are recorded, but the difference is a modelling gap rather than an
unaccounted inflow, and the header says so rather than letting a reader treat it
as one.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from .attribution import BOUNDARY_INTERNAL, LABEL_UNLABELLED, rfv_boundary
from .config import ProjectConfig
from .repository import ProjectMonitorRepository

MISSING = None
SECONDS_PER_YEAR = 365 * 24 * 3600
WAD = Decimal(10) ** 18


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

    by_epoch = {int(s['epoch_number']): s for s in closing_samples}
    # Missing epochs render as a row of dashes with the epoch number, never
    # interpolated: an epoch with no sample is a gap in the record, and drawing
    # a line through it would invent a treasury figure nobody observed.
    for epoch in range(min(epoch_numbers), max(epoch_numbers) + 1):
        sample = by_epoch.get(epoch)
        if sample is None:
            rows.append({'epoch': epoch, 'present': False})
            continue
        row = _build_row(repository, project, sample, previous)
        rows.append(row)
        previous = row
    return rows




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
    repository: ProjectMonitorRepository,
    project: ProjectConfig,
    sample: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    project_name = project.name
    readings = {
        r['name']: r
        for r in repository.fetch_all(
            'SELECT name, value_int, value_json, decimals, state, error_class '
            'FROM reading WHERE sample_id = %s',
            (sample['id'],),
        )
    }

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

    mints = repository.fetch_all(
        'SELECT class, sum(amount) AS total, max(decimals) AS decimals FROM mint '
        'WHERE project = %s AND block > %s AND block <= %s GROUP BY class',
        (project_name, previous_block if previous_block is not None else -1, block),
    )
    emission = {
        row['class']: _scaled(row['total'], row['decimals']) for row in mints
    }
    total_emission = sum((v for v in emission.values() if v is not None), Decimal(0))

    # Grouped by counterparty as well as label, because R6 says an unlabelled
    # flow is reported "with the address" and AC5 asks for outflows BY
    # RECIPIENT. Grouping on the label alone collapses every unknown recipient
    # into one `unlabelled` bucket -- which reads as a single counterparty and
    # is the one shape that makes an unrecognised drain look ordinary.
    flows = repository.fetch_all(
        'SELECT direction, label, counterparty, sum(amount) AS total, '
        'max(decimals) AS decimals '
        'FROM flow WHERE project = %s AND block > %s AND block <= %s '
        'GROUP BY direction, label, counterparty',
        (project_name, previous_block if previous_block is not None else -1, block),
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
    # The residual asks: how much of the rfv move is NOT explained by the USDG
    # that visibly entered and left across `rfv()`'s boundary? A non-zero
    # residual is a vault re-mark, a POL move, or something we have not
    # labelled -- it is a prompt to look, never a number to publish as a
    # category.
    residual = None
    if rfv is not None and previous_rfv is not None:
        residual = (rfv - previous_rfv) - (net_inflow - net_outflow)

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
        'repurchased': _sum_event_field(repository, project_name, previous_block, block,
                                        'InverseBonded', 'netBurned', 9),
        # The dapp sets bought and burned to the same sum, so a buyback burns
        # exactly what it repurchases; both come from `InverseBonded.netBurned`.
        'burned': _sum_event_field(repository, project_name, previous_block, block,
                                   'InverseBonded', 'netBurned', 9),
    }

    return {
        'epoch': epoch,
        'present': True,
        'block': block,
        'block_timestamp': int(sample['block_timestamp']),
        'block_time_utc': _iso8601_utc(int(sample['block_timestamp'])),
        'kind': sample['kind'],
        'endpoint_kind': sample['endpoint_kind'],
        'backing_per_token': backing,
        'backing_change_pct': _pct_change(
            backing, previous.get('backing_per_token') if previous else None
        ),
        'rfv': rfv,
        'treasury_growth_pct': _pct_change(rfv, previous_rfv),
        'supply': supply,
        'emission_rebase': emission.get('rebase'),
        'emission_bond': emission.get('bond'),
        'emission_issuance': emission.get('issuance'),
        'emission_other': emission.get('other'),
        'dilution_pct': (
            total_emission / previous_supply * 100 if previous_supply else None
        ),
        'inflows': {k: str(v) for k, v in inflows.items()},
        'outflows': {k: str(v) for k, v in outflows.items()},
        # Which of those buckets the residual ignored, so a reader who sees a
        # six-figure outflow beside an unmoved residual is told why rather than
        # left to suspect the arithmetic.
        'internal_flows': {'in': internal_inflow_keys, 'out': internal_outflow_keys},
        'residual': residual,
        'rfv_components': {
            'liquid_usdg': liquid,
            'morpho_assets': morpho_assets,
            'pol_rfv': pol,
            'sum': component_sum,
            # Measured non-zero; see the module docstring.
            'sum_minus_rfv': (component_sum - rfv) if component_sum and rfv else None,
        },
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


def _sum_event_field(
    repository: ProjectMonitorRepository,
    project_name: str,
    from_block: Optional[int],
    to_block: int,
    event_name: str,
    field: str,
    decimals: int,
) -> Optional[Decimal]:
    rows = repository.fetch_all(
        'SELECT fields_json FROM event WHERE project = %s AND name = %s '
        'AND block > %s AND block <= %s',
        (project_name, event_name, from_block if from_block is not None else -1, to_block),
    )
    if not rows:
        return Decimal(0)
    total = sum(Decimal(row['fields_json'][field]) for row in rows)
    return total / (Decimal(10) ** decimals)


HEADER_NOTES = (
    'fees unattributed (the 500 bps trading tax path is unidentified; ticket step 3)',
    'Sleeve total is NOT part of rfv() and is never summed into it',
    'rfv() components do not sum to rfv() (measured -1.15% at block 49,867,666); '
    'the difference is a modelling gap, not an unaccounted flow',
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
}

STATE_RENDERING = {
    'not_deployed': 'n/a (not deployed)',
    'no_onchain_source': 'n/a (no source)',
    'failed': 'failed',
    'absent': '-',
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


def render_table(rows: List[Dict[str, Any]]) -> str:
    lines = ['NETNET treasury vs emission, by epoch']
    lines += [f'  note: {note}' for note in HEADER_NOTES]
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
