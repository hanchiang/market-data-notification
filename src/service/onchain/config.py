"""Chain constants, protocol addresses and endpoint resolution for the dossier.

Endpoint and database resolution is **imported from the project monitor, never
re-implemented**: the two packages share one Postgres server and one metered
archive key, and a second copy of `get_archive_endpoint` is how the CU budget
quietly becomes two budgets. The re-exports at the bottom are the whole of that
boundary (design D1, Components).

The Uniswap addresses here are not a second source of truth for the registry
file -- the file is authoritative and is what the build loads. They are the
tripwire: `registry.validate` compares a known chain's registry entry against
them and rejects a mismatch, so an address that was verified on chain cannot be
edited away by accident. Their verification is recorded beside them.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# Imported for its import-time `load_dotenv()`, and for the redacting log-record
# factory it installs: no logger in this package may be configured before that
# factory is in place. The onchain observability module depends on it.
from src.config import config as _backend_config  # noqa: F401
from src.service.project_monitor.config import (
    BLOCKS_PER_HOUR,
    BLOCKS_PER_SECOND,
    get_archive_endpoint,
    get_project_monitor_database_url,
    get_public_endpoint,
)

ROBINHOOD_CHAIN_ID = 4663

# The provider's own chain slug. Not derivable from the chain id: providers name
# chains differently, which is the defect the library's chain-id map exists for.
DEXSCREENER_CHAIN_SLUG: Dict[int, str] = {ROBINHOOD_CHAIN_ID: 'robinhood'}

# The dossier's schema inside the project monitor's database. One schema per
# concern; the monitor's tables stay in `public` and are not touched.
ONCHAIN_SCHEMA = 'onchain'

# Distinct from the monitor's `ADVISORY_LOCK_KEY` (0x704D6F6E, 'pMon') so the
# monitor's `record` and this package's `build` can run at the same time. The
# lock still serialises build-versus-build, which is what it is for.
ADVISORY_LOCK_KEY = 0x6F4E4348  # 'oNCH'

# Every archetype the registry accepts. Phase 1a implements the first only; an
# unknown value is rejected at registry validation rather than reaching a
# collector that would silently build the wrong holder derivation (design DG-5).
ARCHETYPE_LAUNCHPAD_FIXED_SUPPLY = 'launchpad-fixed-supply'
KNOWN_ARCHETYPES = frozenset({ARCHETYPE_LAUNCHPAD_FIXED_SUPPLY})

# Archetypes with no treasury series. `not_applicable` is a stored value, not a
# blank: "this project has no treasury" and "the treasury read failed" must not
# look the same in a diff (requirement V4).
ARCHETYPES_WITHOUT_TREASURY = frozenset({ARCHETYPE_LAUNCHPAD_FIXED_SUPPLY})

SOURCE_CLASSES = frozenset(
    {'chain_rpc', 'chain_explorer', 'dex_provider', 'web', 'x', 'telegram'}
)

# Read from Uniswap's deployment pages and verified on chain on 2026-09-06
# (design, The registry file): code at every address; both v3 seed pools and the
# v3 position manager report `factory()` equal to the v3 factory; the v4 position
# manager and the state view report `poolManager()` equal to the pool manager;
# the state view answers both v4 seed pool ids with a non-zero `sqrtPriceX96`.
VERIFIED_UNISWAP_ADDRESSES: Dict[int, Dict[str, str]] = {
    ROBINHOOD_CHAIN_ID: {
        'v3_factory': '0x1f7d7550b1b028f7571e69a784071f0205fd2efa',
        'v3_position_manager': '0x73991a25c818bf1f1128deaab1492d45638de0d3',
        'v4_pool_manager': '0x8366a39cc670b4001a1121b8f6a443a643e40951',
        'v4_position_manager': '0x58daec3116aae6d93017baaea7749052e8a04fa7',
        'v4_state_view': '0xf3334192d15450cdd385c8b70e03f9a6bd9e673b',
    }
}

DEFAULT_REGISTRY_PATH = Path(__file__).parent / 'registry' / 'projects.json'

# `~/onchain-data/logs/onchain/`, beside the chain-heat series' own data
# directory rather than inside the checkout: a worktree is disposable and a log
# a failure is diagnosed from is not.
DEFAULT_LOG_DIR = Path.home() / 'onchain-data' / 'logs' / 'onchain'
LOG_RETENTION_DAYS = 14

# The watcher runs two hours after the build, so a build cron that did not fire
# is caught on the first missed night at an age of 26 hours. A deadline of 24
# would fire on an ordinary run that started a few minutes late.
DEFAULT_BUILD_DEADLINE_HOURS = 25


@dataclass(frozen=True)
class ChainConstants:
    """Per-chain figures that are properties of the chain, not of the registry."""

    chain_id: int
    blocks_per_second: float
    blocks_per_hour: int


CHAIN_CONSTANTS: Dict[int, ChainConstants] = {
    ROBINHOOD_CHAIN_ID: ChainConstants(
        chain_id=ROBINHOOD_CHAIN_ID,
        # Re-derived from the chain itself on 2026-08-30 by the monitor; imported
        # rather than restated so one measurement serves both packages.
        blocks_per_second=BLOCKS_PER_SECOND,
        blocks_per_hour=BLOCKS_PER_HOUR,
    )
}


def get_chain_constants(chain_id: int) -> ChainConstants:
    if chain_id not in CHAIN_CONSTANTS:
        raise KeyError(f'no chain constants for chain id {chain_id}')
    return CHAIN_CONSTANTS[chain_id]


def get_registry_path() -> Path:
    return _resolve(os.getenv('ONCHAIN_REGISTRY_PATH'), DEFAULT_REGISTRY_PATH)


def get_log_dir() -> Path:
    return _resolve(os.getenv('ONCHAIN_LOG_DIR'), DEFAULT_LOG_DIR)


def _resolve(value: Optional[str], default: Path) -> Path:
    """A path from the environment, with `~` expanded.

    `.env` is read as literal text, so `ONCHAIN_LOG_DIR=~/onchain-data/logs`
    arrives with the tilde intact and `Path()` would happily create a directory
    actually named `~` under the process's cwd -- which for a cron job is
    wherever cron started it.
    """
    if value is None or not value.strip():
        return default
    return Path(value.strip()).expanduser()


def get_build_deadline_hours() -> float:
    return float(
        os.getenv('ONCHAIN_BUILD_DEADLINE_HOURS', str(DEFAULT_BUILD_DEADLINE_HOURS))
    )


# The job's own monthly ceiling on the metered account, in compute units, the
# second guard behind the operator's $1 provider cap (`kb/decisions.md`
# 2026-09-10). The three numbers below are one derivation and `config_test.py`
# re-runs it: the default must stay under the job's share of the cap, leaving
# the rest of the $1 to the NETNET monitor, whose spend on the same key is not
# recorded anywhere the job can read. A steady month is ~200k CU; the backfill
# is a few hundred calls. Configuration, not code: the cost gate freezes it per phase.
ALCHEMY_USD_PER_MILLION_CU = 0.45  # Pay-As-You-Go list price, vendor docs 2026-09-09
ALCHEMY_JOB_SHARE_OF_CAP_USD = 0.70  # of the operator's $1 provider cap
DEFAULT_ALCHEMY_MONTHLY_CU_CEILING = 1_500_000  # about $0.68

LOG_ENDPOINT_PUBLIC = 'public'
LOG_ENDPOINT_ARCHIVE = 'archive'
LOG_ENDPOINTS = frozenset({LOG_ENDPOINT_PUBLIC, LOG_ENDPOINT_ARCHIVE})


def get_alchemy_monthly_cu_ceiling() -> int:
    """Compute units the job may bill this month; a positive integer.

    Rejected at read time rather than at the first call: a bad value would
    otherwise fail the run as a bare `ValueError` with no name, and `0` would
    refuse every call while looking like a configured ceiling.
    """
    value = os.getenv('ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING', '').strip()
    if not value:
        return DEFAULT_ALCHEMY_MONTHLY_CU_CEILING
    if not value.isdecimal() or int(value) <= 0:
        raise ValueError(
            f'ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING must be a positive integer, not {value!r}'
        )
    return int(value)


def get_log_endpoint() -> str:
    """Which endpoint serves log windows: `archive` (default) or `public`.

    Rejected rather than defaulted on a typo: `ONCHAIN_LOG_ENDPOINT=archvie`
    silently running the nightly on the public node is the exact failure the
    setting exists to avoid. Default `archive` since the operator kept the
    account on Pay-As-You-Go (`kb/decisions.md` 2026-09-10, second amendment
    2026-09-11); `public` is the opt-out.
    """
    value = os.getenv('ONCHAIN_LOG_ENDPOINT', LOG_ENDPOINT_ARCHIVE).strip().lower()
    if value not in LOG_ENDPOINTS:
        raise ValueError(
            f'ONCHAIN_LOG_ENDPOINT must be one of {sorted(LOG_ENDPOINTS)}, not {value!r}'
        )
    return value


def get_dexscreener_slug(chain_id: int) -> str:
    if chain_id not in DEXSCREENER_CHAIN_SLUG:
        raise KeyError(f'no dexscreener slug for chain id {chain_id}')
    return DEXSCREENER_CHAIN_SLUG[chain_id]


def get_blockscout_api_key() -> Optional[str]:
    """The explorer key, or None where none is configured.

    Optional rather than required so a checkout without a key still builds: the
    explorer is its own failure unit (design D8), so an unkeyed run degrades to
    `partial` with the chain-derived fields intact instead of refusing to start.
    Empty and whitespace-only read as absent. Why stripping matters: PR #41.
    """
    return os.getenv('BLOCKSCOUT_API_KEY', '').strip() or None


def get_onchain_database_url(runtime_mode: Optional[object] = None) -> str:
    """The dossier's store is the monitor's database, one schema over.

    A thin alias rather than an env var of its own: two connection strings for
    one server is how a `pg_dump` restore ends up with half the data.
    """
    return get_project_monitor_database_url(runtime_mode)  # type: ignore[arg-type]


__all__ = [
    'ADVISORY_LOCK_KEY',
    'ARCHETYPES_WITHOUT_TREASURY',
    'ARCHETYPE_LAUNCHPAD_FIXED_SUPPLY',
    'CHAIN_CONSTANTS',
    'DEFAULT_ALCHEMY_MONTHLY_CU_CEILING',
    'DEFAULT_BUILD_DEADLINE_HOURS',
    'DEFAULT_LOG_DIR',
    'DEFAULT_REGISTRY_PATH',
    'KNOWN_ARCHETYPES',
    'LOG_ENDPOINTS',
    'LOG_ENDPOINT_ARCHIVE',
    'LOG_ENDPOINT_PUBLIC',
    'LOG_RETENTION_DAYS',
    'ONCHAIN_SCHEMA',
    'ROBINHOOD_CHAIN_ID',
    'SOURCE_CLASSES',
    'VERIFIED_UNISWAP_ADDRESSES',
    'ChainConstants',
    'ALCHEMY_JOB_SHARE_OF_CAP_USD',
    'ALCHEMY_USD_PER_MILLION_CU',
    'get_alchemy_monthly_cu_ceiling',
    'get_archive_endpoint',
    'get_blockscout_api_key',
    'get_build_deadline_hours',
    'get_chain_constants',
    'get_dexscreener_slug',
    'get_log_dir',
    'get_log_endpoint',
    'get_onchain_database_url',
    'get_project_monitor_database_url',
    'get_public_endpoint',
    'get_registry_path',
]
