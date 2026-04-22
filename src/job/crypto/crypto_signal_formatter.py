from src.service.crypto_signal.models import (
    CryptoSignalCandidate,
    CryptoSignalDigestView,
)
from src.util.my_telegram import escape_markdown


def build_crypto_signal_message(view: CryptoSignalDigestView) -> str:
    run_timestamp = view.latest_snapshot.run.run_timestamp_utc.strftime(
        '%Y-%m-%d %H:%M UTC'
    )
    lines = [
        '*Crypto trend signal*',
        f'Run: {escape_markdown(run_timestamp)}',
        f'Window: {escape_markdown(view.window_label)}',
        (
            'Market regime: '
            f"*{escape_markdown(view.market_regime_label)}*"
            f" \\({escape_markdown(view.market_regime_reason)}\\)"
        ),
        '',
    ]
    lines.extend(
        _format_candidate_section(
            title='Strong momentum',
            candidates=view.strong_candidates,
            empty_text='No strong candidates met the current threshold.',
        )
    )
    lines.append('')
    lines.extend(
        _format_candidate_section(
            title='Weak momentum',
            candidates=view.weak_candidates,
            empty_text='No weak candidates met the current threshold.',
        )
    )
    lines.append('')
    lines.extend(
        _format_candidate_section(
            title='Watchlist',
            candidates=view.watchlist_candidates,
            empty_text='No watchlist candidates were available for this window.',
        )
    )
    return '\n'.join(lines).strip()


def _format_candidate_section(
    title: str,
    candidates: list[CryptoSignalCandidate],
    empty_text: str,
) -> list[str]:
    lines = [f'*{escape_markdown(title)}*']
    if len(candidates) == 0:
        lines.append(escape_markdown(empty_text))
        return lines

    for candidate in candidates:
        lines.append(_format_candidate(candidate))
    return lines


def _format_candidate(candidate: CryptoSignalCandidate) -> str:
    metric_parts = [
        f"score {_format_signed_int(candidate.score)}",
        f"24h {_format_signed_float(candidate.latest_price_change_24h)}",
        f"obs {candidate.observation_count}",
    ]
    if candidate.latest_price_usd is not None:
        metric_parts.append(f"price {escape_markdown(_format_price(candidate.latest_price_usd))}")
    if candidate.latest_volume_change_pct_24h is not None:
        metric_parts.append(
            'vol chg '
            f"{_format_signed_float(candidate.latest_volume_change_pct_24h)}"
        )

    line = (
        f"• *{escape_markdown(candidate.name)}* "
        f"{escape_markdown(candidate.symbol)}"
        f": {', '.join(metric_parts)}"
    )
    if len(candidate.reason_tags) > 0:
        line += (
            ', reasons '
            f"{escape_markdown(', '.join(candidate.reason_tags))}"
        )
    return line


def _format_price(value: float) -> str:
    if value >= 1000:
        return f'{value:,.2f}'
    if value >= 1:
        return f'{value:.2f}'
    if value >= 0.01:
        return f'{value:.4f}'
    if value >= 0.0001:
        return f'{value:.6f}'
    return f'{value:.8f}'


def _format_signed_float(value: float | None) -> str:
    if value is None:
        return 'n/a'
    return escape_markdown(f'{value:+.2f}%')


def _format_signed_int(value: int) -> str:
    return escape_markdown(f'{value:+d}')
