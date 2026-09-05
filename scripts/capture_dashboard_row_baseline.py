"""Capture the pre-DR14 `report --json` rows that pin DR14's additive promise.

DR14 says the dashboard slice adds exactly three row keys and changes no
existing key's name, type or value. The implement stage checked that once, by
hand, against the operator store. This script makes it a baseline CI can hold:
it runs `report.py` **as it stood before the slice** (commit `PRE_SLICE_REV`,
the last commit before the prefetch refactor and the DR14 fields) against the
deterministic fixture in `dashboard_route_test.seed_golden`, and writes the rows
to `tests/unit/service/project_monitor/fixtures/`.

`test_no_existing_row_key_changed_name_type_or_value` then reseeds the same
fixture, runs today's `render_json`, strips exactly the three new keys, and
compares. A rename, a dropped key, or a value the prefetch refactor moved shows
up as a diff.

Writes to `project_monitor_test` only, which it truncates first. It never opens
the operator database.

    ENV=dev PYTHONPATH="$(pwd)" poetry run python \
        scripts/capture_dashboard_row_baseline.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.service.project_monitor.config import NETNET  # noqa: E402
from src.service.project_monitor.repository import ProjectMonitorRepository  # noqa: E402

PRE_SLICE_REV = '1cf2f66'
BASELINE = (
    REPO_ROOT / 'tests' / 'unit' / 'service' / 'project_monitor' / 'fixtures'
    / 'dashboard_pre_dr14_rows.json'
)


def _load_by_path(name: str, path: Path):
    """Import a module from a path under an explicit name.

    The name matters for the pre-slice report: it uses relative imports
    (`from .attribution import ...`), which resolve against `__package__`,
    which comes from the dotted name given here. Loading it as
    `src.service.project_monitor.<something>` therefore lets it reach its own
    package from a temporary directory.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    # Loaded by path, not imported: `tests/` carries no package `__init__.py`
    # above the project_monitor directory, so there is no importable
    # `tests.unit...` name to reach the fixture seed and the store URL through.
    conftest = _load_by_path(
        'project_monitor_conftest',
        REPO_ROOT / 'tests' / 'unit' / 'service' / 'project_monitor' / 'conftest.py',
    )
    test_module = _load_by_path(
        'dashboard_route_test',
        REPO_ROOT / 'tests' / 'unit' / 'service' / 'project_monitor'
        / 'dashboard_route_test.py',
    )
    source = subprocess.run(
        ['git', 'show', f'{PRE_SLICE_REV}:src/service/project_monitor/report.py'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        old_path = Path(tmp) / 'pre_slice_report.py'
        old_path.write_text(source)
        old_report = _load_by_path(
            'src.service.project_monitor.pre_slice_report', old_path
        )

    repository = ProjectMonitorRepository(conftest._database_url())
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute(
                f'TRUNCATE {", ".join(conftest.TABLES)} RESTART IDENTITY CASCADE'
            )
        repository.commit()
        test_module.seed_golden(repository)
        rows = json.loads(old_report.render_json(
            old_report.load_epoch_rows(repository, NETNET)
        ))
    finally:
        repository.close()

    BASELINE.write_text(json.dumps(
        {'pre_slice_rev': PRE_SLICE_REV,
         'seed': 'dashboard_route_test.seed_golden',
         'rows': rows},
        indent=2, sort_keys=True,
    ) + '\n')
    print(f'{len(rows)} rows -> {BASELINE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
