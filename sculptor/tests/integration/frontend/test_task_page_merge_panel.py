import re
from pathlib import Path

from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.elements.merge_panel import PlaywrightMergePanel
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import select_branch
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.playwright_utils import start_task_and_wait_for_ready
from sculptor.testing.user_stories import user_story


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_shows_current_branch(sculptor_page_: PlaywrightHomePage):
    """Test that the merge panel shows the current branch and agent's branch in the selector."""
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="just say hi, nothing else",
        wait_for_agent_to_finish=False,
    )

    # Get the agent's branch name
    agent_branch = task_page.get_branch_name()

    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.get_target_branch_selector().click()

    options = panel.get_target_branch_options()

    # Check that "current" badge appears (for the currently checked out local branch)
    current = options.filter(has_text="current")
    expect(current).to_have_count(1)

    # Check that the agent's branch is available as an option
    agent_branch_option = options.filter(has_text=agent_branch)
    expect(agent_branch_option).to_be_visible()

    # Check that "agent's mirror" badge appears for the agent branch
    agents_mirror = options.filter(has_text="agent's mirror")
    expect(agents_mirror).to_have_count(1)


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_resets_branches_on_task_switch(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    home_page = sculptor_page_

    starter = home_page.get_task_starter()

    banana = "banana-branch"
    orange = "orange-branch"

    pure_local_repo_.get_current_branch_name()
    pure_local_repo_.create_reset_and_checkout_branch(banana)
    pure_local_repo_.write_file("content.txt", "BANANA")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add local file: banana")

    pure_local_repo_.create_reset_and_checkout_branch(orange)
    pure_local_repo_.write_file("content.txt", "ORANGE")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add local file: orange")

    create_task(starter, "just say banana, nothing else", branch_name=banana)
    create_task(starter, "just say orange, nothing else", branch_name=orange)

    wait_for_tasks_to_build(home_page.get_task_list(), expected_num_tasks=2)

    task_page = navigate_to_task_page(home_page.get_task_list().get_tasks().first)
    task_1_branch = task_page.get_branch_name()
    task_1_source = task_page.get_source_branch_name()
    assert task_1_source in (banana, orange), "wrong branch as a starting point of the task"

    merge_panel = task_page.get_task_header().open_and_get_merge_panel_content()
    expect(merge_panel.get_target_branch_selector(), "to be the matching source branch").to_have_text(task_1_source)

    # select the agent's mirror branch on task 1
    merge_panel.select_target_branch(branch_badge="agent's mirror")
    expect(merge_panel.get_target_branch_selector(), "branch switched correctly").to_have_text(task_1_branch)

    # when switching to the second task, the target branch should no longer be pointing at task 1
    task_page = navigate_to_task_page(task_page.get_task_list().get_tasks().last)
    expect(
        task_page.get_branch_name_element(), "test sanity problem: the two tasks should have different branch names"
    ).not_to_have_text(task_1_branch)

    task_2_branch = task_page.get_branch_name()
    task_2_source = task_page.get_source_branch_name()
    assert task_1_branch != task_2_branch, "test sanity problem: the two tasks should have different branch names"
    assert task_2_source in (banana, orange), "wrong branch as a starting point of the second task"
    assert task_2_source != task_1_source, "the second task should have a different source branch"

    merge_panel = task_page.get_task_header().open_and_get_merge_panel_content()
    expect(merge_panel.get_target_branch_selector(), "to be reset to second task's source").to_have_text(task_2_source)

    # when switching to the first task again, the target branch should again be pointing at matching branch
    task_page = navigate_to_task_page(home_page.get_task_list().get_tasks().first)
    expect(task_page.get_branch_name_element(), "we didn't navigate to the first task like expected").to_have_text(
        task_1_branch
    )
    assert task_page.get_source_branch_name() == task_1_source, (
        "we didn't navigate to the first task like expected (source mismatch)"
    )

    merge_panel = task_page.get_task_header().open_and_get_merge_panel_content()
    expect(merge_panel.get_target_branch_selector(), "merge panel local target updated again").to_have_text(
        task_1_source
    )


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_push_changes_to_agent(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    """Test pushing local changes to the agent via the merge panel."""
    # Start a task and wait for it to complete with a commit
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a README.md file with '# Project' as content and commit it with message 'Initial README'",
        wait_for_agent_to_finish=False,
    )

    chat_panel = task_page.get_chat_panel()
    expect(chat_panel).to_have_attribute("data-is-streaming", "false")

    # Get the branch name for later use
    branch_name = task_page.get_branch_name()

    # Make a local change in the same branch
    pure_local_repo_.checkout_branch(branch_name)
    pure_local_repo_.write_file("local_file.txt", "This is a local change")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add local file")

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()

    # Click the push button to push changes to agent
    push_button = panel.get_push_button()
    push_button.click()

    # Wait for the operation to complete by checking the notices
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)

    # Verify success message appears
    expect(notices).to_contain_text("Finished successfully")

    # TODO: expect changes panel to contain local changes


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_fetch_to_different_branch(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    """Test fetching agent's changes to a different (non-current) branch."""
    # Start a task and let the agent create and commit a file
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a file called agent_work.py with 'print(\"Agent was here\")' and commit it with message 'Add agent_work.py'",
        wait_for_agent_to_finish=True,
    )

    # Get the agent's branch name
    agent_branch = task_page.get_branch_name()

    feature_branch_name = "my-feature-branch"
    assert agent_branch != feature_branch_name, "TEST ERROR: unexpected collision"
    # Create a new branch from current position
    pure_local_repo_.repo.run_git(("checkout", "-b", feature_branch_name))
    assert pure_local_repo_.get_current_branch_name() == feature_branch_name, "TEST ERROR: switch failed"

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()

    # Select the agent's branch as target using the helper
    panel.select_target_branch(branch_name=agent_branch)

    # The button should show "Fetch" since we're not on the current branch
    pull_fetch_button = panel.get_pull_or_fetch_button()
    expect(pull_fetch_button).to_contain_text("Fetch")

    # Click to fetch the agent's changes
    pull_fetch_button.click()

    # Wait for operation to complete
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)
    expect(notices).to_contain_text("Finished successfully")

    # Verify the branch was fetched but not checked out
    assert pure_local_repo_.get_current_branch_name() == feature_branch_name, (
        f"Should still be on the {feature_branch_name}"
    )

    # Verify the agent's branch is available locally
    assert agent_branch in pure_local_repo_.get_branches(), f"Agent branch {agent_branch} should be available"

    # Now switch to the fetched branch to verify the file exists
    pure_local_repo_.checkout_branch(agent_branch)
    assert (pure_local_repo_.base_path / "agent_work.py").exists(), "agent_work.py should exist in fetched branch"

    # Verify the file content
    content = (pure_local_repo_.base_path / "agent_work.py").read_text()
    assert "Agent was here" in content, "File should contain expected content"


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_pull_fast_forward(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    """Test pulling agent's changes to current branch (fast-forward merge)."""
    # Start a task and let the agent create and commit files
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt=(
            "Create two files: "
            + "1. main.py with 'def main(): print(\"Hello\")'"
            + "2. utils.py with 'def helper(): return 42'"
            + "Then commit both files with message 'Add main and utils modules'"
        ),
        wait_for_agent_to_finish=True,
    )

    # Get the branch name
    branch_name = task_page.get_branch_name()

    # Checkout the same branch locally (it should exist but be behind)
    pure_local_repo_.checkout_branch(branch_name)

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()

    # Explicitly select the agent's branch (even though it might be default)
    panel.select_target_branch(branch_name=branch_name)

    # Verify the selector shows the selected branch
    branch_selector = panel.get_target_branch_selector()
    expect(branch_selector).to_contain_text(branch_name)

    # The button should show "Pull" since we're on the current branch
    pull_fetch_button = panel.get_pull_or_fetch_button()
    expect(pull_fetch_button).to_contain_text("Pull")

    # Click to pull the agent's changes
    pull_fetch_button.click()

    # Wait for operation to complete
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)
    expect(notices).to_contain_text("Finished successfully")

    # Verify the files from agent are now in the local repo
    assert (pure_local_repo_.base_path / "main.py").exists(), "main.py should exist after pull"
    assert (pure_local_repo_.base_path / "utils.py").exists(), "utils.py should exist after pull"

    # Verify file contents
    main_content = (pure_local_repo_.base_path / "main.py").read_text()
    assert "def main()" in main_content, "main.py should contain main function"
    assert "Hello" in main_content, "main.py should contain Hello"

    utils_content = (pure_local_repo_.base_path / "utils.py").read_text()
    assert "def helper()" in utils_content, "utils.py should contain helper function"
    assert "42" in utils_content, "utils.py should return 42"

    # Verify we're still on the same branch
    current_branch = pure_local_repo_.repo.run_git(("branch", "--show-current")).strip()
    assert current_branch == branch_name, f"Should still be on {branch_name}"


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dialog_agent_uncommitted_changes_ignore(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    """Test that the sync dialog appears when agent has uncommitted changes and user can choose to ignore them."""
    # Start a task and let the agent create a file but leave it uncommitted in the agent's repo
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a file called draft.py with 'print(\"draft\")' but don't commit it yet",
        wait_for_agent_to_finish=True,
    )

    # Get the branch name
    branch_name = task_page.get_branch_name()

    # Checkout the same branch locally
    pure_local_repo_.checkout_branch(branch_name)

    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.select_target_branch(branch_name=branch_name)
    panel.pull_or_fetch(expect_text="Pull")

    # Wait for and verify the dialog appears
    dialog = sculptor_page_.get_by_role("alertdialog")
    expect(dialog).to_be_visible()

    # Verify dialog content mentions uncommitted changes
    expect(dialog).to_contain_text("uncommitted work")
    expect(dialog).to_contain_text("Merge & ignore")

    # Click "Merge & ignore uncommitted changes" to proceed
    ignore_button = dialog.get_by_role("button", name="Merge & ignore", exact=False)
    ignore_button.click()

    # Wait for operation to complete
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=20_000)
    expect(notices).to_contain_text("Finished successfully")


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dialog_agent_uncommitted_changes_cancel(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    """Test that the sync dialog can be cancelled when agent has uncommitted changes."""
    # Start a task and let the agent create a file but leave it uncommitted
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a file called temp.py with 'print(\"temp\")' but don't commit it",
        wait_for_agent_to_finish=True,
    )

    # Get the branch name
    branch_name = task_page.get_branch_name()

    # Checkout the same branch locally
    pure_local_repo_.checkout_branch(branch_name)

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.select_target_branch(branch_name=branch_name)
    panel.pull_or_fetch(expect_text="Pull")

    # Wait for the dialog
    dialog = sculptor_page_.get_by_role("alertdialog")
    expect(dialog).to_be_visible()

    # Click "Cancel" to abort the operation
    cancel_button = dialog.get_by_role("button", name="Cancel")
    cancel_button.click()

    # Verify the dialog is dismissed and operation is cancelled
    expect(dialog).not_to_be_visible(timeout=10000)

    # Check for cancellation notice
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)
    expect(notices).to_contain_text("Operation cancelled")


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dialog_agent_uncommitted_changes_commit(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    """Test that the sync dialog allows committing agent's uncommitted changes before merging."""
    # Start a task and let the agent create files but leave them uncommitted
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt=(
            "Create two files without committing them: "
            + "1. uncommitted1.py with 'print(\"file1\")' "
            + "2. uncommitted2.py with 'print(\"file2\")'"
        ),
        wait_for_agent_to_finish=True,
    )

    # Get the branch name
    branch_name = task_page.get_branch_name()

    # Checkout the same branch locally
    pure_local_repo_.checkout_branch(branch_name)

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.select_target_branch(branch_name=branch_name)
    panel.pull_or_fetch(expect_text="Pull")

    # Wait for the dialog
    dialog = sculptor_page_.get_by_role("alertdialog")
    expect(dialog).to_be_visible()

    # Verify dialog content mentions uncommitted changes and commit option
    expect(dialog).to_contain_text("uncommitted work")
    expect(dialog).to_contain_text("Commit & Merge")

    # Enter a commit message in the textarea
    commit_message_input = dialog.get_by_test_id(ElementIDs.MERGE_PANEL_DIALOG_COMMIT_MESSAGE_INPUT)
    commit_message_input.fill("Commit uncommitted changes from merge dialog")

    # Click "Commit & Merge" to commit and proceed
    commit_button = dialog.get_by_role("button", name="Commit & Merge")
    commit_button.click()

    # Wait for operation to complete
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=30_000)

    # Verify success message appears and mentions the commit
    expect(notices).to_contain_text("Successfully committed changes in Agent's repository")
    expect(notices).to_contain_text("Finished successfully")

    # Verify the files from agent are now in the local repo after the merge
    assert (pure_local_repo_.base_path / "uncommitted1.py").exists(), "uncommitted1.py should exist after pull"
    assert (pure_local_repo_.base_path / "uncommitted2.py").exists(), "uncommitted2.py should exist after pull"

    # Verify file contents
    file1_content = (pure_local_repo_.base_path / "uncommitted1.py").read_text()
    assert "file1" in file1_content, "uncommitted1.py should contain expected content"

    file2_content = (pure_local_repo_.base_path / "uncommitted2.py").read_text()
    assert "file2" in file2_content, "uncommitted2.py should contain expected content"


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dirty_local_repo_push_ignored_warning(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    """Test pushing to agent when local repo has uncommitted changes - should show warning but proceed."""
    # Start a task and wait for it to complete
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a README.md file with '# Test Project' and commit it",
        wait_for_agent_to_finish=True,
    )

    # Get the branch name
    branch_name = task_page.get_branch_name()

    # Checkout the same branch locally and make uncommitted changes
    pure_local_repo_.checkout_branch(branch_name)
    pure_local_repo_.write_file("uncommitted_file.txt", "This change is not committed")
    # Deliberately NOT committing this change

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.select_target_branch(branch_name=branch_name)

    # Try to push the current (dirty) branch to agent
    push_button = panel.get_push_button()
    push_button.click()

    # Wait for operation to complete
    notices = panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=15000)

    # Should complete successfully but with a warning about uncommitted changes being ignored
    expect(notices).to_contain_text("Finished successfully")
    # The notice should warn about uncommitted changes being ignored
    expect(notices).to_contain_text("uncommitted changes which will be ignored")


