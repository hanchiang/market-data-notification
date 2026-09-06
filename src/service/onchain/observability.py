"""Structured logging, run/span context and the admin alert (P13, P14).

The phase-1 column of the requirement's three-pillar table, in one module:

* **Logging** -- JSON lines to `<ONCHAIN_LOG_DIR>/<job>.log`, rotated daily and
  kept for 14 days. `run_id` and `job` are on every record; `project` and
  `span_id` are on every record written inside a collector. They are injected by
  a filter reading context variables rather than passed down through call
  signatures, because a logger three frames inside a provider client cannot be
  handed a run id.
* **Tracing** -- `run_id` is the run row's id and `span_id` is eight hex
  characters per collector invocation, on every log line AND on every `evidence`
  and `section` row that collector writes. That is what lets a failure be traced
  from an admin-chat message to a log line to the stored evidence with grep and
  one SQL query, and no tracing backend (A12).
* **Alerting** -- one message per job per run, carrying the run id, the job and
  the failed units with their error classes. Never a URL and never a field
  value, and that is enforced rather than asserted: `failed_unit` rejects a unit
  or error class that is not the shape it promises, and `format_alert` replaces
  anything unrecognised with a placeholder. Both, because they fail differently
  -- the first catches the mistake at the point it is made, while the second
  guarantees the property even for a unit built some other way, and an alert
  that refused to send would be the worst of the three outcomes.

The backend's redacting record factory stays in front: it is installed at
`src.config.config` import (which `src.service.onchain.config` forces) and works
by overriding `LogRecord.getMessage`, so the JSON formatter below MUST render the
message through `record.getMessage()` -- formatting `record.msg % record.args`
by hand would route around the scrubber.
"""
import json
import logging
import logging.handlers
import os
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.service.onchain.config import (  # noqa: F401  (forces the redacting factory)
    LOG_RETENTION_DAYS,
    get_log_dir,
)

logger = logging.getLogger('Onchain observability')

run_id_var: ContextVar[Optional[int]] = ContextVar('onchain_run_id', default=None)
job_var: ContextVar[Optional[str]] = ContextVar('onchain_job', default=None)
project_var: ContextVar[Optional[str]] = ContextVar('onchain_project', default=None)
span_id_var: ContextVar[Optional[str]] = ContextVar('onchain_span_id', default=None)

# Everything the formatter must not copy out of the record: these are either
# already rendered into the JSON or are internals of the logging module.
_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord('', 0, '', 0, '', (), None).__dict__
) | {'message', 'asctime', 'taskName'}


def new_span_id() -> str:
    """Eight hex characters. Short enough to grep for, wide enough that two
    collectors in one run do not collide."""
    return secrets.token_hex(4)


