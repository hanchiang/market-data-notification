# market-data-notification-backend Agent Guide

Last verified: 2026-04-02

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
- `src/notification_destination/telegram_notification.py`: Telegram delivery path.
- `tests/unit/`: primary validation surface.

## Repo-Specific Rules
- Treat Telegram delivery, webhook auth, and Redis state as critical paths. Trace changes from router or job entry points through service code before claiming correctness.
- Avoid live sends by default. Use fixtures, explicit `--test_mode=1` job paths, or runtime-mode-aware tests when validating message logic.
- Check the shared library dependency before assuming local `market-data-library/` edits are in use. `pyproject.toml` currently pins the library from git.
- `local-build-push-dockerfile.sh` is a local helper, not the canonical build contract. Keep tracked docs and workflows authoritative for container build and dependency-auth behavior.
- Be careful with startup side effects in `src/server.py`; app startup initializes dependencies, Redis, Telegram bots, and the shared market data clients.

## Validation
- Use workspace `EVALS.md` as the default validation matrix for this repo.
- For auth or routing changes, inspect `src/server.py` and the relevant router module together.
- For message changes, prefer targeted unit tests under `tests/unit/job/` or `tests/unit/service/`.

## Stop And Ask
- The task requires sending real Telegram messages.
- The task requires hitting live provider endpoints when fixtures or mocks are available.
- The task changes auth behavior, webhook secret handling, or production routing assumptions.
