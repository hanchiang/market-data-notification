"""The dossier's readability, pinned to what the 2026-09-11 read of the page
found unreadable: base-unit amounts, fractions, epoch stamps, records cut off
at 160 characters, nested changes printed as two whole blobs, and a page that
buried the diff under forty identity fields.

Every test here reads rendered text or HTML, not the field dictionary, because
readability is a property of what the operator sees.
"""
import json
import re

from src.router.project_monitor.dashboard import render_dossier_page
from src.service.onchain import page, report

WAD = 10 ** 18
HOLDERS = [
    {'address': f'0x{i:040x}', 'balance': str((100 - i) * 1_000_000 * WAD), 'share': 0.01 * (100 - i) / 10}
    for i in range(1, 12)
]


def _section(name, fields, *, changes=None, flagged=None, status='ok'):
    return {
        'name': name, 'status': status, 'error_class': None, 'span_id': None,
        'evidence_ids': [], 'fields': fields,
        'changes': changes if changes is not None else {'added': {}, 'removed': {}, 'changed': []},
        'flagged': flagged or [], 'previous_section_id': 1,
    }


def _dossier(sections, builds=None):
    return {
        'project': 'touch-grass', 'display_name': 'Touch Grass',
        'build': {
            'id': 18, 'run_id': 6, 'block': 60_288_320, 'block_timestamp': 1_789_132_894,
            'outcome': 'ok', 'threshold_version': '2026-09-06.1', 'failed_units': [],
            'started_at': None, 'finished_at': None,
        },
        'builds': builds or [],
        'sections': sections,
    }


IDENTITY = _section('identity', {
    'token_symbol': 'GRASS', 'decimals': 18, 'pair_created_at': 1_788_450_576_000,
    'tick': 133_478,
})


