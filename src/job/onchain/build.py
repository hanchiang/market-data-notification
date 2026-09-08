"""Nightly entrypoint: one run, one build per project, one alert (P4, P13).

Not a `JobWrapper` subclass, for the monitor's reasons: that base class starts
Redis, which this job does not use, and posts the exception TEXT to the admin
chat, which can carry the keyed archive URL. The CLI contract is the same
(`--force_run`, `--test_mode`) so a cron line reads like the monitor's.

Usage:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/onchain/build.py \\
      --force_run=1 [--project touch-grass] [--test_mode=1]

The alert is sent ONCE per run and never once per failed unit (A11), after the
run row is written, with `runtime_mode` passed through so a `--test_mode 1` run
alerts the dev channel instead of the live crypto admin chat.
"""
import argparse
import asyncio
import logging
from typing import Any, Dict, List, Optional

from market_data_library.core.crypto.blockscout import BlockscoutService
from market_data_library.core.crypto.dexscreener import DexscreenerService

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import builder, chain as chain_module
from src.service.onchain.collectors.base import BuildContext
from src.service.onchain.config import (
    get_chain_constants,
    get_onchain_database_url,
    get_registry_path,
)
from src.service.onchain.explorer import ExplorerUnit
from src.service.onchain.observability import (
    configure_job_logging,
    run_context,
    send_run_alert,
)
from src.service.onchain.registry import load_registry, upsert_registry
from src.service.onchain.repository import LockNotAcquiredError, OnchainRepository

logger = logging.getLogger('Onchain build')

JOB_NAME = builder.JOB_BUILD


async def run_build(
    repository: OnchainRepository,
    run_id: int,
    *,
    runtime_mode: RuntimeMode,
    project_key: Optional[str] = None,
) -> builder.RunResult:
    """Everything between the run row opening and closing."""
    registry = load_registry(get_registry_path())
    project_ids = upsert_registry(repository, registry)
    repository.commit()

    projects = builder.select_projects(registry, project_key)
    result = builder.RunResult(run_id=run_id, outcome='ok')
    spend = chain_module.spend_counters()

    state_role = chain_module.state_role(runtime_mode)
    log_role = chain_module.log_role()

    dexscreener = DexscreenerService()
    explorers: Dict[int, BlockscoutService] = {}
    try:
        async with state_role.client() as state_client, log_role.client() as log_client:
            constants = get_chain_constants(
                next(iter(registry.chains.values())).chain_id
            )
            pinned = await chain_module.pin_block_and_window(state_client, constants)
            chain_module.add_spend(
                spend, state_role.endpoint.kind, pinned.header_reads, pinned.header_reads * 16
            )
            result.notes.append(
                f'pinned block {pinned.block}; 24h window from {pinned.window_start_block}'
            )

            for project in projects:
                chain_entry = registry.chain_for(project)
                explorer_service = explorers.get(chain_entry.chain_id)
                if explorer_service is None:
                    explorer_service = BlockscoutService(chain_entry.explorer_api)
                    explorers[chain_entry.chain_id] = explorer_service
                chain_entity = repository.get_entity_by_key(chain_entry.entity_key)
                context = BuildContext(
                    repository=repository,
                    registry=registry,
                    chain=chain_entry,
                    project=project,
                    chain_entity_id=int(chain_entity['id']),
                    project_entity_id=project_ids[project.key],
                    pinned=pinned,
                    state_client=state_client,
                    log_client=log_client,
                    dexscreener=dexscreener,
                    explorer=ExplorerUnit(explorer_service),
                    spend=spend,
                )
                build = await builder.build_project(
                    context, run_id, project_entity_id=project_ids[project.key]
                )
                links = context.identity.get('published_links') or []
                if links:
                    builder.store_candidate_sources(
                        repository, project_ids[project.key], links
                    )
                repository.commit()
                chain_module.add_spend(spend, 'blockscout', context.explorer.calls)
                result.builds.append(build)
                result.failed_units.extend(build.failed_units)
                result.notes.append(
                    f'{project.key}: {build.outcome} '
                    f'({len([s for s in build.sections if s["status"] == "ok"])}/'
                    f'{len(build.sections)} sections ok)'
                )
    finally:
        await dexscreener.cleanup()
        for service in explorers.values():
            await service.cleanup()

    result.spend = spend
    result.outcome = builder.run_outcome(result.builds)
    return result


async def main(
    force_run: bool = False,
    test_mode: bool = False,
    project: Optional[str] = None,
) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    configure_job_logging(JOB_NAME)

    repository: Optional[OnchainRepository] = None
    run_id: Optional[int] = None
    outcome = 'failed'
    failed_units: List[Dict[str, Any]] = []
    notes: List[str] = []
    spend: Dict[str, Any] = {}
    try:
        repository = OnchainRepository(get_onchain_database_url(runtime_mode))
        run_id = repository.start_run(JOB_NAME)
        with run_context(run_id, JOB_NAME):
            try:
                with repository.advisory_lock():
                    result = await run_build(
                        repository, run_id, runtime_mode=runtime_mode, project_key=project
                    )
                    outcome = result.outcome
                    failed_units = result.failed_units
                    notes = result.notes
                    spend = result.spend
            except LockNotAcquiredError:
                outcome = 'skipped'
                notes.append('another onchain run holds the advisory lock')
    except Exception as exc:
        # The CLASS, never the message: the message can carry the keyed URL.
        outcome = 'failed'
        notes.append(f'run failed: {type(exc).__name__}')
        logger.error('onchain build failed: %s', type(exc).__name__, exc_info=True)
        if repository is not None:
            repository.rollback()
    finally:
        if repository is not None and run_id is not None:
            repository.finish_run(
                run_id,
                outcome=outcome,
                failed_units=failed_units,
                spend=spend,
                notes='; '.join(notes),
            )
        if repository is not None:
            repository.close()

    if outcome not in ('ok', 'skipped'):
        # ONE message for the whole run, listing every failed unit -- not one per
        # unit (A11). `runtime_mode` is threaded through so a --test_mode run
        # cannot reach the live crypto admin chat.
        with run_context(run_id, JOB_NAME):
            await send_run_alert(run_id, JOB_NAME, failed_units, runtime_mode=runtime_mode)
    print('; '.join(notes))
    return 0 if outcome in ('ok', 'partial', 'skipped') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force_run', type=int, default=0)
    parser.add_argument('--test_mode', type=int, default=0)
    parser.add_argument('--project', type=str, default=None)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main(
                force_run=bool(args.force_run),
                test_mode=bool(args.test_mode),
                project=args.project,
            )
        )
    )
