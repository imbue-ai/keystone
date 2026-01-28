"""Integration tests for Homepage - Branch List functionality."""

import time

from playwright.sync_api import Locator
from playwright.sync_api import Page
from playwright.sync_api import expect

from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


def wait_for_branch_list_refresh(
    page: Page,
    branch_selector: Locator,
    branch_options_locator: Locator,
    expected_count: int,
    max_retries: int = 5,
) -> None:
    """Wait for branch list to refresh by reopening dropdown until expected count is reached."""
    page.mouse.click(0, 0)
    try:
        expect(branch_options_locator).to_be_hidden(timeout=1000)
    except AssertionError:
        pass

    for attempt in range(max_retries):
        branch_selector.click()
        time.sleep(0.5)

        try:
            expect(branch_options_locator).to_have_count(expected_count, timeout=2000)
            return
        except AssertionError:
            if attempt < max_retries - 1:
                page.mouse.click(0, 0)
                expect(branch_options_locator).to_be_hidden(timeout=5000)
            else:
                raise


@user_story("to see my recent branches in the dropdown menu")
def test_new_branches_show_up_in_dropdown(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test that new branches show up in the dropdown after being created."""

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # The test starts with the "testing" branch
    initial_branch = pure_local_repo_.get_current_branch_name()
    assert initial_branch == "testing", "internal test expectation"

    # Verify we have exactly one branch initially
    branch_selector = task_starter.get_branch_selector()
    expect(branch_selector.filter(has_text="testing")).to_have_count(1, timeout=30000)

    # Create a new branch in the repo
    new_branch_name = "new-feature-branch"
    pure_local_repo_.create_reset_and_checkout_branch(new_branch_name)

    # Should have exactly three branches now (main, testing, new-feature-branch)
    updated_options = task_starter.get_branch_options()
    wait_for_branch_list_refresh(home_page, branch_selector, updated_options, expected_count=3)

    # Find and verify the new branch is in the list
    old_branch_option = updated_options.filter(has_text=initial_branch).filter(has_not_text=" (")
    expect(old_branch_option).to_have_count(1, timeout=30000)
    new_branch_option = updated_options.filter(has_text=new_branch_name).filter(has_not_text=" (")
    expect(new_branch_option).to_have_count(1, timeout=30000)

    # Select the new branch to verify it works
    new_branch_option.click()
    expect(branch_selector.filter(has_text=new_branch_name)).to_have_count(1, timeout=30000)


@user_story("to see my recent branches in the dropdown menu")
def test_branch_remains_selected_when_list_changes(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that the originally selected branch remains selected when the branch list changes."""

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Verify we start with "testing" branch selected
    initial_branch = pure_local_repo_.get_current_branch_name()
    assert initial_branch == "testing"
    branch_selector = task_starter.get_branch_selector()
    expect(branch_selector.filter(has_text=initial_branch)).to_have_count(1)

    # Create a new branch (which changes the branch list)
    new_branch = "another-new-branch"
    pure_local_repo_.create_reset_and_checkout_branch(new_branch)
    pure_local_repo_.checkout_branch(initial_branch)  # Go back to original

    # The branch selector should still show the originally selected branch
    expect(branch_selector.filter(has_text=initial_branch)).to_have_count(1)


@user_story("to see my recent branches in the dropdown menu")
def test_deleted_branches_disappear_from_dropdown(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that branches disappear from the dropdown when they are deleted."""

    # Start from known state
    initial_branch = pure_local_repo_.get_current_branch_name()
    assert initial_branch == "testing"

    # Create a branch to delete
    branch_to_delete = "temporary-branch"
    pure_local_repo_.create_reset_and_checkout_branch(branch_to_delete)
    pure_local_repo_.checkout_branch("testing")  # Switch back

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    branch_selector = task_starter.get_branch_selector()

    # Verify we now have three branches (main, testing, temporary-branch)
    branch_selector.click()
    branch_options = task_starter.get_branch_options()
    expect(branch_options).to_have_count(3, timeout=30000)

    # Verify the temporary branch exists
    branch_to_delete_option = branch_options.filter(has_text=branch_to_delete)
    expect(branch_to_delete_option).to_have_count(1, timeout=30000)

    # Delete the branch
    pure_local_repo_.delete_branch(branch_to_delete, force=True)

    # Should be back to two branches (main, testing)
    updated_options = task_starter.get_branch_options()
    wait_for_branch_list_refresh(home_page, branch_selector, updated_options, expected_count=2)

    # Verify the deleted branch is gone
    deleted_branch_option = updated_options.filter(has_text=branch_to_delete)
    expect(deleted_branch_option).to_have_count(0, timeout=30000)

    # Verify only the testing branch remains
    expect(updated_options.filter(has_text="testing")).to_have_count(1, timeout=30000)
    home_page.mouse.click(0, 0)  # Close dropdown
