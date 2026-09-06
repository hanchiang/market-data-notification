"""The field-level diff (criterion A2) and the failed-field baseline rule (A3).

These run the diff module itself -- there is nothing to mock, and mocking the
component under test would prove only that the test author wrote the rule twice.
"""
from src.service.onchain.diff import (
    diff_fields,
    effective_fields,
    failed_field,
    is_empty,
    summarise,
    with_baselines,
)

FAILED = {'state': 'failed', 'error_class': 'BlockscoutApiError'}


class TestUnchangedInputs:
    def test_two_identical_documents_yield_an_empty_change_list(self):
        """A2: a section whose inputs did not change reports an empty diff."""
        fields = {
            'owner': '0x0000000000000000000000000000000000000000',
            'total_supply': '1000000000000000000000000',
            'holders': 4213,
            'privileged_selectors': ['mint', 'owner'],
        }
        changes = diff_fields(dict(fields), dict(fields))
        assert changes == {'added': {}, 'removed': {}, 'changed': []}
        assert is_empty(changes)

    def test_an_address_list_in_a_different_order_is_not_a_change(self):
        a = '0x1111111111111111111111111111111111111111'
        b = '0x2222222222222222222222222222222222222222'
        changes = diff_fields({'role_holders': [a, b]}, {'role_holders': [b, a]})
        assert is_empty(changes)

    def test_checksum_casing_is_not_a_change(self):
        """The real case, read 2026-09-06: DexScreener returns the Touch Grass
        token lowercase and Blockscout returns it EIP-55 checksummed. The same
        address from two sources must not diff every night."""
        lowercase = '0x16391c40e85fb2246a2c8c17bfa2594c5d3ef84b'
        checksummed = '0x16391C40e85FB2246A2C8c17bfA2594C5d3EF84b'
        assert is_empty(diff_fields({'admin': [lowercase]}, {'admin': [checksummed]}))
        # And the prefix's own case does not decide whether the set rule applies.
        assert is_empty(diff_fields({'admin': [lowercase]}, {'admin': ['0X' + checksummed[2:]]}))

    def test_an_ordered_string_list_is_still_order_sensitive(self):
        """Only 20-byte address lists are sets. A top-ten holder ranking is
        ordered, and a reordering there IS the change worth reporting."""
        changes = diff_fields(
            {'top_holders': ['alice', 'bob']}, {'top_holders': ['bob', 'alice']}
        )
        assert len(changes['changed']) == 1


class TestOneChange:
    def test_a_single_changed_field_yields_exactly_one_change(self):
        previous = {'owner': '0xaaa', 'holders': 10}
        current = {'owner': '0xbbb', 'holders': 10}
        changes = diff_fields(previous, current)
        assert changes['added'] == {} and changes['removed'] == {}
        assert changes['changed'] == [
            {'field': 'owner', 'old': '0xaaa', 'new': '0xbbb'}
        ]
        assert summarise(changes) == (0, 0, 1)

    def test_a_numeric_change_carries_its_delta(self):
        changes = diff_fields({'holders': 4000}, {'holders': 4213})
        assert changes['changed'][0]['delta'] == 213

    def test_a_big_integer_string_still_gets_a_delta(self):
        """Chain amounts are stored as decimal strings because they exceed a
        JSON number; the delta must still be exact."""
        old = str(10**30)
        new = str(10**30 + 7)
        [change] = diff_fields({'total_supply': old}, {'total_supply': new})['changed']
        assert change['delta'] == 7

    def test_a_non_numeric_change_has_no_delta_key(self):
        [change] = diff_fields({'owner': '0xa'}, {'owner': '0xb'})['changed']
        assert 'delta' not in change

    def test_a_boolean_flip_is_a_change_without_a_numeric_delta(self):
        """`True` is an int in Python; a `frozen` flag flipping must not report
        a delta of 1."""
        [change] = diff_fields({'frozen': True}, {'frozen': False})['changed']
        assert change['old'] is True and change['new'] is False
        assert 'delta' not in change

    def test_added_and_removed_members_are_named_for_an_address_list(self):
        a = '0x1111111111111111111111111111111111111111'
        b = '0x2222222222222222222222222222222222222222'
        [change] = diff_fields({'role_holders': [a]}, {'role_holders': [a, b]})[
            'changed'
        ]
        assert change['added_members'] == [b]
        assert change['removed_members'] == []


class TestAddedAndRemoved:
    def test_a_first_build_reports_every_field_as_added(self):
        changes = diff_fields(None, {'owner': '0xa'})
        assert changes['added'] == {'owner': '0xa'}
        assert changes['changed'] == []

    def test_a_field_that_disappeared_is_removed(self):
        changes = diff_fields({'owner': '0xa', 'admin': '0xb'}, {'owner': '0xa'})
        assert changes['removed'] == {'admin': '0xb'}


