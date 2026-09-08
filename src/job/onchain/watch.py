"""The missed-run watcher: the only thing that notices a job that did not run (A11).

Every other alert in this system is sent BY a run. A build whose cron line was
commented out, whose machine was asleep, or whose python failed before the run
row opened sends nothing at all, and the silence is indistinguishable from a
clean night. This job is the thing that reads that silence.

It compares the latest `onchain.build` run's `started_at` with
`ONCHAIN_BUILD_DEADLINE_HOURS` (default 25) and alerts once when the build is
older than that. Scheduled two hours after the build, so a build cron that did
not fire is caught on the first missed night at an age of 26 hours; a deadline
of 24 would fire on an ordinary run that started a few minutes late.

It writes its own run row, which is what makes a silent watcher visible: the
ledger route shows `onchain.watch` runs, so a watcher that itself stopped running
is discoverable rather than being a second silence behind the first.

Usage:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/onchain/watch.py [--test_mode 1]
"""
import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import builder
from src.service.onchain.config import get_build_deadline_hours, get_onchain_database_url
from src.service.onchain.observability import (
    configure_job_logging,
    failed_unit,
    run_context,
    send_run_alert,
)
from src.service.onchain.repository import OnchainRepository

logger = logging.getLogger('Onchain watch')

JOB_NAME = builder.JOB_WATCH
WATCHED_JOB = builder.JOB_BUILD

# The unit name the alert carries. `<job>/missed_run` is the `project/section`
# shape the payload validator enforces -- the watched job's short name stands
# in for the project because the thing that failed is not a project, it is the
# schedule. Named after WATCHED_JOB (test-stage F/E-1) rather than hardcoded as
# `watch/...`, because a payload naming only the watcher told the operator
# nothing about which job's cron line was silent -- A11 requires the message
# to name the missing run. `_UNIT`'s project segment forbids `.`, so the
# `onchain.` prefix is dropped rather than the dotted job name reaching the
# validator and failing sanitisation.
_WATCHED_JOB_SHORT = WATCHED_JOB.rsplit('.', 1)[-1]
MISSED_RUN_UNIT = f'{_WATCHED_JOB_SHORT}/missed_run'
NEVER_RAN_UNIT = f'{_WATCHED_JOB_SHORT}/never_ran'


def evaluate(
    latest_run: Optional[Dict[str, Any]], *, deadline_hours: float, now: datetime
) -> Dict[str, Any]:
    """Is the last build late? Pure, so the deadline arithmetic is testable
    without a store and without a clock."""
    if latest_run is None:
        # The deadline applies even here: there is no age to report (nothing
        # has ever run), but the rule that was exceeded is the same one, and
        # the operator asked for the deadline on the alert unconditionally
        # (post-gate ruling, E-1).
        return {
            'state': 'never_ran',
            'age_hours': None,
            'unit': failed_unit(
                NEVER_RAN_UNIT, 'NoBuildRunRecorded', detail=deadline_hours
            ),
        }
    started = latest_run['started_at']
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    age_hours = (now - started).total_seconds() / 3600
    if age_hours <= deadline_hours:
        return {'state': 'ok', 'age_hours': round(age_hours, 2), 'unit': None}
    return {
        'state': 'missed',
        'age_hours': round(age_hours, 2),
        'unit': failed_unit(
            MISSED_RUN_UNIT, 'BuildDeadlineExceeded', detail=deadline_hours
        ),
    }


async def main(test_mode: bool = False) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    configure_job_logging(JOB_NAME)
    deadline = get_build_deadline_hours()

    repository: Optional[OnchainRepository] = None
    run_id: Optional[int] = None
    outcome = 'failed'
    failed_units: List[Dict[str, str]] = []
    notes: List[str] = []
    try:
        repository = OnchainRepository(get_onchain_database_url(runtime_mode))
        run_id = repository.start_run(JOB_NAME)
        with run_context(run_id, JOB_NAME):
            latest = repository.get_latest_run(WATCHED_JOB)
            verdict = evaluate(
                latest, deadline_hours=deadline, now=datetime.now(timezone.utc)
            )
            if verdict['unit'] is None:
                outcome = 'ok'
                notes.append(
                    f'latest {WATCHED_JOB} run is {verdict["age_hours"]}h old '
                    f'(deadline {deadline}h)'
                )
            else:
                outcome = 'failed'
                failed_units = [verdict['unit']]
                notes.append(
                    f'{WATCHED_JOB} is late: age {verdict["age_hours"]}h '
                    f'exceeds the {deadline}h deadline'
                    if verdict['state'] == 'missed'
                    else f'no {WATCHED_JOB} run has ever been recorded'
                )
                logger.error('missed run: %s', notes[-1])
    except Exception as exc:
        outcome = 'failed'
        notes.append(f'watch failed: {type(exc).__name__}')
        failed_units = [failed_unit('watch/self', type(exc).__name__)]
        logger.error('watch failed: %s', type(exc).__name__, exc_info=True)
    finally:
        if repository is not None and run_id is not None:
            repository.finish_run(
                run_id, outcome=outcome, failed_units=failed_units, notes='; '.join(notes)
            )
        if repository is not None:
            repository.close()

    if outcome != 'ok':
        # One message naming the missing run (A11). `runtime_mode` threaded
        # through: without it a --test_mode watcher posts to the live chat.
        with run_context(run_id, JOB_NAME):
            await send_run_alert(run_id, JOB_NAME, failed_units, runtime_mode=runtime_mode)
    print('; '.join(notes))
    # Zero either way: a missed build is the watcher WORKING. A non-zero exit
    # would make cron mail about it as well, which is a second notification
    # channel nobody asked for and one that fires on the alert, not the fault.
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(test_mode=bool(args.test_mode))))
