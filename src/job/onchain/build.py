"""Nightly entrypoint: one run, one build per project, one alert (P4, P13).

Not a `JobWrapper` subclass, for the monitor's reasons: that base class starts
Redis, which this job does not use, and posts the exception TEXT to the admin
chat, which can carry the keyed archive URL. The CLI contract is the same
(`--test_mode`) so a cron line reads like the monitor's. It deliberately has NO
`--force_run`: the monitor's flag bypasses that job's own schedule check, and
this job has none -- cron decides when it runs, and every invocation builds. A
flag that is parsed and then ignored is worse than no flag, because a cron line
carrying it looks like it is doing something.

Usage:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/onchain/build.py \\
      [--project touch-grass] [--test_mode=1]

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
from src.service.onchain.collectors.base import EXPLORER_KIND, BuildContext
from src.service.onchain.config import (
    get_chain_constants,
    get_onchain_database_url,
    get_registry_path,
)
from src.service.onchain.explorer import ExplorerUnit, build_explorer_service
from src.service.onchain.observability import (
    configure_job_logging,
    failed_unit,
    run_context,
    send_run_alert,
)
from src.service.onchain.registry import load_registry, upsert_registry
from src.service.onchain.repository import LockNotAcquiredError, OnchainRepository
from src.service.onchain.spend import SpendLedger

logger = logging.getLogger('Onchain build')

JOB_NAME = builder.JOB_BUILD


class MultiChainRunError(RuntimeError):
    """The selected projects span more than one chain (see `run_build`)."""


async def run_build(
    repository: OnchainRepository,
    run_id: int,
    *,
    runtime_mode: RuntimeMode,
    project_key: Optional[str] = None,
    ledger: Optional[SpendLedger] = None,
    result: Optional[builder.RunResult] = None,
) -> builder.RunResult:
    """Everything between the run row opening and closing.

    `ledger` is the caller's so a run that raises still leaves its spend where
    `main` can write it to the run row; a ledger created here would die with
    the exception.
    """
    registry = load_registry(get_registry_path())
    project_ids = upsert_registry(repository, registry)
    repository.commit()

    projects = builder.select_projects(registry, project_key)
    # Caller-owned, like the ledger: a run that raises mid-way has still
    # written notes (which endpoints, month-to-date spend) and per-section
    # failed units for the projects that did build, and the row must keep them.
    result = result if result is not None else builder.RunResult(run_id=run_id, outcome='ok')

    # One ledger for the run: both roles' budgets are wrapped by it, so every
    # RPC attempt is counted and the monthly ceiling is checked before each
    # send (P13, cost gates). Month-to-date comes from earlier runs' rows.
    ledger = ledger if ledger is not None else SpendLedger()
    spent_this_month = repository.units_spent_this_month('alchemy')
    state_role = chain_module.state_role(
        runtime_mode, ledger, alchemy_spent_this_month=spent_this_month
    )
    log_role = chain_module.log_role(
        runtime_mode, ledger, alchemy_spent_this_month=spent_this_month
    )
    result.notes.append(
        f'state via {state_role.endpoint.kind}, logs via {log_role.endpoint.kind}; '
        f'{spent_this_month} alchemy CU already spent this month'
    )

    dexscreener = DexscreenerService()
    explorers: Dict[int, BlockscoutService] = {}
    try:
        async with state_role.client() as state_client, log_role.client() as log_client:
            # One pinned block per run, so one chain per run. The registry file
            # and the schema both admit several; pinning the first one's head and
            # reading another chain's state at that height would produce a
            # dossier of numbers from no particular moment. Refused rather than
            # guessed -- when a second chain is added, the run splits per chain.
            chain_ids = {registry.chain_for(project).chain_id for project in projects}
            if len(chain_ids) > 1:
                raise MultiChainRunError(
                    'one run pins one block, so it cannot span chains '
                    f'{sorted(chain_ids)}; run one --project at a time'
                )
            constants = get_chain_constants(next(iter(chain_ids)))
            pinned = await chain_module.pin_block_and_window(state_client, constants, log_client)
            result.notes.append(
                f'pinned block {pinned.block}; 24h window from {pinned.window_start_block}'
            )

            for project in projects:
                chain_entry = registry.chain_for(project)
                explorer_service = explorers.get(chain_entry.chain_id)
                if explorer_service is None:
                    explorer_service = build_explorer_service(chain_entry.explorer_api)
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
                    ledger=ledger,
                )
                try:
                    build = await builder.build_project(
                        context, run_id, project_entity_id=project_ids[project.key]
                    )
                    links = context.identity.get('published_links') or []
                    if links:
                        builder.store_candidate_sources(
                            repository, project_ids[project.key], links
                        )
                    repository.commit()
                finally:
                    # Explorer calls made before a raise still cost requests.
                    ledger.add_requests(EXPLORER_KIND, context.explorer.calls)
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

    result.outcome = builder.run_outcome(result.builds)
    return result


async def main(
    test_mode: bool = False,
    project: Optional[str] = None,
) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    configure_job_logging(JOB_NAME)

    repository: Optional[OnchainRepository] = None
    run_id: Optional[int] = None
    outcome = 'failed'
    # Owned here, not by `run_build`: a run that raises mid-way (a ceiling hit
    # while pinning, a transport failure) has still spent and still recorded
    # which endpoints it used and which sections failed, and the row must say
    # so (P13). Read at the end whichever way the run ended.
    ledger = SpendLedger()
    result = builder.RunResult(run_id=0, outcome='failed')
    failed_units: List[Dict[str, Any]] = result.failed_units
    notes: List[str] = result.notes
    try:
        repository = OnchainRepository(get_onchain_database_url(runtime_mode))
        run_id = repository.start_run(JOB_NAME)
        result.run_id = run_id
        with run_context(run_id, JOB_NAME):
            try:
                with repository.advisory_lock():
                    result = await run_build(
                        repository,
                        run_id,
                        runtime_mode=runtime_mode,
                        project_key=project,
                        ledger=ledger,
                        result=result,
                    )
                    outcome = result.outcome
                    # The same object in production; read back regardless so a
                    # stand-in `run_build` that builds its own result still counts.
                    failed_units = result.failed_units
                    notes = result.notes
            except LockNotAcquiredError:
                outcome = 'skipped'
                notes.append('another onchain run holds the advisory lock')
    except Exception as exc:
        # The CLASS, never the message: the message can carry the keyed URL.
        outcome = 'failed'
        notes.append(f'run failed: {type(exc).__name__}')
        # The alert prints units, not notes: without this it reads "failed with
        # no unit recorded", once an hour for the rest of the month when the
        # refusal lands while pinning the block.
        failed_units.append(failed_unit('run/setup', type(exc).__name__))
        logger.error('onchain build failed: %s', type(exc).__name__, exc_info=True)
        if repository is not None:
            repository.rollback()
    finally:
        if repository is not None and run_id is not None:
            repository.finish_run(
                run_id,
                outcome=outcome,
                failed_units=failed_units,
                spend=ledger.snapshot(),
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
    parser.add_argument('--test_mode', type=int, default=0)
    parser.add_argument('--project', type=str, default=None)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main(test_mode=bool(args.test_mode), project=args.project)
        )
    )
