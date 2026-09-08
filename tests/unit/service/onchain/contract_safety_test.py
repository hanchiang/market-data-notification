"""The bytecode scan, the proxy slots and the freeze rule.

The selector scan is the section's load-bearing read: three of the four seed
tokens' sources are only readable through an explorer that fails regularly, so
the answer to "what privileged functions exist" has to come from the deployed
code. These tests build bytecode by hand rather than fixture it, because what
must be asserted is that a selector present in the code IS found and one absent
is NOT -- and a recorded fixture proves neither direction on its own.
"""
from market_data_library.core.onchain.evm import abi

from src.service.onchain.collectors import contract_safety as safety

ZERO = '0x' + '0' * 40
ALICE = '0x' + '11' * 20
IMPLEMENTATION = '0x' + 'ab' * 20


def _push4(name, arg_types=()):
    return '63' + abi.selector(abi.signature(name, list(arg_types))).removeprefix('0x')


class TestPublishedSlots:
    def test_the_derived_slots_equal_the_published_eip1967_values(self):
        """Pinned against the published constants. The derivation is
        `keccak256(label) - 1`; if the `- 1` were dropped or the label
        misspelled, every proxy read would silently land on the wrong slot and
        report `none`, with nothing failing."""
        assert safety.EIP1967_IMPLEMENTATION_SLOT == (
            '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
        )
        assert safety.EIP1967_ADMIN_SLOT == (
            '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103'
        )
        assert safety.EIP1967_BEACON_SLOT == (
            '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'
        )


class TestSelectorScan:
    def test_every_frozen_selector_is_found_in_a_bytecode_that_has_it(self):
        code = '0x6080604052' + ''.join(
            _push4(name, args) for name, args in safety.PRIVILEGED_FUNCTIONS
        )
        found = safety.scan_selectors(code)
        assert found == sorted(name for name, _ in safety.PRIVILEGED_FUNCTIONS)

    def test_none_are_found_in_a_bytecode_that_has_none(self):
        """The control. A scan that matched anything would pass the test above
        and be useless."""
        assert safety.scan_selectors('0x' + '00' * 200) == []

    def test_one_selector_is_found_without_the_others(self):
        code = '0x6080' + _push4('mint', ['address', 'uint256']) + '5b00'
        assert safety.scan_selectors(code) == ['mint']

    def test_empty_code_is_not_an_error(self):
        assert safety.scan_selectors('0x') == []
        assert safety.scan_selectors('') == []


class TestProxyDetection:
    def test_a_plain_contract_is_not_a_proxy(self):
        detected = safety.detect_proxy('0x6080604052', None, None, None)
        assert detected['proxy'] == 'none'
        assert detected['implementation'] is None

    def test_an_eip1967_implementation_slot_names_the_implementation(self):
        word = '0x' + '0' * 24 + IMPLEMENTATION[2:]
        detected = safety.detect_proxy('0x6080', word, None, None)
        assert detected['proxy'] == 'eip1967'
        assert detected['implementation'] == IMPLEMENTATION

    def test_an_empty_slot_is_not_an_implementation(self):
        """A slot that has never been written reads as 32 zero bytes, which is
        the zero address -- not an implementation at that address."""
        detected = safety.detect_proxy('0x6080', '0x' + '0' * 64, None, None)
        assert detected['proxy'] == 'none'

    def test_an_eip1167_minimal_proxy_is_read_out_of_the_code(self):
        code = (
            '0x' + safety.EIP1167_PREFIX + IMPLEMENTATION[2:] + safety.EIP1167_SUFFIX
        )
        detected = safety.detect_proxy(code, None, None, None)
        assert detected['proxy'] == 'eip1167'
        assert detected['implementation'] == IMPLEMENTATION

    def test_a_beacon_slot_is_its_own_proxy_kind(self):
        word = '0x' + '0' * 24 + IMPLEMENTATION[2:]
        detected = safety.detect_proxy('0x6080', None, None, word)
        assert detected['proxy'] == 'eip1967-beacon'
        assert detected['beacon'] == IMPLEMENTATION


class TestFrozen:
    def test_no_privileged_selector_at_all_is_frozen(self):
        """The four seed tokens' actual shape, read 2026-09-06: no owner, no
        pause, no admin role."""
        assert safety._is_frozen({'privileged_selectors': [], 'proxy': 'none'}) is True

    def test_a_renounced_owner_with_no_roles_is_frozen(self):
        assert safety._is_frozen(
            {'privileged_selectors': ['owner'], 'owner': ZERO,
             'role_holders': [], 'proxy': 'none'}
        ) is True

    def test_a_live_owner_is_not_frozen(self):
        assert safety._is_frozen(
            {'privileged_selectors': ['owner', 'mint'], 'owner': ALICE,
             'role_holders': [], 'proxy': 'none'}
        ) is False

    def test_a_proxy_with_an_admin_is_never_frozen(self):
        """Whatever the token says about ownership, an admin can replace the
        implementation -- so the permission set can still change."""
        assert safety._is_frozen(
            {'privileged_selectors': ['owner'], 'owner': ZERO, 'role_holders': [],
             'proxy': 'eip1967', 'admin': ALICE}
        ) is False

    def test_a_role_holder_is_not_frozen(self):
        assert safety._is_frozen(
            {'privileged_selectors': ['hasRole'], 'owner': 'absent',
             'role_holders': [ALICE], 'proxy': 'none'}
        ) is False
