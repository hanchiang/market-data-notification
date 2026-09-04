"""Seed a year of epochs into `project_monitor_test`, for the AC-D7 measurement.

The requirement freezes the dashboard's render bound at 6 seconds for 1,095
present epochs, which is about a year at one epoch every eight hours. The
operator store holds 138, so the bound can only be measured against a synthetic
fill -- and it has to be re-runnable, or the timing in the change summary is a
number nobody can check.

**Writes to the `_test` database only.** The connection string comes from
`RuntimeMode.from_test_mode(True)`, the same resolution the CLI's `--test_mode 1`
uses. The reading TEMPLATE is read from the operator database read-only, so the
synthetic rows have the real shape (50 readings per sample, the same names and
decimals) rather than a shape that would make the measurement optimistic.

Row counts are scaled from the operator store's per-epoch rates as of
2026-09-04: 50 readings, 34 mints and 98 flows per epoch.

Usage:
  PYTHONPATH="$(pwd)" poetry run python scripts/seed_project_monitor_dashboard_bench.py [--epochs 1095]
"""
import argparse
from decimal import Decimal
from typing import Any, Dict, List

import psycopg
from psycopg.rows import dict_row

from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor.config import NETNET, get_project_monitor_database_url
from src.service.project_monitor.repository import ProjectMonitorRepository

MINTS_PER_EPOCH = 34
FLOWS_PER_EPOCH = 98
BLOCKS_PER_EPOCH = 9_600
SECONDS_PER_EPOCH = 8 * 3600
WAD = 10 ** 18

TABLES = ('reading', 'sample', 'mint', 'flow', 'event', 'run')

# rfv() = liquidUsdg + 0.98 x morphoAssets + polRfv, held exactly in wei, so the
# fill raises no identity alert.
POL_WEI = 100 * WAD
MORPHO_WEI = 500 * WAD
# The vault position has to GROW, not sit still. A constant `morphoAssets` makes
# every epoch's accrual zero, which is outside the 10-95 ppm tolerance and put an
# alert line on 1,094 of 1,095 epochs -- so the measurement timed a store in
# permanent alarm rather than a healthy one. 28 ppm per epoch is the median rate
# observed over the backfill. Linear rather than compounded: over 1,095 epochs
# the position grows 3%, so the realised rate drifts 28 -> 27.2 ppm and stays
# well inside the band. Kept a multiple of 10^15 so `morphoAssets x 98` stays
# divisible by 100 and the identity holds in exact integers.
MORPHO_GROWTH_PPM = 28
MORPHO_STEP_WEI = MORPHO_WEI * MORPHO_GROWTH_PPM // 10 ** 6


def _reading_template(project_name: str) -> List[Dict[str, Any]]:
    """One real closing sample's readings, from the operator store, read-only.

    `autocommit=False` is what makes `read_only` mean anything. psycopg 3 applies
    transaction parameters when it OPENS a transaction, and an autocommit
    connection opens none -- so `read_only = True` beside `autocommit=True` is
    inert, and a write on it goes through. Verified on the `_test` database
    (2026-09-04): the autocommit shape accepted an INSERT; this one raises
    `ReadOnlySqlTransaction`. This is the operator's only copy of the record, so
    the guard here has to be the server's, not the author's discipline.
    """
    url = get_project_monitor_database_url(RuntimeMode(is_test_mode=False))
    with psycopg.connect(url, autocommit=False, row_factory=dict_row) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT id FROM sample WHERE project = %s AND epoch_number IS NOT NULL '
                'ORDER BY epoch_number DESC LIMIT 1',
                (project_name,),
            )
            sample_id = cursor.fetchone()['id']
            cursor.execute(
                'SELECT name, contract, tier, raw_hex, value_int, value_json, '
                'decimals, state, error_class FROM reading WHERE sample_id = %s',
                (sample_id,),
            )
            return list(cursor.fetchall())


def seed(epochs: int) -> None:
    project = NETNET.name
    template = _reading_template(project)
    url = get_project_monitor_database_url(RuntimeMode.from_test_mode(True))
    with ProjectMonitorRepository(url) as repository:
        connection = repository.connection
        with connection.cursor() as cursor:
            cursor.execute(f'TRUNCATE {", ".join(TABLES)} RESTART IDENTITY CASCADE')
            cursor.execute(
                "INSERT INTO run (job, outcome) VALUES ('bench-seed', 'ok') RETURNING id"
            )
            run_id = cursor.fetchone()['id']

            sample_ids = _copy_samples(cursor, project, run_id, epochs)
            _copy_readings(cursor, template, sample_ids)
            _copy_mints(cursor, project, epochs)
            _copy_flows(cursor, project, epochs)
        connection.commit()

    print(f'seeded {epochs} present epochs into the _test database')


def _copy_samples(cursor, project: str, run_id: int, epochs: int) -> List[int]:
    with cursor.copy(
        'COPY sample (run_id, project, block, block_timestamp, epoch_number, kind, '
        'endpoint_kind) FROM STDIN'
    ) as copy:
        for epoch in range(1, epochs + 1):
            copy.write_row((
                run_id, project, epoch * BLOCKS_PER_EPOCH,
                1_700_000_000 + epoch * SECONDS_PER_EPOCH, epoch, 'live', 'public',
            ))
    cursor.execute(
        'SELECT id FROM sample WHERE project = %s ORDER BY epoch_number', (project,)
    )
    return [row['id'] for row in cursor.fetchall()]


def _copy_readings(cursor, template: List[Dict[str, Any]], sample_ids: List[int]) -> None:
    """Every epoch gets the template's readings, with the four figures that must
    move per epoch overwritten so growth, dilution and backing are not all zero."""
    import json

    with cursor.copy(
        'COPY reading (sample_id, name, contract, tier, raw_hex, value_int, '
        'value_json, decimals, state, error_class) FROM STDIN'
    ) as copy:
        for index, sample_id in enumerate(sample_ids, start=1):
            moving = _moving_values(index)
            for reading in template:
                value_int = moving.get(reading['name'], reading['value_int'])
                copy.write_row((
                    sample_id, reading['name'], reading['contract'], reading['tier'],
                    reading['raw_hex'], value_int,
                    json.dumps(reading['value_json'])
                    if reading['value_json'] is not None else None,
                    reading['decimals'], reading['state'], reading['error_class'],
                ))


def _moving_values(epoch: int) -> Dict[str, int]:
    liquid = (1_000_000 + epoch * 1_000) * WAD
    morpho = MORPHO_WEI + epoch * MORPHO_STEP_WEI
    rfv = liquid + morpho * 98 // 100 + POL_WEI
    return {
        'Treasury.rfv': rfv,
        'Treasury.liquidUsdg': liquid,
        'Treasury.morphoAssets': morpho,
        'Treasury.polRfv': POL_WEI,
        'Treasury.backingPerToken': (100 + epoch) * WAD // 10,
        'NET.totalSupply': (12_500_000_000 + epoch * 1_000_000),
    }


def _copy_mints(cursor, project: str, epochs: int) -> None:
    classes = ('rebase', 'bond', 'issuance', 'other')
    with cursor.copy(
        'COPY mint (project, block, tx_hash, log_index, recipient, amount, decimals, '
        'class) FROM STDIN'
    ) as copy:
        for epoch in range(1, epochs + 1):
            base = epoch * BLOCKS_PER_EPOCH
            for i in range(MINTS_PER_EPOCH):
                copy.write_row((
                    project, base - i, f'0xm{epoch}_{i}', i, f'0xrecipient{i % 20}',
                    Decimal(1_000_000 + i), 9, classes[i % len(classes)],
                ))


def _copy_flows(cursor, project: str, epochs: int) -> None:
    """The bucket mix the real store shows: a few labels plus many one-off
    unlabelled senders, which is what makes DR14's fold worth measuring."""
    labels = ('bond', 'issuance', 'rwaDesk', 'taxCollector')
    with cursor.copy(
        'COPY flow (project, block, tx_hash, log_index, direction, counterparty, '
        'amount, decimals, label, rule) FROM STDIN'
    ) as copy:
        for epoch in range(1, epochs + 1):
            base = epoch * BLOCKS_PER_EPOCH
            for i in range(FLOWS_PER_EPOCH):
                labelled = i % 3 == 0
                label = labels[i % len(labels)] if labelled else 'unlabelled'
                counterparty = f'0xparty{i % 4}' if labelled else f'0xsender{epoch}_{i}'
                copy.write_row((
                    project, base - i, f'0xf{epoch}_{i}', i,
                    'in' if i % 2 == 0 else 'out', counterparty,
                    Decimal(2_000_000 + i), 6, label,
                    'label:registry' if labelled else 'unlabelled',
                ))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--epochs', type=int, default=1095)
    args = parser.parse_args()
    seed(args.epochs)