class TestFormatting:
    def test_amounts_are_scaled_by_the_tokens_decimals_and_named(self):
        text = report.render_dossier(_dossier([
            IDENTITY,
            _section('token_economics', {'burned': str(24_003_596 * WAD + 905_748_870_304_250_637)}),
        ]))
        assert '  burned: 24,003,596.9057 GRASS' in text
        assert '24003596905748870304250637' not in text

    def test_a_move_smaller_than_one_unit_still_shows_and_a_decrease_has_one_sign(self):
        formatting = report.Formatting(_dossier([IDENTITY]), section='token_economics')
        assert formatting.amount(str(1000 * WAD + WAD // 10)) == '1,000.1 GRASS'
        assert formatting.amount(str(-(WAD // 2))) == '-0.5 GRASS'
        assert formatting.amount(str(-(3 * WAD + WAD // 4))) == '-3.25 GRASS'
        assert formatting.amount('0') == '0 GRASS'
        assert formatting.amount(str(WAD // 10 ** 6)) == '1e-06 GRASS'

    def test_a_fraction_that_rounds_away_carries_into_the_whole_and_leaves_no_bare_dot(self):
        """Round 2: formatting whole and fraction separately printed
        1000.99999 as `1,000.` -- a token short and a dangling point."""
        formatting = report.Formatting(_dossier([IDENTITY]), section='token_economics')
        assert formatting.amount(str(1000 * WAD + 999_999_999_999_999_999)) == '1,001 GRASS'
        assert formatting.amount(str(1000 * WAD + 1)) == '1,000 GRASS'
        assert formatting.amount(str(1000 * WAD + WAD // 100_000)) == '1,000 GRASS'
        assert formatting.amount(str(1000 * WAD + WAD // 10_000)) == '1,000.0001 GRASS'
        assert formatting.amount(str(999 * WAD + 999_999_999_999_999_999)) == '1,000 GRASS'
        assert '.' not in formatting.amount(str(1000 * WAD + 1)).split(' ')[0]

    def test_a_uint256_max_supply_renders_instead_of_raising(self):
        """Round 3: `quantize` under the default 28-digit context raised on
        anything past 1e24 units, a 500 on the page for a troll token."""
        formatting = report.Formatting(_dossier([IDENTITY]), section='token_economics')
        text = formatting.amount(str(2 ** 256 - 1))
        assert text.startswith('115,792,089,237,316,195,423,570,985,008,687,907,853,269,984,665,640,564,039,457.584')
        assert text.endswith(' GRASS')
        assert formatting.amount(str(10 ** 30 * WAD)) == '1,000,000,000,000,000,000,000,000,000,000 GRASS'

    def test_an_amount_outside_token_economics_is_not_scaled(self):
        """The name sets match bare field names; only the token section's
        amounts are the project token's."""
        text = report.render_dossier(_dossier([
            IDENTITY, _section('onchain_health', {'amount': str(5 * WAD)}),
        ]))
        assert f'amount: {5 * WAD}' in text
        assert 'GRASS' not in text.split('-- onchain_health')[1]

    def test_an_amount_with_unknown_decimals_stays_raw_and_says_so(self):
        """A wrong scale reads as a wrong number; no scale reads as a raw one."""
        text = report.render_dossier(_dossier([
            _section('token_economics', {'burned': str(24 * WAD)}),
        ]))
        assert 'burned: 24,000,000,000,000,000,000 (base units)' in text

    def test_shares_read_as_percentages(self):
        text = report.render_dossier(_dossier([
            IDENTITY, _section('token_economics', {'burned_share': 0.024345}),
        ]))
        assert 'burned_share: 2.43%' in text

    def test_epoch_stamps_read_as_dates_in_seconds_and_milliseconds(self):
        text = report.render_dossier(_dossier([IDENTITY]))
        assert 'pair_created_at: 2026-09-03 15:49 UTC' in text
        assert 'build 18 · run 6 · block 60288320 · 2026-09-11 13:21 UTC' in text

    def test_a_tick_is_a_coordinate_not_a_count(self):
        text = report.render_dossier(_dossier([IDENTITY]))
        assert 'tick: 133478' in text


class TestNoTruncation:
    def test_every_top_holder_prints_on_its_own_line(self):
        """The 160-character cut hid holders three onward; a holder table the
        operator cannot read is the wallet-splitting check with no reader."""
        text = report.render_dossier(_dossier([
            IDENTITY, _section('token_economics', {'top_holders': HOLDERS}),
        ]))
        assert 'top_holders (11):' in text
        for holder in HOLDERS:
            assert f"- {holder['address']}  balance=" in text
        assert '…' not in text

    def test_a_dict_field_prints_one_key_per_line(self):
        text = report.render_dossier(_dossier([
            _section('identity', {'deployer': {'creator': '0xabc', 'creation_tx': '0xdef'}}),
        ]))
        assert '  deployer:\n    creation_tx: 0xdef\n    creator: 0xabc' in text

    def test_an_empty_dict_is_distinguishable_from_a_missing_value(self):
        text = report.render_dossier(_dossier([_section('identity', {'deployer': {}})]))
        assert '  deployer: {}' in text


class TestNestedChanges:
    def test_a_changed_record_list_names_the_item_and_the_key_that_moved(self):
        old = [{'reference': '0xAAA', 'liquidity_usd': 185130.81, 'version': 'v4'}]
        new = [{'reference': '0xAAA', 'liquidity_usd': 184910.6, 'version': 'v4'}]
        text = report.render_dossier(_dossier([
            _section('identity', {'pools': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pools', 'old': old, 'new': new}],
            }),
        ]))
        assert '  ~ pools:\n      0xAAA  liquidity_usd: 185,130.81 -> 184,910.60' in text

    def test_health_pairs_diff_per_metric_not_as_two_blobs(self):
        """2026-09-12 read: `pairs` printed as two 20-line blobs. Its items are
        keyed by `metric`, so the change names the metric and the value that moved."""
        old = [{'metric': 'dex_volume_h24_usd', 'value': 467444.27, 'source': 'dex_provider',
                'counterpart': 'active_addresses_24h', 'counterpart_value': 991,
                'guards_against': 'wash_trading: volume without new counterparties'}]
        new = [dict(old[0], value=449124.39, counterpart_value=984)]
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'pairs': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pairs', 'old': old, 'new': new}],
            }),
        ]))
        assert (
            '  ~ pairs:\n'
            '      dex_volume_h24_usd  counterpart_value: 991 -> 984\n'
            '      dex_volume_h24_usd  value: 467,444.27 -> 449,124.39\n'
        ) in text
        changed_part = text.split('~ pairs:')[1].split('  pairs')[0]
        assert 'guards_against' not in changed_part

    def test_a_record_valued_counterpart_diffs_by_leaf(self):
        old = [{'metric': 'liquidity_usd', 'value': 1.0,
                'counterpart_value': {'largest_owner_share': 0.6244, 'open_positions': 53, 'pool_type': 'v3'}}]
        new = [{'metric': 'liquidity_usd', 'value': 1.0,
                'counterpart_value': {'largest_owner_share': 0.5738, 'open_positions': 17, 'pool_type': 'v3'}}]
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'pairs': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pairs', 'old': old, 'new': new}],
            }),
        ]))
        assert '      liquidity_usd  counterpart_value.largest_owner_share: 62.44% -> 57.38%\n' in text
        assert '      liquidity_usd  counterpart_value.open_positions: 53 -> 17\n' in text
        assert 'pool_type=v3 ->' not in text

    def test_a_pair_share_metric_is_formatted_as_the_state_block_formats_it(self):
        """Review 2026-09-12: the diff printed 0.5738 for a share the state
        block below printed as 57.38%. The row's metric names the value."""
        old = [{'metric': 'primary_pool_share_of_provider_liquidity', 'value': 0.6244,
                'counterpart': 'largest_owner_share', 'counterpart_value': 0.5}]
        new = [dict(old[0], value=0.5738, counterpart_value=0.25)]
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'pairs': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pairs', 'old': old, 'new': new}],
            }),
        ]))
        assert '      primary_pool_share_of_provider_liquidity  value: 62.44% -> 57.38%\n' in text
        assert '      primary_pool_share_of_provider_liquidity  counterpart_value: 50.00% -> 25.00%\n' in text

    def test_records_differing_only_by_none_versus_absent_still_print(self):
        """`_dict_delta` reads None and absent alike; two unequal custody
        records then yield no leaf, and the row must not read as reordered."""
        old = [{'metric': 'liquidity_usd', 'value': 1.0, 'counterpart_value': {'pool_liquidity': None}}]
        new = [{'metric': 'liquidity_usd', 'value': 1.0, 'counterpart_value': {}}]
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'pairs': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pairs', 'old': old, 'new': new}],
            }),
        ]))
        assert '(reordered only)' not in text
        assert '      liquidity_usd  counterpart_value: pool_liquidity=None -> {}\n' in text

    def test_bookkeeping_is_demoted_only_in_onchain_health_and_kept_in_json(self):
        changes = {'added': {}, 'removed': {},
                   'changed': [{'field': 'window', 'old': {'from_block': 1}, 'new': {'from_block': 2}}]}
        elsewhere = [b for b in report.render_blocks(_dossier([
            _section('identity', {'window': {'from_block': 2}}, changes=changes),
        ])) if b.name == 'identity'][0]
        assert elsewhere.has_changes
        assert elsewhere.change_lines == ['  ~ window:', '      from_block: 1 -> 2']
        payload = report.render_json(_dossier([
            _section('onchain_health', {'window': {'from_block': 2}}, changes=changes),
        ]))
        assert '"field": "window"' in payload

    def test_an_appearing_pair_row_formats_its_share_under_the_metric(self):
        new = [{'metric': 'primary_pool_share_of_provider_liquidity', 'value': 0.5738,
                'counterpart': 'largest_owner_share', 'counterpart_value': 0.25, 'guards_against': 'x'}]
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'pairs': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pairs', 'old': [], 'new': new}],
            }),
        ]))
        assert ('      + primary_pool_share_of_provider_liquidity  counterpart=largest_owner_share, '
                'counterpart_value=25.00%, guards_against=x, value=57.38%\n') in text

    def test_a_flag_with_an_empty_diff_does_not_print_no_change(self):
        text = report.render_dossier(_dossier([
            _section('identity', {'owner': '0x1'}, changes={'added': {}, 'removed': {}, 'changed': []},
                     flagged=[{'field': 'owner', 'reason': 'structural_change'}]),
        ]))
        assert '  ! owner: structural_change\n  owner: 0x1' in text

    def test_a_flagged_bookkeeping_field_does_not_read_as_no_change(self):
        changes = {'added': {}, 'removed': {},
                   'changed': [{'field': 'transfer_rows', 'old': 10, 'new': 2, 'delta': -8}]}
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'transfer_rows': 2}, changes=changes,
                     flagged=[{'field': 'transfer_rows', 'reason': 'row count fell'}]),
        ]))
        assert '  ! transfer_rows: row count fell\n  (bookkeeping moved: transfer_rows)' in text
        assert '  no change' not in text.split('-- onchain_health')[1]

    def test_bookkeeping_moves_do_not_lead_and_do_not_open_the_section(self):
        """The fetch window and cursor walk move every night by construction;
        on 2026-09-12 they buried the one real change under twelve lines."""
        bookkeeping = {
            'added': {}, 'removed': {},
            'changed': [
                {'field': 'window', 'old': {'from_block': 1}, 'new': {'from_block': 2}},
                {'field': 'transfer_rows', 'old': 10, 'new': 12, 'delta': 2},
                {'field': 'transfer_fetch', 'old': {'fetched': 5}, 'new': {'fetched': 2}},
            ],
        }
        quiet = _section('onchain_health', {'transfer_rows': 12}, changes=bookkeeping)
        html = render_dossier_page(_dossier([IDENTITY, quiet]))
        moved = html[html.index('id="what-moved"'):html.index('id="pools"')]
        assert 'What moved since previous build (0)' in moved
        assert 'from_block' not in moved
        assert 'bookkeeping moved: transfer_fetch, transfer_rows, window' in moved
        text = report.render_dossier(_dossier([quiet]))
        assert '  no change beyond bookkeeping\n  (bookkeeping moved: transfer_fetch, transfer_rows, window)' in text
        assert 'from_block' not in text.split('-- onchain_health')[1].split('  transfer_rows')[0]

    def test_a_real_change_still_leads_with_bookkeeping_last(self):
        changes = {
            'added': {}, 'removed': {},
            'changed': [
                {'field': 'window', 'old': {'from_block': 1}, 'new': {'from_block': 2}},
                {'field': 'derivation_check', 'old': 'ok', 'new': 'mismatch'},
            ],
        }
        block = [b for b in report.render_blocks(_dossier([
            _section('onchain_health', {'derivation_check': 'mismatch'}, changes=changes),
        ])) if b.name == 'onchain_health'][0]
        assert block.has_changes
        assert block.change_lines == [
            '  ~ derivation_check: ok -> mismatch',
            '  (bookkeeping moved: window)',
        ]

    def test_a_case_only_respelling_of_the_identity_is_not_a_change(self):
        old = [{'address': '0xABC', 'balance': '1', 'share': 0.5}]
        new = [{'address': '0xabc', 'balance': '1', 'share': 0.5}]
        text = report.render_dossier(_dossier([
            IDENTITY,
            _section('token_economics', {'top_holders': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'top_holders', 'old': old, 'new': new}],
            }),
        ]))
        assert '  ~ top_holders:\n      (reordered only)' in text
        assert 'address:' not in text

    def test_a_share_delta_is_in_points_not_percent(self):
        text = report.render_dossier(_dossier([
            IDENTITY,
            _section('token_economics', {'burned_share': 0.03}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'burned_share', 'old': 0.01, 'new': 0.03, 'delta': 0.02}],
            }),
        ]))
        assert '~ burned_share: 1.00% -> 3.00% (delta 2.00 pp)' in text

    def test_a_changed_dict_names_only_the_keys_that_moved(self):
        old = {'amount': str(0), 'positions': 0, 'method': 'v4_tick_math'}
        new = {'amount': str(23 * WAD), 'positions': 1, 'method': 'v4_tick_math'}
        text = report.render_dossier(_dossier([
            IDENTITY,
            _section('token_economics', {'pool_held_share': new}, changes={
                'added': {}, 'removed': {},
                'changed': [{'field': 'pool_held_share', 'old': old, 'new': new}],
            }),
        ]))
        assert '      amount: 0 GRASS -> 23 GRASS' in text
        assert '      positions: 0 -> 1' in text
        assert 'method: v4_tick_math -> v4_tick_math' not in text

    def test_a_first_successful_section_is_one_line_not_every_field_twice(self):
        fields = {'a': 1, 'b': 2, 'c': 3}
        text = report.render_dossier(_dossier([
            _section('onchain_health', fields, changes={
                'added': dict(fields), 'removed': {}, 'changed': [],
            }),
        ]))
        assert 'first successful build of this section: 3 fields, no baseline' in text
        assert '  + a: 1' not in text

    def test_an_all_failed_section_does_not_claim_a_first_successful_build(self):
        failed = {'state': 'failed', 'error_class': 'X', 'baseline': None}
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'a': failed}, changes={
                'added': {'a': failed}, 'removed': {}, 'changed': [],
            }),
        ]))
        assert 'first successful build' not in text
        assert '  + a:' in text

    def test_a_partial_addition_still_lists_what_was_added(self):
        text = report.render_dossier(_dossier([
            _section('onchain_health', {'a': 1, 'b': 2}, changes={
                'added': {'b': 2}, 'removed': {}, 'changed': [],
            }),
        ]))
        assert '  + b: 2' in text


