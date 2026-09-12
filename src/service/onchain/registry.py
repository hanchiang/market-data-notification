"""Load, validate and upsert the project registry (requirement P1, V4, A1).

Adding a project is one object in `registry/projects.json`; this module is what
makes that true rather than aspirational -- nothing here is per-project code.

**A `registry.py` module and a `registry/` directory sit side by side**, as the
design lays them out. Python resolves `src.service.onchain.registry` to THIS
module because the directory has no `__init__.py` and a regular module outranks
a namespace portion -- so adding `registry/__init__.py` would silently redirect
every import here to an empty package. Do not add one.

Validation is strict and happens before any endpoint is touched, because every
failure it catches would otherwise surface as a wrong number rather than an
error: an unknown `archetype` silently selects the wrong holder derivation
(design DG-5), a pool reference of the wrong width silently makes a v4 pool id
look like a v3 pool address, and an edited Uniswap address silently points the
custody read at a contract nobody verified.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from src.service.onchain.config import (
    DEXSCREENER_CHAIN_SLUG,
    KNOWN_ARCHETYPES,
    SOURCE_CLASSES,
    VERIFIED_UNISWAP_ADDRESSES,
    get_archive_endpoint,
    get_public_endpoint,
    get_registry_path,
)
from src.service.onchain.repository import OnchainRepository

# A 20-byte address (a v3 pool) or a 32-byte id (a v4 pool). Nothing else is a
# pool reference on this provider, and accepting a third width would let a token
# address in -- exactly the confusion the identity requirement (P2) forbids.
_ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')
_POOL_ID = re.compile(r'^0x[0-9a-fA-F]{64}$')

REGISTRY_ADMITTED_BY = 'registry'
# The RPC source row when the endpoint's host cannot be read from its URL.
RPC_CONFIGURED_LABEL = 'configured'
DEXSCREENER_WEB = 'https://dexscreener.com/'

UNISWAP_ADDRESS_KEYS = (
    'v3_factory',
    'v3_position_manager',
    'v4_pool_manager',
    'v4_position_manager',
    'v4_state_view',
)


class RegistryError(ValueError):
    """The registry file is not usable. Raised before any network read."""


@dataclass(frozen=True)
class SourceEntry:
    source_class: str
    url: str


@dataclass(frozen=True)
class ChainEntry:
    chain_id: int
    key: str
    display_name: str
    dexscreener_slug: str
    explorer_api: str
    uniswap: Dict[str, str]
    lockers: Sequence[str] = ()

    @property
    def entity_key(self) -> str:
        return f'chain:{self.chain_id}'


@dataclass(frozen=True)
class ProjectEntry:
    key: str
    display_name: str
    chain_id: int
    archetype: str
    pool_ref: str
    sources: Sequence[SourceEntry] = ()

    @property
    def entity_key(self) -> str:
        return f'project:{self.key}'

    @property
    def pool_ref_kind(self) -> str:
        """`pool_id` for a 32-byte v4 reference, `pool_address` for a 20-byte v3
        one. The identity collector confirms this against the chain; here it is
        only the shape."""
        return 'pool_id' if len(self.pool_ref) == 66 else 'pool_address'


@dataclass(frozen=True)
class Registry:
    chains: Dict[int, ChainEntry] = field(default_factory=dict)
    projects: Dict[str, ProjectEntry] = field(default_factory=dict)

    def chain_for(self, project: ProjectEntry) -> ChainEntry:
        return self.chains[project.chain_id]


MARKET_ENTITY_KEY = 'market'


def load_registry(path: Optional[Path] = None) -> Registry:
    source = Path(path) if path is not None else get_registry_path()
    if not source.exists():
        raise RegistryError(f'registry file not found: {source}')
    try:
        payload = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise RegistryError(f'registry file is not valid JSON: {exc}') from exc
    return parse_registry(payload)


def parse_registry(payload: Any) -> Registry:
    if not isinstance(payload, dict):
        raise RegistryError('registry must be a JSON object')

    chains: Dict[int, ChainEntry] = {}
    for raw in _require_list(payload, 'chains'):
        chain = _parse_chain(raw)
        if chain.chain_id in chains:
            raise RegistryError(f'duplicate chain id {chain.chain_id}')
        chains[chain.chain_id] = chain

    projects: Dict[str, ProjectEntry] = {}
    claimed_pools: Dict[tuple, str] = {}
    for raw in _require_list(payload, 'projects'):
        project = _parse_project(raw, chains)
        if project.key in projects:
            raise RegistryError(f'duplicate project key {project.key!r}')
        # A pool entity's key is derived from (chain, reference), so two projects
        # naming the same pool would have the pool claimed by whichever upserted
        # first -- and the second project's health section would silently read
        # the first project's custody.
        pool_claim = (project.chain_id, project.pool_ref)
        if pool_claim in claimed_pools:
            raise RegistryError(
                f'project {project.key!r} and {claimed_pools[pool_claim]!r} '
                f'both name pool {project.pool_ref} on chain {project.chain_id}'
            )
        claimed_pools[pool_claim] = project.key
        projects[project.key] = project

    return Registry(chains=chains, projects=projects)


def _require_list(payload: Dict[str, Any], name: str) -> List[Any]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise RegistryError(f'registry key {name!r} must be a list')
    return value


def _parse_uniswap(chain_id: int, raw: Any) -> Dict[str, str]:
    """The five protocol addresses, shape-checked and then held against the
    on-chain-verified set.

    The second check is the tripwire: these were verified on chain on
    2026-09-06, and an accidental edit would point the custody read at an
    unverified contract while every test still passed. A deliberate change
    updates `config.VERIFIED_UNISWAP_ADDRESSES` with its own verification note.
    """
    if not isinstance(raw, dict):
        raise RegistryError(f'chain {chain_id}: `uniswap` must be an object')
    missing = [key for key in UNISWAP_ADDRESS_KEYS if key not in raw]
    if missing:
        raise RegistryError(f'chain {chain_id}: missing uniswap addresses {missing}')
    for key in UNISWAP_ADDRESS_KEYS:
        if not _ADDRESS.match(str(raw[key])):
            raise RegistryError(
                f'chain {chain_id}: uniswap.{key} is not a 20-byte address'
            )

    verified = VERIFIED_UNISWAP_ADDRESSES.get(chain_id)
    if verified is not None:
        for key, expected in verified.items():
            actual = str(raw[key]).lower()
            if actual != expected.lower():
                raise RegistryError(
                    f'chain {chain_id}: uniswap.{key} is {actual}, but the '
                    f'on-chain-verified address is {expected}. Re-verify before '
                    'changing it, and update config.VERIFIED_UNISWAP_ADDRESSES.'
                )
    return {key: str(raw[key]).lower() for key in UNISWAP_ADDRESS_KEYS}


def _parse_chain(raw: Any) -> ChainEntry:
    if not isinstance(raw, dict):
        raise RegistryError('each chain entry must be an object')
    chain_id = raw.get('chain_id')
    if not isinstance(chain_id, int) or isinstance(chain_id, bool):
        raise RegistryError(f'chain_id must be an integer, got {chain_id!r}')

    uniswap = _parse_uniswap(chain_id, raw.get('uniswap'))

    lockers = raw.get('lockers', [])
    if not isinstance(lockers, list) or any(
        not _ADDRESS.match(str(a)) for a in lockers
    ):
        raise RegistryError(f'chain {chain_id}: `lockers` must be a list of addresses')

    for name in ('key', 'dexscreener_slug', 'explorer_api'):
        if not isinstance(raw.get(name), str) or not raw[name]:
            raise RegistryError(f'chain {chain_id}: {name!r} must be a non-empty string')

    # The same tripwire the Uniswap addresses get, for the same reason: the slug
    # is the provider's own name for the chain and is not derivable from the id,
    # so it is knowledge held in two places. The file stays authoritative; a
    # disagreement with the shipped constant is a loud failure rather than a
    # silent join that matches nothing (the library's chain-id defect).
    expected_slug = DEXSCREENER_CHAIN_SLUG.get(chain_id)
    if expected_slug is not None and raw['dexscreener_slug'] != expected_slug:
        raise RegistryError(
            f'chain {chain_id}: dexscreener_slug is {raw["dexscreener_slug"]!r}, '
            f'but the shipped constant is {expected_slug!r}. A provider slug that '
            'matches nothing does not raise, it silently returns no pairs.'
        )

    return ChainEntry(
        chain_id=chain_id,
        key=raw['key'],
        display_name=raw.get('display_name') or raw['key'],
        dexscreener_slug=raw['dexscreener_slug'],
        explorer_api=raw['explorer_api'],
        uniswap=uniswap,
        lockers=tuple(str(a).lower() for a in lockers),
    )


def _parse_project(raw: Any, chains: Dict[int, ChainEntry]) -> ProjectEntry:
    if not isinstance(raw, dict):
        raise RegistryError('each project entry must be an object')
    key = raw.get('key')
    if not isinstance(key, str) or not key:
        raise RegistryError(f'project key must be a non-empty string, got {key!r}')

    chain_id = raw.get('chain_id')
    if chain_id not in chains:
        raise RegistryError(f'project {key!r}: chain id {chain_id!r} is not declared')

    archetype = raw.get('archetype')
    if archetype not in KNOWN_ARCHETYPES:
        raise RegistryError(
            f'project {key!r}: unknown archetype {archetype!r}; known: '
            f'{sorted(KNOWN_ARCHETYPES)}'
        )

    pool_ref = raw.get('pool_ref')
    if not isinstance(pool_ref, str) or not (
        _ADDRESS.match(pool_ref) or _POOL_ID.match(pool_ref)
    ):
        raise RegistryError(
            f'project {key!r}: pool_ref must be a 20-byte address (v3) or a '
            f'32-byte pool id (v4), got {pool_ref!r}'
        )

    sources = tuple(_parse_source(key, entry) for entry in raw.get('sources', []))
    return ProjectEntry(
        key=key,
        display_name=raw.get('display_name') or key,
        chain_id=chain_id,
        archetype=archetype,
        pool_ref=pool_ref.lower(),
        sources=sources,
    )


def _parse_source(project_key: str, raw: Any) -> SourceEntry:
    if not isinstance(raw, dict):
        raise RegistryError(f'project {project_key!r}: each source must be an object')
    source_class = raw.get('class')
    if source_class not in SOURCE_CLASSES:
        raise RegistryError(
            f'project {project_key!r}: unknown source class {source_class!r}; '
            f'known: {sorted(SOURCE_CLASSES)}'
        )
    url = raw.get('url')
    if not isinstance(url, str) or not url:
        raise RegistryError(f'project {project_key!r}: source url must be non-empty')
    return SourceEntry(source_class=source_class, url=url)


def upsert_registry(
    repository: OnchainRepository, registry: Registry
) -> Dict[str, int]:
    """Write the registry into the entity store; return project key -> entity id.

    Idempotent: every write is keyed by the entity `key` or the (class, url)
    pair, so the nightly re-upsert is a no-op when the file has not changed.
    The chain's RPC, explorer and DEX provider are admitted here as
    `chain_rpc`, `chain_explorer` and `dex_provider` sources -- the design's
    DG-3 gap: they are sources the collectors read, and the requirement's
    admission path (P6) is phase 1b, so the registry admits them. The RPC row
    is the endpoint's HOST, never its URL: see `rpc_source_handle`.
    """
    market_id = repository.upsert_entity(
        level='market', key=MARKET_ENTITY_KEY, display_name='Market'
    )

    chain_ids: Dict[int, int] = {}
    for chain in registry.chains.values():
        chain_entity_id = repository.upsert_entity(
            level='chain',
            key=chain.entity_key,
            display_name=chain.display_name,
            parent_id=market_id,
            attrs={
                'chain_id': chain.chain_id,
                'key': chain.key,
                'dexscreener_slug': chain.dexscreener_slug,
                'explorer_api': chain.explorer_api,
                'uniswap': dict(chain.uniswap),
                'lockers': list(chain.lockers),
            },
        )
        chain_ids[chain.chain_id] = chain_entity_id

        # Three chain-level rows, so the coverage grid reads one table for
        # every class (UX brief, slice B) instead of two classes from config.
        chain_sources = (
            ('chain_rpc', rpc_source_handle()),
            ('chain_explorer', chain.explorer_api),
            ('dex_provider', f'{DEXSCREENER_WEB}{chain.dexscreener_slug}'),
        )
        for source_class, handle in chain_sources:
            source_id = repository.upsert_source(
                source_class=source_class,
                url_or_handle=handle,
                admission='admitted',
                admitted_by=REGISTRY_ADMITTED_BY,
                evidence=_registry_evidence(),
            )
            repository.link_source_to_entity(source_id, chain_entity_id)

    project_ids: Dict[str, int] = {}
    for project in registry.projects.values():
        project_entity_id = repository.upsert_entity(
            level='project',
            key=project.entity_key,
            display_name=project.display_name,
            parent_id=chain_ids[project.chain_id],
            attrs={
                'archetype': project.archetype,
                'pool_ref': project.pool_ref,
                'pool_ref_kind': project.pool_ref_kind,
                'chain_id': project.chain_id,
            },
        )
        project_ids[project.key] = project_entity_id

        for source in project.sources:
            source_id = repository.upsert_source(
                source_class=source.source_class,
                url_or_handle=source.url,
                admission='admitted',
                admitted_by=REGISTRY_ADMITTED_BY,
                evidence=_registry_evidence(),
            )
            repository.link_source_to_entity(source_id, project_entity_id)

    return project_ids


def rpc_source_handle() -> str:
    """The `chain_rpc` row's handle: the host of the endpoint the collectors
    read, and nothing else of its URL.

    The keyed archive endpoint carries its key in the URL path (Alchemy's
    `/v2/<key>`), and `url_or_handle` is a stored column that reaches the
    dossier JSON and the overview page. So the URL is reduced to its hostname
    before it is written: no scheme, no path, no query. When no archive
    endpoint is configured the public endpoint's host is written instead,
    which is then the RPC the collectors actually read. A URL with no
    readable host falls back to `configured (<kind>)` rather than to any part
    of the URL.
    """
    endpoint = get_archive_endpoint() or get_public_endpoint()
    host = urlsplit(str(endpoint.url)).hostname or ''
    if not host:
        return f'{RPC_CONFIGURED_LABEL} ({endpoint.kind})'
    return host


def _registry_evidence() -> Dict[str, Any]:
    return {
        'path': 'src/service/onchain/registry/projects.json',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
    }
