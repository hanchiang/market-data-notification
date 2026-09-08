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
        'metric': 'holder_count', 'value': 41, 'source': 'transfer_history',
        'counterpart': 'new_vs_returning_24h_and_top_ten_share',
        'counterpart_value': {'new': 3, 'returning': 8, 'top_ten_share': 0.4},
        'counterpart_source': 'transfer_history',
        'guards_against': 'wallet_splitting: one holder becoming twenty',
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
    def test_a_paired_metric_never_also_gets_an_unguarded_line_of_its_own(self):
        """The dossier stores `holder_count` inside its pair. A collector that
        also stored it at the top level would put the same gameable figure on the
        page WITHOUT its counterpart, which is the thing A4 forbids."""
        text = report.render_dossier(_dossier())
        assert '  holder_count: 41' not in text
        assert not any(
            line.strip().startswith('holder_count:') for line in text.splitlines()
        )
        # It is still on the page -- inside the row that guards it.
        assert any('holder_count=41' in line for line in text.splitlines())

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
            # F5 (test round 1): the counterpart's NAME appearing is not the
            # counterpart's VALUE appearing -- `_render_pairs` would happily
            # print `counterpart=None` and this loop stayed green until the
            # value itself was checked.
            assert pair['counterpart_value'] is not None
            assert f"{report._short(pair['counterpart_value'])}" in row

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


class TestPairingAgainstTheProductsOwnList:
    """The tests above render `PAIRS`, a literal in this file. That literal held
    three of the five pairs `health._pairs()` actually builds, so two gameable
    metrics -- `dex_trades_h24` and `primary_pool_share_of_provider_liquidity` --
    were rendered by no test at all, and a sixth pair added tomorrow would be
    rendered by no test either. A4 is a universal over the gameable metrics, and a
    universal discharged against a hand-copied subset is not discharged.

    These drive the real list instead, so the renderer is exercised over whatever
    the collector currently builds.
    """

    # Frozen deliberately: this is the tripwire, not a restatement. A sixth pair
    # is a real product decision (a new gameable metric and the counterpart that
    # guards it), and it must not land by a test quietly widening to accept it --
    # the design's pairing table has to move in the same change (E-2, test
    # round 1: docs/design/2026-09-06-project-dossier-collectors.md, the
    # Gameable-metric table -- this set is exactly its five rows).
    GAMEABLE_METRICS = {
        'dex_volume_h24_usd',
        'dex_trades_h24',
        'holder_count',
        'liquidity_usd',
        'primary_pool_share_of_provider_liquidity',
    }

    @staticmethod
    def _real_pairs():
        from src.service.onchain.collectors import health

        class _Project:
            key = 'touch-grass'
            pool_ref = '0xpool'

        class _Context:
            project = _Project()
            identity = {
                'pools': [
                    {'reference': '0xpool', 'liquidity_usd': 48000.0},
                    {'reference': '0xother', 'liquidity_usd': 12000.0},
                ]
            }

        return health._pairs(
            provider={
                'volume_h24_usd': 125000.0,
                'txns_h24': 310,
                'liquidity_usd': 48000.0,
            },
            active=91,
            counterparties=57,
            holders={'holder_count': 41, 'top_ten_share': 0.4},
            split={'new': 3, 'returning': 8},
            custody={'pool_type': 'v4', 'pool_liquidity': '29277002188455995842192'},
            context=_Context(),
        )

    def test_the_collector_builds_exactly_the_frozen_gameable_set(self):
        metrics = [pair['metric'] for pair in self._real_pairs()]
        assert len(metrics) == len(set(metrics))
        assert set(metrics) == self.GAMEABLE_METRICS

    def test_every_pair_the_collector_builds_carries_a_counterpart_and_a_mode(self):
        for pair in self._real_pairs():
            assert pair['counterpart'], pair['metric']
            assert pair['counterpart_source'], pair['metric']
            # The mode is what makes the pairing auditable rather than decorative:
            # A4 requires each pair to name the gaming it guards against.
            assert ':' in pair['guards_against'], pair['metric']

    def test_no_metric_the_collector_pairs_can_render_on_a_bare_line(self):
        """The render guard reads `fields['pairs']`, so it is self-referential:
        it suppresses a bare line only for metrics that are already in a pair.
        This drives every real metric through the top level as well, which is the
        shape the collector shipped once, and asserts the guard catches all five
        rather than the one `holder_count` case pinned above.
        """
        pairs = self._real_pairs()
        top_level = {pair['metric']: pair['value'] for pair in pairs}
        text = report.render_dossier(_dossier(sections=[{
            'name': 'onchain_health', 'status': 'ok', 'error_class': None,
            'span_id': None, 'evidence_ids': [], 'fields': {'pairs': pairs, **top_level},
            'changes': {}, 'flagged': [], 'previous_section_id': None,
        }]))
        for metric in self.GAMEABLE_METRICS:
            assert not any(
                line.strip().startswith(f'{metric}:') for line in text.splitlines()
            ), metric
            row = next(line for line in text.splitlines() if f'{metric}=' in line)
            pair = next(p for p in pairs if p['metric'] == metric)
            assert pair['counterpart'] in row
            assert pair['guards_against'] in row

    def test_both_liquidity_rows_name_the_pool_type(self):
        """A4's last clause applies to every row whose counterpart is a custody
        read, not only to `liquidity_usd`."""
        pairs = self._real_pairs()
        text = report.render_dossier(_dossier(sections=[{
            'name': 'onchain_health', 'status': 'ok', 'error_class': None,
            'span_id': None, 'evidence_ids': [], 'fields': {'pairs': pairs},
            'changes': {}, 'flagged': [], 'previous_section_id': None,
        }]))
        for pair in pairs:
            if 'pool_type' not in pair:
                continue
            row = next(line for line in text.splitlines() if pair['metric'] in line)
            assert 'pool type v4' in row, pair['metric']
