"""The dossier as one HTML page: mission control over the stored rows (UX brief,
slice A; design D6).

Server-rendered from the SAME `load_dossier` payload the CLI prints, so the page
and `report --project X` cannot disagree about a figure. This module adds shape
and the brief number forms; every exact form stays in `report.py`, which is the
one place a number is formatted. Top to bottom: header with the build picker and
the source badges, the KPI strip with deltas and sparklines, the trend charts
(folded), What moved as a table of leaf changes, pools, holders, pool liquidity
by owner, the metric pairs (A4), and the raw text report folded at the end.

Every panel and every tile carries a decision tag (parent requirement P15,
criterion A14): the operator decision the element informs and its edge class,
so nothing on the page is a number without a reason to read it.

Local-first: inline CSS and JS; the only `<script src>` is the vendored Chart.js
the monitor already serves, on this origin. Opening the page sends nothing
anywhere else.
"""
import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.service.onchain import diff as diff_module
from src.service.onchain import report
from src.service.onchain.report import (
    AMOUNT_FIELDS,
    BOOKKEEPING_FIELDS,
    DASH,
    LIST_KEYS,
    Formatting,
    abbrev_count,
    abbrev_money,
    abbrev_pct,
    brief,
    delta_kind,
    delta_text,
    money_exact,
    short_address,
)

CHART_SCRIPT = '/project-monitor/static/chart.umd.js'
# A sparkline over fewer points reads as a trend that is not there.
SPARKLINE_MIN_POINTS = 7
CHART_RANGES = ('7', '30', 'all')

SOURCE_CLASSES = ('chain_rpc', 'chain_explorer', 'dex_provider', 'web', 'x', 'telegram')
# Until slice B writes their rows, these two classes are configuration: the RPC
# endpoints come from the chain config and the DEX provider appears only as a
# `source` string inside the health pairs. Their badges say so.
CONFIGURED_CLASSES = frozenset({'chain_rpc', 'dex_provider'})

# Element id -> the decision it informs and its edge class (P15 / A14). The
# text after the colon is the hover title; the text before the ` · ` is the
# visible label. A test asserts every rendered panel and tile has an entry.
DECISION_TAGS: Dict[str, str] = {
    'sources': 'admit / drop a source · slow: which capture arms this project has',
    'tile-liquidity': 'exit · slow: liquidity that can be pulled',
    'tile-volume': 'ignore / watch · slow: volume is washable; read with its pair',
    'tile-trades': 'watch · slow: trades are churnable; read with its pair',
    'tile-holders': 'add / reduce · slow: distribution widening or narrowing',
    'tile-top-ten': 'reduce · slow: concentration',
    'tile-pools': 'watch · slow: fragmentation around a launch',
    'tile-price': 'enter / add · slow: valuation context for sizing',
    'tile-fdv': 'enter / add · slow: valuation context for sizing',
    'what-moved': 'watch · slow: what changed since the previous build',
    'pools': 'watch · slow: where the liquidity sits',
    'holders': 'reduce / exit · slow: a privileged holder growing',
    'custody': 'exit · slow: who can pull the liquidity',
    'pairs': 'ignore · slow: a metric that fails its pair is noise',
    'raw-diff': 'context: supports What moved',
    # Not in the brief's mapping; added because the charts block is a panel
    # and A14 puts a tag on every one. Flagged in the slice A report.
    'charts': 'watch · slow: the trend behind each tile',
}

# (tile id, label, metric name in `report.HISTORY_METRICS`)
TILES: Tuple[Tuple[str, str, str], ...] = (
    ('tile-liquidity', 'Primary pool liquidity', 'liquidity_usd'),
    ('tile-volume', 'DEX volume 24h', 'volume_h24_usd'),
    ('tile-trades', 'DEX trades 24h', 'trades_h24'),
    ('tile-holders', 'Holders', 'holders'),
    ('tile-top-ten', 'Top-10 share', 'top_ten_share'),
    ('tile-pools', 'Pools', 'pool_count'),
    ('tile-price', 'Price', 'price_usd'),
    ('tile-fdv', 'FDV', 'fdv_usd'),
)

PANEL_IDS = ('what-moved', 'pools', 'holders', 'custody', 'pairs', 'raw-diff')
# The What-moved label for one item of a keyed list; a pair row is named by
# its metric alone, since that is the name the state block prints it under.
LIST_NOUNS = {'pools': 'pool', 'top_holders': 'holder', 'pairs': ''}

CUSTODY_COLOURS = {'project': '#7aa2ff', 'locker': '#3ecf8e', 'eoa': '#5a6475', 'contract': '#e8c04a'}

