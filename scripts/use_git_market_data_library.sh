#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${repo_root}"

# Do not let an already-activated sibling repo virtualenv override this repo's Poetry env.
unset VIRTUAL_ENV
unset POETRY_ACTIVE
unset PYTHONHOME
unset PYTHONPATH

# The lock-refresh this script may tell the operator to run. Scrubbed, because
# the environment it defends against is still set in the caller's shell.
relock="(unset VIRTUAL_ENV POETRY_ACTIVE; poetry lock && poetry sync)"

# Both halves of the pin are read from pyproject.toml, not restated here: a
# hardcoded ref went four releases stale and silently downgraded anyone who ran
# this bare. Parsed as TOML rather than matched by regex so that a reformatted
# or relocated declaration fails loudly instead of yielding a plausible value
# from the wrong table.
read -r pinned_repo pinned_ref < <(poetry run python -c '
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    dep = tomllib.load(fh)["tool"]["poetry"]["dependencies"]["market-data-library"]
print(dep["git"], dep["rev"])
' pyproject.toml)

git_repo="${MARKET_DATA_LIBRARY_GIT_REPO:-git+${pinned_repo}}"
git_ref="${MARKET_DATA_LIBRARY_GIT_REF:-${pinned_ref}}"

poetry run python -m pip uninstall -y market-data-library >/dev/null 2>&1 || true
# `--no-deps` like the sibling-override script: the lock governs every other
# package, and without it pip resolved the library's own ranges afresh and
# upgraded h11 past the bound httpcore declares.
poetry run python -m pip install --no-deps --force-reinstall "market-data-library @ ${git_repo}@${git_ref}"

# `--no-deps` turns a dependency the lock does not carry into an ImportError in
# whatever job first reaches it, so detect it here rather than advise it. Likely
# only via MARKET_DATA_LIBRARY_GIT_REF, which the lock does not constrain.
if ! poetry run python -m pip check; then
  echo "market-data-library ${git_ref} needs packages the lock does not have." >&2
  echo "Run: ${relock}" >&2
  exit 1
fi

echo "Installed market-data-library at ${git_ref}."
echo "If that version changed its own dependencies, run: ${relock}"
