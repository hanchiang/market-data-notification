# Market Data Notification Backend: Engineering Practices & Project Guide

This document provides project-specific engineering practices, architecture notes, and useful information for Copilot and contributors. It supplements universal best practices in `copilot-instructions.md`.

---

## 🏗️ Architecture Overview

- **Language**: Python 3.12+
- **Framework**: FastAPI (REST API)
- **Database**: Redis (with Selenium Chromium for browser automation)
- **Async HTTP**: aiohttp
- **Notifications**: python-telegram-bot
- **Job Scheduling**: Cron-based, custom time checking
- **Testing**: pytest, pytest-asyncio, coverage

### Key Patterns
- **Service Layer**: Business logic, data transformation, caching
- **Third Party Service Layer**: Raw API clients, authentication, rate limiting
- **Dependency Injection**: Centralized in `src/dependencies.py`
- **Message Sender**: Each notification type has a dedicated sender class
- **Error Notification**: All jobs send error messages to Telegram admin channel
- **Event Emitter**: Async event handling via `src/event/event_emitter.py`

---

## 📦 Project Structure

See `README.md` for a full directory tree and component breakdown.
- `src/config/`: Environment/config management
- `src/job/`: Scheduled jobs (stocks, crypto, backup)
- `src/service/`: Business logic, data processing
- `src/third_party_service/`: External API clients
- `src/router/`: FastAPI endpoints
- `src/type/`: Data models and type definitions
- `src/util/`: Utilities (logging, date, exceptions, Telegram helpers)
- `src/dependencies.py`: Dependency injection container
- `src/server.py`: FastAPI app entrypoint

---

## 📝 Code Conventions

- **Type hints required** for all public functions
- **Async/await** for I/O and network operations
- **Small, focused functions** (single responsibility)
- **Snake_case** for functions/variables, **PascalCase** for classes
- **PEP 8** style enforced by Black and Ruff
- **Docstrings** for complex functions and business logic
- **Early returns** to reduce nesting
- **Consistent error handling**: Use try/except, log errors, notify admin
- **Logging**: Use `src/util/logger.py` for structured logs
- **Test mode**: Use `--test_mode=1` for dev/test channels and relaxed thresholds

---

## 🔒 Error Handling & Validation

- All jobs catch exceptions and send error notifications to Telegram admin
- Use `src/util/exception.py` for custom exceptions
- Log errors with context and timestamps
- Clean up Redis and dependencies in `finally` blocks
- Validate all external inputs (webhooks, API data)
- Avoid bare `except:`; catch specific exceptions

---

## 🧪 Testing

- **Unit tests**: `tests/unit/` for business logic
- **Integration tests**: Test system interactions and API endpoints
- **Test data**: `tests/data/` for fixture JSONs
- **Coverage**: Use `coverage` tool, aim for 80%+
- **Mock external dependencies** (Redis, Telegram, APIs)
- **Test both happy paths and edge cases**
- **Run tests**: `poetry run pytest` or via CI

---

## 🚀 Performance & Optimization

- Optimize for readability first, performance second
- Use caching for expensive API calls (Redis)
- Profile bottlenecks before optimizing
- Use async for concurrent I/O
- Avoid premature optimization
- Monitor job run times (see logs)

---

## 📚 Dependencies

- Managed via Poetry (`pyproject.toml`)
- Pin major versions for stability
- Key dependencies: FastAPI, aiohttp, redis, python-telegram-bot, market-data-library (private repo)
- Dev dependencies: pytest, pytest-asyncio, coverage, mypy, black, ruff
- See `pyproject.toml` for full list

---

## 🔐 Security

- Secrets managed via `.env` and GitHub Actions secrets
- Never commit secrets to source control
- Sanitize all external inputs
- Use HTTPS for webhooks and API endpoints
- Principle of least privilege for API tokens and Redis access

---

## 🛠️ Deployment & CI/CD

- **Docker**: Use `docker-compose.yml` for local/dev setup
- **GitHub Actions**: Automated test, deploy, cron jobs, backup
- **Environment variables**: Set via `.env` or CI secrets
- **Production deploy**: On merge to master
- **Backup**: Redis data backed up via shell script and emailed

---

## 📝 Documentation & Contribution

- See `README.md` for setup, workflow, and troubleshooting
- See `CONTRIBUTING.md` for adding new data sources and overextended levels
- Document new features and update API docs
- Add tests for all new code
- Use clear, descriptive commit messages

---

## ⚡ Project-Specific Tips for Copilot

- Always use type hints and docstrings
- Prefer async for network/database operations
- Use dependency injection for all services
- Log errors and notify admin on failure
- Cache expensive operations in Redis
- Follow message sender pattern for notifications
- Use test mode for development and CI
- Reference `copilot-instructions.md` for universal best practices

---

## 🗂️ Useful References

- [README.md](../README.md): Setup, architecture, workflows
- [copilot-instructions.md](copilot-instructions.md): Universal engineering practices
- [CONTRIBUTING.md](../CONTRIBUTING.md): Data source and feature contribution
- [pyproject.toml](../pyproject.toml): Dependencies and tooling

---

*Update this file as the project evolves. Use it as context for Copilot and contributors.*
