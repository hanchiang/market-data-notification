#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
library_root="${repo_root}/../market-data-library"

if [[ ! -f "${library_root}/pyproject.toml" ]]; then
  echo "Expected sibling market-data-library repo at ${library_root}" >&2
  exit 1
fi

cd "${repo_root}"

# Do not let an already-activated sibling repo virtualenv override this repo's Poetry env.
unset VIRTUAL_ENV
unset POETRY_ACTIVE
unset PYTHONHOME
unset PYTHONPATH

site_packages="$(poetry run python -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))' )"
override_file="${site_packages}/market_data_library_local_override.pth"

poetry run python -m pip uninstall -y market-data-library >/dev/null 2>&1 || true
printf '%s\n' "${library_root}" > "${override_file}"

echo "Using local market-data-library from ${library_root}"
