# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push origin HEAD:main  # Push current branch to origin/main
   git status  # MUST show "up to date with origin"
   ```
   **Note:** You're typically on a worktree branch (e.g., `agent-foo-1234`) that tracks `origin/main`. 
   Plain `git push` won't work. Use `git push origin HEAD:main` to push your branch to remote main.
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
- **Always use merge, never rebase** - Preserve commit history with merge commits
- **Never amend or force-push commits already pushed** - Add new fix commits instead

## Code Style

- **No inline imports** - Put all imports at the top of the file, not inside functions
- **Always use type annotations** - Add Python type annotations to all function parameters and return values
- **Use uv for running Python** - Run tests with `uv run pytest`, not `python -m pytest`

## Linting & Type Checking

This project uses **ruff** (linter/formatter) and **pyright** (type checker). Pre-commit hooks run these automatically.

```bash
uv run ruff check .           # Lint
uv run ruff check . --fix     # Auto-fix lint issues
uv run ruff format .          # Format code
uv run pyright                # Type check
```

To install pre-commit hooks after cloning:
```bash
uv sync
uv run pre-commit install
```
