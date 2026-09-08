"""The dossier renderer: the pairing rule (A4) and the reading order.

A4 is a rendering criterion as much as a collection one -- "no gameable metric
appears without its paired counterpart ON THE SAME ROW" is about what the
operator sees. So these tests read the rendered text, not the field dictionary.
"""
from src.service.onchain import report

PAIRS = [
    {
        'metric': 'dex_volume_h24_usd', 'value': 125000.0, 'source': 'dex_provider',
        'counterpart': 'active_addresses_24h', 'counterpart_value': 91,
        'counterpart_source': 'transfer_history',
        'guards_against': 'wash_trading: volume without new counterparties',
    },
    {
        'metric': 'liquidity_usd', 'value': 48000.0, 'source': 'dex_provider',
        'counterpart': 'custody', 'counterpart_value': {'pool_type': 'v4'},
        'counterpart_source': 'position_events',
        'guards_against': 'liquidity_pull: depth nobody is committed to',
        'pool_type': 'v4',
    },
]


def _dossier(**overrides):
    dossier = {
        'project': 'touch-grass',
        'display_name': 'Touch Grass',
        'build': {
            'id': 12, 'run_id': 4, 'block': 900, 'block_timestamp': 1,
            'outcome': 'ok', 'threshold_version': '2026-09-06.1',
            'failed_units': [], 'started_at': None, 'finished_at': None,
        },
        'sections': [
            {
                'name': 'onchain_health', 'status': 'ok', 'error_class': None,
                'span_id': 'abc12345', 'evidence_ids': [1, 2],
                'fields': {'pairs': PAIRS, 'holder_count': 41},
                'changes': {'added': {}, 'removed': {}, 'changed': []},
                'flagged': [], 'previous_section_id': 9,
            }
        ],
    }
    dossier.update(overrides)
    return dossier


class TestPairing:
    def test_every_gameable_metric_prints_beside_its_counterpart(self):
        text = report.render_dossier(_dossier())
        for pair in PAIRS:
            row = next(
                line for line in text.splitlines() if pair['metric'] in line
            )
            # Same ROW, which is the criterion. A counterpart printed three
            # lines below would satisfy a field-level check and fail A4.
            assert pair['counterpart'] in row
            assert pair['guards_against'] in row

    def test_the_liquidity_row_names_its_pool_type(self):
        """A4's last clause: the custody read is defined differently for v3 and
        v4, so the row has to say which one it is."""
        text = report.render_dossier(_dossier())
        row = next(line for line in text.splitlines() if 'liquidity_usd' in line)
        assert 'pool type v4' in row

    def test_a_missing_counterpart_renders_as_visibly_absent(self):
        """The pairs are a list of objects, so there is no field a lone metric
        could be stored in -- and if one were built anyway, the row still has a
        counterpart slot, so the absence prints instead of the metric appearing
        alone and looking complete."""
        lone = [{'metric': 'dex_volume_h24_usd', 'value': 1, 'guards_against': 'x'}]
        text = report.render_dossier(_dossier(sections=[{
            'name': 'onchain_health', 'status': 'ok', 'error_class': None,
            'span_id': None, 'evidence_ids': [], 'fields': {'pairs': lone},
            'changes': {}, 'flagged': [], 'previous_section_id': None,
        }]))
        row = next(line for line in text.splitlines() if 'dex_volume_h24_usd' in line)
        assert 'None=None' in row


class TestReadingOrder:
    def test_flagged_changes_print_before_the_rest_of_the_diff(self):
        section = {
            'name': 'contract_safety', 'status': 'ok', 'error_class': None,
            'span_id': None, 'evidence_ids': [],
            'fields': {'owner': '0xbbb', 'holder_count': 5},
            'changes': {
                'added': {}, 'removed': {},
                'changed': [
                    {'field': 'holder_count', 'old': 4, 'new': 5, 'delta': 1},
                    {'field': 'owner', 'old': '0xaaa', 'new': '0xbbb'},
                ],
            },
            'flagged': [{'field': 'owner', 'reason': 'structural_change'}],
            'previous_section_id': 3,
        }
        lines = report.render_dossier(_dossier(sections=[section])).splitlines()
        flag_line = next(i for i, line in enumerate(lines) if line.startswith('  ! owner'))
        owner_change = next(i for i, line in enumerate(lines) if line.startswith('  ~ owner'))
        holder_change = next(i for i, line in enumerate(lines) if line.startswith('  ~ holder_count'))
        assert flag_line < owner_change < holder_change

    def test_an_unchanged_section_says_so(self):
        assert 'no change' in report.render_dossier(_dossier())

    def test_a_failed_field_prints_its_error_class_and_its_baseline(self):
        """A3: a fetch failure must read as a failure, never as a value that
        changed to nothing."""
        section = {
            'name': 'contract_safety', 'status': 'partial',
            'error_class': 'BlockscoutApiError', 'span_id': None, 'evidence_ids': [],
            'fields': {
                'verified_source': {
                    'state': 'failed', 'error_class': 'BlockscoutApiError',
                    'baseline': {'verified': True},
                }
            },
            'changes': {'added': {}, 'removed': {}, 'changed': []},
            'flagged': [], 'previous_section_id': 3,
        }
        text = report.render_dossier(_dossier(sections=[section]))
        assert 'FAILED (BlockscoutApiError)' in text
        assert 'baseline' in text
        assert '[partial]' in text

    def test_a_project_with_no_build_says_so_rather_than_printing_nothing(self):
        assert 'no build yet' in report.render_dossier(
            {'project': 'zzz', 'display_name': 'ZZZ', 'build': None, 'sections': []}
        )


class TestLedgerPayload:
    def test_a_run_row_renders_its_failed_units_and_spend(self):
        payload = report.render_runs([
            {
                'id': 7, 'job': 'onchain.build', 'started_at': None, 'finished_at': None,
                'outcome': 'partial',
                'failed_units_json': [{'unit': 'zzz/onchain_health', 'error_class': 'EvmRpcError'}],
                'spend_json': {'requests': {'public': 12}}, 'notes': 'x',
            }
        ])
        assert payload['runs'][0]['failed_units'][0]['unit'] == 'zzz/onchain_health'
        assert payload['runs'][0]['spend'] == {'requests': {'public': 12}}