class TestFailedFields:
    def test_a_failed_field_keeps_its_previous_value_and_is_left_out(self):
        """A3: a fetch failure must never look like a change to nothing."""
        previous = {'verified_source': True, 'owner': '0xa'}
        current = {'verified_source': FAILED, 'owner': '0xa'}

        assert effective_fields(current, previous)['verified_source'] is True
        assert is_empty(diff_fields(previous, current))

    def test_a_chain_field_still_diffs_while_an_explorer_field_is_failed(self):
        """D8's whole point: the explorer is its own failure unit, so chain-only
        fields diff normally in the same section."""
        previous = {'verified_source': True, 'owner': '0xa'}
        current = {'verified_source': FAILED, 'owner': '0xb'}
        changes = diff_fields(previous, current)
        assert [c['field'] for c in changes['changed']] == ['owner']

    def test_a_failed_field_with_no_previous_value_is_not_reported_as_added(self):
        """There is no baseline to keep, and reporting the failure marker as a
        new value would put an error class into the operator's diff."""
        changes = diff_fields({}, {'verified_source': FAILED})
        assert changes['added'] == {}
        assert is_empty(changes)

    def test_a_recovered_field_shows_the_change_against_the_kept_baseline(self):
        previous = {'verified_source': True}
        current = {'verified_source': False}
        [change] = diff_fields(previous, current)['changed']
        assert change == {'field': 'verified_source', 'old': True, 'new': False}


class TestBaselineSurvivesStorage:
    """Round-1 finding P2-2: a `partial` section stores only a failure marker, so
    without a carried baseline the night after an outage reports the next real
    change as an appearance rather than as the change it is."""

    def test_a_change_across_an_outage_is_reported_as_a_change(self):
        night_1 = {'verified_source': True, 'owner': '0xa'}
        night_2 = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError'), 'owner': '0xa'},
            night_1,
        )
        assert night_2['verified_source']['baseline'] is True

        changes = diff_fields(night_2, {'verified_source': False, 'owner': '0xa'})
        assert changes['added'] == {}
        assert changes['changed'] == [
            {'field': 'verified_source', 'old': True, 'new': False}
        ]

    def test_the_baseline_chains_across_two_consecutive_outages(self):
        night_1 = {'verified_source': True}
        night_2 = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError')}, night_1
        )
        night_3 = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError')}, night_2
        )
        assert night_3['verified_source']['baseline'] is True
        assert diff_fields(night_3, {'verified_source': False})['changed'] == [
            {'field': 'verified_source', 'old': True, 'new': False}
        ]

    def test_the_outage_night_itself_still_diffs_empty(self):
        night_1 = {'verified_source': True}
        night_2 = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError')}, night_1
        )
        assert is_empty(diff_fields(night_1, night_2))

    def test_a_field_that_never_read_carries_no_baseline(self):
        stored = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError')}, {}
        )
        assert 'baseline' not in stored['verified_source']
        assert is_empty(diff_fields({}, stored))

    def test_a_recovery_to_the_same_value_is_not_a_change(self):
        night_1 = {'verified_source': True}
        night_2 = with_baselines(
            {'verified_source': failed_field('BlockscoutApiError')}, night_1
        )
        assert is_empty(diff_fields(night_2, {'verified_source': True}))

    def test_a_baseline_already_present_is_not_overwritten(self):
        marker = failed_field('BlockscoutApiError', baseline='kept')
        stored = with_baselines({'f': marker}, {'f': 'something-else'})
        assert stored['f']['baseline'] == 'kept'


class TestNumericGuard:
    """Round-1 finding P3-1: a non-finite delta is rejected by Postgres `jsonb`
    and would abort the build transaction mid-write."""

    def test_an_infinity_named_token_produces_no_delta(self):
        [change] = diff_fields({'name': 'Infinity'}, {'name': 'INF'})['changed']
        assert 'delta' not in change

    def test_nan_strings_produce_no_delta(self):
        [change] = diff_fields({'name': 'nan'}, {'name': 'NaN'})['changed']
        assert 'delta' not in change

    def test_hex_and_scientific_notation_are_not_numbers_here(self):
        for old, new in (('0x10', '0x11'), ('1e400', '1e401'), ('1_000', '1_001')):
            [change] = diff_fields({'v': old}, {'v': new})['changed']
            assert 'delta' not in change, (old, new)

    def test_a_uint256_delta_is_exact_not_rounded(self):
        old = str(10**30)
        new = str(10**30 + 7)
        [change] = diff_fields({'supply': old}, {'supply': new})['changed']
        assert change['delta'] == 7
        assert isinstance(change['delta'], int)

    def test_a_non_finite_float_value_produces_no_delta(self):
        [change] = diff_fields({'v': float('inf')}, {'v': 1.0})['changed']
        assert 'delta' not in change

    def test_every_delta_survives_json_dumps_into_jsonb(self, onchain_repository):
        """The actual failure mode, checked against the real server rather than
        by reasoning about `json.dumps`."""
        import json
        cases = [
            ({'name': 'Infinity'}, {'name': 'INF'}),
            ({'name': 'nan'}, {'name': 'NaN'}),
            ({'supply': str(10**30)}, {'supply': str(10**30 + 7)}),
            ({'v': '0.8'}, {'v': '0.9'}),
        ]
        for previous, current in cases:
            payload = json.dumps(diff_fields(previous, current))
            with onchain_repository.connection.cursor() as cursor:
                cursor.execute('SELECT %s::jsonb AS j', (payload,))
                assert cursor.fetchone()['j'] is not None
        onchain_repository.rollback()
