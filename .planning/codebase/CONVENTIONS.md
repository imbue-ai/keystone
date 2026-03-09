# Coding Conventions

**Analysis Date:** 2026-03-09

## Naming Patterns

**Files:**
- Modules use lowercase with underscores: `agent_runner.py`, `eval_schema.py`, `llm_provider.py`
- Test files follow pattern: `test_<module>.py` (e.g., `test_guardrail.py`, `test_llm_provider.py`)
- Private modules prefixed with underscore when used internally within packages

**Functions:**
- Use snake_case for all function names: `init_git_repo()`, `parse_bootstrap_result()`, `_run_git()`
- Private/internal functions prefixed with single underscore: `_run_git()`, `_s3_exists()`, `_check_docker_available()`
- Async functions use same naming: `async def run()`, no special prefix

**Variables:**
- Local variables and parameters use snake_case: `project_root`, `repo_paths`, `agent_config`, `cache_key`
- Constants use UPPER_SNAKE_CASE: `DEFAULT_AGENT_TIMEOUT`, `TIMEOUT_EXIT_CODE`, `SAMPLES_DIR`
- Private module variables use underscore prefix: `_NOISY_LOGGERS`, `_exit_code`, `_work_dir`
- Collection types use plural: `repo_paths`, `outputs`, `results`, `events`

**Types:**
- Classes use PascalCase: `AgentRunner`, `LocalAgentRunner`, `RepoEntry`, `EvalConfig`, `BootstrapResult`
- Type hints use union syntax with pipe: `str | None`, `int | None`, `dict[str, Any]`
- Enum classes use PascalCase and UPPER_SNAKE_CASE values: `StreamType.STDOUT`, `LLMModel.OPUS`

**Pydantic Models:**
- Model names use PascalCase and suffixed based on purpose: `EvalConfig` (configuration), `EvalResult` (output), `KeystoneRepoResult` (result)
- Field documentation uses `Field(..., description="...")` for clarity
- Type hints are strict: `str`, `int`, `bool`, with explicit `| None` for optional fields

## Code Style

**Formatting:**
- Line length: 100 characters (configured in `pyproject.toml` `tool.ruff`)
- Indentation: 4 spaces (Python standard)
- String quotes: Double quotes for consistency (ruff-format enforced)

**Linting:**
- Tool: Ruff 0.9.0+ (via pre-commit and pyproject.toml)
- Enabled rules: E (errors), W (warnings), F (pyflakes), I (isort), B (flake8-bugbear), C4 (comprehensions), UP (pyupgrade), ARG (unused args), SIM (simplify), TCH (type-checking), PTH (pathlib), PLC (pylint conventions), RUF (ruff-specific)
- Ignored: E501 (line too long—handled by formatter), B008 (function call in default arg—standard typer pattern), G004 (f-string in logging—cleaner than % formatting)

**Type Checking:**
- Tool: Pyright (config in `pyrightconfig.json`, mode: `basic`)
- Python version: 3.12+
- All public functions and classes should have type hints
- Return types should be explicit (use `-> None` for functions with no return)
- Use `from __future__ import annotations` for forward references in type definitions

## Import Organization

**Order:**
1. `from __future__ import annotations` (if needed, for forward refs)
2. Standard library imports (abc, io, os, pathlib, subprocess, tempfile, etc.)
3. Third-party imports (pydantic, prefect, fsspec, pytest, typer, rich)
4. Local/relative imports (keystone, evals modules)
5. Type imports inside `if TYPE_CHECKING:` block (only needed for circular refs)

**Path Aliases:**
- First-party modules: `keystone`, `evals` (configured in `pyproject.toml` `tool.ruff.lint.isort.known-first-party`)
- Imports from subpackages use full module path: `from keystone.agent_log import AgentLog`, `from keystone.llm_provider import AgentProvider`
- Avoid relative imports; use absolute imports for clarity

**Examples:**
```python
from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

import fsspec
from pydantic import BaseModel, Field

from keystone.schema import BootstrapResult
from keystone.agent_log import AgentLog
```

## Error Handling

**Patterns:**
- Raise specific exceptions rather than catching broadly: `raise GitError(f"Failed to initialize git repo: {e.stderr}") from e`
- Custom exceptions inherit from `Exception` and are defined at module/package level (e.g., `GitError` in `conftest.py`)
- Use `check=False` with subprocess to handle non-zero exits explicitly; use `check=True` (default) to auto-raise
- Catch specific exceptions: `except subprocess.CalledProcessError as e:` not bare `except:`
- Function docstrings document what exceptions are raised when relevant

**Assertions in Tests:**
- Use `assert condition` for simple checks
- Use `assert condition, "message"` for context when assertion fails
- Use `pytest.skip(reason)` to skip tests when preconditions aren't met
- Use `pytest.raises(ExceptionType)` context manager to verify exceptions are raised

