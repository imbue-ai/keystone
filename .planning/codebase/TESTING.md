# Testing Patterns

**Analysis Date:** 2026-03-09

## Test Framework

**Runner:**
- Pytest 8.0.0+
- Config: `pytest.ini` (root config registers markers for all subprojects)
- Parallel execution enabled by default: `addopts = -n auto` (via pytest-xdist 3.0+)

**Assertion Library:**
- Built-in `assert` statements (standard pytest)
- Rich assertion introspection provided by pytest

**Run Commands:**
```bash
pytest                                    # Run all tests with parallel workers
pytest -n 0                               # Run sequentially (no parallel)
pytest -m "not modal"                     # Exclude Modal tests
pytest -m "not agentic"                   # Exclude agent tests (non-deterministic)
pytest -k "test_guardrail"                # Run tests matching pattern
pytest --log-cli-level=DEBUG              # Show DEBUG logs in console
pytest --cov keystone --cov evals         # Run with coverage
```

## Test File Organization

**Location:**
- Co-located with source code in `tests/` directories
- Keystone tests: `keystone/tests/test_*.py` (separate from source)
- Evals tests: `evals/test_*.py` (same directory as evals modules)

**Naming:**
- Test files: `test_<module>.py` (matches source module name)
- Test functions: `test_<functionality>()` (descriptive, not numbered)
- Test classes: `Test<ClassName>` (optional, for organizing related tests)

**Structure:**
```
keystone/
├── src/keystone/
│   ├── agent_runner.py
│   ├── agent_log.py
│   └── ...
└── tests/
    ├── conftest.py              # Shared fixtures (init_git_repo, project_root, etc.)
    ├── test_agent_runner.py
    ├── test_agent_log.py
    ├── test_guardrail.py
    └── fixtures/                # Static test data

evals/
├── eval_schema.py
├── eval_cli.py
├── conftest.py                  # Logger suppression for evals
├── test_eval_flow.py
├── test_config_parsing.py
└── test_repos_jsonl.py
```

## Test Structure

**Suite Organization:**

Test suites can use standalone functions or class-based organization (no strict pattern, but class-based is common for grouped related tests).

**Standalone function pattern** (used in `test_guardrail.py`, `test_eval_flow.py`):
```python
def test_guardrail_fails_with_no_devcontainer(workspace: Path) -> None:
    """Guardrail should fail when no .devcontainer directory exists."""
    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "FAIL" in result.stdout
    assert ".devcontainer/ directory is MISSING" in result.stdout
```

**Class-based pattern** (used in `test_llm_provider.py`):
```python
class TestClaudeProvider:
    def setup_method(self) -> None:
        """Run before each test method in this class."""
        self.provider = ClaudeProvider()

    def test_name_and_default_cmd(self) -> None:
        assert self.provider.name == "claude"
        assert self.provider.default_cmd == "claude"

    def test_build_command(self) -> None:
        cmd = self.provider.build_command("Fix the bug", 5.0, "claude")
        assert cmd[0] == "claude"
```

**Patterns:**
- Setup: Use pytest fixtures (preferred) or `setup_method()` in classes
- Teardown: Use pytest fixture context managers or `teardown_method()` in classes
- Assertions: Plain `assert` statements with optional messages

## Mocking

**Framework:** Manual mocking patterns (no unittest.mock used)

**Patterns:**

Avoid over-mocking. Real calls and file I/O are preferred when:
- Testing integration (e.g., subprocess calls in `test_guardrail.py`)
- Using temporary directories (safe with `tmp_path` fixture)
- Testing config parsing with real JSON files

Mocking is used for:
- Environment variables: `monkeypatch` fixture from pytest
- LLM provider output parsing: Real JSON strings in test data

**Example from codebase:**
```python
def test_env_vars_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test provider behavior when env var is not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert self.provider.env_vars() == {}
```

**What to Mock:**
- Environment variables (use `monkeypatch`)
- External API responses (parse real JSON instead of mocking)
- LLM output: Use actual JSON formatted strings from real agent output

**What NOT to Mock:**
- Subprocess calls (let tests use real git, docker if needed)
- File system operations (use `tmp_path` fixture)
- Config file parsing (use real config files from `examples/` and `test_data/`)

## Fixtures and Factories

**Test Data:**

Custom fixtures are defined in `conftest.py` files and reused across tests.

**Git repository fixture** (`keystone/tests/conftest.py`):
```python
@pytest.fixture
def project_root(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Create a temporary copy of a sample project initialized as a git repo.

    Use with indirect parametrization to specify the sample name:
        @pytest.mark.parametrize("project_root", ["python_project"], indirect=True)
    """
    sample_name = getattr(request, "param", "python_project")
    original_project_root = SAMPLES_DIR / sample_name
    project_dir = tmp_path / "project"
    shutil.copytree(original_project_root, project_dir)
    init_git_repo(project_dir)
    return project_dir
```

**Temporary database fixture** (`keystone/tests/test_agent_log.py`):
```python
@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.sqlite"
```