def _start_task_and_pull_into_current(
    home_page: PlaywrightHomePage,
    source_branch_name: str | None = None,
    dialogs: list[tuple[str, str]] | None = None,
    pure_local_repo: MockRepoState = None,
    create_uncommitted_changes: bool = False,
) -> tuple[PlaywrightTaskPage, PlaywrightMergePanel]:
    if source_branch_name is not None:
        select_branch(home_page.get_task_starter(), source_branch_name)

    task_page = start_task_and_wait_for_ready(
        home_page,
        # TODO: pull the exact filenames away from here, make it into a variable for easier scenario creation
        prompt="Create and commit a file called agent_feature.py with 'def feature(): return True'",
        wait_for_agent_to_finish=True,
    )

    if create_uncommitted_changes:
        pure_local_repo.write_file("local_changes.txt", "Local uncommitted changes")

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()
    panel.select_target_branch(branch_badge="current")

    pull_fetch_button = panel.get_pull_or_fetch_button()
    expect(pull_fetch_button).to_contain_text("Pull")
    pull_fetch_button.click()

    if dialogs:
        for dialog_id, dialog_option in dialogs:
            dialog = task_page.get_by_role("alertdialog")
            expect(dialog).to_have_attribute("data-dialog-id", dialog_id)
            dialog.get_by_role("button", name=dialog_option, exact=True).click()

    return task_page, panel


