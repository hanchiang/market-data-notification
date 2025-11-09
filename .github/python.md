---
applyTo: "**/*.py"
---

# Python Best Practices for lolbot

This document outlines Python-specific best practices and conventions for Python files in the lolbot League of Legends bot project.

## 🐍 Python Version & Environment

### Version Requirements
- **Python 3.11+** required for optimal tooling compatibility
- Use Poetry for dependency management (no pip/requirements.txt)
- Always work within the Poetry virtual environment: `poetry shell`

### Environment Setup
```bash
# Fresh setup
poetry install
poetry shell
pre-commit install

# Daily workflow
poetry run ruff format .
poetry run ruff check .
poetry run mypy .
poetry run pytest -q
```

## 🔍 Type System & Annotations

### Strict Typing Enforcement
- **ALL functions must have type annotations** (mypy strict mode enabled)
- Use `from __future__ import annotations` for forward references
- Example from `cli.py`:
```python
from __future__ import annotations

def hello(name: str = "World") -> None:
    """Say hello (example command)."""
    rprint(f"[bold green]Hello, {name}![/]")
```

### Project Type Patterns
Use `TypedDict` for structured data (create a types module as needed)
Type aliases for clarity: `ExampleType = dict[str, int]`
Example type pattern (to be implemented):
```python
from typing import TypedDict

class ExampleType(TypedDict):
    field1: str
    field2: int
    # Add more fields as needed
```
```

### mypy Configuration
- Relaxed typing in tests: `disallow_untyped_defs = False` for `tests.*`
- Strict everywhere else - no `# type: ignore` without good reason
- Use `reveal_type()` for debugging type inference

## 🎨 Code Style & Formatting

### Ruff Configuration (pyproject.toml)
- **Line length: 100 characters**
- **Double quotes** for strings
- **Space indentation** (4 spaces)
- **LF line endings** (Unix style)

Import Organization
- Standard library imports first
- Third-party imports second
- Local imports last
- Use `ruff check --fix` to auto-organize imports
- Example:
```python
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

# Local imports
from src.config import config
from src.util import logger

if TYPE_CHECKING:
    from src.type import ExampleType
```

### Naming Conventions
- **snake_case** for functions, variables, modules
- **PascalCase** for classes, TypedDicts
- **UPPER_CASE** for constants
- Boolean variables: `is_*`, `has_*`, `can_*`
- Private attributes: single underscore prefix `_private`

### Common Anti-Patterns (Avoid These)

❌ **Anti-Pattern: Missing Type Annotations**
```python
def process_detections(detections):  # What type? Mypy fails!
    return [d for d in detections if d["conf"] > 0.5]
```

✅ **Correct:**
```python
def process_detections(detections: list[Detection]) -> list[Detection]:
    """Filter detections by confidence threshold."""
    return [d for d in detections if d["conf"] > 0.5]
```

---

❌ **Anti-Pattern: Bare Except**
```python
try:
    result = risky_operation()
except:  # Catches KeyboardInterrupt, SystemExit, etc!
    pass  # Silently swallows errors
```

✅ **Correct:**
```python
try:
    result = risky_operation()
except SpecificError as e:
    logger.error("Operation failed: %s", e)
    raise  # Or handle appropriately
```

---

❌ **Anti-Pattern: Mutable Default Arguments**
```python
def add_entity(entities=[]) -> list[Entity]:  # BUG: Shared across calls!
    entities.append(new_entity)
    return entities
```

✅ **Correct:**
```python
def add_entity(entities: list[Entity] | None = None) -> list[Entity]:
    """Add entity to list (creates new list if None)."""
    if entities is None:
        entities = []
    entities.append(new_entity)
    return entities
```

---

❌ **Anti-Pattern: Unnecessary List Comprehension**
```python
# Slow: Creates intermediate list
detections = [d for d in all_detections if d["team"] == "enemy"]
for d in detections:
    process(d)
```

✅ **Correct:**
```python
# Fast: Generator expression, lazy evaluation
for d in (d for d in all_detections if d["team"] == "enemy"):
    process(d)
```

## 🚀 Performance Considerations