class TestProjectKey:
    def test_the_prefix_is_stripped_and_a_bare_key_is_returned_whole(self):
        assert report.project_key('project:touch-grass') == 'touch-grass'
        assert report.project_key('touch-grass') == 'touch-grass'
        assert report.project_key('project:a:b') == 'a:b'


class TestPageShape:
    def _page(self):
        changed = _section('token_economics', {'burned': str(WAD)}, changes={
            'added': {}, 'removed': {},
            'changed': [{'field': 'burned', 'old': '0', 'new': str(WAD), 'delta': WAD}],
        })
        quiet = _section('contract_safety', {'owner': 'absent'})
        builds = [
            {'id': 18, 'run_id': 6, 'block': 60_288_320, 'outcome': 'ok', 'block_timestamp': 1_789_132_894},
            {'id': 17, 'run_id': 5, 'block': 60_270_535, 'outcome': 'partial', 'block_timestamp': 1_789_046_000},
        ]
        return render_dossier_page(
            _dossier([IDENTITY, quiet, changed], builds=builds),
            projects=['not-a-website', 'touch-grass'],
        )

    def test_what_moved_leads_and_the_raw_text_follows(self):
        html = self._page()
        assert html.index('id="what-moved"') < html.index('id="raw-diff"')
        moved = html[html.index('id="what-moved"'):html.index('id="pools"')]
        assert 'What moved since previous build (1)' in moved
        assert '<td title="burned">burned</td>' in moved and '0 GRASS' in moved and '1 GRASS' in moved
        assert 'contract_safety' not in moved

    def test_the_raw_diff_is_folded_and_holds_every_report_line(self):
        html = self._page()
        assert '<details class="panel wide" id="raw-diff">' in html
        assert '<details open' not in html
        raw = html[html.index('id="raw-diff"'):]
        assert '-- token_economics [ok]' in raw and '-- contract_safety [ok]' in raw
        assert '~ burned: 0 GRASS -&gt; 1 GRASS' in raw

    def test_builds_and_projects_are_linked_and_the_current_ones_are_not(self):
        html = self._page()
        assert '<option value="?build=17">build 17 (run 5) · 2026-09-10 13:13 UTC · partial</option>' in html
        assert '<option value="?build=18" selected>build 18 (run 6) · 2026-09-11 13:21 UTC</option>' in html
        assert '<a class="m" id="prev-build" href="?build=17">prev build 17</a>' in html
        assert '<a href="not-a-website">not-a-website</a>' in html
        assert '<strong>touch-grass</strong>' in html

    def test_links_carry_the_store_they_were_read_from(self):
        """A bare `?build=17` drops `test_mode`, and the default store is
        production: a test-store reader would switch stores by clicking."""
        changed = _section('token_economics', {'burned': str(WAD)})
        builds = [
            {'id': 18, 'block': 1, 'outcome': 'ok', 'block_timestamp': 1_789_132_894},
            {'id': 17, 'block': 0, 'outcome': 'ok', 'block_timestamp': 1_789_046_000},
        ]
        html = render_dossier_page(
            _dossier([IDENTITY, changed], builds=builds),
            projects=['not-a-website', 'touch-grass'], test_mode=True,
        )
        assert '<option value="?build=17&test_mode=1">' in html
        assert 'href="?build=17&test_mode=1"' in html
        assert '<a href="not-a-website?test_mode=1">' in html
        assert 'href="not-a-website?"' not in self._page()

    def test_a_project_key_that_is_not_a_plain_slug_is_never_an_href(self):
        html = render_dossier_page(
            _dossier([IDENTITY]), projects=['javascript:alert(1)', 'project:', 'touch-grass'],
        )
        assert 'javascript:' not in html
        assert 'href="project:' not in html
        assert '<strong>touch-grass</strong>' in html

    def test_a_failed_section_is_itself_a_what_moved_row(self):
        """A section that could not be built is what changed since the
        previous build, even with no field diff to show."""
        failed = _section('onchain_health', {}, status='failed')
        failed['error_class'] = 'EvmTransportError'
        failed['changes'] = {}
        html = render_dossier_page(_dossier([IDENTITY, failed]))
        moved = html[html.index('id="what-moved"'):html.index('id="pools"')]
        assert 'What moved since previous build (1)' in moved
        assert '<td title="section">section</td>' in moved and 'failed (EvmTransportError)' in moved
        assert 'section not built' in moved
        assert '-- onchain_health [failed] (EvmTransportError)' in html[html.index('id="raw-diff"'):]

    def test_the_page_references_no_host(self):
        """Local-first: the page must be readable with the network off, and a
        loopback origin that could read every other route here must load
        nothing from anywhere else. The one script is the vendored Chart.js on
        this origin."""
        html = self._page()
        assert not re.search(r'(src|href)="(https?:)?//', html)
        assert re.findall(r'<script src="([^"]*)"', html) == [page.CHART_SCRIPT]
        assert page.CHART_SCRIPT.startswith('/project-monitor/')
        assert '<link' not in html and '@import' not in html

    def test_chain_data_is_escaped(self):
        html = render_dossier_page(_dossier([
            _section('identity', {'token_name': '<script>alert(1)</script>'}),
        ]))
        assert '<script>alert' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html

    def test_no_build_says_so_and_still_links_the_other_projects(self):
        html = render_dossier_page(
            {'project': 'zzz', 'display_name': 'ZZZ', 'build': None, 'sections': []},
            projects=['touch-grass', 'zzz'],
        )
        assert 'no build yet' in html
        assert '<a href="touch-grass">touch-grass</a>' in html
        assert html.endswith('</body></html>')


