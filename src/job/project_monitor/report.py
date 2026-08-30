"""Print the AC5 epoch table. Runs against any connection string.

No chain access: every figure comes from stored rows, which is what makes the
report runnable on a `pg_dump` restored onto the operator's machine.

Usage:
  PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/report.py [--json]
"""
import argparse

from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor.config import NETNET, get_project_monitor_database_url
from src.service.project_monitor.report import load_epoch_rows, render_json, render_table
from src.service.project_monitor.repository import ProjectMonitorRepository


def main(as_json: bool = False, test_mode: bool = False) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    with ProjectMonitorRepository(
        get_project_monitor_database_url(runtime_mode)
    ) as repository:
        rows = load_epoch_rows(repository, NETNET.name)
    print(render_json(rows) if as_json else render_table(rows))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(main(as_json=args.json, test_mode=bool(args.test_mode)))
