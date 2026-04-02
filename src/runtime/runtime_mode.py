from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeMode:
    """Immutable entry-point context for test-oriented runtime behavior.

    The defaults stay production-safe so call sites that forget to thread a
    runtime mode do not silently inherit dev-only behavior from process state.
    """

    is_test_mode: bool = False
    use_dev_telegram: bool = False
    relax_thresholds: bool = False
    allow_stale_replay: bool = False
    bypass_schedule: bool = False

    @classmethod
    def from_test_mode(cls, test_mode: bool) -> "RuntimeMode":
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