**Error Messages:**
- Include relevant context (file paths, git hashes, repo IDs): `f"Missing result file for {result.repo_entry.id}"`
- Use f-strings in error messages and logging (despite "no f-strings" being common Python advice, ruff ignores G004)

## Logging

**Framework:** Standard library `logging` module

**Patterns:**
- Get logger at module level: `logger = logging.getLogger(__name__)` (not inside functions)
- Use logging levels appropriately:
  - `logger.debug()`: Detailed information for diagnosing issues (e.g., subprocess output)
  - `logger.info()`: Informational messages (e.g., "Starting eval flow", "Repo cloned successfully")
  - `logger.warning()`: Warning messages for unexpected but recoverable conditions
  - `logger.error()`: Error messages when operations fail
- Use f-strings in log messages (G004 is ignored in ruff config)

**Examples from codebase:**
```python
logger = logging.getLogger(__name__)

logger.debug(f"Running command: {' '.join(cmd)}")
logger.info(f"Processing repo {repo_id}")
logger.error(f"Agent exited with code {exit_code}")
```

**Configuration:**
- Root logger configured in CLI modules (e.g., `eval_cli.py`) with `logging.basicConfig()`
- Third-party loggers suppressed in conftest.py to reduce noise in test output
- INFO level for project modules, WARNING for third-party (e.g., docker, httpx, grpc)

## Comments

**When to Comment:**
- Comments explain WHY, not WHAT (code should be clear about what it does)
- Use for non-obvious algorithmic choices or workarounds: `# Use file:// URIs for S3 prefixes so tests don't need real AWS credentials`
- Use for temporary/experimental code: Comments with "HACK", "TODO", "FIXME" tracked in `TODO.md`
- Use for documenting complex logic or integration points

**Docstrings:**
- All public functions and classes have docstrings
- Use triple double-quotes `"""` for docstrings
- Docstring format: One-line summary (optional blank line if more detail), then paragraphs
- For functions, document Args, Returns, and Raises if relevant (using Google style format)

**Examples:**
```python
def init_git_repo(path: Path, add_all: bool = True, commit: bool = True) -> None:
    """Initialize a git repository and optionally add/commit all files.

    This is useful for tests that need a git repo from a non-git directory.
    Uses config that doesn't depend on global git settings.
    """

class AgentRunner(ABC):
    """Abstract base class for running the keystone agent.

    Subclasses implement specific execution environments (local subprocess or Modal).
    """
```

## Function Design

**Size:** Functions should be focused and concise
- Most functions: 10-50 lines
- Complex logic split into helpers with descriptive names: `_run_git()`, `_check_docker_available()`
- Private helpers use underscore prefix and are documented with docstrings

**Parameters:**
- Positional args first, then keyword args
- Use type hints on all parameters
- Long parameter lists (5+) use Pydantic models instead of individual args
- Default arguments use simple/hashable values (not function calls except Field defaults)

**Return Values:**
- Always include return type hint (use `-> None` for no return)
- Return single values or Pydantic models for complex data
- Use `tuple[Type1, Type2]` for multiple return values when semantically related
- Generators use `Iterator[Type]` and yield values

**Examples:**
```python
def _get_git_info() -> tuple[str, bool]:
    """Return (commit_hash, is_dirty) for the current repo."""
    # ...
    return commit_hash, is_dirty

def run(self, prompt: str, project_archive: bytes) -> Iterator[StreamEvent]:
    """Run the agent and yield output events."""
    # ...
    yield StreamEvent(stream=StreamType.STDOUT, line=line)

def parse_bootstrap_result(stdout: str) -> BootstrapResult:
    """Extract and parse the JSON BootstrapResult from CLI stdout."""
    # ...
    return BootstrapResult.model_validate_json(json_str)
```

## Module Design

**Exports:**
- Modules explicitly export public interfaces via docstrings and type hints
- No `__all__` variable used (rely on convention: no underscore prefix = public)
- Private/implementation details use underscore prefix: `_run_git()`, `_s3_exists()`

**Barrel Files:**
- Used in `keystone/llm_provider/__init__.py` to re-export provider classes and event types:
  ```python
  from keystone.llm_provider.base import AgentProvider, AgentEvent, AgentTextEvent, ...
  __all__ = [...]
  ```
- Simplifies imports: `from keystone.llm_provider import AgentProvider` instead of long path

**Organization:**
- Group related functions and classes in single modules (e.g., all S3 utilities in one file)
- Use docstrings to separate logical sections within files: `# ── Section Name ─────────`
- Abstract base classes in base.py; concrete implementations in separate modules (e.g., `claude.py`, `codex.py`)

**Examples:**
- `keystone/llm_provider/base.py`: Base types and abstract `AgentProvider`
- `keystone/llm_provider/claude.py`: `ClaudeProvider` implementation
- `keystone/agent_runner.py`: Abstract `AgentRunner` + `LocalAgentRunner` + helper functions

---

*Convention analysis: 2026-03-09*
