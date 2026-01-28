import re
from pathlib import Path
from queue import Queue
from typing import Final

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.claude_code_sdk.diff_tracker import _get_file_contents_at_commit_hash
from sculptor.agents.default.claude_code_sdk.diff_tracker import _is_file_present_at_commit_hash
from sculptor.agents.default.claude_code_sdk.diff_tracker import create_unified_diff
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment

_FILE_CONTENTS: Final[str] = """def foo() -> None:
    pass"""

_NEW_FILE_CONTENTS: Final[str] = """def foo() -> None:
    print('this is new!')"""

_FILE_PATH: Final[str] = "main.py"


def _create_local_environment_from_local_path(
    path: Path, environment_config: LocalEnvironmentConfig, test_root_concurrency_group: ConcurrencyGroup
) -> LocalEnvironment:
    environment = LocalEnvironment(
        environment_id=LocalEnvironmentID(str(path)),
        config=environment_config,
        project_id=ProjectID(),
        concurrency_group=test_root_concurrency_group,
    )
    return environment


def _setup_repo_in_environment_with_initial_files_commit(environment: LocalEnvironment) -> str:
    environment.write_file(str(environment.get_workspace_path() / _FILE_PATH), _FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "init"])
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-am", "initial commit"])
    _, commit_hash, _ = run_git_command_in_environment(environment=environment, command=["git", "rev-parse", "HEAD"])
    return commit_hash


@pytest.fixture
def environment_config() -> LocalEnvironmentConfig:
    return LocalEnvironmentConfig()


@pytest.fixture
def environment_and_initial_repo_commit_hash(
    tmp_path: Path, environment_config: LocalEnvironmentConfig, test_root_concurrency_group: ConcurrencyGroup
) -> tuple[LocalEnvironment, str]:
    environment = _create_local_environment_from_local_path(tmp_path, environment_config, test_root_concurrency_group)
    initial_repo_commit_hash = _setup_repo_in_environment_with_initial_files_commit(environment=environment).strip()
    return environment, initial_repo_commit_hash


