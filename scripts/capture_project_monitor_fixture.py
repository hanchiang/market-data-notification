#!/usr/bin/env python3
"""Capture the AC6 fixture: raw JSON-RPC responses at one pinned block.

Committed with the fixture so it can be recaptured at a new block when the read
plan changes -- a fixture whose capture procedure is lost becomes unmaintainable
the first time a getter is added.

Runs against the PUBLIC endpoint by default and therefore costs nothing on the
metered account. That is also why the fixture is safe to commit: no auth is
sent, so none can be echoed back in a response body.

  PYTHONPATH="$(pwd)" python scripts/capture_project_monitor_fixture.py \
      --out tests/unit/service/project_monitor/fixtures
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

from market_data_library.core.onchain.evm import (  # noqa: E402
    EvmClient,
    public_rpc_budget,
)

from src.service.project_monitor import recorder  # noqa: E402
from src.service.project_monitor.config import NETNET, get_public_endpoint  # noqa: E402


def _raw_to_dict(raw) -> dict:
    return {
        'method': raw.method,
        'params': raw.params,
        'body': raw.body,
        'endpoint_kind': raw.endpoint_kind,
    }


async def capture(out_dir: Path, log_window_blocks: int) -> None:
    project = NETNET
    endpoint = get_public_endpoint(supports_batch=True)
    async with EvmClient(endpoint, public_rpc_budget(min_request_interval_seconds=0.2)) as client:
        head, _ = await client.block_number()
        # Pin a few blocks back from head so a reorg-free, fully-propagated
        # block is captured rather than the tip.
        block = head - 20
        sample = await recorder.read_state(client, project, block)

        window = await recorder.read_log_window(
            client, project, project.name,
            block - log_window_blocks, block,
            net_decimals=9, usdg_decimals=6,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'sample_raw_responses.json').write_text(
        json.dumps(
            {
                'block': sample.block,
                'block_timestamp': sample.block_timestamp,
                'epoch_number': sample.epoch_number,
                'endpoint_kind': sample.endpoint_kind,
                'raw_responses': [_raw_to_dict(r) for r in sample.raw_responses],
            },
            indent=2,
        )
    )
    # The expected values sit BESIDE the raw responses, so AC3 and AC6 are a
    # comparison rather than a re-run of the same decoder against itself.
    (out_dir / 'sample_expected.json').write_text(
        json.dumps(
            {
                'block': sample.block,
                'epoch_number': sample.epoch_number,
                'readings': [
                    {
                        k: (str(v) if k == 'value_int' and v is not None else v)
                        for k, v in reading.items()
                    }
                    for reading in sample.readings
                ],
                'failed_peripheral': sample.failed_peripheral,
            },
            indent=2,
        )
    )
    (out_dir / 'log_window.json').write_text(
        json.dumps(
            {
                'from_block': block - log_window_blocks,
                'to_block': block,
                'raw_responses': [_raw_to_dict(r) for r in window['raw_responses']],
                'mints': [{**m, 'amount': str(m['amount'])} for m in window['mints']],
                'flows': [{**f, 'amount': str(f['amount'])} for f in window['flows']],
                'events': window['events'],
            },
            indent=2,
        )
    )
    print(f'captured block {block}, epoch {sample.epoch_number}')
    print(f'  readings        {len(sample.readings)}')
    print(f'  raw responses   {len(sample.raw_responses)}')
    print(f'  mints           {len(window["mints"])}')
    print(f'  flows           {len(window["flows"])}')
    print(f'  events          {len(window["events"])}')


async def capture_issuance_window(out_dir: Path, block: int) -> None:
    """Capture one single-block log window around a known issuance transaction.

    Separate from the head capture because the desk executes rarely: a window
    taken near head contains no issuance mint at all, and AC6 asks for one. The
    block is passed in rather than searched for, so the fixture is reproducible
    without re-scanning ~1.7M blocks of `PremiumSold` logs to find it again.

    Block 49,066,320 holds `0x0fe14bcf...7b19`, the execution the dapp-crawl
    trace names.
    """
    endpoint = get_public_endpoint(supports_batch=True)
    async with EvmClient(endpoint, public_rpc_budget()) as client:
        window = await recorder.read_log_window(
            client, NETNET, NETNET.name, block, block,
            net_decimals=9, usdg_decimals=6,
        )
    (out_dir / 'issuance_window.json').write_text(
        json.dumps(
            {
                'from_block': block,
                'to_block': block,
                'note': 'the premiumSeller execution recorded in the dapp crawl '
                        'trace, 0x0fe14bcf...7b19',
                'raw_responses': [_raw_to_dict(r) for r in window['raw_responses']],
                'mints': [{**m, 'amount': str(m['amount'])} for m in window['mints']],
                'flows': [{**f, 'amount': str(f['amount'])} for f in window['flows']],
                'events': window['events'],
            },
            indent=2,
        )
        + '\n'
    )
    print(f'captured issuance window at block {block}: '
          f'{len(window["mints"])} mints, {len(window["flows"])} flows')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--issuance_block', type=int, default=None,
        help='capture only the single-block issuance window at this block',
    )
    parser.add_argument(
        '--out', default='tests/unit/service/project_monitor/fixtures', type=Path
    )
    parser.add_argument('--log_window_blocks', type=int, default=36000)
    args = parser.parse_args()
    if args.issuance_block is not None:
        asyncio.run(capture_issuance_window(args.out, args.issuance_block))
    else:
        asyncio.run(capture(args.out, args.log_window_blocks))
