"""Admin-chat alert for a failure the cron WRAPPER saw and the job could not report.

Every other onchain alert is sent by a run: `build.py` and `watch.py` alert on
their own outcome. The wrapper around them (`~/onchain-data/dossier_build.sh`)
sees the failures that happen before or around a run -- the interpreter
missing after a worktree was removed, a lock still held by a stalled run, a
timeout kill, a crash before the run row opened. Cron's own channel for those
is mail, and the operator's host has no mail transport, so the wrapper calls
this instead. Ruled 2026-09-12: "use telegram admin channel for error alert".

The payload goes through `send_run_alert` with `failed_unit`'s validation, so
the wrapper can put no free text -- and therefore no path, URL or credential --
into the chat: a unit like `cron/build`, an exception-class-shaped reason, and
an optional number of HOURS (the payload renders detail with an `h` suffix, as
the watcher's deadline). Exits 0 whether or not the alert was delivered: a failed
alert about a failed build is logged, not escalated into a third failure.

Usage:
  PYTHONPATH="$(pwd)" .venv/bin/python src/job/onchain/cron_alert.py \
      --job onchain.build --unit cron/build --reason BuildTimeout [--detail 1]
"""
import argparse
import asyncio
import logging
import re
from typing import Optional

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain.observability import (
    configure_job_logging,
    failed_unit,
    run_context,
    send_run_alert,
)

logger = logging.getLogger('Onchain cron alert')

# `job` becomes a log file name; the alert payload's own `_JOB` pattern, applied
# here too so `--job ../x` cannot escape the log directory.
_JOB = re.compile(r'^[a-z0-9_.]+\Z')


async def main(
    job: str, unit: str, reason: str, detail: Optional[float] = None, test_mode: bool = False
) -> int:
    if not _JOB.match(job):
        raise ValueError('job must be a dotted lowercase name')
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    configure_job_logging(job)
    with run_context(None, job):
        delivered = await send_run_alert(
            None, job, [failed_unit(unit, reason, detail)], runtime_mode=runtime_mode
        )
        logger.info('cron alert %s', 'delivered' if delivered else 'not delivered')
    print('delivered' if delivered else 'not delivered')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', default='onchain.build')
    parser.add_argument('--unit', default='cron/build')
    parser.add_argument('--reason', required=True, help='an exception-class-shaped name')
    parser.add_argument('--detail', type=float, default=None, help='hours')
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(
        job=args.job, unit=args.unit, reason=args.reason, detail=args.detail,
        test_mode=bool(args.test_mode),
    )))
