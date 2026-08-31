"""Print the AC5 epoch table. Runs against any connection string.

No chain access: every figure comes from stored rows, which is what makes the
report runnable on a `pg_dump` restored onto the operator's machine.

Usage:
  PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/report.py [--json]
"""
import argparse

from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor.config import NETNET, get_project_monitor_database_url
from src.service.project_monitor.report import (
    identity_breaks,
    load_epoch_rows,
    render_json,
    render_table,
)
from src.service.project_monitor.repository import ProjectMonitorRepository

EXIT_IDENTITY_BROKEN = 2


def main(as_json: bool = False, test_mode: bool = False) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    with ProjectMonitorRepository(
        get_project_monitor_database_url(runtime_mode)
    ) as repository:
        rows = load_epoch_rows(repository, NETNET)
    print(render_json(rows) if as_json else render_table(rows))
    # A broken `rfv()` identity exits non-zero so a scheduled run cannot report
    # it only in text nobody reads. The table still prints in full: the epochs
    # are what says how far back the break goes.
    return EXIT_IDENTITY_BROKEN if identity_breaks(rows) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(main(as_json=args.json, test_mode=bool(args.test_mode)))