CSS = (
    ':root{--bg:#0f1217;--panel:#171b22;--line:#262c36;--fg:#e6e9ee;--mute:#8a93a3;'
    '--up:#3ecf8e;--down:#ff6b6b;--acc:#7aa2ff}'
    '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);'
    'font:14px/1.45 -apple-system,Segoe UI,Inter,Roboto,sans-serif;padding:20px 24px}'
    'a{color:var(--acc);text-decoration:none}h1{font-size:20px;margin:0}'
    'h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);'
    'margin:0 0 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}'
    'nav{display:flex;gap:14px;margin-bottom:18px;border-bottom:1px solid var(--line);'
    'padding-bottom:10px;flex-wrap:wrap}nav a{padding:4px 8px;border-radius:6px}'
    'nav strong{padding:4px 8px;border-radius:6px;background:var(--panel)}'
    '.head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}'
    '.head .m{color:var(--mute)}select{background:var(--panel);color:var(--fg);'
    'border:1px solid var(--line);border-radius:6px;padding:3px 6px;font:inherit}'
    '.badges{display:flex;gap:10px;margin:6px 0 16px;color:var(--mute);font-size:12px;'
    'flex-wrap:wrap;align-items:center}.b{display:inline-flex;align-items:center;gap:5px}'
    '.dot{width:9px;height:9px;border-radius:50%;background:var(--line);display:inline-block}'
    '.dot.ok{background:var(--up)}.dot.cand{background:#e8c04a}'
    '.dot.cfg{background:var(--up);outline:2px dashed #2b6;outline-offset:1px}'
    '.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;'
    'margin-bottom:12px}.kpi{background:var(--panel);border:1px solid var(--line);'
    'border-radius:10px;padding:12px 14px;min-width:0}'
    '.kpi .l{color:var(--mute);font-size:12px;display:flex;justify-content:space-between;'
    'gap:6px;flex-wrap:wrap}.kpi .v{font-size:22px;font-weight:600;margin:2px 0;'
    'font-variant-numeric:tabular-nums}.kpi .d{font-size:12px}'
    '.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--mute)}'
    '.spark{height:26px;margin-top:8px;border-radius:4px;color:var(--mute);font-size:10px;'
    'display:flex;align-items:center;justify-content:center;'
    'background:repeating-linear-gradient(90deg,var(--line) 0 2px,transparent 2px 8px)}'
    '.spark canvas{width:100%;height:26px}'
    '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;'
    'margin-bottom:12px}.panel{background:var(--panel);border:1px solid var(--line);'
    'border-radius:10px;padding:12px 14px;min-width:0;overflow-x:auto}.wide{grid-column:1/-1}'
    'table{width:100%;border-collapse:collapse;font-size:13px}th{color:var(--mute);'
    'font-weight:500;text-align:left;padding:4px 6px;border-bottom:1px solid var(--line)}'
    'td{padding:5px 6px;border-bottom:1px solid #1d222b;vertical-align:top}'
    'td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}'
    '.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;min-width:60px}'
    '.bar i{display:block;height:100%;background:var(--acc)}'
    '.stack{display:flex;height:16px;border-radius:5px;overflow:hidden;margin:6px 0;'
    'background:var(--line)}.stack i{display:block;height:100%}'
    '.legend{color:var(--mute);font-size:12px}'
    '.tag{font-size:11px;padding:1px 6px;border-radius:4px;background:var(--line);'
    'color:var(--mute);text-transform:none;letter-spacing:0;font-weight:400;cursor:help}'
    '.tag.p{background:#243a2e;color:var(--up)}.tag.bad{background:#3a2424;color:var(--down)}'
    '.foot{color:var(--mute);font-size:12px;margin-top:8px}'
    'details summary{cursor:pointer;color:var(--mute)}'
    'pre{font-size:12px;color:var(--mute);white-space:pre-wrap;margin:8px 0 0}'
    '.flagn{color:#e8c04a}.chips{display:flex;gap:6px;margin:8px 0}'
    '.chip{background:var(--line);color:var(--mute);border:0;border-radius:6px;'
    'padding:3px 10px;font:inherit;font-size:12px;cursor:pointer}'
    '.chip.on{background:var(--acc);color:#0f1217}'
    '.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}'
    '.chart{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}'
    '.chart .l{color:var(--mute);font-size:12px;margin-bottom:4px}'
    '.chart canvas{width:100%;height:160px}'
)

