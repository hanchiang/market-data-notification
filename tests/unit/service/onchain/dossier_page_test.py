"""The dossier's readability, pinned to what the 2026-09-11 read of the page
found unreadable: base-unit amounts, fractions, epoch stamps, records cut off
at 160 characters, nested changes printed as two whole blobs, and a page that
buried the diff under forty identity fields.

Every test here reads rendered text or HTML, not the field dictionary, because
readability is a property of what the operator sees.
"""
import re

from src.router.project_monitor.dashboard import render_dossier_page
from src.service.onchain import report

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
            {'id': 18, 'block': 60_288_320, 'outcome': 'ok', 'block_timestamp': 1_789_132_894},
            {'id': 17, 'block': 60_270_535, 'outcome': 'partial', 'block_timestamp': 1_789_046_000},
        ]
        return render_dossier_page(
            _dossier([IDENTITY, quiet, changed], builds=builds),
            projects=['not-a-website', 'touch-grass'],
        )

    def test_the_diff_leads_and_the_sections_follow(self):
        html = self._page()
        assert html.index('Changes since the previous build') < html.index('<h2>Sections</h2>')
        lead = html[html.index('class="lead"'):html.index('<h2>Sections</h2>')]
        assert '~ burned: 0 GRASS -&gt; 1 GRASS' in lead
        assert 'contract_safety' not in lead

    def test_only_a_changed_section_opens_by_default(self):
        html = self._page()
        assert '<details open><summary>-- token_economics [ok]</summary>' in html
        assert '<details><summary>-- contract_safety [ok]</summary>' in html

    def test_builds_and_projects_are_linked_and_the_current_ones_are_not(self):
        html = self._page()
        assert '<a href="?build=17">17 (2026-09-10)</a>' in html
        assert '<strong>18 (2026-09-11)</strong>' in html
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
        assert '<a href="?build=17&test_mode=1">' in html
        assert '<a href="not-a-website?test_mode=1">' in html
        assert 'href="not-a-website?"' not in self._page()

    def test_a_project_key_that_is_not_a_plain_slug_is_never_an_href(self):
        html = render_dossier_page(
            _dossier([IDENTITY]), projects=['javascript:alert(1)', 'project:', 'touch-grass'],
        )
        assert 'javascript:' not in html
        assert 'href="project:' not in html
        assert '<strong>touch-grass</strong>' in html

    def test_a_failed_section_opens_and_leads_even_with_no_field_diff(self):
        failed = _section('onchain_health', {}, status='failed')
        failed['error_class'] = 'EvmTransportError'
        failed['changes'] = {}
        html = render_dossier_page(_dossier([IDENTITY, failed]))
        lead = html[html.index('class="lead"'):html.index('<h2>Sections</h2>')]
        assert '-- onchain_health [failed] (EvmTransportError)' in lead
        assert '<details open><summary>-- onchain_health [failed]' in html

    def test_the_page_references_no_host(self):
        """Local-first: the page must be readable with the network off, and a
        loopback origin that could read every other route here must load
        nothing from anywhere else."""
        html = self._page()
        assert not re.search(r'(src|href)="(https?:)?//', html)
        assert '<script' not in html and '<link' not in html

    def test_chain_data_is_escaped(self):
        page = render_dossier_page(_dossier([
            _section('identity', {'token_name': '<script>alert(1)</script>'}),
        ]))
        assert '<script>alert' not in page
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page

    def test_no_build_says_so_and_still_links_the_other_projects(self):
        page = render_dossier_page(
            {'project': 'zzz', 'display_name': 'ZZZ', 'build': None, 'sections': []},
            projects=['touch-grass', 'zzz'],
        )
        assert 'no build yet' in page
        assert '<a href="touch-grass">touch-grass</a>' in page
        assert page.endswith('</body></html>')