# -- Slice A: the mission-control page (UX brief 2026-09-12) -------------------

POOL_A = '0x64c5dbbee60473344dc6f7b11391ff9c7bb7464c0d5ecfb0d311ff26df9f8c77'
POOL_B = '0x1111111111111111111111111111111111111111111111111111111111111111'
CUSTODY = {
    'pool_type': 'v4', 'pool_liquidity': '29277002188455995842192', 'open_positions': 1,
    'owner_count': 1, 'largest_owner': '0x' + 'ab' * 20, 'largest_owner_share': 1.0,
    'share_by_class': {'eoa': 1.0}, 'liquidity_by_class': {'eoa': '29277002188455995842192'},
}


def _pairs(liquidity, volume, trades, holders, top_ten):
    return [
        {'metric': 'liquidity_usd', 'value': liquidity, 'source': 'dex_provider',
         'counterpart': 'custody', 'counterpart_value': CUSTODY,
         'guards_against': 'liquidity_pull: depth nobody is committed to', 'pool_type': 'v4'},
        {'metric': 'dex_volume_h24_usd', 'value': volume, 'source': 'dex_provider',
         'counterpart': 'active_addresses_24h', 'counterpart_value': 2724,
         'guards_against': 'wash_trading: volume without new counterparties'},
        {'metric': 'dex_trades_h24', 'value': trades, 'source': 'dex_provider',
         'counterpart': 'pool_counterparties_24h', 'counterpart_value': 300,
         'guards_against': 'bot_churn: trades without distinct counterparties'},
        {'metric': 'holder_count', 'value': holders, 'source': 'transfer_history',
         'counterpart': 'new_vs_returning_24h_and_top_ten_share',
         'counterpart_value': {'new': 12, 'returning': 40, 'top_ten_share': top_ten},
         'guards_against': 'wallet_splitting: one holder becoming twenty'},
        {'metric': 'primary_pool_share_of_provider_liquidity', 'value': 0.4987,
         'source': 'dex_provider', 'counterpart': 'primary_pool_onchain_liquidity',
         'counterpart_value': '29277002188455995842192',
         'guards_against': 'fragmentation: many small third-party pools around a launch',
         'pool_type': 'v4'},
    ]


def _mission_control_dossier(history_points=0):
    old_pairs = _pairs(169_900.0, 413_000.0, 1_123, 6_601, 0.268672)
    new_pairs = _pairs(173_738.69, 418_800.0, 1_161, 6_598, 0.266329)
    health = _section('onchain_health', {
        'pairs': new_pairs, 'price_usd': 0.003095, 'fdv_usd': 3_048_089.0,
        'window': {'from_block': 2}, 'transfer_rows': 5, 'derivation_check': 'ok',
    }, changes={'added': {}, 'removed': {}, 'changed': [
        {'field': 'pairs', 'old': old_pairs, 'new': new_pairs},
        {'field': 'window', 'old': {'from_block': 1}, 'new': {'from_block': 2}},
        {'field': 'price_usd', 'old': 0.0031, 'new': 0.003095, 'delta': -0.000005},
    ]})
    pools = [
        {'reference': POOL_A, 'dex': 'uniswap', 'version': 'v4', 'liquidity_usd': 173_738.69},
        {'reference': POOL_B, 'dex': 'uniswap', 'version': 'v3', 'liquidity_usd': 174_700.0},
    ]
    identity = _section('identity', {
        'token_symbol': 'GRASS', 'decimals': 18, 'pool_count': 12, 'pool_ref': POOL_A,
        'pools': pools,
    }, changes={'added': {}, 'removed': {}, 'changed': [
        {'field': 'pools', 'old': [dict(pools[0], liquidity_usd=169_900.0), pools[1]], 'new': pools},
    ]})
    economics = _section('token_economics', {
        'top_holders': HOLDERS, 'top_ten_share': 0.266329, 'burned_share': 0.024,
        'pool_held_share': {'share': 0.0251, 'positions': 1, 'amount': '1', 'method': 'v4_tick_math'},
        'total_supply': str(10 ** 9 * WAD),
    }, changes={'added': {}, 'removed': {}, 'changed': [
        {'field': 'top_ten_share', 'old': 0.268672, 'new': 0.266329, 'delta': -0.002343},
        {'field': 'top_holders', 'old': [dict(HOLDERS[0], share=0.0349), *HOLDERS[1:]], 'new': HOLDERS},
    ]})
    dossier = _dossier([identity, health, economics], builds=[
        {'id': 26, 'run_id': 9, 'block': 2, 'outcome': 'ok', 'block_timestamp': 1_789_132_894},
        {'id': 24, 'run_id': 8, 'block': 1, 'outcome': 'failed', 'block_timestamp': 1_789_100_000},
        {'id': 22, 'run_id': 7, 'block': 0, 'outcome': 'ok', 'block_timestamp': 1_789_046_000},
    ])
    dossier['build'] = dict(dossier['build'], id=26, run_id=9)
    dossier['chain'] = 'Robinhood Chain'
    dossier['archetype'] = 'launchpad-fixed-supply'
    dossier['sources'] = [
        {'class': 'web', 'admission': 'admitted', 'scope': 'project'},
        {'class': 'web', 'admission': 'admitted', 'scope': 'project'},
        {'class': 'x', 'admission': 'candidate', 'scope': 'project'},
        {'class': 'chain_explorer', 'admission': 'admitted', 'scope': 'chain'},
    ]
    history = {'project': 'touch-grass', 'metrics': list(report.HISTORY_METRICS), 'points': [
        {'build_id': i, 'block_timestamp': 1_788_000_000 + i * 86_400,
         'values': {m: (float(i) if m != 'fdv_usd' else None) for m in report.HISTORY_METRICS}}
        for i in range(history_points)
    ]}
    return dossier, history


def _element(html, element_id):
    """The markup from an element's `id` to the next panel or tile id."""
    start = html.index(f'id="{element_id}"')
    following = [m.start() for m in re.finditer(r' id="(tile-[a-z-]+|[a-z-]+)"', html) if m.start() > start]
    return html[start:following[0]] if following else html[start:]