# Reads the inlined history once; sparklines draw when the page loads, the
# charts when their `<details>` first opens (a hidden canvas has no size).
# Without Chart.js (a test client, a blocked file) the page is still complete:
# every number is already in the HTML.
JS = """(function(){
var el=document.getElementById('history');if(!el)return;
var H=JSON.parse(el.textContent),P=H.points||[];
function series(m,pts){return pts.map(function(p){return p.values[m]})}
function labels(pts){return pts.map(function(p){return p.block_timestamp?new Date(p.block_timestamp*1000).toISOString().slice(0,10):'build '+p.build_id})}
if(!window.Chart)return;
var line=function(m,pts,axes){return {type:'line',data:{labels:labels(pts),datasets:[{label:m,data:series(m,pts),borderColor:'#7aa2ff',borderWidth:1.5,pointRadius:axes?2:0,spanGaps:false}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},tooltip:{enabled:!!axes}},scales:{x:{display:!!axes,ticks:{color:'#8a93a3'},grid:{color:'#262c36'}},y:{display:!!axes,ticks:{color:'#8a93a3'},grid:{color:'#262c36'}}}}}};
document.querySelectorAll('canvas[data-spark]').forEach(function(c){new Chart(c,line(c.dataset.spark,P,false))});
var charts={};
function draw(range){var pts=range==='all'?P:P.slice(-parseInt(range,10));
document.querySelectorAll('canvas[data-chart]').forEach(function(c){var m=c.dataset.chart;if(charts[m])charts[m].destroy();charts[m]=new Chart(c,line(m,pts,true))})}
document.querySelectorAll('.chip').forEach(function(b){b.addEventListener('click',function(){document.querySelectorAll('.chip').forEach(function(x){x.classList.toggle('on',x===b)});draw(b.dataset.range)})});
var d=document.getElementById('charts');if(d)d.addEventListener('toggle',function(){if(d.open&&!Object.keys(charts).length)draw('7')});
})();"""


def render_dossier_page(
    dossier: Dict[str, Any],
    *,
    projects: Optional[List[str]] = None,
    test_mode: bool = False,
    history: Optional[Dict[str, Any]] = None,
) -> str:
    """The dossier as one self-contained page; `history` is `load_history`'s
    payload for the same project (None renders every tile as `0/7 builds`)."""
    title = f"{dossier.get('display_name') or dossier.get('project')} dossier"
    # Every link carries the store it was read from: a bare `?build=17` would
    # drop `test_mode` and switch a test-store reader to production unnoticed.
    query = '&test_mode=1' if test_mode else ''
    project_query = '?test_mode=1' if test_mode else ''
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body>',
    ]
    if projects:
        parts.append('<nav>' + ' '.join(
            f'<a href="{key}{project_query}">{key}</a>' if key != dossier.get('project')
            else f'<strong>{key}</strong>'
            for key in projects if is_link_safe(key)
        ) + '</nav>')
    build = dossier.get('build')
    if build is None:
        parts.append(f'<h1>{escape(title)}</h1><pre>no build yet</pre></body></html>')
        return ''.join(parts)

    page = _Page(dossier, history or {'points': []}, query)
    parts.extend([
        page.header(),
        page.source_badges(),
        page.kpi_strip(),
        page.charts(),
        '<div class="grid">',
        page.what_moved(),
        page.pools(),
        page.holders(),
        page.custody(),
        page.pairs(),
        page.raw_diff(),
        '</div>',
        page.history_json(),
        f'<script src="{CHART_SCRIPT}"></script><script>{JS}</script>',
        '</body></html>',
    ])
    return ''.join(parts)


