# market-data-notification-backend Agent Guide

Last verified: 2026-09-06

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
- One earlier read of `DISABLE_TELEGRAM_ADMIN` is allowed, for a caller whose own `init_telegram_bots()` failure would surface as an alert failure: skip the init when the flag is set, and log the same suppression wording. Otherwise a disabled run with no credentials reports "alert failed" where it should report "alert suppressed". `send_run_alert` in `src/service/onchain/observability.py` is the worked example. The alternative is to wrap the init in its own try/except with its own log line, which is what `src/job/project_monitor/record.py:216-219` does. Either way this guards the init, not the send.
- **Do not report a failure to the admin chat through `send_message_to_channel` in new code.** That function returns early on `DISABLE_TELEGRAM`, so a job or sender crash reported that way is silenced by a deployed secret. This is about crash and failure *reports*, not about the function: the event emitter's notice handler deliberately delivers routine webhook chatter to the admin chat through `send_message_to_channel`, because that chatter is exactly what an operator should be able to mute. Four job-side call sites did this until 2026-09-06 and now call `send_message_to_admin`; the one that mattered most was `MessageSenderWrapper.start`, which swallows its own exception and returns `None`, so its alert is the only notice that failure ever gets. Pass `runtime_mode` when the caller has one, so a `--test_mode=1` crash still redirects to the dev channel.
- A router emits by **intent**, not by destination. `send_alert_to_telegram` is for errors and warnings and reaches `send_message_to_admin`; `send_notice_to_telegram` is for routine chatter (the webhook success and idempotency-skip messages) and stays on `send_message_to_channel`, so `DISABLE_TELEGRAM` can quiet it without silencing the warnings. Both handlers live in `src/event/event_emitter.py` and take **no chat id**, so a router cannot choose a destination; the notice handler still *delivers* through `send_message_to_channel`, which is the point of it. What the missing chat id buys is that an alert cannot be addressed onto the muted transport by mistake. The `market_data_type` → admin chat id **map** was removed on 2026-09-06 — a ready-made dict is what made the mistake easy to write. The resolver `get_admin_channel_id_from_market_data_type` remains and still yields that id; the notice handler uses it deliberately. So the admin chat is still addressable through `send_message_to_channel` by anyone who wants it, and what stops a *failure report* going that way is the rule above, not the type system. The webhook warnings (wrong secret, source IP outside the whitelist) used to emit `send_to_telegram` with an admin chat id, which meant `DISABLE_TELEGRAM` silenced every intrusion warning in production; that event no longer exists. Neither handler may raise: pyee turns a handler exception into an `'error'` event whose handler alerts the same way, so a Telegram outage would feed itself. The alert path routes through `_alert_admin`, which swallows and logs; the notice handler has its own try/except.
- Emit with keyword arguments only, and never with a chat id under any name (`channel=`, `chat_id=` and three more spellings are rejected by the AST test). The handlers are keyword-only so a positional emit raises `TypeError` (which pyee turns into an alerting `'error'` event) rather than being dropped: `emit` returns `False` for an unhandled non-`error` event and every call site ignores it, so a name mismatch **deletes** the message with no exception and no log. `tests/unit/event/event_emitter_test.py::test_every_router_emit_names_a_registered_event` parses the AST of `src/router/tradingview/tradingview.py` and fails on an emit there that names an unregistered event, carries a chat-id keyword, or changes its alert/notice intent. It scans that one file only, so a router added elsewhere needs its own scan added — the test does not cover it.
- **`async_ee` is process-global, and the TradingView router is currently its only emitter.** Both the alert handler and `on_error` therefore apply `_router_runtime_mode()`, which redirects to the dev channel under `SIMULATE_TRADINGVIEW_TRAFFIC`. That is right only while the assumption holds: a job or service that starts emitting `send_alert_to_telegram` would have its *real* failure redirected to the dev channel during a simulation, which is the opposite of `send_message_to_admin`'s own stance (a real failure during a simulation is still real). A second emitter must either scope the redirect to the emitting module or accept that.
- The error fallback inside `send_crypto_signal_message` is still gated on `DISABLE_TELEGRAM`, and that is correct: it fires only when a user-facing send has already failed, so it rides inside output the flag is meant to mute.
- **Guard every `send_message_to_admin` call site**, without exception — as of 2026-09-06 all nine in `src/` are, `_alert_admin` in the event emitter included. `send_message_to_admin` lets a delivery error propagate, and `send_message_to_channel`, which most of these sites used before, does not. That difference is not local: an unguarded alert inside `_send_supporting_error_alert` put a raise on the path out of `_load_supporting_messages`, whose contract is that a failing section still leaves a digest to send, so two consecutive admin-send failures would have dropped the whole user-facing stocks digest. Log the failure so it is distinguishable from the failure being reported, which otherwise logs in the same shape a few lines earlier. The six job-side sites use the prefix `failed to alert the admin:`; `record.py`, `observability.py` and `_alert_admin` each carry their own wording naming their own subsystem, so no single grep finds all nine. Match the neighbours of the site you are editing.
- The pytest session defaults `DISABLE_TELEGRAM_ADMIN=true` in `tests/conftest.py`, so a test that neither overrides the flag nor stubs the sender cannot post to the real admin chat. It is `setdefault`, so an exported variable or a `monkeypatch.setenv` still wins. A local *job* run is deliberately left unprotected: a real failure reaching the admin chat is wanted.
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
