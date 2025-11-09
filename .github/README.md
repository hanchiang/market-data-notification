# 🤖 Copilot Instructions & Project Guidance

This directory contains AI-assisted development guidelines for the **market-data-notification-backend** project.

## 📚 Documentation Structure

### 🌍 [copilot-instructions.md](./copilot-instructions.md)
**Universal software engineering best practices** - Apply to ANY project, language, or domain.

Use this when asking about:
- Architecture patterns (SOLID, design patterns)
- Code quality standards (naming, structure, comments)
- Testing strategies (unit, integration, coverage)
- Error handling and validation
- Security best practices
- Version control and collaboration

**Example prompts:**
- "How should I structure error handling for this module?"
- "What's the best way to test this integration?"
- "Review this code for security vulnerabilities"

---

### 🐍 [python.md](./python.md)
**Python-specific best practices** - Type hints, performance, tooling, idioms.

Use this when asking about:
- Python type annotations (mypy strict mode)
- Performance optimization (NumPy, Numba, Cython)
- Poetry dependency management
- Async patterns with asyncio
- Testing with pytest
- Profiling with py-spy

**Example prompts:**
- "How should I type-annotate this detection function?"
- "Optimize this NumPy loop for better performance"
- "Add proper error handling to this CLI command"
- "What's the best way to structure this async pipeline?"

---

## 🔍 Quick Reference

### When to consult which file:

```
Question about...                     → Check file
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
General code quality/testing          → copilot-instructions.md
Python syntax/type hints/tooling      → python.md
Git workflow/CI/CD                    → copilot-instructions.md
```

### Precedence Rules

When guidelines conflict:
2. **Language-specific patterns** (python.md) override universal guidelines
3. **Performance requirements** (≤15ms) override readability preferences

---

## 🎯 AI-Assisted Development Workflow

### 1️⃣ **Planning Phase**
- Consult `project.md` for phase requirements
- Check KPIs and acceptance criteria
- Review dependencies and risks

### 2️⃣ **Implementation Phase**
- Follow `python.md` for code structure
- Use `copilot-instructions.md` for design patterns
- Apply `project.md` for performance budgets

### 3️⃣ **Review Phase**
- Validate against `project.md` acceptance criteria
- Check `python.md` pre-commit checklist
- Ensure `copilot-instructions.md` best practices

---

## ⚙️ How AI Reads These Files

GitHub Copilot and Claude automatically load these files when you:
- Open a Python file (`python.md` applies via `applyTo: "**/*.py"`)
- Ask project-specific questions (`project.md` via `applyTo: "**/*"`)
- Request code reviews (all files are considered)

**Pro tips:**
- Start prompts with `#file:project.md` to explicitly reference a guidance file
- Use `#codebase` to include full project context
- Reference specific phases: "Implement Phase 5 micro simulator per project.md"

---

## 🚀 Getting Started

### For New Contributors:
1. Read `copilot-instructions.md` for universal patterns
2. Read `project.md` sections 🎮 Project Context and 🏗️ Architecture
3. Skim `python.md` for Python-specific conventions
4. Review current phase status in `project.md`

### For AI Coding Assistants:
- **Always** check latency budgets before suggesting solutions
- **Prioritize** type safety (mypy strict mode)
- **Consider** real-time constraints (≤15ms total tick)
- **Validate** against phase acceptance criteria

---

## 📝 Maintenance

These files should be updated when:
- [ ] New phases are completed (update `project.md` status)
- [ ] Architecture changes (update `project.md` and `python.md`)
- [ ] New patterns emerge (add to relevant file)
- [ ] Performance budgets change (update `project.md`)
- [ ] Dependencies added/removed (update `python.md`)

Last updated: 2024-01-XX (update this after major changes)

---

*For the full project plan, see [project.md](./project.md)*