class _Page:
    """One render: the dossier's sections by name, the brief and exact forms
    bound to the token, and the history for the sparklines."""

    def __init__(self, dossier: Dict[str, Any], history: Dict[str, Any], query: str) -> None:
        self.dossier = dossier
        self.build = dossier['build']
        self.query = query
        self.sections: List[Dict[str, Any]] = list(dossier.get('sections') or [])
        self.by_name = {str(s.get('name')): s for s in self.sections}
        self.formatting = Formatting(dossier)
        self.history = history
        self.current = report.metric_values(self.sections)
        self.previous = self._previous_values()
        self.identity = self.fields('identity')
        self.economics = self.fields('token_economics')
        self.health = self.fields('onchain_health')
        self.pair_rows = {
            str(p.get('metric')): p for p in self.health.get('pairs') or [] if isinstance(p, dict)
        }
        self.symbol = (
            self.identity.get('token_symbol') or self.identity.get('onchain_symbol') or '?'
        )

    def fields(self, name: str) -> Dict[str, Any]:
        section = self.by_name.get(name)
        return dict(section.get('fields') or {}) if section else {}

    # -- header ---------------------------------------------------------

    def header(self) -> str:
        build = self.build
        formatting = self.formatting
        when = build.get('block_timestamp')
        stamp = formatting.scalar('block_timestamp', when) if when else ''
        outcome = str(build.get('outcome'))
        outcome_class = 'p' if outcome == 'ok' else 'bad'
        parts = [
            '<header class="head" id="header">',
            f'<h1>{escape(self.dossier.get("display_name") or self.dossier.get("project"))}</h1>',
            f'<span class="m">{escape(self.symbol)} · {escape(self.dossier.get("chain") or "chain " + DASH)}'
            f' · {escape(self.dossier.get("archetype") or DASH)}</span>',
            self._build_picker(),
            f'<span class="m">{escape(stamp)}</span>' if stamp else '',
            self._previous_build(),
            f'<span class="tag {outcome_class}" title="build outcome">{escape(outcome)}</span>',
            '</header>',
        ]
        failed = build.get('failed_units') or []
        if failed:
            parts.append('<div class="foot flagn">failed units: ' + escape(', '.join(
                f"{unit.get('unit')} ({unit.get('error_class')})" for unit in failed
            )) + '</div>')
        return ''.join(parts)

    def _build_picker(self) -> str:
        """Build ids with the run in parentheses: the ledger numbers runs and
        the store numbers builds, and the two diverge. A `<select>` rather than
        a link row so ten builds do not become a line of numbers."""
        builds = self.dossier.get('builds') or []
        current = int(self.build['id'])
        if not any(int(b['id']) == current for b in builds):
            builds = [dict(self.build), *builds]
        options = []
        for b in builds:
            label = _build_label(b, self.formatting)
            selected = ' selected' if int(b['id']) == current else ''
            options.append(
                f'<option value="?build={int(b["id"])}{self.query}"{selected}>{escape(label)}</option>'
            )
        return (
            '<select id="build-picker" aria-label="build" '
            'onchange="location.search=this.value">' + ''.join(options) + '</select>'
        )

    def _previous_build(self) -> str:
        """The build this one is diffed against, named rather than implied.
        Only `ok` and `partial` sections are baselines, so the previous build
        is the nearest earlier one that produced any."""
        current = int(self.build['id'])
        previous = next(
            (b for b in sorted(self.dossier.get('builds') or [], key=lambda b: -int(b['id']))
             if int(b['id']) < current and b.get('outcome') in ('ok', 'partial')),
            None,
        )
        if previous is None:
            return '<span class="m" id="prev-build">prev build —</span>'
        return (
            f'<a class="m" id="prev-build" href="?build={int(previous["id"])}{self.query}">'
            f'prev build {int(previous["id"])}</a>'
        )

    def source_badges(self) -> str:
        """One badge per source class: filled with the admitted count, amber
        when only candidates exist, dashed when the class is configuration
        rather than a stored row (rpc and dex, until slice B)."""
        rows = [r for r in self.dossier.get('sources') or [] if isinstance(r, dict)]
        out = []
        for source_class in SOURCE_CLASSES:
            admitted = sum(1 for r in rows if r.get('class') == source_class and r.get('admission') == 'admitted')
            candidates = sum(1 for r in rows if r.get('class') == source_class and r.get('admission') == 'candidate')
            if admitted:
                dot, note = 'ok', f' {admitted}'
            elif source_class in CONFIGURED_CLASSES:
                dot, note = 'cfg', ' (config)'
            elif candidates:
                dot, note = 'cand', f' cand {candidates}'
            else:
                dot, note = '', ' none'
            out.append(
                f'<span class="b" title="{escape(source_class)}: {admitted} admitted, '
                f'{candidates} candidate"><span class="dot {dot}"></span>{escape(source_class)}{note}</span>'
            )
        return (
            f'<div class="badges" id="sources">{_tag("sources")}sources:' + ''.join(out)
            + '<span class="m">● admitted · dashed = configuration</span></div>'
        )

    # -- KPI strip ------------------------------------------------------

    def kpi_strip(self) -> str:
        counts = self.sparkline_counts()
        tiles = []
        for tile_id, label, metric in TILES:
            value = self.current.get(metric)
            previous = self.previous.get(metric)
            text, direction = delta_text(previous, value, delta_kind(metric))
            exact_previous = _exact(metric, previous) if previous is not None else DASH
            if counts[metric] >= SPARKLINE_MIN_POINTS:
                spark = f'<canvas data-spark="{metric}" height="26"></canvas>'
            else:
                spark = f'{counts[metric]}/{SPARKLINE_MIN_POINTS} builds'
            extra = ''
            if metric == 'holders':
                split = (self.pair_rows.get('holder_count') or {}).get('counterpart_value')
                if isinstance(split, dict) and 'new' in split:
                    extra = (f'<div class="d flat">new {abbrev_count(split.get("new"))} · '
                             f'returning {abbrev_count(split.get("returning"))}</div>')
            tiles.append(
                f'<div class="kpi" id="{tile_id}"><div class="l"><span>{escape(label)}</span>'
                f'{_tag(tile_id)}</div>'
                f'<div class="v" title="{escape(_exact(metric, value))}">{escape(brief(metric, value))}</div>'
                f'<div class="d {direction}" title="previous build: {escape(exact_previous)}">'
                f'{escape(text)} <span class="flat">vs prev build</span></div>{extra}'
                f'<div class="spark">{spark}</div></div>'
            )
        return '<div class="kpis" id="kpis">' + ''.join(tiles) + '</div>'

    def sparkline_counts(self) -> Dict[str, int]:
        points = self.history.get('points') or []
        return {
            metric: sum(1 for p in points if (p.get('values') or {}).get(metric) is not None)
            for metric in report.HISTORY_METRICS
        }

    def _previous_values(self) -> Dict[str, Optional[float]]:
        """Each KPI's value in the previous build, read from the section diff:
        the `old` side of a changed field, None when the field was added or the
        section has no diff (first build), the current value when unchanged."""
        previous: Dict[str, Optional[float]] = {}
        for metric, (section_name, path) in report.HISTORY_METRICS.items():
            section = self.by_name.get(section_name)
            changes = (section or {}).get('changes') or {}
            current = self.current.get(metric)
            if section is None or not changes or current is None:
                previous[metric] = None
                continue
            field = path[0]
            entry = next(
                (e for e in changes.get('changed') or [] if e.get('field') == field), None
            )
            if entry is None:
                previous[metric] = None if field in (changes.get('added') or {}) else current
                continue
            old = entry.get('old')
            if field == 'pairs':
                old = next(
                    (p.get('value') for p in old or [] if isinstance(p, dict) and p.get('metric') == path[1]),
                    None,
                )
            previous[metric] = old if isinstance(old, (int, float)) and not isinstance(old, bool) else None
        return previous

    # -- charts ---------------------------------------------------------

    def charts(self) -> str:
        chips = ''.join(
            f'<button class="chip{" on" if r == "7" else ""}" data-range="{r}" type="button">{r}</button>'
            for r in CHART_RANGES
        )
        canvases = ''.join(
            f'<div class="chart"><div class="l">{escape(label)}</div>'
            f'<canvas data-chart="{metric}" height="160"></canvas></div>'
            for _, label, metric in TILES
        )
        points = len(self.history.get('points') or [])
        return (
            '<details id="charts" class="panel wide"><summary>Trends over the last '
            f'{points} ok builds · one chart per tile · range in builds {_tag("charts")}</summary>'
            f'<div class="chips">{chips}</div><div class="charts">{canvases}</div></details>'
        )

    def history_json(self) -> str:
        # `<` escaped so a value can never close the script element.
        payload = json.dumps(self.history, sort_keys=True, default=str).replace('<', '\\u003c')
        return f'<script id="history" type="application/json">{payload}</script>'

    # -- What moved -----------------------------------------------------

    def what_moved(self) -> str:
        rows, bookkeeping = self._moved_rows()
        body = ''.join(
            f'<tr><td><span class="tag">{escape(r.section)}</span></td><td>{escape(r.label)}</td>'
            f'<td class="n" title="{escape(r.before_exact)}">{escape(r.before)}</td>'
            f'<td class="n" title="{escape(r.after_exact)}">{escape(r.after)}</td>'
            f'<td class="n {r.direction}">{escape(r.delta)}</td>'
            f'<td class="flagn">{escape(r.flag or "")}</td></tr>'
            for r in rows
        )
        if not rows:
            body = '<tr><td colspan="6" class="foot">no change in any section</td></tr>'
        foot = (
            f'<div class="foot">bookkeeping moved: {escape(", ".join(sorted(bookkeeping)))}</div>'
            if bookkeeping else ''
        )
        return (
            f'<section class="panel wide" id="what-moved"><h2>What moved since previous build '
            f'({len(rows)}){_tag("what-moved")}</h2><table><tr><th>section</th><th>field</th>'
            '<th class="n">before</th><th class="n">after</th><th class="n">Δ</th><th>flag</th></tr>'
            f'{body}</table>{foot}</section>'
        )

    def _moved_rows(self) -> Tuple[List['_Row'], set]:
        rows: List[_Row] = []
        bookkeeping: set = set()
        for section in self.sections:
            section_rows, moved = _section_rows(section, self.formatting)
            rows.extend(section_rows)
            bookkeeping |= moved
        order = {n: i for i, n in enumerate(report.SECTION_ORDER)}
        rows.sort(key=lambda r: (r.flag is None, order.get(r.section, len(order)), r.label))
        return rows, bookkeeping

    # -- Pools ----------------------------------------------------------

    def pools(self) -> str:
        pools = [p for p in (self.identity.get('pools') or []) if isinstance(p, dict)]
        pools.sort(key=lambda p: -(p.get('liquidity_usd') or 0))
        total = sum(p.get('liquidity_usd') or 0 for p in pools)
        primary = str(self.identity.get('pool_ref') or '').lower()
        rows = ''.join(
            f'<tr><td title="{escape(p.get("reference"))}">{escape(short_address(p.get("reference")))}'
            + (' <span class="tag">primary</span>' if primary and str(p.get('reference')).lower() == primary else '')
            + f'</td><td>{escape(p.get("dex"))} {escape(p.get("version") or "")}</td>'
            f'<td class="n" title="{escape(_exact("liquidity_usd", p.get("liquidity_usd")))}">'
            f'{escape(abbrev_money(p.get("liquidity_usd")))}</td>'
            f'<td class="n">{escape(abbrev_pct((p.get("liquidity_usd") or 0) / total) if total else DASH)}</td></tr>'
            for p in pools
        ) or '<tr><td colspan="4" class="foot">no pools read</td></tr>'
        return (
            f'<section class="panel" id="pools"><h2>Pools · sum {escape(abbrev_money(total) if pools else DASH)}'
            f'{_tag("pools")}</h2><table><tr><th>pool</th><th>dex</th><th class="n">liquidity</th>'
            f'<th class="n">share</th></tr>{rows}</table></section>'
        )

    # -- Holders --------------------------------------------------------

    def holders(self) -> str:
        holders = self.economics.get('top_holders')
        holders = [h for h in holders if isinstance(h, dict)] if isinstance(holders, list) else []
        holders = holders[:10]
        formatting = self.formatting.for_section('token_economics')
        top = max((float(h.get('share') or 0) for h in holders), default=0) or 1e-9
        rows = ''.join(
            f'<tr><td>{i + 1}</td><td title="{escape(h.get("address"))}">{escape(short_address(h.get("address")))}</td>'
            f'<td class="n" title="{escape(formatting.amount(h.get("balance")))}">{escape(formatting.amount_brief(h.get("balance")))}</td>'
            f'<td class="n" title="{escape(_exact("share", h.get("share")))}">{escape(abbrev_pct(h.get("share")))}</td>'
            f'<td><div class="bar"><i style="width:{min(100.0, float(h.get("share") or 0) * 100 / top):.0f}%"></i></div></td></tr>'
            for i, h in enumerate(holders)
        ) or '<tr><td colspan="5" class="foot">no holder table</td></tr>'
        pool_held = self.economics.get('pool_held_share')
        if isinstance(pool_held, dict) and not diff_module.is_failed(pool_held):
            held = f'{abbrev_pct(pool_held.get("share"))}'
            if pool_held.get('positions') is not None:
                held += f' ({pool_held.get("positions")} positions)'
        elif diff_module.is_failed(pool_held):
            held = f'FAILED ({pool_held.get("error_class")})'
        else:
            held = DASH
        foot = (
            f'top-10 share {escape(_brief_or_failed("top_ten_share", self.economics.get("top_ten_share")))}'
            f' · pool-held {escape(held)}'
            f' · burned {escape(_brief_or_failed("burned_share", self.economics.get("burned_share")))}'
        )
        return (
            f'<section class="panel" id="holders"><h2>Top holders{_tag("holders")}</h2>'
            '<table><tr><th>#</th><th>address</th><th class="n">balance</th><th class="n">share</th><th></th></tr>'
            f'{rows}</table><div class="foot">{foot}</div></section>'
        )

    # -- Pool liquidity by owner -----------------------------------------

    def custody(self) -> str:
        pair = self.pair_rows.get('liquidity_usd') or {}
        record = pair.get('counterpart_value')
        record = record if isinstance(record, dict) else {}
        by_class = record.get('share_by_class') or {}
        amounts = record.get('liquidity_by_class') or {}
        stack = ''.join(
            f'<i style="width:{float(v or 0) * 100:.1f}%;background:{CUSTODY_COLOURS.get(k, "#888")}" '
            f'title="{escape(k)} {escape(abbrev_pct(v))} · {escape(amounts.get(k, DASH))} liquidity units"></i>'
            for k, v in by_class.items()
        )
        legend = ' · '.join(
            f'{escape(k)}{"*" if k == "eoa" else ""} {escape(abbrev_pct(v))}' for k, v in by_class.items()
        ) or 'no positions read'
        primary_share = (self.pair_rows.get('primary_pool_share_of_provider_liquidity') or {}).get('value')
        foot = (
            f'largest owner {escape(short_address(record.get("largest_owner") or DASH))} holds '
            f'{escape(abbrev_pct(record.get("largest_owner_share")))} · '
            f'{escape(record.get("open_positions", DASH))} open positions · '
            f'{escape(record.get("owner_count", DASH))} owners · pool type {escape(record.get("pool_type", DASH))}<br>'
            f'primary pool is {escape(abbrev_pct(primary_share))} of provider-reported liquidity<br>'
            '* eoa = not identified as project or locker; the collector does not check code at the address'
        )
        return (
            f'<section class="panel" id="custody"><h2>Primary pool liquidity by owner{_tag("custody")}</h2>'
            f'<div class="stack">{stack}</div><div class="legend">{legend}</div>'
            f'<div class="foot">{foot}</div></section>'
        )

    # -- Metric pairs ---------------------------------------------------

    def pairs(self) -> str:
        pairs = [p for p in (self.health.get('pairs') or []) if isinstance(p, dict)]
        formatting = self.formatting.for_section('onchain_health')
        rows = ''.join(
            f'<tr><td>{escape(p.get("metric"))}</td>'
            f'<td class="n" title="{escape(formatting.inline(str(p.get("metric")), p.get("value")))}">'
            f'{escape(brief(str(p.get("metric")), p.get("value")))}</td>'
            f'<td>{escape(p.get("counterpart"))}</td>'
            f'<td class="n" title="{escape(formatting.inline(str(p.get("counterpart")), p.get("counterpart_value")))}">'
            f'{escape(_counterpart(p))}</td>'
            f'<td class="flat">{escape(p.get("guards_against"))}'
            + (f' <span class="tag">{escape(p.get("pool_type"))}</span>' if p.get('pool_type') else '')
            + '</td></tr>'
            for p in pairs
        ) or '<tr><td colspan="5" class="foot">no pairs: the health section did not build</td></tr>'
        return (
            f'<section class="panel wide" id="pairs"><h2>Metric pairs (A4): each number beside the '
            f'one that would expose it{_tag("pairs")}</h2><table><tr><th>metric</th><th class="n">value</th>'
            f'<th>counterpart</th><th class="n">value</th><th>guards against</th></tr>{rows}</table></section>'
        )

    # -- Raw diff -------------------------------------------------------

    def raw_diff(self) -> str:
        lines = list(report.header_lines(self.dossier, self.formatting))
        for block in report.render_blocks(self.dossier):
            lines.append('')
            lines.extend(block.lines())
        return (
            f'<details class="panel wide" id="raw-diff"><summary>Raw diff and every field '
            f'(the text report, what `report --project` prints) {_tag("raw-diff")}</summary>'
            f'<pre>{escape(chr(10).join(lines))}</pre></details>'
        )


