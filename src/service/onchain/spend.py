"""The run's spend ledger and the job-side ceiling (requirement cost gates, P13).

Every RPC attempt is counted at ONE point: the library client calls
`budget.reserve(cost)` before each send, retries included, so a budget wrapped
here sees every attempt whether it later succeeds, is refused or times out.
The earlier ledger charged per call site and recorded 2 public requests for a
run that sent dozens (run 1); a call site that forgets to charge cannot exist
here because there is no call site.

    EvmClient._send_with_retry            SpendLedger
    ------------------------              -----------
    reserve(cost) per attempt  --------> MeteredBudget.reserve
                                            SpendMeter.admit(cost)
                                              ceiling check, then count
                                            EndpointBudget.reserve (the window)

The ceiling is the second guard behind the provider's own account cap: the
provider's cap stops every consumer of the key, the NETNET monitor included,
so the job refuses its own next call first. A refusal is a
`SpendCeilingReachedError`, which the builder records as the section's error
class and the run alerts once (A11). It is deliberately NOT an `EvmClientError`:
collectors treat those as "unavailable, carry on", and a skip the cost gate
mandates must be visible in the ledger, not absorbed into a warning.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class SpendCeilingReachedError(RuntimeError):
    """The next call would take this month's metered spend past the ceiling."""

    def __init__(self, kind: str, *, spent: int, cost: int, ceiling: int) -> None:
        super().__init__(
            f'{kind}: {spent} units spent this month plus {cost} for the next call '
            f'exceeds the ceiling of {ceiling}'
        )
        self.kind = kind
        self.spent = spent
        self.cost = cost
        self.ceiling = ceiling


@dataclass
class SpendMeter:
    """What one endpoint kind has been asked for during this run."""

    kind: str
    # Units are only meaningful on an endpoint that publishes a cost model. The
    # public RPC gets a request count and no units, so the ledger cannot invent
    # a bill the operator could not check against an invoice.
    bills_units: bool = False
    ceiling_units: Optional[int] = None
    # Month-to-date from earlier runs' ledger rows, so the ceiling is monthly
    # even though a meter lives for one run.
    spent_before_run: int = 0
    requests: int = 0
    units: int = 0

    def __post_init__(self) -> None:
        # A ceiling compares against `units`, which a non-billing meter never
        # moves: the setting would look configured and enforce nothing.
        if self.ceiling_units is not None and not self.bills_units:
            raise ValueError(f'{self.kind}: a ceiling needs a meter that bills units')

    def admit(self, cost: int) -> None:
        """Count one attempt, or refuse it before it is sent."""
        if self.ceiling_units is not None:
            spent = self.spent_before_run + self.units
            if spent + cost > self.ceiling_units:
                raise SpendCeilingReachedError(
                    self.kind, spent=spent, cost=cost, ceiling=self.ceiling_units
                )
        self.requests += 1
        if self.bills_units:
            self.units += cost


class MeteredBudget:
    """An `EndpointBudget` whose every `reserve` passes through a meter first.

    Everything else is the wrapped budget's own, delegated by attribute lookup,
    so the client cannot tell the difference and the library needs no change.
    """

    def __init__(self, inner: Any, meter: SpendMeter) -> None:
        self._inner = inner
        self.meter = meter

    async def reserve(self, cost: int) -> None:
        # The inadmissible-cost check runs before the count: a batch no window
        # could ever hold is refused by the library and is never sent, so it
        # must not appear in the ledger as an attempt.
        self._inner.check_admissible(cost)
        self.meter.admit(cost)
        await self._inner.reserve(cost)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass
class SpendLedger:
    """Every meter for one run, and the `spend_json` shape written at its end."""

    meters: Dict[str, SpendMeter] = field(default_factory=dict)
    budgets: Dict[str, MeteredBudget] = field(default_factory=dict)

    def meter(self, kind: str, **options: Any) -> SpendMeter:
        """The meter for `kind`, created on first use.

        One meter per kind, not per role: when the log plane is switched to
        the archive endpoint both roles draw on the same account, and two
        meters would let each spend up to the whole ceiling. A second caller
        asking for different options gets an error, not the first caller's
        meter: silently keeping the first options would drop a ceiling.
        """
        existing = self.meters.get(kind)
        if existing is not None:
            clashes = {k: v for k, v in options.items() if getattr(existing, k) != v}
            if clashes:
                raise ValueError(f'{kind}: meter already exists with different {sorted(clashes)}')
            return existing
        created = SpendMeter(kind=kind, **options)
        self.meters[kind] = created
        return created

    def budget_for(self, budget: Any, **meter_options: Any) -> MeteredBudget:
        """One wrapped budget per kind, shared by every role on that endpoint.

        Sharing only the meter is not enough: each `EndpointBudget` owns a
        rate-limiter window, so two roles on the keyed endpoint with two
        budgets would each pace at the account's full quota and drive it at
        twice the published rate during the one setting this exists for.
        """
        kind = budget.endpoint_kind
        existing = self.budgets.get(kind)
        if existing is not None:
            self.meter(kind, **meter_options)  # the options clash check
            return existing
        created = MeteredBudget(budget, self.meter(kind, **meter_options))
        self.budgets[kind] = created
        return created

    def add_requests(self, kind: str, requests: int) -> None:
        """HTTP providers with no budget object (the explorer, DexScreener)."""
        if requests:
            self.meter(kind).requests += requests

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        requests = {k: m.requests for k, m in self.meters.items() if m.requests}
        units = {k: m.units for k, m in self.meters.items() if m.bills_units and m.units}
        return {'requests': requests, 'compute_units': units}
