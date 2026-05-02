from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeMode:
    """Immutable entry-point context for test-oriented runtime behavior.

    The defaults stay production-safe so call sites that forget to thread a
    runtime mode do not silently inherit dev-only behavior from process state.
    """

    is_test_mode: bool = False
    # Route outbound Telegram sends to dev destinations when an entry point is
    # explicitly exercising local/test behavior. This is intentionally separate
    # from is_test_mode so future diagnostics can use test storage without
    # changing delivery targets, or vice versa.
    use_dev_telegram: bool = False
    # Loosen ranking and alert thresholds so stale fixtures or narrow replay
    # samples can still render useful operator-review messages in test mode.
    relax_thresholds: bool = False
    # Permit replaying old local Redis or SQLite data during manual validation.
    # Normal scheduled jobs should reject stale payloads rather than making a
    # digest look current when the upstream source did not refresh.
    allow_stale_replay: bool = False
    # Let explicit CLI test runs skip cron-window checks while keeping scheduled
    # production invocations bound to their configured runtime windows.
    bypass_schedule: bool = False

    @classmethod
    def from_test_mode(cls, test_mode: bool) -> "RuntimeMode":
        # CLI jobs still expose one operator-facing test flag, so derive the
        # narrower runtime concerns from that entry-point switch in one place.
        return cls(
            is_test_mode=test_mode,
            use_dev_telegram=test_mode,
            relax_thresholds=test_mode,
            allow_stale_replay=test_mode,
            bypass_schedule=test_mode,
        )


# Shared default for request handlers and helpers that should remain in the
# normal production-style path unless a caller explicitly opts into test mode.
DEFAULT_RUNTIME_MODE = RuntimeMode()
