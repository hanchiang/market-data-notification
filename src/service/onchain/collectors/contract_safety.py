"""Contract safety: what can still be done to this token, and by whom.

The question the section answers is not "is this a scam" but "what powers exist".
A privileged function that exists and has never been called is still a power, so
the section reads the deployed CODE rather than the project's claims:

* **Selectors are found in the bytecode**, as `PUSH4` constants before the
  dispatch compare. That is deterministic and needs no ABI, which matters because
  three of the four seed tokens' sources are only readable through an explorer
  that fails regularly. The verified ABI, when the explorer does answer,
  cross-checks the scan rather than replacing it.
* **A proxy is read from its storage slots**, not from the explorer's
  `proxy_type`: EIP-1967 puts the implementation and admin at fixed slots, and
  those are facts on chain.
* **When the token is a proxy the scan runs against the implementation.** A
  proxy's own runtime code is a 30-byte delegate stub in which no privileged
  selector appears, so scanning it would report every upgradeable token as
  frozen -- the exact inversion of the truth.

The explorer fields (`verified_source`, `exit_path`, the launcher discovery) are
their own failure unit (design D8): when the explorer breaks they carry the error
class, the section is `partial`, and the chain-only fields diff normally.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from market_data_library.core.onchain.evm import abi
from market_data_library.core.onchain.evm.keccak import keccak256

from src.service.onchain.collectors import uniswap
from src.service.onchain.collectors.base import (
    SECTION_CONTRACT_SAFETY,
    STATUS_OK,
    STATUS_PARTIAL,
    BuildContext,
    SectionResult,
)
from src.service.onchain.diff import failed_field

logger = logging.getLogger('Onchain contract safety')

UNAVAILABLE = 'unavailable'
ZERO_ADDRESS = '0x' + '0' * 40

def _eip1967_slot(label: str) -> str:
    """`keccak256(label) - 1`, the EIP-1967 storage slot for one proxy role.

    Derived rather than transcribed. The three constants are widely published,
    and a single wrong hex digit in one of them would read slot zero forever --
    reporting every upgradeable token as a plain contract, with nothing failing.
    Deriving them means the definition is the code; `contract_safety_test.py`
    pins the three published values so a change to the derivation is caught too.
    """
    return hex(int.from_bytes(keccak256(label.encode()), 'big') - 1)


EIP1967_IMPLEMENTATION_SLOT = _eip1967_slot('eip1967.proxy.implementation')
EIP1967_ADMIN_SLOT = _eip1967_slot('eip1967.proxy.admin')
EIP1967_BEACON_SLOT = _eip1967_slot('eip1967.proxy.beacon')

# EIP-1167 minimal proxy: the runtime code is a fixed prologue, the 20-byte
# target, and a fixed epilogue.
EIP1167_PREFIX = '363d3d373d3d3d363d73'
EIP1167_SUFFIX = '5af43d82803e903d91602b57fd5bf3'

# The frozen selector list (design, companion doc). Frozen means: this list is
# what version 1 of the threshold table scores `privileged_selectors` changes
# against, so adding a name here is a threshold change, not a refactor.
PRIVILEGED_FUNCTIONS: List[tuple] = [
    ('owner', []),
    ('transferOwnership', ['address']),
    ('renounceOwnership', []),
    ('mint', ['address', 'uint256']),
    ('pause', []),
    ('unpause', []),
    ('blacklist', ['address']),
    ('setFee', ['uint256']),
    ('setMaxTx', ['uint256']),
    ('hasRole', ['bytes32', 'address']),
    ('getRoleAdmin', ['bytes32']),
    ('upgradeTo', ['address']),
]

DEFAULT_ADMIN_ROLE = '0x' + '0' * 64

_PUSH4 = re.compile(r'63([0-9a-f]{8})')


def scan_selectors(code: str) -> List[str]:
    """Which of the frozen selectors appear as `PUSH4` constants.

    Solidity's dispatcher compares the incoming selector against each function's
    own, emitted as `PUSH4 <selector>`, so a selector present in the code is a
    function the contract dispatches. The scan is over the hex text and is
    therefore susceptible to a false positive from four bytes of constant data
    that happen to follow a `0x63` byte -- accepted deliberately: a false
    positive over-reports a power (the safe direction), and the verified ABI
    cross-check catches it whenever the explorer answers.
    """
    body = (code or '').removeprefix('0x').lower()
    present = {match.group(1) for match in _PUSH4.finditer(body)}
    found = []
    for name, arg_types in PRIVILEGED_FUNCTIONS:
        selector = abi.selector(abi.signature(name, arg_types)).removeprefix('0x')
        if selector in present:
            found.append(name)
    return sorted(found)


def detect_proxy(code: str, implementation_slot: str, admin_slot: str, beacon_slot: str) -> Dict[str, Any]:
    body = (code or '').removeprefix('0x').lower()
    implementation = _address_from_slot(implementation_slot)
    admin = _address_from_slot(admin_slot)
    beacon = _address_from_slot(beacon_slot)

    if body.startswith(EIP1167_PREFIX) and body.endswith(EIP1167_SUFFIX):
        target = '0x' + body[len(EIP1167_PREFIX):len(EIP1167_PREFIX) + 40]
        return {'proxy': 'eip1167', 'implementation': target, 'admin': None, 'beacon': None}
    if implementation:
        return {'proxy': 'eip1967', 'implementation': implementation, 'admin': admin, 'beacon': None}
    if beacon:
        return {'proxy': 'eip1967-beacon', 'implementation': None, 'admin': admin, 'beacon': beacon}
    return {'proxy': 'none', 'implementation': None, 'admin': admin, 'beacon': None}


def _address_from_slot(word: Optional[str]) -> Optional[str]:
    if not word:
        return None
    raw = word.removeprefix('0x').rjust(64, '0')
    address = '0x' + raw[-40:]
    return None if address == ZERO_ADDRESS else address


async def collect(context: BuildContext) -> SectionResult:
    token_address = context.identity.get('token_address')
    if not token_address:
        raise IdentityUnresolvedError('contract safety needs a resolved token address')

    fields: Dict[str, Any] = {}
    associated = _associated_contracts(context)
    fields['associated_contracts'] = associated

    slots = [
        (token_address, EIP1967_IMPLEMENTATION_SLOT),
        (token_address, EIP1967_ADMIN_SLOT),
        (token_address, EIP1967_BEACON_SLOT),
    ]
    slot_values = []
    for address, slot in slots:
        value, raw = await context.state_client.get_storage_at(address, slot, context.block)
        context.record_jsonrpc(raw)
        slot_values.append(value)

    code, code_raw = await context.state_client.get_code(token_address, context.block)
    context.record_jsonrpc(code_raw)
    proxy = detect_proxy(code, slot_values[0], slot_values[1], slot_values[2])
    fields.update(proxy)

    scanned_address = proxy['implementation'] or token_address
    scanned_code = code
    if proxy['implementation']:
        scanned_code, implementation_raw = await context.state_client.get_code(
            proxy['implementation'], context.block
        )
        context.record_jsonrpc(implementation_raw)
    fields['scanned_address'] = scanned_address
    fields['privileged_selectors'] = scan_selectors(scanned_code)

    fields.update(await _roles(context, token_address, scanned_address, fields['privileged_selectors']))
    fields['hook_permissions'] = _hook_permissions(context)
    fields['frozen'] = _is_frozen(fields)

    explorer_fields = await _explorer_fields(context, token_address)
    fields.update(explorer_fields)

    status = STATUS_PARTIAL if context.explorer.failed else STATUS_OK
    return SectionResult(
        name=SECTION_CONTRACT_SAFETY,
        status=status,
        fields=fields,
        error_class=context.explorer.error_class if context.explorer.failed else None,
        evidence_ids=list(context.evidence_ids),
    )


class IdentityUnresolvedError(RuntimeError):
    """The section cannot run because identity resolved no token address.

    Its own class because it is the error the build records for the other three
    sections when no identity section has ever succeeded (design, The build, 4).
    """


def _associated_contracts(context: BuildContext) -> List[str]:
    """The contracts whose powers reach this token besides the token itself.

    A launchpad token's interesting surface is not the ERC-20 (all four seed
    tokens have no owner, no pause and no admin role); it is the launcher that
    minted it and, for a v4 pair, the hook that can interpose on every swap.
    """
    candidates = []
    hooks = context.identity.get('hooks')
    if hooks and hooks not in (ZERO_ADDRESS, 'unavailable'):
        candidates.append(str(hooks).lower())
    deployer = context.identity.get('deployer')
    if isinstance(deployer, dict):
        for key in ('creation_tx_to', 'creator'):
            value = deployer.get(key)
            if isinstance(value, str) and value.startswith('0x') and len(value) == 42:
                candidates.append(value.lower())
    return sorted(set(candidates))


async def _roles(
    context: BuildContext,
    token_address: str,
    scanned_address: str,
    selectors: List[str],
) -> Dict[str, Any]:
    """`owner()` when the selector is there, and `hasRole` against a candidate set.

    A role MAPPING cannot be enumerated from outside -- there is no "list the
    admins" call -- so the only honest form is "these addresses were checked, and
    these hold the role". The checked set is stored with the answer for exactly
    that reason (the child requirement's rule).
    """
    fields: Dict[str, Any] = {'owner': 'absent', 'role_holders': UNAVAILABLE, 'checked_addresses': []}
    if 'owner' in selectors:
        try:
            data, raw = await context.state_client.call(
                token_address, uniswap.call_data('owner'), context.block
            )
            context.record_jsonrpc(raw)
            fields['owner'] = str(abi.decode_single('address', data)).lower()
        except Exception as exc:
            # A present selector that reverts is a fact worth storing, not a
            # failure: it means the function exists but refuses, which is what
            # some renounced-ownership patterns look like.
            logger.info('owner() reverted: %s', type(exc).__name__)
            fields['owner'] = 'reverted'

    if 'hasRole' not in selectors:
        return fields

    candidates = sorted(set(_role_candidates(context, fields)))
    fields['checked_addresses'] = candidates
    holders = []
    for address in candidates:
        try:
            data, raw = await context.state_client.call(
                scanned_address,
                uniswap.call_data('hasRole', ['bytes32', 'address'], [DEFAULT_ADMIN_ROLE, address]),
                context.block,
            )
            context.record_jsonrpc(raw)
            if abi.decode_single('bool', data):
                holders.append(address)
        except Exception as exc:
            logger.info('hasRole reverted for %s: %s', address, type(exc).__name__)
    fields['role_holders'] = holders
    return fields


def _role_candidates(context: BuildContext, fields: Dict[str, Any]) -> List[str]:
    candidates = list(_associated_contracts(context))
    owner = fields.get('owner')
    if isinstance(owner, str) and owner.startswith('0x'):
        candidates.append(owner)
    deployer = context.identity.get('deployer')
    if isinstance(deployer, dict):
        for value in deployer.values():
            if isinstance(value, str) and value.startswith('0x') and len(value) == 42:
                candidates.append(value.lower())
    return candidates


def _hook_permissions(context: BuildContext) -> Any:
    hooks = context.identity.get('hooks')
    if hooks == UNAVAILABLE:
        return UNAVAILABLE
    if not hooks:
        return {'kind': 'not_applicable'}
    return uniswap.decode_hook_permissions(str(hooks))


def _is_frozen(fields: Dict[str, Any]) -> Any:
    """Can the permission set still change?

    Deliberately conservative: `frozen` is True only when there is no privileged
    selector at all, or the owner is renounced with no role mapping AND, for a
    proxy, no admin. Anything else is False -- an unknown is not a freeze.
    """
    selectors = fields.get('privileged_selectors') or []
    if not selectors:
        return True
    owner = fields.get('owner')
    owner_renounced = owner in (ZERO_ADDRESS, 'absent')
    roles = fields.get('role_holders')
    roles_empty = roles == UNAVAILABLE or roles == []
    if fields.get('proxy') != 'none' and fields.get('admin'):
        return False
    return bool(owner_renounced and roles_empty)


async def _explorer_fields(context: BuildContext, token_address: str) -> Dict[str, Any]:
    """`verified_source` and `exit_path`, the section's explorer sub-unit."""
    if context.explorer.failed:
        marker = failed_field(context.explorer.error_class or 'unknown')
        return {'verified_source': dict(marker), 'exit_path': dict(marker)}

    contract = await context.explorer.contract(token_address)
    if context.explorer.failed:
        marker = failed_field(context.explorer.error_class or 'unknown')
        return {'verified_source': dict(marker), 'exit_path': dict(marker)}
    if contract is None:
        return {'verified_source': {'verified': False}, 'exit_path': UNAVAILABLE}

    context.record_http(
        f'blockscout:smart-contracts/{token_address}', contract.raw, 'blockscout'
    )
    abi_names = {
        str(entry.get('name') or '').lower()
        for entry in (contract.abi or [])
        if entry.get('type') == 'function'
    }
    return {
        'verified_source': {
            'verified': bool(contract.is_verified),
            'compiler': contract.compiler_version,
            'file_path': contract.file_path,
            'name': contract.name,
        },
        'exit_path': sorted(
            name for name in abi_names if name in ('redeem', 'refund', 'withdraw', 'exit')
        ) or 'unavailable',
        'abi_cross_check': sorted(abi_names & {
            name.lower() for name, _ in PRIVILEGED_FUNCTIONS
        }),
    }
