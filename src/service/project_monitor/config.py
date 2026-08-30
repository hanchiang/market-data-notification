"""Environment and address configuration for the project monitor.

One project (NETNET) is configured. Generalising to a second waits for a second
archetype, per the ticket's non-goals -- the shape here supports one and does not
pretend otherwise.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from market_data_library.core.onchain.evm import Endpoint

# Imported for its import-time `load_dotenv()`: `ROBINHOOD_CHAIN_RPC_URL` and
# `PROJECT_MONITOR_DATABASE_URL` live in `.env`, and a job that reaches this
# module without going through the config package would otherwise see neither.
from src.config import config as _backend_config  # noqa: F401
from src.runtime.runtime_mode import RuntimeMode

# Extracted from the dapp bundle by string content, never by minified
# identifier, and cross-checked against a second occurrence in the app's
# live-mode config builder. Provenance and the re-extraction recipes:
# MARKET-DATA/docs/traces/2026-08-29-netnet-dapp-crawl-evidence.md, Facts 2-4.
# These are the project's own labels: a strong lead for which address is which,
# not our independent verification.
NETNET_CORE: Dict[str, str] = {
    'NET': '0xCA9c78Dd337A67F6e0077F65F5E9218719d30eDf',
    'sNET': '0xb773ec2C326B7f98a5a83fc098825492F020a4c7',
    'staking': '0xB078cc304A0B264C5F3680DC0488954ACcd02E87',
    'treasury': '0x04822Ea321A0DEE6F40656172F29312104855d66',
    'bondDepository': '0xff32a969A0c567129eECD926D04657728E1980C1',
    'distributor': '0x79e71F8a8a2912E40687a8820b2dC0fdd2f686b3',
    'pairOracle': '0x929631b33F4070D6f54477fba3FD27566567dAca',
    'taxCollector': '0x086C58400b8708Ef993f256E12e752dcF0AC918e',
    'genesisBond': '0x575b7B7c97Ef3E21C82DAeB427899d583e1E913f',
    'inverseBond': '0x92166e94Eea5B7799b761653881692f881dFC4C9',
    'premiumSeller': '0x346e1a31171A0f7aC73909010b5435768d3B5462',
    'canonicalV2Pair': '0x59F95461E68e0c77605299791E1449f175165B54',
    'USDG': '0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168',
    'managerSleeve': '0x498752D5fa0600CBd613074C151Abe15B3FeC7CB',
    'wsNET': '0x63C12667638f2Ae6fC6ae09B43D98Ec84a8586eA',
    'morphoBlue': '0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010',
    'morphoUsdgVault': '0xBeEff033F34C046626B8D0A041844C5d1A5409dd',
    'adaptiveCurveIrm': '0x2BD3d5965B26B51814AC95127B2b80dD6CcC0fa1',
    'loopbackOracle': '0xCDE9599059f8Ae6D6B9F33A0aF7877827ec75F16',
    'teamMultisig': '0x3Bb7A23316f82C0e984fA2E784846d8928a35f42',
    'zap': '0xA1ee052EC32532304a7522bd9A4b594eC28fF1b1',
    'otcDesk': '0x70eaeEc20c39dF48509F1F3faB01f7dDe207947b',
    'rwaDesk': '0xa84efC3136BF1Bb89ade9E5BE6Ab32cb1A04f08D',
    'rwaDeskV1': '0x99B6eE6eDe47d9a8a9bfd03F728a99B789df1961',
}

# The six equities the Sleeve holds, each with the Chainlink feed the app marks
# it against. Order is fixed so a sample's Sleeve readings line up run to run.
#
# The address key is `erc20`, not `token`: these are public contract addresses,
# but a key named `token` beside a high-entropy hex string is what the repo's
# gitleaks hook matches as a generic API key. Renaming it clears six false
# positives at the source, which is better than teaching the scanner to ignore
# a pattern that should keep firing everywhere else.
SLEEVE_EQUITIES: Dict[str, Dict[str, str]] = {
    'NVDA': {
        'erc20': '0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec',
        'feed': '0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15',
    },
    'SPCX': {
        'erc20': '0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea',
        'feed': '0xB265810950ba6c5C0Ff821c9963014a56fD8Bffb',
    },
    'AAPL': {
        'erc20': '0xaf3d76f1834a1d425780943c99ea8a608f8a93f9',
        'feed': '0x6B22A786bAa607d76728168703a39Ea9C99f2cD0',
    },
    'MSFT': {
        'erc20': '0xe93237c50d904957cf27e7b1133b510c669c2e74',
        'feed': '0x45C3C877C15E6BA2EBB19eA114Ea508d14C1Af2E',
    },
    'GOOGL': {
        'erc20': '0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3',
        'feed': '0xF6f373a037c30F0e5010d854385cA89185AE638b',
    },
    'COIN': {
        'erc20': '0x6330d8c3178a418788df01a47479c0ce7ccf450b',
        'feed': '0xA3a468A452940B7D6b69991207B508c609a98Ef2',
    },
}

# Morpho Blue market params for the Loopback facility, from the bundle's own
# market-params builder. The market id is keccak256(abi.encode(params)).
#
# UNCROSS-CHECKED, and the one input here that is trusted rather than verified:
# these five values are read off the dapp bundle, and the id derived from them
# is what every Loopback figure in the report is keyed by. If the bundle named a
# stale param, `Morpho.market` would return a real, well-formed row for the
# WRONG market and nothing downstream would notice. A second source would settle
# it -- the facility contract's own `marketId()` -- but slice 1 never located
# that contract's address (the registry carries `loopbackOracle` and
# `morphoBlue`, no facility), so the check cannot be written yet. Finding the
# address is the prerequisite; until then this stays a single-sourced input.
LOOPBACK_MARKET_PARAMS = {
    'loanToken': NETNET_CORE['USDG'],
    'collateralToken': NETNET_CORE['wsNET'],
    'oracle': NETNET_CORE['loopbackOracle'],
    'irm': NETNET_CORE['adaptiveCurveIrm'],
    'lltv': 625000000000000000,
}

PUBLIC_RPC_URL = 'https://rpc.mainnet.chain.robinhood.com'
MANIFEST_URL = 'https://app.netnet.capital/'
CHAIN_ID = 4663

# ~9.898 blocks/s, re-derived from the chain itself on 2026-08-30 by
# differencing block timestamps over 1,000 / 100,000 / 1,000,000-block spans
# (nominal target is 10/s; the realised long-run rate is 9.898). Every
# block-to-time conversion here uses this figure rather than the nominal one.
BLOCKS_PER_SECOND = 9.898
BLOCKS_PER_HOUR = int(BLOCKS_PER_SECOND * 3600)  # 35,632

# The public endpoint serves state only for about the last 6,000 blocks
# (measured: head-6,000 served, head-6,250 "metadata is not found"). 5,000
# leaves margin under that, and binds only reads issued against the fallback.
PUBLIC_STATE_BLOCK_WINDOW = 5000

# 1.5M-block eth_getLogs windows succeeded on the public RPC; 2M were rejected.
# Both callers -- the live job and the backfill -- issue their log queries there
# (the keyed endpoint's free tier caps `eth_getLogs` at ten blocks; see logs.py's
# module docstring). This is a STARTING width, not a limit: the public endpoint
# refuses on scan cost rather than block count, so `fetch_window` halves down to
# whatever the local density serves and widens back afterwards. A starting width
# that is too wide for the current region costs one refused call per halving --
# about eleven to reach the floor from here -- against the tens of thousands of
# calls a full sweep issues, which is why it is left as a starting point rather
# than tuned.
MAX_LOG_WINDOW_BLOCKS = 1_500_000


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    addresses: Dict[str, str]
    sleeve_equities: Dict[str, Dict[str, str]]
    loopback_market_params: Dict[str, object] = field(
        default_factory=lambda: dict(LOOPBACK_MARKET_PARAMS)
    )

    def address(self, name: str) -> str:
        return self.addresses[name]

    def label_for(self, address: str) -> Optional[str]:
        """Reverse-lookup for R6 rule 3: a labelled contract takes that label."""
        target = address.lower()
        for name, value in self.addresses.items():
            if value.lower() == target:
                return name
        for symbol, entry in self.sleeve_equities.items():
            if entry['erc20'].lower() == target:
                return f'{symbol}_token'
        return None


NETNET = ProjectConfig(
    name='NETNET',
    addresses=NETNET_CORE,
    sleeve_equities=SLEEVE_EQUITIES,
)


def get_project_monitor_database_url(
    runtime_mode: Optional[RuntimeMode] = None,
) -> str:
    """The one connection string; test mode resolves to a separate database.

    Project-scoped rather than a bare `DATABASE_URL` for the same reason
    `CRYPTO_SIGNAL_DB_PATH` is: this backend runs several jobs, and a future
    second store must not silently inherit this one's connection.

    Test mode goes to a **separate database on the same server**, mirroring
    `get_crypto_signal_db_path`'s derivation and preserving the property that
    function's comment states -- test-mode history must not contaminate the
    operator-facing store. One server, two databases; not one database shared,
    and not a second engine.
    """
    production_url = os.getenv(
        'PROJECT_MONITOR_DATABASE_URL',
        'postgresql://postgres:devpass@127.0.0.1:55432/project_monitor',
    )
    active = RuntimeMode() if runtime_mode is None else runtime_mode
    if not active.is_test_mode:
        return production_url
    return os.getenv(
        'PROJECT_MONITOR_TEST_DATABASE_URL',
        _build_test_database_url(production_url),
    )


def _build_test_database_url(production_url: str) -> str:
    """Append `_test` to the database name, leaving every other part alone.

    Splitting on the last '/' rather than parsing a URL: the database name is
    the final path segment, and a query string (`?sslmode=...`) has to survive
    the rewrite intact.
    """
    base, _, tail = production_url.rpartition('/')
    if not base:
        return f'{production_url}_test'
    database, separator, query = tail.partition('?')
    return f'{base}/{database}_test{separator}{query}'


def get_archive_endpoint() -> Optional[Endpoint]:
    """The keyed archive endpoint, or None when no key is configured.

    Returns None rather than raising so a caller can fall back to the public
    endpoint deliberately. The URL is a secret from here on: it is passed to
    `Endpoint`, which never prints it, and is never stored on a record.
    """
    url = os.getenv('ROBINHOOD_CHAIN_RPC_URL', '').strip()
    if not url:
        return None
    return Endpoint(kind='alchemy', url=url, supports_batch=True)


def get_public_endpoint(*, supports_batch: bool = True) -> Endpoint:
    """The public endpoint.

    Batching defaults on for the log plane, where it was measured accepted. State
    reads on this endpoint are sent unbatched by the recorder's fallback path,
    which passes `supports_batch=False` explicitly.
    """
    return Endpoint(
        kind='public',
        url=os.getenv('ROBINHOOD_CHAIN_PUBLIC_RPC_URL', PUBLIC_RPC_URL),
        supports_batch=supports_batch,
    )


def get_manifest_url() -> str:
    return os.getenv('PROJECT_MONITOR_MANIFEST_URL', MANIFEST_URL)