### Game Bot Specific
- **Real-time constraints**: Total tick budget ≤15ms for 60+ FPS operation
- Use `asyncio` for concurrent operations (vision processing + decision making)
- Use `numpy` for numerical computations on game coordinates
- Profile with `py-spy` or `cProfile` for bottlenecks
- **Numba JIT**: Use `@numba.jit` for hot numerical loops
- **Cython**: Consider for state reconstruction if <1.5ms proves impossible
- Performance monitoring pattern:
```python
import time
from functools import wraps
import numba

@numba.jit(nopython=True, cache=True)
def compute_trajectories(positions: np.ndarray, velocities: np.ndarray) -> np.ndarray:
    """Ultra-fast trajectory prediction with Numba JIT."""
    # Runs at C speed, no Python overhead
    return positions + velocities * 0.016  # One frame

def timed(threshold_ms: float = 16.0):
    """Decorator to log functions exceeding threshold."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            duration = (time.perf_counter() - start) * 1000
            if duration > threshold_ms:
                logger.warning(f"{func.__name__} took {duration:.2f}ms (>{threshold_ms}ms)")
            return result
        return wrapper
    return decorator
```

### Python Performance Anti-Patterns

❌ **Anti-Pattern: Loop Instead of Vectorization**
```python
# Slow: Python loop (1000x slower)
distances = []
for i in range(len(positions)):
    distances.append(np.linalg.norm(positions[i] - target))
```

✅ **Correct:**
```python
# Fast: Vectorized NumPy (runs in C/Fortran)
distances = np.linalg.norm(positions - target, axis=1)
```

---

❌ **Anti-Pattern: String Concatenation in Loop**
```python
# Slow: O(n²) string copies
report = ""
for line in log_lines:
    report += line + "\n"  # Creates new string each iteration
```

✅ **Correct:**
```python
# Fast: O(n) with list join
report = "\n".join(log_lines)
```

---

❌ **Anti-Pattern: Reading File Line-by-Line Without Generator**
```python
# Memory hog: Loads entire file
lines = open("huge_log.txt").readlines()  # 10GB RAM!
for line in lines:
    process(line)
```

✅ **Correct:**
```python
# Memory efficient: Streams line-by-line
with open("huge_log.txt") as f:
    for line in f:  # Generator, only one line in memory
        process(line)
```

## 📦 Dependency Management

### Poetry Best Practices
- Pin major versions in `pyproject.toml`: `typer = "^0.12.5"`
- Use `poetry update` instead of manually editing versions
- Group dependencies logically:
  - Core runtime: `typer`, `rich`
  - Game processing: `opencv-python`, `numpy`, `pillow`, `torch`, `onnxruntime`
  - Development: `ruff`, `mypy`, `pytest`, `pytest-cov`
  - Optional: `dxcam` (Windows screen capture), `pynput` (input control)

### Adding New Dependencies
```bash
# Runtime dependency
poetry add opencv-python

# Development dependency
poetry add --group dev pytest-mock

# Optional dependency (platform-specific)
poetry add --optional dxcam

# Update pre-commit if adding type stubs
poetry add --group dev types-opencv-python
```

## 🔧 Development Workflow

### Pre-commit Hooks
- **Automatic formatting and linting** on every commit
- Hooks run: ruff format, ruff check, mypy, basic file checks
- If hooks fail, fix issues and re-commit
- Override only in emergencies: `git commit --no-verify`

### Code Review Checklist
- [ ] All functions have type annotations
- [ ] No mypy errors or warnings
- [ ] Tests pass with coverage maintained
- [ ] CLI help text updated for new commands
- [ ] Rich formatting used for user-facing output
- [ ] Logging statements use appropriate levels
- [ ] Performance impact considered for real-time code (≤15ms budget)
- [ ] Memory cleanup handled properly
- [ ] Error handling with specific exceptions

### Debugging
- Use `rich.inspect()` for object exploration
- `logging_setup.py` configures structured logging
- `python -m pdb` for interactive debugging
- Rich tracebacks enabled by default
- Use `logger.debug()` for detailed diagnostics (disabled in production)

### When to Optimize (Decision Tree)

```
Is there a performance issue?
├─ No → Don't optimize (premature optimization is evil)
└─ Yes → Profile with py-spy
    ├─ Bottleneck is I/O? → Use async, caching, or batching
    ├─ Bottleneck is NumPy? → Already optimal (C/Fortran)
    ├─ Bottleneck is Python loop?
    │   ├─ Can vectorize with NumPy? → Do that first
    │   ├─ No vectorization possible? → Try Numba JIT
    │   ├─ Numba fails? → Try Cython
    │   └─ Still too slow? → Consider Rust FFI (see project.md)
    └─ Bottleneck is algorithm? → Improve big-O complexity first
```

---

**Remember**: **Strict typing**, **comprehensive testing**, **real-time performance (≤15ms)**, and **clean architecture** are the four pillars of this Python codebase.

**See also:**
- [project.md](./project.md) for real-time performance budgets and optimization strategies
- [copilot-instructions.md](./copilot-instructions.md) for universal best practices
- [README.md](./README.md) for guidance navigation