class TestPanelsAndDecisionTags:
    def test_every_panel_and_tile_is_on_the_page_in_the_briefs_order(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        order = ['header', 'sources', 'kpis', *[t[0] for t in page.TILES], 'charts', *page.PANEL_IDS]
        positions = [html.index(f'id="{element_id}"') for element_id in order]
        assert positions == sorted(positions)

    def test_every_rendered_panel_and_tile_carries_its_decision_tag(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        rendered = re.findall(
            r'<(?:section|div|details)\b[^>]*\bclass="(?:panel|kpi|badges)(?: [^"]*)?"[^>]*\bid="([^"]+)"|'
            r'<(?:section|div|details)\b[^>]*\bid="([^"]+)"[^>]*\bclass="(?:panel|kpi|badges)(?: [^"]*)?"',
            html,
        )
        ids = {a or b for a, b in rendered}
        assert ids >= {'sources', *page.PANEL_IDS, *[t[0] for t in page.TILES]}
        for element_id in ids:
            assert element_id in page.DECISION_TAGS, element_id
            expected = f'<span class="tag" title="{page.DECISION_TAGS[element_id]}">'
            assert expected in _element(html, element_id), element_id

    def test_the_tag_mapping_is_the_briefs(self):
        assert page.DECISION_TAGS['tile-liquidity'] == 'exit · slow: liquidity that can be pulled'
        assert page.DECISION_TAGS['raw-diff'] == 'context: supports What moved'
        assert page.DECISION_TAGS['tile-price'] == page.DECISION_TAGS['tile-fdv']


class TestKpiStrip:
    def test_values_are_the_payloads_figures_in_brief_form_with_the_exact_form_on_hover(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        assert '<div class="v" title="$173,738.69">$173.7k</div>' in _element(html, 'tile-liquidity')
        assert '<div class="v" title="$418,800.00">$418.8k</div>' in _element(html, 'tile-volume')
        assert '<div class="v" title="1,161">1,161</div>' in _element(html, 'tile-trades')
        assert '<div class="v" title="6,598">6,598</div>' in _element(html, 'tile-holders')
        assert '<div class="v" title="26.6329%">26.63%</div>' in _element(html, 'tile-top-ten')
        assert '<div class="v" title="12">12</div>' in _element(html, 'tile-pools')
        assert '<div class="v" title="$0.003095">$0.003095</div>' in _element(html, 'tile-price')
        assert '<div class="v" title="$3,048,089.00">$3.0m</div>' in _element(html, 'tile-fdv')

    def test_deltas_are_computed_on_stored_values_and_rounded_once(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        assert '<div class="d down" title="previous build: 26.8672%">-0.23 pp <span' in _element(html, 'tile-top-ten')
        assert '<div class="d up" title="previous build: $169,900.00">+2.3% <span' in _element(html, 'tile-liquidity')
        assert '<div class="d down" title="previous build: 6,601">-3 <span' in _element(html, 'tile-holders')
        assert '<div class="d flat" title="previous build: 12">= <span' in _element(html, 'tile-pools')
        # FDV was not in the diff at all: unchanged, so `=`; price was.
        assert '= <span' in _element(html, 'tile-fdv')
        assert '<div class="d down" title="previous build: $0.0031">-0.2% <span' in _element(html, 'tile-price')

    def test_a_first_build_says_so_instead_of_inventing_a_delta(self):
        dossier, history = _mission_control_dossier()
        for section in dossier['sections']:
            section['changes'] = {}
        html = render_dossier_page(dossier, history=history)
        assert 'first build <span class="flat">vs prev build</span>' in _element(html, 'tile-liquidity')

    def test_the_holders_tile_carries_the_new_versus_returning_split(self):
        dossier, history = _mission_control_dossier()
        assert 'new 12 · returning 40' in _element(render_dossier_page(dossier, history=history), 'tile-holders')

    def test_the_build_picker_names_the_run_and_the_previous_build(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        assert '<option value="?build=26" selected>build 26 (run 9) · 2026-09-11 13:21 UTC</option>' in html
        assert '<option value="?build=24">build 24 (run 8) · 2026-09-11 04:13 UTC · failed</option>' in html
        # The failed build 24 produced no baseline; 22 is what this build is diffed against.
        assert 'href="?build=22">prev build 22</a>' in html
        assert 'GRASS · Robinhood Chain · launchpad-fixed-supply' in html

    def test_the_header_lists_a_section_diffed_against_a_different_build(self):
        dossier, history = _mission_control_dossier()
        for section in dossier['sections']:
            section['previous_build_id'] = 24
        dossier['sections'][1]['previous_build_id'] = 22  # onchain_health failed in 24
        html = render_dossier_page(dossier, history=history)
        assert '<a class="m" id="prev-build" href="?build=24">prev build 24 · onchain_health vs 22</a>' in html
        for section in dossier['sections']:
            section['previous_build_id'] = 22
        assert 'prev build 22</a>' in render_dossier_page(dossier, history=history)
        # A tie goes to the nearer (higher) build: two sections on 24, two on 22.
        dossier['sections'].append(_section('contract_safety', {'owner': 'absent'}))
        for section, build_id in zip(dossier['sections'], (24, 22, 24, 22)):
            section['previous_build_id'] = build_id
        html = render_dossier_page(dossier, history=history)
        assert 'prev build 24 · onchain_health vs 22 · contract_safety vs 22</a>' in html

    def test_source_badges_count_admitted_rows_and_mark_configured_classes(self):
        dossier, history = _mission_control_dossier()
        badges = _element(render_dossier_page(dossier, history=history), 'sources')
        assert '<span class="dot ok"></span>web 2<' in badges
        assert '<span class="dot cand"></span>x cand 1<' in badges
        assert '<span class="dot ok"></span>chain_explorer 1<' in badges
        assert '<span class="dot cfg"></span>chain_rpc (config)<' in badges
        assert '<span class="dot cfg"></span>dex_provider (config)<' in badges
        assert '<span class="dot "></span>telegram none<' in badges


class TestSparklinesAndCharts:
    def test_under_seven_points_the_tile_shows_the_count_and_no_sparkline(self):
        dossier, history = _mission_control_dossier(history_points=3)
        html = render_dossier_page(dossier, history=history)
        assert html.count('>3/7 builds<') == 7
        assert '>0/7 builds<' in _element(html, 'tile-fdv')
        assert 'data-spark=' not in html

    def test_seven_points_emit_the_sparkline_dataset(self):
        dossier, history = _mission_control_dossier(history_points=7)
        html = render_dossier_page(dossier, history=history)
        assert '<canvas data-spark="liquidity_usd"' in _element(html, 'tile-liquidity')
        assert 'data-spark="fdv_usd"' not in html  # every fdv point is a gap
        inline = json.loads(re.search(
            r'<script id="history" type="application/json">(.*?)</script>', html
        ).group(1))
        assert len(inline['points']) == 7
        assert inline['points'][0]['values']['fdv_usd'] is None
        assert inline['points'][6]['values']['liquidity_usd'] == 6.0

    def test_the_charts_block_is_folded_with_the_three_range_chips(self):
        dossier, history = _mission_control_dossier(history_points=7)
        charts = _element(render_dossier_page(dossier, history=history), 'charts')
        assert charts.startswith('id="charts" class="panel wide"><summary>')
        assert re.findall(r'data-range="([^"]+)"', charts) == ['7', '30', 'all']
        assert re.findall(r'data-chart="([^"]+)"', charts) == list(report.HISTORY_METRICS)

    def test_a_history_value_cannot_close_the_script_element(self):
        dossier, history = _mission_control_dossier(history_points=1)
        history['points'][0]['values']['liquidity_usd'] = '</script><script>alert(1)</script>'
        html = render_dossier_page(dossier, history=history)
        assert '</script><script>alert' not in html


class TestWhatMovedTable:
    def test_counts_leaf_rows_and_puts_bookkeeping_in_the_footer(self):
        dossier, history = _mission_control_dossier()
        moved = _element(render_dossier_page(dossier, history=history), 'what-moved')
        rows = re.findall(r'<tr><td><span class="tag">([a-z_]+)</span></td><td title="[^"]*">([^<]*)</td>', moved)
        assert [label for _, label in rows] == [
            'pool 0x64c5…8c77 liquidity_usd',
            'dex_trades_h24', 'dex_volume_h24_usd', 'holder_count',
            'holder_count counterpart.top_ten_share', 'liquidity_usd', 'price_usd',
            'holder 0x0000…0001 share', 'top_ten_share',
        ]
        assert f'What moved since previous build ({len(rows)})' in moved
        assert '<div class="foot">bookkeeping moved: window</div>' in moved
        assert '<td>window</td>' not in moved

    def test_cells_are_brief_with_the_exact_value_on_hover_and_a_pp_delta_for_shares(self):
        dossier, history = _mission_control_dossier()
        moved = _element(render_dossier_page(dossier, history=history), 'what-moved')
        # Hover is the exact form (stored precision, cents), never the visible text again;
        # the field cell's hover is its glossary sentence, after the full identity.
        assert (f'<td title="{report.GLOSSARY["top_ten_share"]}">top_ten_share</td>'
                '<td class="n" title="26.8672%">26.87%</td>'
                '<td class="n" title="26.6329%">26.63%</td><td class="n down">-0.23 pp</td>') in moved
        assert (f'<td title="pool {POOL_A} liquidity_usd — {report.GLOSSARY["liquidity_usd"]}">'
                'pool 0x64c5…8c77 liquidity_usd</td>'
                '<td class="n" title="$169,900.00">$169.9k</td>'
                '<td class="n" title="$173,738.69">$173.7k</td><td class="n up">+2.3%</td>') in moved
        assert f'<td title="holder {HOLDERS[0]["address"]} share">holder 0x0000…0001 share</td>' in moved
        assert f'<td class="a" title="{POOL_A}">' in _element(render_dossier_page(dossier, history=history), 'pools')

    def test_a_flag_on_a_keyed_list_field_does_not_add_a_second_row(self):
        """Round 1: the flag check keyed on the label's first word, so a
        flagged `pools` change produced its leaf row AND a synthetic
        `pools — — flagged` row, and the title count was one too many."""
        dossier, history = _mission_control_dossier()
        dossier['sections'][0]['flagged'] = [{'field': 'pools', 'reason': 'pool_removed'}]
        moved = _element(render_dossier_page(dossier, history=history), 'what-moved')
        rows = re.findall(r'<tr><td><span class="tag">[a-z_]+</span></td><td title="[^"]*">([^<]*)</td>', moved)
        assert rows.count('pools') == 0
        assert rows[0] == 'pool 0x64c5…8c77 liquidity_usd'
        assert moved.count('<td class="flagn">pool_removed</td>') == 1
        assert f'What moved since previous build ({len(rows)})' in moved

    def test_liquidity_units_in_what_moved_are_compact_with_a_percentage_delta(self):
        """Round 2: the custody record's `pool_liquidity` printed its 23-digit
        integer and `liquidity_by_class.eoa` was formatted under `eoa`; both
        were strings, so the delta column said `changed`."""
        dossier, history = _mission_control_dossier()
        health = dossier['sections'][1]
        new_pairs = health['fields']['pairs']
        old_pairs = [dict(p) for p in new_pairs]
        old_pairs[0] = dict(new_pairs[0], counterpart_value=dict(
            CUSTODY, pool_liquidity='28277002188455995842192',
            liquidity_by_class={'eoa': '28277002188455995842192'},
        ))
        health['changes'] = {'added': {}, 'removed': {}, 'changed': [
            {'field': 'pairs', 'old': old_pairs, 'new': new_pairs},
        ]}
        moved = _element(render_dossier_page(dossier, history=history), 'what-moved')
        for label, name in (('liquidity_usd counterpart.pool_liquidity', 'pool_liquidity'),
                            ('liquidity_usd counterpart.liquidity_by_class.eoa', 'liquidity_by_class')):
            assert (f'<td title="{label} — {report.GLOSSARY[name]}">{label}</td>'
                    '<td class="n" title="28277002188455995842192 L">2.83e22 L</td>'
                    '<td class="n" title="29277002188455995842192 L">2.93e22 L</td>'
                    '<td class="n up">+3.5%</td>') in moved, label
        assert '28277002188455995842192</td>' not in moved
        assert 'changed</td>' not in moved

    def test_flagged_rows_come_first(self):
        dossier, history = _mission_control_dossier()
        dossier['sections'][2]['flagged'] = [{'field': 'top_ten_share', 'reason': 'concentration_rose'}]
        moved = _element(render_dossier_page(dossier, history=history), 'what-moved')
        first = re.search(r'<tr><td><span class="tag">[a-z_]+</span></td><td title="[^"]*">([^<]*)</td>', moved).group(1)
        assert first == 'top_ten_share'
        assert '<td class="flagn">concentration_rose</td>' in moved


class TestPanels:
    def test_pools_are_sorted_by_liquidity_with_the_sum_and_the_primary_marked(self):
        dossier, history = _mission_control_dossier()
        pools = _element(render_dossier_page(dossier, history=history), 'pools')
        assert 'Pools · sum $348.4k' in pools
        assert pools.index('0x1111…1111') < pools.index('0x64c5…8c77')
        assert '0x64c5…8c77 <span class="tag">primary</span>' in pools
        assert '<td class="n">50.14%</td>' in pools and '<td class="n">49.86%</td>' in pools

    def test_holders_show_ten_rows_with_bars_and_the_two_summary_lines(self):
        dossier, history = _mission_control_dossier()
        holders = _element(render_dossier_page(dossier, history=history), 'holders')
        assert holders.count('<div class="bar">') == 10
        assert '<td class="n" title="99,000,000 GRASS">99.00M GRASS</td>' in holders
        assert 'top-10 share 26.63% · pool-held 2.51% (1 positions) · burned 2.40%' in holders

    def test_the_custody_bar_names_eoa_for_what_it_is(self):
        dossier, history = _mission_control_dossier()
        custody = _element(render_dossier_page(dossier, history=history), 'custody')
        assert 'eoa* 100.00%' in custody
        assert 'title="eoa 100.00% · 2.93e22 L (29277002188455995842192)"' in custody
        assert '* eoa = not identified as project or locker; the collector does not check code at the address' in custody
        assert 'largest owner 0xabab…abab holds 100.00% · 1 open positions · 1 owners' in custody
        assert 'primary pool is 49.87% of provider-reported liquidity' in custody

    def test_metric_pairs_put_the_guard_text_in_its_own_column(self):
        dossier, history = _mission_control_dossier()
        pairs = _element(render_dossier_page(dossier, history=history), 'pairs')
        assert (f'<tr><td title="{report.GLOSSARY["dex_volume_h24_usd"]}">dex_volume_h24_usd</td>'
                '<td class="n" title="$418,800.00">$418.8k</td>'
                f'<td title="{report.GLOSSARY["active_addresses_24h"]}">active_addresses_24h</td>'
                '<td class="n" title="2,724">2,724</td>'
                '<td class="flat">wash_trading: volume without new counterparties</td></tr>') in pairs
        assert 'new 12 / returning 40 / top-10 26.63%' in pairs
        assert ('<td class="n" title="29277002188455995842192 L">2.93e22 L</td>') in pairs
        assert '<td class="n" title="$418,800.00">$418.8k</td>' in pairs

    def test_a_page_with_only_an_identity_section_still_has_every_panel(self):
        html = render_dossier_page(_dossier([IDENTITY]))
        for element_id in ('sources', *page.PANEL_IDS, *[t[0] for t in page.TILES]):
            assert f'id="{element_id}"' in html, element_id
        assert 'no pairs: the health section did not build' in html
        assert '>—<' in _element(html, 'tile-liquidity')


class TestBriefForms:
    def test_money_counts_and_shares(self):
        assert report.abbrev_money(173738.69) == '$173.7k'
        assert report.abbrev_money(1_234_567) == '$1.2m'
        assert report.abbrev_money(418.5) == '$418.50'
        assert report.abbrev_money(0.003095) == '$0.003095'
        assert report.abbrev_money(None) == '—'
        assert report.abbrev_count(1161) == '1,161'
        assert report.abbrev_count(29277002188455995842192) == '29,277,002,188.46T'
        assert report.abbrev_pct(0.266329) == '26.63%'
        assert report.money_exact(3048089.0) == '$3,048,089.00'

    def test_deltas(self):
        assert report.delta_text(0.268672, 0.266329, 'share') == ('-0.23 pp', 'down')
        assert report.delta_text(169900.0, 173738.69, 'money') == ('+2.3%', 'up')
        assert report.delta_text(6601, 6598, 'count') == ('-3', 'down')
        assert report.delta_text(12, 12, 'count') == ('=', 'flat')
        assert report.delta_text(None, 12, 'count') == ('first build', 'flat')
        assert report.delta_text(12, None, 'count') == ('—', 'flat')

    def test_amount_brief_scales_by_decimals_and_keeps_the_exact_form_apart(self):
        formatting = report.Formatting(_dossier([IDENTITY]), section='token_economics')
        assert formatting.amount_brief(str(24_800_000 * WAD)) == '24.80M GRASS'
        assert formatting.amount(str(24_800_000 * WAD)) == '24,800,000 GRASS'
        assert report.short_address(POOL_A) == '0x64c5…8c77'
        assert report.short_address('dex_trades_h24') == 'dex_trades_h24'

    def test_liquidity_units_are_compact_and_never_scaled(self):
        assert report.abbrev_liquidity('29277002188455995842192') == '2.93e22 L'
        assert report.abbrev_liquidity(1234) == '1,234 L'
        assert report.abbrev_liquidity(None) == '—'
        formatting = report.Formatting(_dossier([IDENTITY]), section='onchain_health')
        assert formatting.exact('primary_pool_onchain_liquidity', '29277002188455995842192') == '29277002188455995842192 L'

    def test_exact_forms_for_titles(self):
        formatting = report.Formatting(_dossier([IDENTITY]))
        assert formatting.exact('top_ten_share', 0.268672) == '26.8672%'
        assert formatting.exact('liquidity_usd', 173738.69) == '$173,738.69'
        assert formatting.exact('holders', 6598) == '6,598'
        assert formatting.exact('holders', None) == '—'
        assert formatting.for_section('token_economics').exact('balance', str(24_800_000 * WAD)) == '24,800,000 GRASS'


# -- Canonical-source links and the field glossary (operator read, 2026-09-12) --

EXPLORER_API = 'https://robinhoodchain.blockscout.com/api/v2/'
TOKEN = '0x' + 'aa' * 20
DEPLOYER = '0x' + 'bb' * 20
CREATION_TX = '0x' + 'cc' * 32


def _linked_dossier():
    dossier, history = _mission_control_dossier()
    dossier['explorer_api'] = EXPLORER_API
    dossier['dexscreener_slug'] = 'robinhood'
    dossier['sections'][0]['fields'].update({
        'token_address': TOKEN, 'pool_id': POOL_A, 'pool_address': None,
        'deployer': {'creator': DEPLOYER}, 'creation_tx': CREATION_TX,
    })
    return dossier, history


ANCHOR = 'target="_blank" rel="noopener noreferrer"'


class TestLinks:
    def test_urls_are_built_from_the_chain_record(self):
        links = report.Links(EXPLORER_API, 'robinhood')
        assert links.address(HOLDERS[0]['address']) == (
            'https://robinhoodchain.blockscout.com/address/' + HOLDERS[0]['address']
        )
        assert links.tx(CREATION_TX) == 'https://robinhoodchain.blockscout.com/tx/' + CREATION_TX
        assert links.token(TOKEN) == 'https://robinhoodchain.blockscout.com/token/' + TOKEN
        assert links.pool(POOL_A) == 'https://dexscreener.com/robinhood/' + POOL_A
        assert links.pool('0x' + 'ab' * 20) == 'https://dexscreener.com/robinhood/0x' + 'ab' * 20
        assert links.hosts() == {'robinhoodchain.blockscout.com', 'dexscreener.com'}

    def test_no_base_or_a_non_hex_value_gives_no_link(self):
        assert report.Links(None, None).address(TOKEN) is None
        assert report.Links(None, None).pool(POOL_A) is None
        assert report.Links(EXPLORER_API, 'robinhood').address('javascript:alert(1)') is None
        assert report.Links(EXPLORER_API, 'robinhood').tx(TOKEN) is None  # a tx hash is 32 bytes
        assert report.Links('http://insecure/api/v2/', 'x').address(TOKEN) is None
        assert report.Links.from_dossier(_mission_control_dossier()[0]).hosts() == {'dexscreener.com'}

    def test_the_page_links_holders_pools_custody_identity_and_what_moved(self):
        dossier, history = _linked_dossier()
        html = render_dossier_page(dossier, history=history)
        holder = 'https://robinhoodchain.blockscout.com/address/' + HOLDERS[0]['address']
        assert f'<a href="{holder}" {ANCHOR}>0x0000…0001</a>' in _element(html, 'holders')
        pools = _element(html, 'pools')
        assert f'<a href="https://dexscreener.com/robinhood/{POOL_A}" {ANCHOR}>0x64c5…8c77</a>' in pools
        assert f'<a href="https://dexscreener.com/robinhood/{POOL_B}" {ANCHOR}>0x1111…1111</a>' in pools
        # POOL_B is 32 bytes, so no v3 explorer link; a 20-byte v3 address would get one.
        assert 'explorer</a>' not in pools
        assert f'<a href="https://robinhoodchain.blockscout.com/address/0x{"ab" * 20}" {ANCHOR} title="0x{"ab" * 20}">0xabab…abab</a>' in _element(html, 'custody')
        identity = _element(html, 'identity')
        assert f'token <a href="https://robinhoodchain.blockscout.com/token/{TOKEN}"' in identity
        assert f'pool <a href="https://dexscreener.com/robinhood/{POOL_A}"' in identity
        assert f'deployer <a href="https://robinhoodchain.blockscout.com/address/{DEPLOYER}"' in identity
        assert f'creation tx <a href="https://robinhoodchain.blockscout.com/tx/{CREATION_TX}"' in identity
        moved = _element(html, 'what-moved')
        assert f'pool <a href="https://dexscreener.com/robinhood/{POOL_A}" {ANCHOR}>0x64c5…8c77</a> liquidity_usd' in moved
        assert f'holder <a href="{holder}" {ANCHOR}>0x0000…0001</a> share' in moved

    def test_a_pool_reference_links_the_same_way_everywhere(self):
        """One rule for a pool reference wherever it appears (identity line,
        What moved, Pools panel): DexScreener primary, the explorer as a
        secondary `#` when the reference is a contract address. A wallet in
        the same What-moved table links to the explorer only."""
        dossier, history = _linked_dossier()
        v3 = '0x' + 'dd' * 20
        fields = dossier['sections'][0]['fields']
        fields['pools'].append({'reference': v3, 'dex': 'uniswap', 'version': 'v3', 'liquidity_usd': 10.0})
        fields['pool_address'], fields['pool_id'] = v3, None
        dossier['sections'][0]['changes']['changed'].append({
            'field': 'pools',
            'old': [{'reference': v3, 'dex': 'uniswap', 'version': 'v3', 'liquidity_usd': 5.0}],
            'new': [{'reference': v3, 'dex': 'uniswap', 'version': 'v3', 'liquidity_usd': 10.0}],
        })
        html = render_dossier_page(dossier, history=history)
        primary = f'<a href="https://dexscreener.com/robinhood/{v3}" {ANCHOR}'
        secondary = (f'<a class="m" href="https://robinhoodchain.blockscout.com/address/{v3}" {ANCHOR} '
                     'title="pool contract on the explorer">#</a>')
        pair = f'{primary}>0xdddd…dddd</a> {secondary}'
        assert pair in _element(html, 'pools')
        assert f'pool {pair} liquidity_usd' in _element(html, 'what-moved')
        assert f'pool {primary} title="{v3}">0xdddd…dddd</a> {secondary}' in _element(html, 'identity')
        # A v4 pool id is not an address: DexScreener only, no `#`.
        assert f'<a href="https://dexscreener.com/robinhood/{POOL_B}" {ANCHOR}>0x1111…1111</a></td>' in _element(html, 'pools')
        # Nothing on the page links a pool reference to the explorer as its primary.
        assert f'<a href="https://robinhoodchain.blockscout.com/address/{v3}"' not in html
        holder = 'https://robinhoodchain.blockscout.com/address/' + HOLDERS[0]['address']
        assert f'holder <a href="{holder}" {ANCHOR}>0x0000…0001</a> share' in _element(html, 'what-moved')

    def test_a_quote_in_the_chain_record_cannot_leave_the_href_attribute(self):
        """The explorer host is registry content, not validated at runtime; a
        double quote in it must be escaped on every path that renders it,
        including the secondary `#` pool link."""
        dossier, history = _linked_dossier()
        dossier['explorer_api'] = 'https://evil.example/" onmouseover="alert(1)/api/v2/'
        v3 = '0x' + 'dd' * 20
        dossier['sections'][0]['fields']['pools'].append(
            {'reference': v3, 'dex': 'uniswap', 'version': 'v3', 'liquidity_usd': 10.0}
        )
        html = render_dossier_page(dossier, history=history)
        assert 'onmouseover="' not in html
        assert '&quot; onmouseover=&quot;alert(1)' in html
        for href in re.findall(r'<a[^>]* href="([^"]*)"', html):
            assert '"' not in href

    def test_every_external_reference_is_a_navigation_link_to_an_allowed_host(self):
        dossier, history = _linked_dossier()
        html = render_dossier_page(dossier, history=history)
        hosts = {m.group(1) for m in re.finditer(r'https?://([^/"\s]+)', html)}
        assert hosts == {'robinhoodchain.blockscout.com', 'dexscreener.com'}
        assert not re.search(r'<(?:script|link|img|iframe)[^>]*(?:src|href)="(?:https?:)?//', html)
        assert 'fetch(' not in html and 'url(' not in html
        for anchor in re.findall(r'<a href="https?://[^"]*"[^>]*>', html):
            assert ANCHOR in anchor, anchor

    def test_without_a_chain_record_the_page_shows_plain_text(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        assert 'https://' not in html
        assert '<td class="a" title="' + HOLDERS[0]['address'] + '">0x0000…0001</td>' in _element(html, 'holders')


class TestGlossary:
    def test_every_kpi_label_and_pair_name_has_a_non_empty_glossary_title(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        for tile_id, label, metric in page.TILES:
            field = report.HISTORY_METRICS[metric][1][-1]
            assert report.GLOSSARY[field]
            assert f'<span title="{report.GLOSSARY[field]}">{label}</span>' in _element(html, tile_id), label
        pairs = _element(html, 'pairs')
        for pair in dossier['sections'][1]['fields']['pairs']:
            for name in (pair['metric'], pair['counterpart']):
                cell = re.search(rf'<td title="([^"]*)">{re.escape(name)}</td>', pairs)
                assert cell and cell.group(1), name

    def test_the_required_fields_are_covered_with_a_source_suffix(self):
        required = {
            'liquidity_usd', 'dex_volume_h24_usd', 'dex_trades_h24', 'holder_count', 'top_ten_share',
            'pool_count', 'price_usd', 'fdv_usd', 'active_addresses_24h', 'pool_counterparties_24h',
            'new', 'returning', 'pool_held_share', 'burned_share', 'total_supply', 'sqrt_price_x96',
            'tick', 'primary_pool_share_of_provider_liquidity', 'primary_pool_onchain_liquidity',
            'share_by_class', 'liquidity_by_class', 'largest_owner_share', 'open_positions',
            'transfer_fetch', 'transfer_rows', 'window', 'verified', 'proxy', 'owner', 'role_holders',
            'privileged_selectors', 'exit_path',
        }
        assert required <= set(report.GLOSSARY)
        for name, sentence in report.GLOSSARY.items():
            assert 'From ' in sentence and sentence.endswith('.'), name

    def test_each_panel_has_a_folded_glossary_listing_its_fields(self):
        dossier, history = _mission_control_dossier()
        html = render_dossier_page(dossier, history=history)
        for panel, fields in page.PANEL_GLOSSARY.items():
            block = _element(html, panel)
            assert '<details class="gloss"><summary title="what these fields mean">?</summary>' in block
            for field in fields:
                assert f'<dt>{field}</dt><dd>{report.GLOSSARY[field]}</dd>' in block, (panel, field)
        strip = html[html.index('id="kpis"'):html.index('id="charts"')]
        assert '<details class="gloss strip">' in strip and '<dt>fdv_usd</dt>' in strip
        moved = _element(html, 'what-moved')
        assert '<dt>top_ten_share</dt>' in moved and '<dt>share</dt>' not in moved
        assert f'<h2 title="{report.GLOSSARY["top_ten_share"]}">Top holders' in html
        assert html[html.index('id="raw-diff"'):].count('<dt>') == len(report.GLOSSARY)