def _wait_for_final_notice(panel: PlaywrightMergePanel, is_expecting_success: bool):
    all_notices = panel.get_all_footer_notices()
    expect(all_notices.last).to_have_attribute("data-notice-type", re.compile(r"success|error"))
    expected_attribute_value = "success" if is_expecting_success else "error"
    assert all_notices.last.get_attribute("data-notice-type") == expected_attribute_value, (
        "final notice stabilized on the wrong value"
    )


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dirty_local_repo_pull_simple(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    task_page, panel = _start_task_and_pull_into_current(
        home_page=sculptor_page_,
        # force the local changes to not be propagated
        source_branch_name=pure_local_repo_.get_current_branch_name(),
        pure_local_repo=pure_local_repo_,
        create_uncommitted_changes=True,
    )
    _wait_for_final_notice(panel, is_expecting_success=True)

    # there should be a warning message mentioning uncommitted changes
    expect(
        panel.get_all_footer_notices().filter(has_text="Merging into a repository with uncommitted changes")
    ).to_be_visible()

    assert (pure_local_repo_.base_path / "agent_feature.py").exists(), "changes from the agent exist after pull"


@user_story("to interact with the agent's changes and my own changes")
def test_sync_dirty_local_repo_pull_conflict_ff(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    start_branch = pure_local_repo_.get_current_branch_name()

    local_edit_mark = "LOCAL_EDIT_MARK"
    pure_local_repo_.create_reset_and_checkout_branch("working-branch")
    pure_local_repo_.write_file("agent_feature.py", local_edit_mark)
    pure_local_repo_.stage_all_changes()

    task_page, panel = _start_task_and_pull_into_current(
        home_page=sculptor_page_,
        source_branch_name=start_branch,
    )
    _wait_for_final_notice(panel, is_expecting_success=False)

    # there should be a warning message mentioning uncommited changes
    expect(
        panel.get_all_footer_notices().filter(has_text="Merging into a repository with uncommitted changes")
    ).to_be_visible()
    # there should be an error about overwriting local files
    expect(
        panel.get_all_footer_notices().filter(has_text="The merge was blocked by uncommitted changes")
    ).to_be_visible()
    assert pure_local_repo_.read_file(Path("agent_feature.py")) == local_edit_mark, (
        "no changes from the agent should have landed"
    )


def test_sync_dirty_local_repo_pull_conflict_divergent(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
):
    start_branch = pure_local_repo_.get_current_branch_name()

    local_edit_mark = "LOCAL_EDIT_MARK"
    pure_local_repo_.create_reset_and_checkout_branch("working-branch")
    pure_local_repo_.write_file("local_changes.txt", "Local uncommitted changes")
    pure_local_repo_.commit("non-conflicting commit")
    pure_local_repo_.write_file("agent_feature.py", local_edit_mark)
    # flipping this actually changes the git's behavior
    pure_local_repo_.stage_all_changes()

    task_page, panel = _start_task_and_pull_into_current(
        home_page=sculptor_page_,
        source_branch_name=start_branch,
        dialogs=[
            ("FF_MERGE_NOT_POSSIBLE", "Merge"),
            ("MERGE_FAILED_ALERT", "Ok"),
        ],
    )

    expect(
        panel.get_all_footer_notices().filter(has_text="The merge was blocked by uncommitted changes")
    ).to_be_visible()
    assert pure_local_repo_.read_file(Path("agent_feature.py")) == local_edit_mark, (
        "no changes from the agent should have landed"
    )


@user_story("to interact with the agent's changes and my own changes")
def test_merge_panel_push_with_tag_cleanup(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState):
    """Test that push to agent works correctly and doesn't create or remove tags in the local repository.

    This test verifies that:
    1. Local changes can be successfully pushed to the agent
    2. The merge operation completes successfully
    3. No tags are added or removed in the local repository during the operation

    TODO: This test should inspect the git state inside the agent's container to verify
    that the temporary tag (sculptor-merge-source-{commit_hash}) is properly cleaned up
    after a successful merge. Currently, no tests have access to the agent's git repository,
    so this verification is not possible. The implementation in merge_actions.py ensures
    the cleanup happens.
    """
    # Start a task and wait for it to complete with a commit
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Create a hello.py file with 'print(\"Hello from agent\")' and commit it with message 'Add hello.py'",
        wait_for_agent_to_finish=True,
    )

    # Get the branch name for later use
    branch_name = task_page.get_branch_name()

    # Make a local change in the same branch
    pure_local_repo_.checkout_branch(branch_name)
    pure_local_repo_.write_file("local_change.py", "print('Local change')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add local change")

    # Capture the state of tags before the push operation
    tags_before = set(pure_local_repo_.repo.run_git(["tag", "-l"]).strip().splitlines())

    # Open the merge panel
    panel = task_page.get_task_header().open_and_get_merge_panel_content()

    # Click the push button to push changes to agent
    panel.select_target_branch(branch_name=branch_name)
    push_button = panel.get_push_button()
    push_button.click()

    _wait_for_final_notice(panel, is_expecting_success=True)

    # Capture the state of tags after the push operation
    tags_after_push = set(pure_local_repo_.repo.run_git(["tag", "-l"]).strip().splitlines())

    # Verify that no tags were added or removed in the local repository after push
    assert tags_before == tags_after_push, (
        "Tags should not change in the local repository during push operation. "
        + f"Tags added: {tags_after_push - tags_before}, Tags removed: {tags_before - tags_after_push}"
    )

    # Now perform a fetch operation to pull changes from the agent
    # This verifies that our two-pronged approach works:
    # 1. The temporary tag is cleaned up after merge (delete_tag in merge_actions.py)
    # 2. Fetch/pull operations use --no-tags to prevent pulling any tags from the remote
    panel.select_target_branch(branch_name=branch_name)
    pull_fetch_button = panel.get_pull_or_fetch_button()
    expect(pull_fetch_button).to_contain_text("Pull")
    pull_fetch_button.click()

    # Wait for the fetch/pull operation to complete
    _wait_for_final_notice(panel, is_expecting_success=True)

    # Capture the state of tags after the pull operation
    tags_after_pull = set(pure_local_repo_.repo.run_git(["tag", "-l"]).strip().splitlines())

    # Verify that no tags were added even after fetch
    # With both --no-tags and tag cleanup, no temporary tags should appear
    assert tags_before == tags_after_pull, (
        "Tags should not change in the local repository even after fetch operation. "
        + f"Tags added: {tags_after_pull - tags_before}, Tags removed: {tags_before - tags_after_pull}"
    )

    # Verify the merge was successful by checking files exist
    assert (pure_local_repo_.base_path / "hello.py").exists(), "Agent's file should exist after merge"
    assert (pure_local_repo_.base_path / "local_change.py").exists(), "Local file should still exist"
