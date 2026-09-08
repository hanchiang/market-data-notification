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
from typing import Any, Dict, List, Optional, Sequence

from market_data_library.core.crypto.dexscreener import DexscreenerService
from market_data_library.core.onchain.evm import ALCHEMY_CU_COSTS, EvmClient

from src.service.onchain import chain as chain_module
from src.service.onchain import evidence as evidence_store
from src.service.onchain.explorer import ExplorerUnit
from src.service.onchain.registry import ChainEntry, ProjectEntry, Registry
from src.service.onchain.repository import OnchainRepository

CU_COSTS_BY_ENDPOINT = {'alchemy': ALCHEMY_CU_COSTS}

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
    spend: Dict[str, Dict[str, int]] = field(default_factory=dict)
    evidence_ids: List[int] = field(default_factory=list)

    @property
    def block(self) -> int:
        return self.pinned.block

    def charge(self, endpoint_kind: str, requests: int, *, methods: Sequence[str] = ()) -> None:
        """Bill one endpoint for work this collector caused (P13).

        Counted per JSON-RPC MEMBER, not per HTTP request, because that is how
        the metered endpoint bills: a batch of five `eth_call` costs five members'
        worth of compute units whether it travels as one request or five. A count
        of HTTP requests would understate the bill by the batch size and make the
        capacity envelope the design rests on read low.
        """
        # Compute units exist on the METERED endpoint only. The public node
        # publishes no cost model, so charging it Alchemy's table would invent a
        # bill -- it gets a request count and no units, which is what the
        # capacity envelope needs from it.
        table = CU_COSTS_BY_ENDPOINT.get(endpoint_kind, {})
        units = sum(table.get(method, 0) for method in methods)
        chain_module.add_spend(self.spend, endpoint_kind, requests, units)

    def charge_logs(self, requests: int) -> None:
        """Bill the LOG endpoint for `eth_getLogs` round trips.

        Log responses are not stored as evidence -- they are the raw material the
        decoded rows already carry -- so they never pass through
        `record_jsonrpc` and would otherwise be free in the ledger. They are the
        bulk of a first build's traffic, which is exactly what the capacity
        envelope needs to see.
        """
        if requests:
            self.charge(self.log_client.endpoint.kind, requests, methods=['eth_getLogs'] * requests)

    def record_jsonrpc(self, raw: Any, *, entity_id: Optional[int] = None) -> int:
        """Store one JSON-RPC response as evidence, charge it, and remember its id."""
        self.charge(raw.endpoint_kind, 1, methods=[raw.method])
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
        self.charge(endpoint_kind, 1)
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