**Sample data fixture** (`evals/test_eval_flow.py`):
```python
@pytest.fixture
def sample_repos(tmp_path: Path) -> tuple[Path, list[str]]:
    """Create git repos from samples and return (repo_list_path, repo_paths)."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_paths: list[str] = []

    for sample_name in ["python_project", "go_project"]:
        src = SAMPLES_DIR / sample_name
        if not src.exists():
            pytest.skip(f"Sample not found: {src}")
        dest = repos_dir / sample_name
        shutil.copytree(src, dest)
        init_git_repo(dest)
        repo_paths.append(str(dest))

    return repo_list_path, repo_paths
```

**Location:**
- Fixtures shared across tests: `conftest.py` (fixtures are auto-discovered)
- Sample projects: `samples/` directory (python_project, go_project, go_project)
- Test data: `evals/test_data/` and `evals/examples/`
- Reusable helpers: `conftest.py` includes helper functions like `init_git_repo()` and `parse_bootstrap_result()`

## Coverage

**Requirements:** No explicit coverage target enforced (not configured in pytest.ini)

**View Coverage:**
```bash
pytest --cov keystone --cov evals --cov-report html
# Opens htmlcov/index.html
```

## Test Markers

Pytest markers defined in `pytest.ini` allow selective test execution:

```ini
markers =
    manual: marks tests as manual (deselect with '-k not manual')
    modal: marks tests that run on Modal (deterministic, deselect with '-m "not modal"')
    local_docker: marks tests that expect a local Docker daemon (deselect with '-m "not local_docker"')
    agentic: marks tests that run a real coding agent (non-deterministic, deselect with '-m "not agentic"')
```

**Usage:**
```python
@pytest.mark.modal
def test_eval_flow_fake_agent(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    """Test the eval flow with fake agent on Modal (no LLM)."""
    # ...

@pytest.mark.modal
@pytest.mark.agentic
def test_eval_flow_claude_on_modal(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    """End-to-end test with real Claude agent on Modal."""
    # ...
```

**Deselection:**
```bash
pytest -m "not modal"                     # Skip Modal tests
pytest -m "not agentic"                   # Skip non-deterministic agent tests
pytest -m "local_docker"                  # Run only Docker tests
```

## Test Types

**Unit Tests:**
- Scope: Single module/function in isolation
- Examples: `test_guardrail.py` (each test validates one guardrail check), `test_llm_provider.py` (parse_stdout_line methods)
- Approach: Use fixtures for setup, plain assertions, no external dependencies
- No network calls, no real agent execution

**Integration Tests:**
- Scope: Multiple modules working together
- Examples: `test_agent_log.py` (AgentLog with temporary SQLite database), `test_config_parsing.py` (parsing real config files)
- Approach: Use real file systems and databases, subprocess calls to git/docker
- Temporary directories via `tmp_path` fixture ensure cleanup

**E2E Tests:**
- Scope: Full flow from config to result (agent running on Modal or locally)
- Framework: Pytest with Modal markers
- Examples: `test_eval_flow.py` with markers `@pytest.mark.modal` and `@pytest.mark.agentic`
- Approach: Run actual agent process, verify complete output structure
- Marked to allow skipping in CI if resources unavailable

## Common Patterns

**Async Testing:**

No async tests in current codebase. Functions use subprocess for concurrency (e.g., Prefect tasks), not async/await.

**Error Testing:**

Use `pytest.raises()` context manager to verify exceptions:
```python
def test_invalid_config():
    """Test that invalid config raises appropriate error."""
    with pytest.raises(ValueError, match="Invalid field"):
        EvalConfig.model_validate({"invalid": "data"})
```

**Parametrized Tests:**

Use `@pytest.mark.parametrize()` to test multiple inputs:
```python
@pytest.mark.parametrize("config_file", CONFIG_FILES, ids=lambda p: str(p.relative_to(EVALS_DIR)))
def test_config_file_parses(config_file: Path) -> None:
    """Each config file should parse as a valid EvalRunConfig."""
    config = EvalRunConfig.model_validate(json5.loads(config_file.read_text()))
    assert len(config.configs) > 0
```

**Subprocess Testing:**

Run real subprocesses (git, docker, bash) in tests with `subprocess.run()`:
```python
def _run_guardrail(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run guardrail.sh in the given workspace."""
    return subprocess.run(
        ["bash", str(workspace / "guardrail.sh")],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
```

**Test Output and Debugging:**

Use print statements in tests—pytest captures and displays on failure:
```python
def test_eval_flow_fake_agent(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    # ...
    success_count = sum(1 for r in output.results if r.success)
    print(f"\nTotal: {success_count}/{len(output.results)} succeeded")
```

Run with `pytest -s` to see print output even on success.

## Pre-commit Hooks

**Hooks defined in `.pre-commit-config.yaml`:**

1. **nbstripout** — Remove notebook output before committing
2. **ruff** — Format and lint Python code (`ruff --fix`, `ruff-format`)
3. **pyright** — Type checking via `./scripts/typecheck`
4. **pytest-discovery** — Validate test discovery via `./scripts/pytest-discovery`

**These hooks run automatically on `git commit`** to enforce quality standards before changes are committed.

---

*Testing analysis: 2026-03-09*