class _Row:
    __slots__ = ('section', 'label', 'before', 'before_exact', 'after', 'after_exact', 'delta', 'direction', 'flag')

    def __init__(self, section, label, before, before_exact, after, after_exact, delta, direction, flag):
        self.section, self.label = section, label
        self.before, self.before_exact = before, before_exact
        self.after, self.after_exact = after, after_exact
        self.delta, self.direction, self.flag = delta, direction, flag


def _section_rows(section: Dict[str, Any], formatting: Formatting) -> Tuple[List['_Row'], set]:
    """One section's What-moved rows and the bookkeeping fields it moved.

    A section that did not build is itself a row (what changed is that it
    could not be read); a first successful build is one row, not every field
    twice; bookkeeping goes to the footer, never the table.
    """
    name = str(section.get('name'))
    formatting = formatting.for_section(name)
    flagged = {f.get('field'): str(f.get('reason')) for f in section.get('flagged') or []}
    rows: List[_Row] = []
    status = str(section.get('status'))
    if status != 'ok':
        shown = f'{status} ({section.get("error_class")})' if section.get('error_class') else status
        rows.append(_Row(name, 'section', 'built', 'built', status, status, shown, 'down',
                         'section not built'))
    changes = section.get('changes') or {}
    skip = BOOKKEEPING_FIELDS.get(name, frozenset())
    added = changes.get('added') or {}
    if _is_first_build(section, changes):
        rows.append(_Row(name, f'first successful build of this section: {len(added)} fields',
                         DASH, DASH, DASH, DASH, 'baseline', 'flat', None))
        return rows, set()
    bookkeeping: set = set()
    for entry in changes.get('changed') or []:
        field = str(entry.get('field'))
        if field in skip:
            bookkeeping.add(field)
            continue
        for label, fmt_name, old, new in _leaves(field, field, entry.get('old'), entry.get('new')):
            rows.append(_leaf_row(name, label, fmt_name, old, new, formatting, flagged.get(field)))
    for field, value in added.items():
        if field in skip:
            bookkeeping.add(field)
        else:
            rows.append(_edge_row(name, field, value, formatting, flagged.get(field), added=True))
    for field, value in (changes.get('removed') or {}).items():
        if field in skip:
            bookkeeping.add(field)
        else:
            rows.append(_edge_row(name, field, value, formatting, flagged.get(field), added=False))
    # A flag on a field whose diff is empty still names the field.
    named = {r.label.split(' ')[0].split('.')[0] for r in rows}
    rows.extend(
        _Row(name, str(field), DASH, DASH, DASH, DASH, 'flagged', 'flat', reason)
        for field, reason in flagged.items() if field is not None and field not in named
    )
    return rows, bookkeeping


