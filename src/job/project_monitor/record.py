"""Hourly entrypoint: one sample, one manifest snapshot, one run row.

Deliberately **not** a `JobWrapper` subclass. That base class starts Redis
before the job body -- which this job does not use -- and posts the exception
*text* to the admin chat, which could carry the keyed URL. This is a thin
entrypoint with the same CLI contract (`--force_run`, `--test_mode`) so
`run_notification_job.sh` works unchanged, reusing `init_telegram_bots` and the
send function and nothing else.

Usage:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/record.py \
      --force_run=1 --test_mode=1
"""
import argparse
import asyncio
import logging
from typing import Optional

from market_data_library.core.onchain.evm import (
    EvmClient,
    EvmClientError,
    alchemy_budget,
    public_rpc_budget,
)

from src.notification_destination.telegram_notification import (
    init_telegram_bots,
    send_message_to_admin,
)
from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor import recorder
from src.service.project_monitor.config import (
    NETNET,
    PUBLIC_STATE_BLOCK_WINDOW,
    get_archive_endpoint,
    get_manifest_url,
    get_project_monitor_database_url,
    get_public_endpoint,
)
from src.service.project_monitor.manifest import (
    ManifestExtractionError,
    diff_registries,
    fetch_manifest,
    is_extractor_verified,
)
from src.service.project_monitor.repository import (
    LockNotAcquiredError,
    ProjectMonitorRepository,
)
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown

logger = logging.getLogger('Project monitor record')

JOB_NAME = 'project_monitor.record'


async def _read_state_on(endpoint, budget, repository: ProjectMonitorRepository):
    """Pin the head on this endpoint and read the whole plan at it.

    The block is pinned on the SAME endpoint that serves the reads: pinning on
    one and reading on another can name a block the second has not seen.
    """
    async with EvmClient(endpoint, budget) as client:
        head, _ = await client.block_number()
        deploy_blocks = repository.get_deploy_blocks(NETNET.name)
        sample = await recorder.read_state(
            client, NETNET, head, deploy_blocks=deploy_blocks
        )
        return sample, head


async def run_sample(
    repository: ProjectMonitorRepository,
    run_id: int,
    *,
    runtime_mode: RuntimeMode,
    progress: Optional[dict] = None,
) -> dict:
    """Pin a block, read state, fetch the window, commit once.

    `progress` is written as the run advances so the caller can name the
    endpoint in use when a failure happened. Passing the value back in the
    return dict would not do: on the failing path there is no return.
    """
    progress = progress if progress is not None else {}
    archive = get_archive_endpoint()
    # `--test_mode` points state reads at the public endpoint, so a manual run
    # spends nothing on the metered account.
    use_archive = archive is not None and not runtime_mode.is_test_mode
    state_endpoint = archive if use_archive else get_public_endpoint(supports_batch=False)
    state_budget = alchemy_budget() if use_archive else public_rpc_budget()
    progress['endpoint_kind'] = state_endpoint.kind

    log_endpoint = get_public_endpoint(supports_batch=True)
    project = NETNET

    notes = []
    sample = None
    if use_archive:
        try:
            sample, pinned_block = await _read_state_on(
                state_endpoint, state_budget, repository
            )
        except (EvmClientError, recorder.CoreReadFailedError) as exc:
            # Endpoint failover (design: Endpoint roles). The whole state plan is
            # re-issued on the fallback from the first batch -- never mixed within
            # one sample, because a sample whose reads came from two endpoints
            # cannot be reasoned about if they disagree.
            #
            # `CoreReadFailedError` belongs in this tuple and is the case the
            # failover mostly exists for: a mid-plan core batch exhausting its
            # retries is wrapped by the recorder, so catching only
            # `EvmClientError` would fall back for a failure in the head pin and
            # for nothing after it. Widening to bare `Exception` is the wrong fix
            # -- it would route a programming error into a fallback retry and
            # then report it as an endpoint problem.
            notes.append(f'archive state read failed ({type(exc).__name__}); '
                         'falling back to the public endpoint')
            use_archive = False

    if sample is None:
        fallback = get_public_endpoint(supports_batch=False)
        progress['endpoint_kind'] = fallback.kind
        sample, pinned_block = await _read_state_on(
            fallback, public_rpc_budget(), repository
        )
        # The public endpoint serves state only for ~6,000 blocks (measured:
        # head-6,000 served, head-6,250 "metadata is not found"), so a fallback
        # sample is only ever a fresh one. Recorded on the run row because it
        # changes what the sample is evidence of.
        notes.append(
            f'state read on the public endpoint (serves ~{PUBLIC_STATE_BLOCK_WINDOW} '
            'blocks of history; historical backfill needs the archive endpoint)'
        )

    net_decimals = _decimal_places(sample, 'NET.decimals', 9)
    usdg_decimals = _decimal_places(sample, 'USDG.decimals', 6)

    from_block, origin_note = recorder.resolve_window_start(
        repository, project.name, pinned_block
    )
    if origin_note:
        notes.append(origin_note)

    async with EvmClient(log_endpoint, public_rpc_budget()) as log_client:
        window = await recorder.read_log_window(
            log_client,
            project,
            project.name,
            from_block,
            pinned_block,
            net_decimals=net_decimals,
            usdg_decimals=usdg_decimals,
        )

    sample_id = recorder.commit_sample(
        repository,
        run_id=run_id,
        project_name=project.name,
        sample=sample,
        kind=recorder.KIND_LIVE,
        log_window=window,
    )
    if sample.failed_peripheral:
        notes.append('failed peripheral reads: ' + ', '.join(sample.failed_peripheral))
    return {
        'sample_id': sample_id,
        'block': pinned_block,
        'epoch': sample.epoch_number,
        'notes': notes,
    }


