"""What every collector is handed, and what it hands back.

One context object rather than a parameter list per collector, because the four
collectors read the same six things (the store, the two chain clients, the
provider, the explorer unit, the pinned block) and a signature per collector
would make adding a fifth section a five-file change.

`SectionResult` is deliberately not a database row: a collector decides its own
status and fields and nothing else. The builder stamps the span, carries the
baselines, writes the row and diffs it -- so a collector cannot accidentally
close a build, and a section that raises still leaves the build to continue (A3).
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from market_data_library.core.crypto.dexscreener import DexscreenerService
from market_data_library.core.onchain.evm import EvmClient

from src.service.onchain import chain as chain_module
from src.service.onchain import evidence as evidence_store
from src.service.onchain.explorer import ExplorerUnit
from src.service.onchain.registry import ChainEntry, ProjectEntry, Registry
from src.service.onchain.repository import OnchainRepository
from src.service.onchain.spend import SpendLedger

EXPLORER_KIND = 'blockscout'

logger = logging.getLogger('Onchain collectors')

STATUS_OK = 'ok'
STATUS_PARTIAL = 'partial'
STATUS_FAILED = 'failed'

SECTION_IDENTITY = 'identity'
SECTION_CONTRACT_SAFETY = 'contract_safety'
SECTION_ONCHAIN_HEALTH = 'onchain_health'
SECTION_TOKEN_ECONOMICS = 'token_economics'

# The order the build runs them in. Identity first because the other three need
# the addresses it resolves; the rest in the order the report prints them, so a
# reader following a build's log lines sees the same sequence as the page.
SECTION_ORDER = [
    SECTION_IDENTITY,
    SECTION_CONTRACT_SAFETY,
    SECTION_ONCHAIN_HEALTH,
    SECTION_TOKEN_ECONOMICS,
]


@dataclass
class BuildContext:
    """One project's build at one pinned block."""

    repository: OnchainRepository
    registry: Registry
    chain: ChainEntry
    project: ProjectEntry
    chain_entity_id: int
    project_entity_id: int
    pinned: chain_module.PinnedBlock
    state_client: EvmClient
    log_client: EvmClient
    dexscreener: DexscreenerService
    explorer: ExplorerUnit
    # Filled by the identity collector and read by the other three. On a build
    # where identity failed, the builder loads it from the latest `ok` or
    # `partial` identity section instead -- the chain-resolved addresses are
    # immutable, so an old one is as good as a new one (design, The build, 4).
    identity: Dict[str, Any] = field(default_factory=dict)
    # RPC spend is not charged here. The two clients' budgets are wrapped by
    # this ledger, which counts every attempt at `reserve` (P13; `spend.py`).
    # Collectors only add the HTTP providers that have no budget object.
    ledger: SpendLedger = field(default_factory=SpendLedger)
    evidence_ids: List[int] = field(default_factory=list)

    @property
    def block(self) -> int:
        return self.pinned.block

    def record_jsonrpc(self, raw: Any, *, entity_id: Optional[int] = None) -> int:
        """Store one JSON-RPC response as evidence and remember its id."""
        evidence_id = evidence_store.store_response(
            self.repository,
            entity_id=entity_id if entity_id is not None else self.project_entity_id,
            kind=evidence_store.KIND_JSONRPC,
            method_or_url=raw.method,
            params=raw.params,
            body=raw.body,
            endpoint_kind=raw.endpoint_kind,
            block=self.block,
        )
        self.evidence_ids.append(evidence_id)
        return evidence_id

    def record_http(
        self,
        url: str,
        body: Any,
        endpoint_kind: str,
        *,
        entity_id: Optional[int] = None,
        params: Optional[Any] = None,
    ) -> int:
        # The explorer unit counts its own calls, failures included, and the
        # job adds them at the end of each build; counting it here too would
        # double every blockscout request.
        if endpoint_kind != EXPLORER_KIND:
            self.ledger.add_requests(endpoint_kind, 1)
        evidence_id = evidence_store.store_response(
            self.repository,
            entity_id=entity_id if entity_id is not None else self.project_entity_id,
            kind=evidence_store.KIND_HTTP,
            method_or_url=url,
            params=params,
            body=body,
            endpoint_kind=endpoint_kind,
            block=self.block,
        )
        self.evidence_ids.append(evidence_id)
        return evidence_id

    def token_entity_id(self) -> Optional[int]:
        address = self.identity.get('token_address')
        if not address:
            return None
        return self.repository.upsert_entity(
            level='token',
            key=f'token:{self.chain.chain_id}:{str(address).lower()}',
            parent_id=self.project_entity_id,
            display_name=self.identity.get('token_symbol'),
            attrs={'address': str(address).lower()},
        )

    def pool_entity_id(self) -> int:
        version = self.identity.get('version') or 'unknown'
        reference = self.identity.get('pool_id') or self.identity.get(
            'pool_address'
        ) or self.project.pool_ref
        return self.repository.upsert_entity(
            level='pool',
            key=f'pool:{self.chain.chain_id}:{version}:{str(reference).lower()}',
            parent_id=self.project_entity_id,
            display_name=f'{self.project.display_name} {version} pool',
            attrs={'version': version, 'reference': str(reference).lower()},
        )


@dataclass
class SectionResult:
    """One collector's answer. Statuses are the design's failure units (A3)."""

    name: str
    status: str
    fields: Dict[str, Any] = field(default_factory=dict)
    error_class: Optional[str] = None
    evidence_ids: List[int] = field(default_factory=list)
    # Stamped by the builder, never by a collector: a collector does not know
    # its own span, and the span has to be on the FAILED rows too (A12).
    span_id: Optional[str] = None

    @classmethod
    def failed(cls, name: str, error_class: str, evidence_ids: Optional[List[int]] = None) -> 'SectionResult':
        return cls(
            name=name,
            status=STATUS_FAILED,
            fields={},
            error_class=error_class,
            evidence_ids=list(evidence_ids or []),
        )
