#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git_repo="${MARKET_DATA_LIBRARY_GIT_REPO:-git+ssh://git@github.com/hanchiang/market_data_api.git}"
git_ref="${MARKET_DATA_LIBRARY_GIT_REF:-1.3.0}"

cd "${repo_root}"

# Do not let an already-activated sibling repo virtualenv override this repo's Poetry env.
unset VIRTUAL_ENV
unset POETRY_ACTIVE
unset PYTHONHOME
unset PYTHONPATH

site_packages="$(poetry run python -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))' )"
override_file="${site_packages}/market_data_library_local_override.pth"

rm -f "${override_file}"
poetry run python -m pip install --force-reinstall "market-data-library @ ${git_repo}@${git_ref}"