def _edge_row(section, field, value, formatting: Formatting, flag, *, added: bool) -> '_Row':
    shown, exact = _brief_for(field, value, formatting), formatting.inline(field, value)
    if added:
        return _Row(section, field, DASH, DASH, shown, exact, 'added', 'up', flag)
    return _Row(section, field, shown, exact, DASH, DASH, 'removed', 'down', flag)

def _is_first_build(section: Dict[str, Any], changes: Dict[str, Any]) -> bool:
    added = changes.get('added') or {}
    if not added or changes.get('changed') or changes.get('removed'):
        return False
    fields = section.get('fields') or {}
    present = {k for k in fields if not diff_module.is_failed(fields[k])}
    return bool(present) and set(added) >= present


def _leaves(label: str, name: str, old: Any, new: Any) -> Iterator[Tuple[str, str, Any, Any]]:
    """Every leaf that differs between two values of one field: (row label,
    the name to format under, old, new). Dicts recurse by key; record lists
    with a known identity key match by that key, so a pool's liquidity or a
    holder's share is one row and not two whole blobs."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                yield from _leaves(f'{label}.{key}', key, old.get(key), new.get(key))
        return
    key = LIST_KEYS.get(name)
    if key and _is_record_list(old) and _is_record_list(new):
        before = {str(item.get(key)).lower(): item for item in old}
        after = {str(item.get(key)).lower(): item for item in new}
        for ident in sorted(set(before) | set(after)):
            item = after.get(ident) or before[ident]
            # `pool 0x64c5…8c77 liquidity_usd`, `holder 0x8366…0951 share`,
            # and a pair row under its own metric name, as the state block does.
            noun = LIST_NOUNS.get(name, name)
            shown = f'{noun} {short_address(item.get(key))}'.strip()
            if ident not in before:
                yield (shown, name, None, 'added')
            elif ident not in after:
                yield (shown, name, 'removed', None)
            else:
                for inner in sorted(set(before[ident]) | set(after[ident])):
                    if inner == key or before[ident].get(inner) == after[ident].get(inner):
                        continue
                    yield from _leaves(
                        shown if name == 'pairs' and inner == 'value'
                        else f'{shown} {"counterpart" if inner == "counterpart_value" else inner}',
                        report._value_label(after[ident], key, inner),
                        before[ident].get(inner), after[ident].get(inner),
                    )
        return
    yield (label, name, old, new)


def _leaf_row(section, label, name, old, new, formatting: Formatting, flag) -> _Row:
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (old, new))
    if numeric:
        delta, direction = delta_text(old, new, delta_kind(name))
    elif old is None and new == 'added' or old == 'removed' and new is None:
        delta, direction = (new or old), ('up' if new else 'down')
        old = new = None
    else:
        delta, direction = 'changed', 'flat'
    return _Row(
        section, label,
        _brief_for(name, old, formatting), formatting.inline(name, old) if old is not None else DASH,
        _brief_for(name, new, formatting), formatting.inline(name, new) if new is not None else DASH,
        delta, direction, flag,
    )


def _brief_for(name: str, value: Any, formatting: Formatting) -> str:
    if value is None:
        return DASH
    if name in AMOUNT_FIELDS and formatting.section in Formatting.SCALED_SECTIONS:
        return formatting.amount_brief(value)
    if isinstance(value, (dict, list)):
        return 'record'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return brief(name, value)
    return formatting.scalar(name, value)


def _brief_or_failed(name: str, value: Any) -> str:
    if diff_module.is_failed(value):
        return f'FAILED ({value.get("error_class")})'
    return brief(name, value) if isinstance(value, (int, float)) else DASH


def _counterpart(pair: Dict[str, Any]) -> str:
    value = pair.get('counterpart_value')
    if isinstance(value, dict):
        if 'new' in value:
            return (f'new {abbrev_count(value.get("new"))} / returning {abbrev_count(value.get("returning"))}'
                    f' / top-10 {abbrev_pct(value.get("top_ten_share"))}')
        if 'owner_count' in value or 'largest_owner_share' in value:
            return (f'{value.get("owner_count", DASH)} owners, largest '
                    f'{abbrev_pct(value.get("largest_owner_share"))}')
        return 'record'
    if isinstance(value, str) and value.lstrip('-').isdigit():
        return f'{abbrev_count(int(value))} liquidity units'
    return brief(str(pair.get('counterpart')), value)


def _exact(metric: str, value: Any) -> str:
    """The exact form for a title attribute, chosen the way `brief` chooses."""
    if value is None:
        return DASH
    if 'share' in metric:
        return f'{float(value) * 100:.4f}%'
    if metric.endswith('_usd'):
        return money_exact(value)
    return f'{value:,}'


def _build_label(build: Dict[str, Any], formatting: Formatting) -> str:
    label = f'build {int(build["id"])}'
    if build.get('run_id') is not None:
        label += f' (run {int(build["run_id"])})'
    when = build.get('block_timestamp')
    if when:
        label += f" · {formatting.scalar('block_timestamp', when)}"
    if build.get('outcome') and build.get('outcome') != 'ok':
        label += f" · {build['outcome']}"
    return label


def _tag(element_id: str) -> str:
    text = DECISION_TAGS[element_id]
    label = text.split(' · ')[0] if ' · ' in text else text.split(':')[0]
    return f'<span class="tag" title="{escape(text)}">{escape(label)}</span>'


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def is_link_safe(key: Any) -> bool:
    """A registry key is `[A-Za-z0-9._-]+`; anything else never becomes an
    href, where escaping would not stop a `javascript:` scheme."""
    return bool(re.fullmatch(r'[A-Za-z0-9._-]+', str(key)))


def escape(text: Any) -> str:
    """Escaped because the page renders CHAIN DATA.

    A token's `name()` is arbitrary bytes chosen by whoever deployed it, and it
    reaches this page through the dossier. An unescaped `<script>` in a token
    name would run on a loopback origin that can read every other route here.
    """
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )
