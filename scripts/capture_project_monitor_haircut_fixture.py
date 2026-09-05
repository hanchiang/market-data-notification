"""Rebuild `tests/.../fixtures/haircut_epochs.json` from the operator store.

Six real epochs -- two the ticket names by value plus the quiet epoch and the
predecessor each of them needs -- so the haircut tests assert the figures the
chain produced rather than figures invented to match the model.

The tests are fixture-based rather than pointed at this store on purpose: the
suite runs against a truncated `project_monitor_test` (and in CI against a tmpfs
server that has never seen a backfill), so a store-backed assertion would be
green only on the operator's machine and would silently skip everywhere else.

Usage (needs the operator stack up, and reads it read-only):
  PYTHONPATH="$(pwd)" python scripts/capture_project_monitor_haircut_fixture.py
"""
import json
import os
from pathlib import Path

import psycopg

DEFAULT_URL = 'postgresql://postgres:devpass@127.0.0.1:55432/project_monitor'
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / 'tests/unit/service/project_monitor/fixtures/haircut_epochs.json'
)
READINGS = (
    'Treasury.rfv',
    'Treasury.liquidUsdg',
    'Treasury.morphoAssets',
    'Treasury.polRfv',
)
# Each group is consecutive and starts one epoch before the first one asserted:
# a residual is a delta, so the earliest epoch in a group can only serve as the
# predecessor of the next.
GROUPS = ((118, 119, 120), (131, 132, 133))

PROVENANCE = {
    'source': (
        'the operator store: compose service project_monitor_postgres, database '
        'project_monitor, 133 backfilled epochs as of 2026-08-31'
    ),
    'readings': (
        "each epoch's closing sample and its four Treasury readings, value_int in "
        'wei exactly as the chain returned them'
    ),
    'flows': (
        "every flow row in the epoch's (previous close block, close block] window, "
        'summed per (direction, counterparty, label). The report groups on exactly '
        'those three, so its totals are identical to the ones the individual rows '
        'would give; only the row count differs.'
    ),
    'rebuild': 'scripts/capture_project_monitor_haircut_fixture.py',
}


def main() -> int:
    url = os.getenv('PROJECT_MONITOR_DATABASE_URL', DEFAULT_URL)
    epochs = {}
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        for group in GROUPS:
            for epoch in group:
                cursor.execute(
                    'SELECT DISTINCT ON (epoch_number) id, block, block_timestamp, '
                    'project FROM sample WHERE epoch_number = %s '
                    'ORDER BY epoch_number, block DESC, id DESC',
                    (epoch,),
                )
                sample_id, block, timestamp, project = cursor.fetchone()
                cursor.execute(
                    'SELECT name, value_int::text, decimals FROM reading '
                    'WHERE sample_id = %s AND name = ANY(%s) ORDER BY name',
                    (sample_id, list(READINGS)),
                )
                epochs[str(epoch)] = {
                    'block': block,
                    'block_timestamp': timestamp,
                    'readings': {
                        name: {'value_int': value, 'decimals': decimals}
                        for name, value, decimals in cursor.fetchall()
                    },
                    'flows': [],
                }
            for previous, epoch in zip(group, group[1:]):
                cursor.execute(
                    'SELECT direction, counterparty, label, sum(amount)::text, '
                    'max(decimals) FROM flow WHERE project = %s AND block > %s '
                    'AND block <= %s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3',
                    (project, epochs[str(previous)]['block'], epochs[str(epoch)]['block']),
                )
                epochs[str(epoch)]['flows'] = [
                    {
                        'direction': direction,
                        'counterparty': counterparty,
                        'label': label,
                        'amount': amount,
                        'decimals': decimals,
                    }
                    for direction, counterparty, label, amount, decimals in cursor.fetchall()
                ]
    OUTPUT.write_text(
        json.dumps(
            {'provenance': PROVENANCE, 'groups': [list(g) for g in GROUPS], 'epochs': epochs},
            indent=2,
            sort_keys=True,
        )
        + '\n'
    )
    print(f'wrote {OUTPUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
