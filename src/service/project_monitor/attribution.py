"""Inflow and outflow attribution, in requirement R6's order.

The order is not a preference list -- each rule exists because the one after it
gets the answer wrong for that case:

1. **bond**, by TRANSACTION, not by sender. A bond purchase is paid from the
   bonder's own wallet, so the Bond Depository is never the USDG sender:
   measured 2026-08-29, 332 inflows into the Treasury, **zero** from the
   depository, while 124 of them (53% of value) shared a transaction hash with
   a NET mint to it. Sender labelling alone cannot see a single bond.
2. **issuance**, by transaction for the same reason: the USDG sender is the
   buyer's wallet, not the premium sales desk.
3. **labelled sender**, from the project's own address graph.
4. **unlabelled**, carrying the address. No flow is dropped for lack of a label
   -- an unattributed flow is a fact about our labelling, not about the chain.

Rule 1 has two implementations. The primary reads the `BondCreated` log, which
carries depositor, market id and both amounts in one record. Rule 1b correlates
a USDG inflow with a NET mint to the depository in the same transaction, and is
used only where `BondCreated` is absent from the deployed bytecode. Both are
implemented because the design could not verify the event's presence when it was
written; it is present (checked 2026-08-30), so 1b is the fallback in practice.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .config import ProjectConfig
from .logs import MINT_BOND, MINT_ISSUANCE

RULE_BOND_EVENT = 'bond:BondCreated'
RULE_BOND_CORRELATION = 'bond:same-transaction-mint'
RULE_ISSUANCE_CORRELATION = 'issuance:same-transaction-mint'
RULE_LABELLED_SENDER = 'labelled-counterparty'
RULE_UNLABELLED = 'unlabelled'

LABEL_BOND = 'bond'
LABEL_ISSUANCE = 'issuance'
LABEL_UNLABELLED = 'unlabelled'

DIRECTION_IN = 'in'
DIRECTION_OUT = 'out'


@dataclass(frozen=True)
class AttributionResult:
    label: str
    rule: str
    # Present only for the bond path, where the event carries them.
    depositor: Optional[str] = None
    market_id: Optional[int] = None


def index_mints_by_transaction(
    mint_rows: Sequence[Dict[str, object]]
) -> Dict[str, List[str]]:
    """`{transaction hash: [mint class, ...]}` for the correlation rules."""
    index: Dict[str, List[str]] = {}
    for row in mint_rows:
        index.setdefault(str(row['tx_hash']).lower(), []).append(str(row['class']))
    return index


def index_bond_events_by_transaction(
    event_rows: Sequence[Dict[str, object]]
) -> Dict[str, Dict[str, object]]:
    return {
        str(row['tx_hash']).lower(): row
        for row in event_rows
        if row.get('name') == 'BondCreated'
    }


def attribute_inflow(
    *,
    tx_hash: str,
    sender: str,
    project: ProjectConfig,
    mints_by_tx: Dict[str, List[str]],
    bond_events_by_tx: Dict[str, Dict[str, object]],
    use_bond_event: bool = True,
) -> AttributionResult:
    key = tx_hash.lower()

    if use_bond_event and key in bond_events_by_tx:
        fields = bond_events_by_tx[key].get('fields_json') or {}
        return AttributionResult(
            label=LABEL_BOND,
            rule=RULE_BOND_EVENT,
            depositor=fields.get('depositor'),
            market_id=int(fields['marketId']) if 'marketId' in fields else None,
        )

    classes = mints_by_tx.get(key, [])
    if MINT_BOND in classes:
        return AttributionResult(label=LABEL_BOND, rule=RULE_BOND_CORRELATION)
    if MINT_ISSUANCE in classes:
        return AttributionResult(label=LABEL_ISSUANCE, rule=RULE_ISSUANCE_CORRELATION)

    label = project.label_for(sender)
    if label:
        return AttributionResult(label=label, rule=RULE_LABELLED_SENDER)
    return AttributionResult(label=LABEL_UNLABELLED, rule=RULE_UNLABELLED)


def attribute_outflow(recipient: str, project: ProjectConfig) -> AttributionResult:
    """Outflows are attributed by recipient, the same way inflows are by sender.

    The two correlation rules do not apply: they exist because a bond's payer is
    not the desk, which is a fact about who *sends* USDG in.
    """
    label = project.label_for(recipient)
    if label:
        return AttributionResult(label=label, rule=RULE_LABELLED_SENDER)
    return AttributionResult(label=LABEL_UNLABELLED, rule=RULE_UNLABELLED)


def build_flow_rows(
    *,
    project_name: str,
    project: ProjectConfig,
    inflows: Iterable[Dict[str, object]],
    outflows: Iterable[Dict[str, object]],
    mint_rows: Sequence[Dict[str, object]],
    bond_event_rows: Sequence[Dict[str, object]],
    usdg_decimals: int,
    use_bond_event: bool = True,
) -> List[Dict[str, object]]:
    mints_by_tx = index_mints_by_transaction(mint_rows)
    bond_events_by_tx = index_bond_events_by_transaction(bond_event_rows)

    rows: List[Dict[str, object]] = []
    for transfer in inflows:
        result = attribute_inflow(
            tx_hash=str(transfer['tx_hash']),
            sender=str(transfer['from']),
            project=project,
            mints_by_tx=mints_by_tx,
            bond_events_by_tx=bond_events_by_tx,
            use_bond_event=use_bond_event,
        )
        rows.append(
            {
                'project': project_name,
                'block': transfer['block'],
                'tx_hash': transfer['tx_hash'],
                'log_index': transfer['log_index'],
                'direction': DIRECTION_IN,
                'counterparty': transfer['from'],
                'amount': transfer['value'],
                'decimals': usdg_decimals,
                'label': result.label,
                'rule': result.rule,
            }
        )
    for transfer in outflows:
        result = attribute_outflow(str(transfer['to']), project)
        rows.append(
            {
                'project': project_name,
                'block': transfer['block'],
                'tx_hash': transfer['tx_hash'],
                'log_index': transfer['log_index'],
                'direction': DIRECTION_OUT,
                'counterparty': transfer['to'],
                'amount': transfer['value'],
                'decimals': usdg_decimals,
                'label': result.label,
                'rule': result.rule,
            }
        )
    return rows
