# market-data-notification-backend Agent Guide

Last verified: 2026-08-24

## Scope
- Applies to `market-data-notification-backend/` unless a deeper `AGENTS.md` overrides it.
- Follow the workspace root `AGENTS.md` first for cross-repo rules.
- Use workspace-root task memory for canonical active work. Any repo-local `ACTIVE_TASK.md` or `ACTIVE_TASKS/` paths are scratch only unless the human explicitly asks for them.

## Repo Role
- FastAPI webhook receiver and scheduled job backend for stocks and crypto notifications.
- Owns Redis-backed transient state, message composition, and Telegram delivery.
- Consumes shared provider logic from `market-data-library/`.

## Important Paths
- `src/server.py`: app startup, middleware, router registration, and auth checks.
- `src/config/config.py`: environment contract, Telegram settings, webhook secrets, Redis settings, and job thresholds.
- `src/data_source/market_data_library.py`: shared library initialization boundary.
- `src/router/`: API and webhook entry points.
- `src/service/`: business logic and transformation layer.
- `src/job/`: scheduled notification workflows.
- `src/dependencies.py`: dependency-injection container; what `src/server.py` startup actually builds.
- `src/event/event_emitter.py`: async event dispatch, so a job's effects are not always visible at its call site.
- `src/notification_destination/telegram_notification.py`: Telegram delivery path.
- `tests/unit/`: primary validation surface.

## Repo-Specific Rules
- Treat Telegram delivery, webhook auth, and Redis state as critical paths. Trace changes from router or job entry points through service code before claiming correctness.
- Avoid live sends by default. Use fixtures, explicit `--test_mode=1` job paths, or runtime-mode-aware tests when validating message logic.
- **`DISABLE_TELEGRAM` mutes user-facing output only. Alerts sent through `send_message_to_admin` are never gated on it.** They are gated on `DISABLE_TELEGRAM_ADMIN`, which defaults to false, because an alert channel fails safe by firing. `DISABLE_TELEGRAM` is a deployed secret, so the flag an operator reaches for during an incident must not silence that incident's own alarm. `send_message_to_admin` is the only place the send itself is gated, so do not add a second check at a call site. (Operator ruling 2026-09-06, after two unauthorised live sends traced to the gap.)
- One earlier read of `DISABLE_TELEGRAM_ADMIN` is allowed, and any caller that does its own `init_telegram_bots()` needs it: skip the init when the flag is set, and log the same suppression wording. Without it a disabled run with no credentials fails during init and reports "alert failed" where it should report "alert suppressed". `send_run_alert` in `src/service/onchain/observability.py` is the worked example. A caller that inherits an init done elsewhere does not need it — `src/job/project_monitor/record.py` relies on `main()` having built the bots already. This guards the init, not the send.
- Error alerts that leave through `send_message_to_channel` or `send_crypto_signal_message` are still gated on `DISABLE_TELEGRAM`. Only the admin path was moved.
- The pytest session defaults `DISABLE_TELEGRAM_ADMIN=true` in `tests/conftest.py`, so a test that forgets to stub the sender cannot post to the real admin chat. A local *job* run is deliberately left unprotected: a real failure reaching the admin chat is wanted.
- Check the shared library dependency before assuming local `market-data-library/` edits are in use. `pyproject.toml` currently pins the library from git.
- `local-build-push-dockerfile.sh` is a local helper, not the canonical build contract. Keep tracked docs and workflows authoritative for container build and dependency-auth behavior.
- Be careful with startup side effects in `src/server.py`; app startup initializes dependencies, Redis, Telegram bots, and the shared market data clients. The wiring itself lives in `src/dependencies.py`.

## Validation
- Use workspace `EVALS.md` as the default validation matrix for this repo.
- For auth or routing changes, inspect `src/server.py` and the relevant router module together.
- For message changes, prefer targeted unit tests under `tests/unit/job/` or `tests/unit/service/`.

## Stop And Ask
- The task requires sending real Telegram messages.
- The task requires hitting live provider endpoints when fixtures or mocks are available.
- The task changes auth behavior, webhook secret handling, or production routing assumptions.
