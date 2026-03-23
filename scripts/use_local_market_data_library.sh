#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
library_root="${repo_root}/../market-data-library"

if [[ ! -f "${library_root}/pyproject.toml" ]]; then
  echo "Expected sibling market-data-library repo at ${library_root}" >&2
  exit 1
fi

cd "${repo_root}"

poetry run python -m pip install --editable "${library_root}"
