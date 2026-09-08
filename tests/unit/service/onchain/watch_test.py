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
        assert verdict['unit'] == {
            'unit': 'watch/missed_run', 'error_class': 'BuildDeadlineExceeded'
        }
        assert verdict['age_hours'] == 26.0

    def test_a_ledger_with_no_build_at_all_is_its_own_state(self):
        """Distinct from "late": nothing has ever run, which on a fresh install
        means the cron line was never added."""
        verdict = watch.evaluate(None, deadline_hours=25, now=NOW)
        assert verdict['state'] == 'never_ran'
        assert verdict['unit']['error_class'] == 'NoBuildRunRecorded'

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Postgres can hand back a naive datetime depending on the column type,
        and subtracting one from an aware `now` raises -- which would turn the
        watcher into the second silence behind the first."""
        naive = {'started_at': (NOW - timedelta(hours=30)).replace(tzinfo=None)}
        assert watch.evaluate(naive, deadline_hours=25, now=NOW)['state'] == 'missed'

    def test_the_alert_unit_carries_no_project_content(self):
        """A11: each message carries the run id and exception class and no
        project content. The unit is built from constants, so there is no path
        by which a token name or a URL reaches it."""
        unit = watch.evaluate(_run(48), deadline_hours=25, now=NOW)['unit']
        assert set(unit) == {'unit', 'error_class'}
        assert '0x' not in unit['unit'] and '://' not in unit['unit']