def test_is_file_present_at_commit_hash(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, initial_repo_commit_hash = environment_and_initial_repo_commit_hash
    assert _is_file_present_at_commit_hash(
        environment=environment, commit_hash=initial_repo_commit_hash, relative_file_path=Path(_FILE_PATH)
    )

    assert not _is_file_present_at_commit_hash(
        environment=environment, commit_hash=initial_repo_commit_hash, relative_file_path=Path("does_not_exist.py")
    )


def test_get_file_contents_at_commit_hash(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, initial_repo_commit_hash = environment_and_initial_repo_commit_hash
    file_contents = _get_file_contents_at_commit_hash(
        environment=environment, commit_hash=initial_repo_commit_hash, relative_file_path=Path(_FILE_PATH)
    )
    assert file_contents == _FILE_CONTENTS


def test_diff_tracker_get_file_snapshot(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, _ = environment_and_initial_repo_commit_hash
    diff_tracker = DiffTracker(
        environment=environment,
        output_message_queue=Queue(),
    )
    assert (
        diff_tracker._get_file_snapshot(file_path=str(environment.get_workspace_path() / _FILE_PATH)) == _FILE_CONTENTS
    )
    assert (
        diff_tracker._get_file_snapshot(file_path=str(environment.get_workspace_path() / "does_not_exist.py")) is None
    )


def test_compute_diff_after_edit_to_existing_file(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, initial_repo_commit_hash = environment_and_initial_repo_commit_hash
    diff_tracker = DiffTracker(
        environment=environment,
        output_message_queue=Queue(),
    )
    file_path = str(environment.get_workspace_path() / _FILE_PATH)
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    diff = diff_tracker._compute_diff_for_file_path(file_path=file_path)
    assert diff is not None
    assert (
        "\n".join(diff.splitlines()[2:])
        == f"""--- a{file_path}
+++ b{file_path}
@@ -1,2 +1,2 @@
 def foo() -> None:
-    pass
\\ No newline at end of file
+    print('this is new!')
\\ No newline at end of file"""
    )


def test_compute_diff_after_edit_to_new_file(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, _ = environment_and_initial_repo_commit_hash
    diff_tracker = DiffTracker(
        environment=environment,
        output_message_queue=Queue(),
    )
    file_path = str(environment.get_workspace_path() / "blah.py")
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    diff = diff_tracker._compute_diff_for_file_path(file_path=file_path)
    assert diff is not None
    assert (
        "\n".join(diff.splitlines()[3:])
        == f"""--- /dev/null
+++ b{file_path}
@@ -0,0 +1,2 @@
+def foo() -> None:
+    print('this is new!')
\\ No newline at end of file"""
    )


def test_attempt_to_compute_diff_for_non_existent_file(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    environment, _ = environment_and_initial_repo_commit_hash
    diff_tracker = DiffTracker(
        environment=environment,
        output_message_queue=Queue(),
    )
    file_path = str(environment.get_workspace_path() / "does_not_exist.py")
    assert diff_tracker._compute_diff_for_file_path(file_path=file_path) is None


def normalize_diff(diff: str | None) -> str | None:
    """Normalize git diff output by replacing index hashes with zeros."""
    if not diff:
        return diff

    # Replace index hashes with normalized format
    # Matches patterns like "index 6ad36e52f0..2c3562bdb8 100644"
    # and replaces with "index 0000000..0000000 100644"
    diff = re.sub(r"index [0-9a-f]+\.\.[0-9a-f]+", "index 0000000..0000000", diff)

    # For new files: "index 0000000000..b47864eced" -> "index 0000000..0000000"
    diff = re.sub(r"index [0-9a-f]+\.\.[0-9a-f]+", "index 0000000..0000000", diff)

    return diff


def test_no_change_returns_empty_string(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test that identical content returns empty string."""
    assert create_unified_diff("test.txt", "hello world", "hello world", test_root_concurrency_group) == ""
    assert create_unified_diff("test.bin", b"hello world", b"hello world", test_root_concurrency_group) == ""


def test_regular_text_diff(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff for modified text file."""
    old_content = "Line 1\nLine 2\nLine 3\n"
    new_content = "Line 1\nLine 2 modified\nLine 3\nLine 4\n"

    result = create_unified_diff("test.txt", old_content, new_content, test_root_concurrency_group)
    result = normalize_diff(result)

    expected = """diff --git a/test.txt b/test.txt
index 0000000..0000000 100644
--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,4 @@
 Line 1
-Line 2
+Line 2 modified
 Line 3
+Line 4
"""
    assert result == expected


def test_file_creation(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff for newly created file."""
    result = create_unified_diff("new_file.txt", None, "Hello, world!\nThis is new.\n", test_root_concurrency_group)
    result = normalize_diff(result)

    expected = """diff --git a/new_file.txt b/new_file.txt
new file mode 100644
index 0000000..0000000
--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1,2 @@
+Hello, world!
+This is new.
"""
    assert result == expected


def test_no_newline_at_end_of_file(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test handling files without newline at end."""
    # Adding newline to file without one
    result = create_unified_diff(
        "no_newline.txt", "Line without newline", "Line without newline\n", test_root_concurrency_group
    )
    result = normalize_diff(result)

    expected = """diff --git a/no_newline.txt b/no_newline.txt
index 0000000..0000000 100644
--- a/no_newline.txt
+++ b/no_newline.txt
@@ -1 +1 @@
-Line without newline
\\ No newline at end of file
+Line without newline
"""
    assert result == expected


def test_binary_file_modification(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff for binary files shows binary diff marker."""
    old_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    new_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02"

    result = create_unified_diff("image.png", old_content, new_content, test_root_concurrency_group)

    assert result is not None
    # Binary files produce different output depending on git version
    assert "Binary files a/image.png and b/image.png differ" in result or "GIT binary patch" in result


def test_empty_file_to_content(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff from empty file to file with content."""
    result = create_unified_diff("empty.txt", "", "Now has content\n", test_root_concurrency_group)
    result = normalize_diff(result)

    expected = """diff --git a/empty.txt b/empty.txt
index 0000000..0000000 100644
--- a/empty.txt
+++ b/empty.txt
@@ -0,0 +1 @@
+Now has content
"""
    assert result == expected


def test_unicode_content(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff with unicode content."""
    old_content = "Hello 世界\n"
    new_content = "Hello 世界! 🎉\n"

    result = create_unified_diff("unicode.txt", old_content, new_content, test_root_concurrency_group)
    result = normalize_diff(result)

    expected = """diff --git a/unicode.txt b/unicode.txt
index 0000000..0000000 100644
--- a/unicode.txt
+++ b/unicode.txt
@@ -1 +1 @@
-Hello 世界
+Hello 世界! 🎉
"""
    assert result == expected


def test_nested_file_path(test_root_concurrency_group: ConcurrencyGroup) -> None:
    """Test diff with nested directory structure in filepath."""
    result = create_unified_diff(
        "src/components/Button.tsx",
        "export const Button = () => <button>Click</button>\n",
        "export const Button = () => <button>Click me!</button>\n",
        test_root_concurrency_group,
    )
    result = normalize_diff(result)

    expected = """diff --git a/src/components/Button.tsx b/src/components/Button.tsx
index 0000000..0000000 100644
--- a/src/components/Button.tsx
+++ b/src/components/Button.tsx
@@ -1 +1 @@
-export const Button = () => <button>Click</button>
+export const Button = () => <button>Click me!</button>
"""
    assert result == expected
