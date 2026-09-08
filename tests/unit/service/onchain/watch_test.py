"""The missed-run watcher's deadline arithmetic (A11, first half).

`evaluate` is pure so the deadline can be tested without a store and without
waiting a day. The store-backed half -- that a ledger whose latest build is 26
hours old produces exactly one alert -- lives in `build_job_test.py`.
"""
from datetime import datetime, timedelta, timezone

from src.job.onchain import watch

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)


def _run(hours_ago: float):
    return {'started_at': NOW - timedelta(hours=hours_ago)}


class TestDeadline:
    def test_a_fresh_build_is_not_late(self):
        verdict = watch.evaluate(_run(3), deadline_hours=25, now=NOW)
        assert verdict['state'] == 'ok'
        assert verdict['unit'] is None

    def test_a_build_a_few_minutes_late_is_still_not_late(self):
        """Why the deadline is 25 and not 24: a nightly cron that starts at
        03:05 instead of 03:00 must not alert."""
        verdict = watch.evaluate(_run(24.2), deadline_hours=25, now=NOW)
        assert verdict['state'] == 'ok'

    def test_a_build_missed_for_one_night_is_caught(self):
        """The watcher runs two hours after the build, so a cron that did not
        fire is caught at an age of 26 hours."""
        verdict = watch.evaluate(_run(26), deadline_hours=25, now=NOW)
        assert verdict['state'] == 'missed'
        # Names the watched job ('build'), not the watcher (E-1, test round 1):
        # the watcher's own run id is already on the "run N" line the alert
        # sends, and it is the ONLY place a message can say which job's cron
        # line went silent. `detail` carries the deadline hours (E-1,
        # post-gate operator ruling) so the alert can render "(25h)".
        assert verdict['unit'] == {
            'unit': 'build/missed_run', 'error_class': 'BuildDeadlineExceeded',
            'detail': 25,
        }
        assert verdict['age_hours'] == 26.0

    def test_a_ledger_with_no_build_at_all_is_its_own_state(self):
        """Distinct from "late": nothing has ever run, which on a fresh install
        means the cron line was never added."""
        verdict = watch.evaluate(None, deadline_hours=25, now=NOW)
        assert verdict['state'] == 'never_ran'
        assert verdict['unit']['error_class'] == 'NoBuildRunRecorded'
        # The deadline applies even with no age to report (post-gate ruling,
        # E-1): there is no build to be late, but the rule that would have
        # been exceeded is still the operator's configured deadline.
        assert verdict['unit']['detail'] == 25

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Postgres can hand back a naive datetime depending on the column type,
        and subtracting one from an aware `now` raises -- which would turn the
        watcher into the second silence behind the first."""
        naive = {'started_at': (NOW - timedelta(hours=30)).replace(tzinfo=None)}
        assert watch.evaluate(naive, deadline_hours=25, now=NOW)['state'] == 'missed'

    def test_the_alert_unit_carries_no_project_content(self):
        """A11: each message carries the run id and exception class and no
        project content. The unit is built from constants, so there is no path
        by which a token name or a URL reaches it. `detail` (E-1, post-gate
        ruling) is a number -- the deadline hours -- which cannot carry
        project content either."""
        unit = watch.evaluate(_run(48), deadline_hours=25, now=NOW)['unit']
        assert set(unit) == {'unit', 'error_class', 'detail'}
        assert '0x' not in unit['unit'] and '://' not in unit['unit']


class TestRenderedAlertCarriesTheDeadline:
    """Post-gate ruling, E-1: the missed-run alert should name the deadline
    hours, not only the exceeded-class. The obvious route -- widening
    `_ERROR_CLASS` to admit "BuildDeadlineExceeded (25h)" -- was ruled out:
    that pattern is what stops a collector's `str(exc)` (a request URL, a
    credential for an archive read) from reaching the admin chat. Instead
    `failed_unit` carries the deadline as a separate, type-checked numeric
    `detail`, and `format_alert` renders it. This drives the real path
    end to end: `evaluate`'s own unit straight into `format_alert`, hitting
    the exact target string the operator specified.
    """

    def test_the_missed_run_alert_names_the_deadline(self):
        from src.service.onchain.observability import format_alert

        verdict = watch.evaluate(_run(26), deadline_hours=25, now=NOW)
        message = format_alert(7, watch.JOB_NAME, [verdict['unit']])
        assert message == (
            'onchain onchain.watch run 7\n'
            '- build/missed_run: BuildDeadlineExceeded (25h)'
        )

    def test_the_never_ran_alert_also_names_the_deadline(self):
        from src.service.onchain.observability import format_alert

        verdict = watch.evaluate(None, deadline_hours=25, now=NOW)
        message = format_alert(7, watch.JOB_NAME, [verdict['unit']])
        assert message == (
            'onchain onchain.watch run 7\n'
            '- build/never_ran: NoBuildRunRecorded (25h)'
        )

    def test_a_fractional_deadline_renders_without_a_trailing_zero(self):
        """`get_build_deadline_hours()` always returns a float
        (`ONCHAIN_BUILD_DEADLINE_HOURS` parsed with `float()`), so the whole-
        number case (25.0 -> "25h", not "25.0h") is the common one, not an
        edge case -- and a genuinely fractional deadline still renders."""
        from src.service.onchain.observability import format_alert

        verdict = watch.evaluate(_run(26), deadline_hours=24.5, now=NOW)
        message = format_alert(7, watch.JOB_NAME, [verdict['unit']])
        assert message.endswith('(24.5h)')
