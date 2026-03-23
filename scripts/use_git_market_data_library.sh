#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git_repo="${MARKET_DATA_LIBRARY_GIT_REPO:-git+ssh://git@github.com/hanchiang/market_data_api.git}"
git_ref="${MARKET_DATA_LIBRARY_GIT_REF:-1.0.0}"

cd "${repo_root}"

poetry run python -m pip install --force-reinstall "market-data-library @ ${git_repo}@${git_ref}"
