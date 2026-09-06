"""Structured logs, run and span ids, and the alert payload (P13, P14, A12).

The logging tests write to a real `TimedRotatingFileHandler` in `tmp_path` and
read the file back. A mocked handler would assert that the formatter was called,
not that a grep for a run id finds every line of that run -- which is the actual
criterion (A12).
"""
import asyncio
import json
import logging
from pathlib import Path

import pytest

from src.service.onchain.observability import (
    UNRECOGNISED,
    AlertPayloadError,
    ContextFilter,
    JsonFormatter,
    collect_failed_units,
    collector_span,
    configure_job_logging,
    current_run_id,
    current_span_id,
    failed_unit,
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


class TestAlertBoundary:
    """Round-1 finding P2-4. A11 and P13 are universals over what reaches the
    admin chat, so they are discharged by a check at the boundary rather than by
    one example of benign input."""

    @pytest.mark.parametrize('error_class', [
        # What `str(exc)` produces from the library HTTP client -- the archive
        # endpoint's URL is a credential, and this is the shape that leaks it.
        'HttpClientError: HTTP error 500 (url=https://rhc.g.alchemy.com/v2/SECRETKEY)',
        'Failed to fetch blockscout data: https://robinhoodchain.blockscout.com/api/v2/addresses/0xabc',
        'total_supply was 1000000000000000000000000',
        '',
    ])
    def test_a_message_shaped_error_class_is_refused_at_construction(
        self, error_class
    ):
        with pytest.raises(AlertPayloadError):
            failed_unit('zzz/onchain_health', error_class)

    @pytest.mark.parametrize('unit', [
        'https://rhc.g.alchemy.com/v2/SECRETKEY',
        '0x16391C40e85FB2246A2C8c17bfA2594C5d3EF84b',
        'zzz',
        'a/b/c/d',
        'ZZZ/Onchain Health',
        '',
    ])
    def test_a_unit_that_is_not_project_section_field_is_refused(self, unit):
        with pytest.raises(AlertPayloadError):
            failed_unit(unit, 'EvmRpcError')

    @pytest.mark.parametrize('unit', [
        'touch-grass/identity',
        'not-a-website/contract_safety/verified_source',
        'zzz/onchain_health',
    ])
    def test_the_real_unit_shapes_are_accepted(self, unit):
        assert failed_unit(unit, 'EvmRpcError')['unit'] == unit

    def test_format_alert_replaces_anything_unrecognised_rather_than_interpolating(
        self,
    ):
        """The second half of the boundary: a unit built some other way, or read
        back from a row written before the check existed, must not reach the
        chat. The alert still fires -- a failed run that says nothing is worse."""
        message = format_alert(88, 'onchain.build', [
            {'unit': 'zzz/onchain_health',
             'error_class': 'HttpClientError (url=https://rhc.g.alchemy.com/v2/SECRETKEY)'},
            {'unit': 'https://rhc.g.alchemy.com/v2/SECRETKEY',
             'error_class': 'EvmRpcError'},
        ])
        assert 'SECRETKEY' not in message
        assert 'http' not in message
        assert message.count(UNRECOGNISED) == 2
        # It still names the run and still fires.
        assert 'run 88' in message

    def test_collect_failed_units_degrades_instead_of_raising(self):
        """These rows are already in the database by the time this runs, so a
        value that should never have been stored must not also cost the run its
        alert."""
        units = collect_failed_units([
            {'project': 'zzz', 'name': 'onchain_health', 'status': 'failed',
             'error_class': 'HttpClientError: https://rhc.g.alchemy.com/v2/SECRETKEY',
             'fields_json': {}},
        ])
        assert units == [
            {'unit': 'zzz/onchain_health', 'error_class': UNRECOGNISED}
        ]
        assert 'SECRETKEY' not in format_alert(1, 'onchain.build', units)

    def test_a_dropped_value_is_logged_so_it_can_be_found(self, job_log):
        with run_context(3, 'onchain.build'):
            format_alert(3, 'onchain.build', [
                {'unit': 'zzz/onchain_health', 'error_class': 'a message with spaces'},
            ])
        assert any(
            'alert payload dropped a value' in line['message']
            for line in _read_lines(job_log)
        )

    @pytest.mark.parametrize('job', [
        'onchain.build\nhttps://rhc.g.alchemy.com/v2/SECRETKEY',
        'https://rhc.g.alchemy.com/v2/SECRETKEY',
    ])
    def test_the_job_name_is_sanitised_like_the_rest_of_the_payload(self, job):
        """`job` is a constant at every call site today, so this is about the
        contract rather than a live leak: the docstring says the payload is never
        a URL, and an unsanitised f-string interpolation made that false."""
        message = format_alert(9, job, [])
        assert 'SECRETKEY' not in message
        assert 'http' not in message
        assert UNRECOGNISED in message
        assert 'run 9' in message

    def test_a_trailing_newline_does_not_slip_through_the_unit_pattern(self):
        """`$` matches before a single trailing newline; `\\Z` does not. Nothing
        leaks either way -- this pins the anchor so a rewrite cannot loosen it."""
        with pytest.raises(AlertPayloadError):
            failed_unit('zzz/onchain_health\n', 'EvmRpcError')
        assert format_alert(
            9, 'onchain.build', [{'unit': 'zzz/onchain_health\n',
                                  'error_class': 'EvmRpcError'}]
        ).endswith(f'- {UNRECOGNISED}: EvmRpcError')

    def test_the_send_path_inits_the_bots_then_sends_the_escaped_alert(
        self, monkeypatch
    ):
        """The five lines that touch the wire, exercised with recorders instead
        of a transport: `init_telegram_bots()` must run BEFORE the send (it
        populates the map `send_message_to_admin` indexes, so the other order is
        a KeyError swallowed by the except), the text is the escaped payload, and
        the destination is the CRYPTO admin chat.

        `DISABLE_TELEGRAM=false` is pinned for the same reason as the failure
        test: the recorders are what keep this off the network, and the test must
        assert the same thing whichever way the developer's env is set. With the
        flag on, the real sender returns None and this asserts nothing.
        """
        import src.notification_destination.telegram_notification as telegram_notification
        import src.service.onchain.observability as obs
        from src.type.market_data_type import MarketDataType
        from src.util.my_telegram import escape_markdown

        calls = []
        # Returns a stand-in for the Message the real sender returns: None is
        # how it reports a suppressed send, so a recorder returning None would
        # make this assert the suppressed path instead of the delivered one.
        sent_message = object()

        async def record_send(message, market_data_type):
            calls.append(('send', message, market_data_type))
            return sent_message

        monkeypatch.setenv('DISABLE_TELEGRAM', 'false')
        monkeypatch.setattr(
            telegram_notification, 'init_telegram_bots',
            lambda: calls.append(('init',)),
        )
        monkeypatch.setattr(
            telegram_notification, 'send_message_to_admin', record_send
        )

        units = [failed_unit('zzz/onchain_health', 'EvmRpcError')]

        async def run():
            with run_context(6, 'onchain.build'):
                return await obs.send_run_alert(6, 'onchain.build', units)

        assert asyncio.run(run()) is True
        assert [call[0] for call in calls] == ['init', 'send']
        _, message, market_data_type = calls[1]
        assert message == escape_markdown(
            format_alert(6, 'onchain.build', units)
        )
        assert market_data_type is MarketDataType.CRYPTO
        # The escaping is not a no-op on this payload, so the assertion above
        # would still hold if `escape_markdown` were dropped from the product --
        # pin the observable consequence too.
        assert 'zzz/onchain\\_health' in message

    def test_a_failed_alert_send_is_logged_with_its_exception_class(
        self, job_log, monkeypatch
    ):
        """`exc_info=True` so the JSON line carries `exc_class`: the formatter
        emits the class and never the traceback, and a silent alert failure
        would otherwise leave nothing to grep.

        The transport is stubbed, which is what keeps this off the wire: this
        worktree's `.env` carries real credentials, and a test that let the real
        send run with the flag off would post to the operator's admin chat --
        which is exactly what happened once while writing this file.
        """
        import src.notification_destination.telegram_notification as telegram_notification
        import src.service.onchain.observability as obs

        async def refuse(*_args, **_kwargs):
            raise RuntimeError('transport stubbed by the test; nothing was sent')

        # Pinned, not inherited: with `DISABLE_TELEGRAM=true` in the developer's
        # environment the guard returns before the stub and no line is written,
        # so this test would pass only in the configuration where an unstubbed
        # send reaches the wire. The stubs above are what keep it off the
        # network; the flag is not doing that job here.
        monkeypatch.setenv('DISABLE_TELEGRAM', 'false')
        monkeypatch.setattr(telegram_notification, 'init_telegram_bots', lambda: None)
        monkeypatch.setattr(telegram_notification, 'send_message_to_admin', refuse)

        async def run():
            with run_context(4, 'onchain.build'):
                return await obs.send_run_alert(4, 'onchain.build', [])

        assert asyncio.run(run()) is False
        lines = [
            line for line in _read_lines(job_log)
            if 'alert could not be sent' in line['message']
        ]
        assert lines and lines[0]['exc_class'] == 'RuntimeError'

    def test_a_disabled_run_reports_suppression_even_with_no_credentials(
        self, job_log, monkeypatch
    ):
        """The environment `DISABLE_TELEGRAM` actually targets is usually one
        with no bot token at all, and `init_telegram_bots()` raises there. If the
        flag were only checked by the sender, the init would raise first, the
        broad except would catch it, and the operator would read "alert could not
        be sent" for a run whose alert was deliberately withheld -- a real
        failure and a suppression telling the same story.

        So the stub raises the way the real init does, rather than being a
        harmless recorder that could never expose the ordering.
        """
        import src.notification_destination.telegram_notification as telegram_notification
        import src.service.onchain.observability as obs

        def no_credentials():
            raise RuntimeError('telegram stocks bot token is missing')

        monkeypatch.setenv('DISABLE_TELEGRAM', 'true')
        monkeypatch.setattr(
            telegram_notification, 'init_telegram_bots', no_credentials
        )

        async def run():
            with run_context(5, 'onchain.build'):
                return await obs.send_run_alert(5, 'onchain.build', [])

        assert asyncio.run(run()) is False
        messages = [line['message'] for line in _read_lines(job_log)]
        assert any('suppressed by the sender' in message for message in messages)
        assert not any('could not be sent' in message for message in messages)

    def test_a_send_the_sender_withholds_is_reported_as_suppressed(
        self, job_log, monkeypatch
    ):
        """The other half: the flag is off here, so the job hands the message to
        the sender, and the sender is the one that withholds it -- which it
        reports by returning None instead of a Message. The job must read that
        as suppression rather than delivery, or `send_run_alert` returns True for
        an alert nobody received.

        What is asserted is what the code guarantees -- no message is SENT -- and
        not the old call-site property that no bot is built. Bot construction
        does no I/O (`telegram.Bot.__init__` only builds `HTTPXRequest`
        objects), so it was never the property worth pinning.
        """
        import src.notification_destination.telegram_notification as telegram_notification
        import src.service.onchain.observability as obs

        async def withhold(*_args, **_kwargs):
            return None

        monkeypatch.setenv('DISABLE_TELEGRAM', 'false')
        monkeypatch.setattr(telegram_notification, 'init_telegram_bots', lambda: None)
        monkeypatch.setattr(
            telegram_notification, 'send_message_to_admin', withhold
        )

        async def run():
            with run_context(7, 'onchain.build'):
                return await obs.send_run_alert(7, 'onchain.build', [])

        assert asyncio.run(run()) is False
        messages = [line['message'] for line in _read_lines(job_log)]
        assert any('suppressed by the sender' in message for message in messages)
        assert not any('could not be sent' in message for message in messages)