class ContextFilter(logging.Filter):
    """Stamp run, job, project and span onto every record that passes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.job = job_var.get()
        record.project = project_var.get()
        record.span_id = span_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the context ids always present.

    The four context keys are emitted even when null: a grep for `"span_id"` has
    to distinguish "outside a collector" from "a line written before the filter
    was installed", and an absent key cannot.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            'ts': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'level': record.levelname,
            'logger': record.name,
            # Through getMessage(), so the redacting record factory applies.
            'message': record.getMessage(),
            'run_id': getattr(record, 'run_id', None),
            'job': getattr(record, 'job', None),
            'project': getattr(record, 'project', None),
            'span_id': getattr(record, 'span_id', None),
        }
        if record.exc_info:
            # The exception CLASS, never the formatted traceback: a traceback can
            # carry a keyed URL, which is the whole reason the monitor's alert
            # path sends class names only.
            payload['exc_class'] = record.exc_info[0].__name__ if record.exc_info[0] else None
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str, sort_keys=True)


def configure_job_logging(
    job: str, *, log_dir: Optional[str] = None, level: int = logging.INFO
) -> logging.Handler:
    """Install the JSON file handler for one job on the root logger.

    On the root logger, not on this package's: the lines worth having in a run's
    log include the library HTTP client's and psycopg's, and a per-package
    handler would drop exactly the ones that explain a failure.

    Idempotent -- a second call for the same job returns the handler already
    installed rather than doubling every line, which matters because a job
    module can be imported by a test that already configured it.
    """
    job_var.set(job)
    directory = log_dir if log_dir is not None else str(get_log_dir())
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f'{job}.log')

    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, '_onchain_job', None) == job:
            return existing

    handler = logging.handlers.TimedRotatingFileHandler(
        path, when='midnight', backupCount=LOG_RETENTION_DAYS, encoding='utf-8'
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    handler.setLevel(level)
    handler._onchain_job = job  # type: ignore[attr-defined]
    root.addHandler(handler)
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    return handler


@contextmanager
def run_context(run_id: Optional[int], job: str) -> Iterator[None]:
    run_token = run_id_var.set(run_id)
    job_token = job_var.set(job)
    try:
        yield
    finally:
        run_id_var.reset(run_token)
        job_var.reset(job_token)


@contextmanager
def collector_span(project: str, span_id: Optional[str] = None) -> Iterator[str]:
    """One collector invocation: its span id is what ties its log lines to the
    evidence rows it wrote (A12). Yields the id so the caller can stamp rows."""
    span = span_id or new_span_id()
    project_token = project_var.set(project)
    span_token = span_id_var.set(span)
    try:
        yield span
    finally:
        span_id_var.reset(span_token)
        project_var.reset(project_token)


def current_run_id() -> Optional[int]:
    return run_id_var.get()


def current_span_id() -> Optional[str]:
    return span_id_var.get()


# A unit is `project/section` or `project/section/field`; project keys are
# lowercase-with-hyphens (registry), section and field names are snake_case.
# `\Z` and not `$` in every pattern here: `$` also matches just before a single
# trailing newline, so `'zzz/onchain_health\n'` matched and would have put a
# line break into the chat message.
_UNIT = re.compile(r'^[a-z0-9-]+(/[a-z0-9_]+){1,2}\Z')
# A Python exception CLASS name, never a message. `str(exc)` from the library's
# HTTP client carries the request URL, and the archive endpoint's URL is a
# credential, so a message-shaped value must not reach the admin chat.
_ERROR_CLASS = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*\Z')

# The job name, as it appears in the entrypoint and in `job_var`: dotted
# lowercase (`onchain.build`). Sanitised like everything else in the payload --
# it is a constant at every current call site, but the module's contract is that
# nothing reaching the admin chat is interpolated unchecked, and a future caller
# passing an error string here would otherwise leak it.
_JOB = re.compile(r'^[a-z0-9_.]+\Z')

UNRECOGNISED = '<unrecognised>'


class AlertPayloadError(ValueError):
    """A failed unit was built from something that could carry project content."""


def format_alert(
    run_id: Optional[int], job: str, failed_units: Sequence[Any]
) -> str:
    """The alert payload: run id, job, failed units with their error classes.

    Never a URL and never a field value. A unit is `project/section` or
    `project/section/field` with its error class -- enough to say which collector
    to look at, and nothing that could carry a secret or project content.

    Anything not of that shape is replaced by `<unrecognised>` rather than
    interpolated. The alert still fires, because a run that failed and then said
    nothing is worse than one that says a unit it could not name; the run row
    holds the full record either way, and the log line names the unit.
    """
    safe_job = _safe(job, _JOB)
    lines = [f'onchain {safe_job} run {run_id if run_id is not None else "unknown"}']
    if not failed_units:
        lines.append('failed with no unit recorded')
        return '\n'.join(lines)
    for unit in failed_units:
        if isinstance(unit, dict):
            name = _safe(unit.get('unit'), _UNIT)
            error_class = _safe(unit.get('error_class'), _ERROR_CLASS)
        else:
            name, error_class = _safe(unit, _UNIT), 'unknown'
        lines.append(f'- {name}: {error_class}')
    return '\n'.join(lines)


def _safe(value: Any, pattern: 're.Pattern[str]') -> str:
    if isinstance(value, str) and pattern.match(value):
        return value
    if value is None:
        return 'unknown'
    logger.warning(
        'alert payload dropped a value that does not match %s', pattern.pattern
    )
    return UNRECOGNISED


# One wording for both suppression paths -- the flag caught here before the bot
# init, and a send the sender withheld -- because to the operator reading the log
# they are the same event and should grep as one. Deliberately does NOT name the
# sender: on the first path no sender is reached, which is the whole point of
# checking before the init.
_SUPPRESSED = (
    'onchain run alert was suppressed (DISABLE_TELEGRAM_ADMIN); '
    'the run row still holds the record'
)


async def send_run_alert(
    run_id: Optional[int], job: str, failed_units: Sequence[Any]
) -> bool:
    """One admin-chat message per job per run. Returns whether it was DELIVERED.

    Imported lazily and guarded in full: this is called after the run row has
    already been written, and an alert failure must never mask or undo the run
    outcome (the monitor's pattern). `init_telegram_bots` populates the global
    bot map `send_message_to_admin` indexes, so it has to run first or the alert
    path raises KeyError and swallows itself.

    `DISABLE_TELEGRAM_ADMIN` is honoured by the sender for the SEND -- and it,
    not `DISABLE_TELEGRAM`, is the switch: a production operator muting
    user-facing channels must still receive run failures. The check below is not
    a second copy of the sender's: it guards the INIT. `init_telegram_bots()`
    raises when credentials are absent, and the environment the flag targets is
    usually exactly that one -- a local or CI process with no bot token -- so
    without it a disabled run reports "alert could not be sent" where the
    operator should read "alert suppressed". The monitor's `_alert` does not
    need this because `main()` wraps its own `init_telegram_bots()` in
    try/except; this job has no such entrypoint step.

    A send the sender suppresses comes back as None, and that is logged too: an
    operator reading this job's log must be able to tell an alert that was
    withheld from a run that never tried to alert.
    """
    try:
        from src.config.config import get_disable_telegram_admin
        from src.notification_destination import telegram_notification
        from src.type.market_data_type import MarketDataType
        from src.util.my_telegram import escape_markdown

        if get_disable_telegram_admin():
            logger.info(_SUPPRESSED)
            return False

        telegram_notification.init_telegram_bots()
        # Resolved through the module rather than imported by name so a test
        # can stub the transport without the send having already been bound.
        delivered = await telegram_notification.send_message_to_admin(
            escape_markdown(format_alert(run_id, job, failed_units)),
            MarketDataType.CRYPTO,
        )
        if delivered is None:
            logger.info(_SUPPRESSED)
            return False
        return True
    except Exception:
        # `exc_info` so the JSON line carries `exc_class`: the formatter emits
        # the class and never the traceback, so this is safe and is the only
        # thing left to grep when an alert silently fails to send.
        logger.warning('onchain run alert could not be sent', exc_info=True)
        return False


def failed_unit(unit: str, error_class: str) -> Dict[str, str]:
    """One failed unit for the run row and the alert.

    Both fields are validated here, at the point the unit is built, because this
    is where a mistake is cheap to see: a collector that records `str(exc)` as
    its error class would otherwise put the library HTTP client's request URL --
    and for an archive read, a credential -- into the admin chat. Raising is
    right at construction time; `format_alert` degrades instead of raising,
    because by then the run has already failed.
    """
    if not _UNIT.match(unit or ''):
        raise AlertPayloadError(
            f'a failed unit must be project/section or project/section/field, '
            f'got {unit!r}'
        )
    if not _ERROR_CLASS.match(error_class or ''):
        raise AlertPayloadError(
            'a failed unit carries an exception CLASS name, never a message: '
            f'got a value of length {len(error_class or "")}'
        )
    return {'unit': unit, 'error_class': error_class}


def collect_failed_units(sections: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Failed units for the run row and the alert, from STORED section rows.

    Degrades rather than raises, unlike `failed_unit`: the rows are already in
    the database by the time this runs, so a value that should never have been
    stored must not also cost us the run's alert. The offending value is replaced
    by `<unrecognised>` and logged, so the JSON log line still names the section.
    """
    units: List[Dict[str, str]] = []
    for section in sections:
        project = section.get('project') or 'unknown'
        name = section.get('name') or 'unknown'
        if section.get('status') == 'failed':
            units.append(
                _degrading_unit(
                    f'{project}/{name}', section.get('error_class') or 'unknown'
                )
            )
            continue
        for field_name, value in (section.get('fields_json') or {}).items():
            if isinstance(value, dict) and value.get('state') == 'failed':
                units.append(
                    _degrading_unit(
                        f'{project}/{name}/{field_name}',
                        value.get('error_class') or 'unknown',
                    )
                )
    return units


def _degrading_unit(unit: str, error_class: str) -> Dict[str, str]:
    return {
        'unit': _safe(unit, _UNIT),
        'error_class': _safe(error_class, _ERROR_CLASS),
    }
