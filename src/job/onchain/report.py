"""Print a dossier. Stored rows only, so it runs anywhere the store is restored.

Usage:
  PYTHONPATH="$(pwd)" poetry run python src/job/onchain/report.py \\
      --project touch-grass [--json] [--build 12] [--test_mode 1]
  PYTHONPATH="$(pwd)" poetry run python src/job/onchain/report.py --all

The connection is opened READ ONLY, so the server refuses a write on it: the
report is the surface an operator runs while a build is in flight, and it must
not be able to touch what the build is writing.
"""
import argparse
import sys
from typing import Optional

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain.config import get_onchain_database_url
from src.service.onchain.report import (
    UnknownBuildError,
    UnknownProjectError,
    load_all,
    load_dossier,
    render_dossier,
    render_json,
)
from src.service.onchain.repository import OnchainRepository

# A build whose outcome is not `ok` exits non-zero so a scheduled invocation
# cannot report it only in text nobody reads. The dossier still prints in full.
EXIT_BUILD_NOT_OK = 2
# A typo in `--project` or a `--build` id that is another project's: one line
# on stderr, not a traceback, and a code distinct from "built but not ok".
EXIT_NOT_FOUND = 3


def main(
    project: Optional[str] = None,
    all_projects: bool = False,
    as_json: bool = False,
    build: Optional[int] = None,
    test_mode: bool = False,
) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    with OnchainRepository(
        get_onchain_database_url(runtime_mode), read_only=True
    ) as repository:
        if all_projects or project is None:
            dossiers = load_all(repository)
        else:
            try:
                dossiers = [load_dossier(repository, project, build_id=build)]
            except (UnknownProjectError, UnknownBuildError) as exc:
                print(exc.args[0], file=sys.stderr)
                return EXIT_NOT_FOUND

    if as_json:
        print(render_json(dossiers if len(dossiers) != 1 else dossiers[0]))
    else:
        print('\n\n'.join(render_dossier(dossier) for dossier in dossiers))

    not_ok = [
        dossier
        for dossier in dossiers
        if dossier.get('build') and dossier['build']['outcome'] != 'ok'
    ]
    return EXIT_BUILD_NOT_OK if not_ok else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=str, default=None)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--build', type=int, default=None)
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(
        main(
            project=args.project,
            all_projects=args.all,
            as_json=args.json,
            build=args.build,
            test_mode=bool(args.test_mode),
        )
    )
