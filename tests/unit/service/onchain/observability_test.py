"""Structured logs, run and span ids, and the alert payload (P13, P14, A12).

The logging tests write to a real `TimedRotatingFileHandler` in `tmp_path` and
read the file back. A mocked handler would assert that the formatter was called,
not that a grep for a run id finds every line of that run -- which is the actual
criterion (A12).
"""
import json
import logging
from pathlib import Path

import pytest

from src.service.onchain.observability import (
    ContextFilter,
    JsonFormatter,
    collect_failed_units,
    collector_span,
    configure_job_logging,
    current_run_id,
    current_span_id,
    format_alert,
    new_span_id,
    run_context,
)


def _read_lines(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def job_log(tmp_path):
    """Install the handler, yield its path, and always remove it again: it is on
    the ROOT logger, so a leaked handler would write every later test's records
    into this file."""
    handler = configure_job_logging('onchain.test', log_dir=str(tmp_path))
    yield tmp_path / 'onchain.test.log'
    logging.getLogger().removeHandler(handler)
    handler.close()


class TestStructuredLog:
    def test_every_line_of_a_run_carries_its_run_id_and_job(self, job_log):
        """A12: the operator greps the log for the run id from the alert, and
        every line of that run must come back."""
        logger = logging.getLogger('onchain.test.logger')
        with run_context(4242, 'onchain.build'):
            logger.info('run started')
            logger.info('registry upserted: %d projects', 4)
        logger.info('outside the run')

        lines = _read_lines(job_log)
        in_run = [line for line in lines if line['run_id'] == 4242]
        assert len(in_run) == 2
        assert {line['job'] for line in in_run} == {'onchain.build'}
        assert in_run[1]['message'] == 'registry upserted: 4 projects'
        assert lines[-1]['run_id'] is None

    def test_lines_inside_a_collector_carry_the_project_and_span(self, job_log):
        logger = logging.getLogger('onchain.test.logger')
        with run_context(7, 'onchain.build'):
            with collector_span('touch-grass') as span:
                logger.info('reading identity')
            logger.info('between collectors')

        lines = _read_lines(job_log)
        inside = [line for line in lines if line['message'] == 'reading identity'][0]
        outside = [line for line in lines if line['message'] == 'between collectors'][0]
        assert inside['span_id'] == span
        assert inside['project'] == 'touch-grass'
        # Emitted as null rather than omitted, so a grep can tell "outside a
        # collector" from "written before the filter was installed".
        assert outside['span_id'] is None
        assert 'project' in outside and outside['project'] is None

    def test_a_third_party_logger_is_captured_too(self, job_log):
        """The handler is on the root logger on purpose: the lines that explain
        a failure are usually the HTTP client's, not ours."""
        with run_context(9, 'onchain.build'):
            logging.getLogger('market_data_library.http.http_client').warning('429')
        assert any(line['run_id'] == 9 for line in _read_lines(job_log))

    def test_the_redacting_record_factory_still_applies(self, job_log):
        """The backend's scrubber overrides `LogRecord.getMessage`, so the JSON
        formatter must render through it. Formatting `msg % args` by hand would
        route around the scrubber and write a bot token into the file."""
        token = '1234567890:AAHfakefakefakefakefakefakefakefakefake'
        with run_context(1, 'onchain.build'):
            logging.getLogger('onchain.test.logger').info(
                'calling https://api.telegram.org/bot%s/sendMessage', token
            )
        text = job_log.read_text()
        assert token not in text
        assert 'bot<redacted>' in text

    def test_an_exception_is_logged_as_its_class_never_its_traceback(self, job_log):
        """A traceback can carry a keyed URL; the class name cannot."""
        with run_context(2, 'onchain.build'):
            try:
                raise ValueError('https://rpc.example.com/v2/SECRETKEY refused')
            except ValueError:
                logging.getLogger('onchain.test.logger').error(
                    'read failed', exc_info=True
                )
        [line] = [line for line in _read_lines(job_log) if line['run_id'] == 2]
        assert line['exc_class'] == 'ValueError'
        assert 'SECRETKEY' not in json.dumps(line)

    def test_the_handler_is_installed_once_per_job(self, tmp_path):
        first = configure_job_logging('onchain.once', log_dir=str(tmp_path))
        second = configure_job_logging('onchain.once', log_dir=str(tmp_path))
        try:
            assert first is second
            assert (
                sum(
                    1
                    for h in logging.getLogger().handlers
                    if getattr(h, '_onchain_job', None) == 'onchain.once'
                )
                == 1
            )
        finally:
            logging.getLogger().removeHandler(first)
            first.close()

    def test_rotation_is_daily_and_keeps_fourteen_files(self, tmp_path):
        handler = configure_job_logging('onchain.rot', log_dir=str(tmp_path))
        try:
            assert handler.when == 'MIDNIGHT'
            assert handler.backupCount == 14
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()


class TestContext:
    def test_span_ids_are_eight_hex_characters_and_do_not_repeat(self):
        spans = {new_span_id() for _ in range(200)}
        assert len(spans) == 200
        assert all(len(s) == 8 and int(s, 16) >= 0 for s in spans)

    def test_the_context_is_restored_after_each_scope(self):
        assert current_run_id() is None
        with run_context(5, 'onchain.build'):
            assert current_run_id() == 5
            with collector_span('zzz') as span:
                assert current_span_id() == span
            assert current_span_id() is None
        assert current_run_id() is None

    def test_a_nested_span_restores_the_outer_one(self):
        with run_context(6, 'onchain.build'):
            with collector_span('zzz') as outer:
                with collector_span('zzz'):
                    pass
                assert current_span_id() == outer

    def test_the_filter_stamps_a_bare_record(self):
        record = logging.LogRecord('x', logging.INFO, 'f', 1, 'm', (), None)
        with run_context(11, 'onchain.watch'):
            ContextFilter().filter(record)
        assert record.run_id == 11 and record.job == 'onchain.watch'

    def test_extra_fields_reach_the_json_without_colliding(self):
        record = logging.LogRecord('x', logging.INFO, 'f', 1, 'm', (), None)
        record.block = 51234567
        ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        assert payload['block'] == 51234567
        assert payload['message'] == 'm'


class TestAlert:
    def test_the_payload_names_the_run_the_job_and_the_failed_units(self):
        message = format_alert(
            88, 'onchain.build',
            [{'unit': 'zzz/onchain_health', 'error_class': 'EvmRpcError'},
             {'unit': 'zzz/contract_safety/verified_source',
              'error_class': 'BlockscoutApiError'}],
        )
        assert 'run 88' in message and 'onchain.build' in message
        assert 'zzz/onchain_health: EvmRpcError' in message
        assert 'zzz/contract_safety/verified_source: BlockscoutApiError' in message

    def test_the_payload_carries_no_url_and_no_field_value(self):
        """P13: run id, job, unit and exception class, never project content."""
        message = format_alert(
            88, 'onchain.build',
            [{'unit': 'zzz/identity', 'error_class': 'BlockscoutApiError'}],
        )
        assert 'http' not in message
        assert '0x' not in message

    def test_a_run_that_failed_with_no_unit_still_says_so(self):
        assert 'no unit recorded' in format_alert(3, 'onchain.build', [])

    def test_failed_units_are_collected_from_stored_sections(self):
        """One entry per failed unit, and a `partial` section contributes its
        failed FIELDS -- the explorer sub-unit (design D8)."""
        units = collect_failed_units([
            {'project': 'zzz', 'name': 'onchain_health', 'status': 'failed',
             'error_class': 'EvmRpcError', 'fields_json': {}},
            {'project': 'zzz', 'name': 'contract_safety', 'status': 'partial',
             'fields_json': {
                 'owner': '0xa',
                 'verified_source': {'state': 'failed',
                                     'error_class': 'BlockscoutApiError'},
             }},
            {'project': 'zzz', 'name': 'identity', 'status': 'ok',
             'fields_json': {'pool_id': '0x1'}},
        ])
        assert units == [
            {'unit': 'zzz/onchain_health', 'error_class': 'EvmRpcError'},
            {'unit': 'zzz/contract_safety/verified_source',
             'error_class': 'BlockscoutApiError'},
        ]

    def test_an_all_ok_build_produces_no_units_so_no_alert_fires(self):
        assert collect_failed_units([
            {'project': 'zzz', 'name': 'identity', 'status': 'ok',
             'fields_json': {'pool_id': '0x1'}},
        ]) == []