def _decimal_places(sample: recorder.SampleResult, name: str, fallback: int) -> int:
    for reading in sample.readings:
        if reading['name'] == name and reading['state'] == 'ok':
            return int(reading['value_int'])
    return fallback


async def run_manifest_snapshot(repository: ProjectMonitorRepository) -> str:
    """R9. Independent of the chain sample in both directions: its failure is
    recorded on the run row and does not abort the sample, and a failed sample
    does not skip the snapshot."""
    project_name = NETNET.name
    previous = repository.get_latest_manifest_snapshot(project_name)
    snapshot = await fetch_manifest(get_manifest_url())
    verified = is_extractor_verified(previous, snapshot)
    snapshot_id = repository.insert_manifest_snapshot(
        project=project_name,
        bundle_filename=snapshot.bundle_filename,
        build_hash=snapshot.build_hash,
        bundle_sha256=snapshot.bundle_sha256,
        registry=snapshot.registry,
        extractor_verified=verified,
    )
    diffs = diff_registries(previous['registry_json'] if previous else None, snapshot.registry)
    repository.insert_manifest_diffs(snapshot_id, diffs.as_rows())
    repository.commit()
    return (
        f'manifest {snapshot.bundle_filename} build={snapshot.build_hash} '
        f'names={len(snapshot.registry)} verified={verified} '
        f'diffs={len(diffs.as_rows())}'
    )


async def main(force_run: bool = False, test_mode: bool = False) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    database_url = get_project_monitor_database_url(runtime_mode)

    # Bots are initialised BEFORE the guarded body: `send_message_to_admin`
    # indexes a global map that only `init_telegram_bots` populates, so without
    # this the alert path itself raises KeyError and the failure is swallowed --
    # exactly the outcome the operator ruled out when they asked for alerts.
    try:
        init_telegram_bots()
    except Exception:
        logger.warning('telegram bots unavailable; the failure alert path is off')

    repository: Optional[ProjectMonitorRepository] = None
    run_id: Optional[int] = None
    outcome = 'failed'
    error_class: Optional[str] = None
    notes: list = []
    progress: dict = {}
    try:
        repository = ProjectMonitorRepository(database_url)
        run_id = repository.start_run(JOB_NAME)
        with repository.advisory_lock():
            try:
                result = await run_sample(
                    repository, run_id, runtime_mode=runtime_mode, progress=progress
                )
                notes.extend(result['notes'])
                notes.append(f'sample at block {result["block"]} epoch {result["epoch"]}')
                outcome = 'ok'
            except Exception as exc:
                repository.rollback()
                error_class = type(exc).__name__
                notes.append(f'sample failed: {error_class}')
                logger.error('sample failed: %s', error_class)

            try:
                notes.append(await run_manifest_snapshot(repository))
            except (ManifestExtractionError, Exception) as exc:
                repository.rollback()
                notes.append(f'manifest failed: {type(exc).__name__}')
                if outcome == 'ok':
                    outcome = 'partial'
    except LockNotAcquiredError:
        outcome = 'skipped'
        notes.append('another run holds the advisory lock')
        error_class = 'LockNotAcquiredError'
    except Exception as exc:
        error_class = type(exc).__name__
        notes.append(f'run failed: {error_class}')
    finally:
        if repository is not None and run_id is not None:
            # The run row is written BEFORE the alert is attempted, so a failing
            # alert can never cost us the record of what the run did.
            repository.finish_run(
                run_id, outcome=outcome, error_class=error_class, notes='; '.join(notes)
            )
        if repository is not None:
            repository.close()

    if outcome not in ('ok', 'skipped'):
        await _alert(run_id, error_class or 'unknown', progress.get('endpoint_kind'))
    print('; '.join(notes))
    return 0 if outcome in ('ok', 'partial', 'skipped') else 1


async def _alert(
    run_id: Optional[int], error_class: str, endpoint_kind: Optional[str] = None
) -> None:
    """One admin-chat message: run id, endpoint kind, exception CLASS.

    Never the exception's message text, which could carry a URL. The endpoint
    kind is the 'alchemy'/'public' label, never the URL -- it is here because it
    is what tells the operator whether to look at the metered account or the
    public node before opening anything.

    The class name goes through the MarkdownV2 escaper because a name carrying
    `.` or `_` is rejected by Telegram otherwise. The whole send is guarded so an
    alert failure never masks the run outcome.
    """
    try:
        message = escape_markdown(
            f'project_monitor run {run_id} failed on '
            f'{endpoint_kind or "unknown"}: {error_class}'
        )
        await send_message_to_admin(message, MarketDataType.CRYPTO)
    except Exception:
        logger.warning('failure alert could not be sent')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force_run', type=int, default=0)
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(main(force_run=bool(args.force_run), test_mode=bool(args.test_mode)))
    )
