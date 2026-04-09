#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${repo_root}"

# Keep the backend Poetry environment authoritative when probing the import source.
unset VIRTUAL_ENV
unset POETRY_ACTIVE
unset PYTHONHOME
unset PYTHONPATH

site_packages="$(poetry run python -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))' )"
override_file="${site_packages}/market_data_library_local_override.pth"

if [[ -f "${override_file}" ]]; then
  override_target="$(head -n 1 "${override_file}")"
  printf 'Dependency mode: local-sibling-override\n'
  printf 'Override file: %s\n' "${override_file}"
  printf 'Configured source: %s\n' "${override_target}"
else
  printf 'Dependency mode: git-installed-package\n'
  printf 'Override file: not present\n'
fi

module_dir="$(poetry run python -c 'import os, market_data_library; print(os.path.realpath(os.path.dirname(market_data_library.__file__)))')"
printf 'Imported module path: %s\n' "${module_dir}"
