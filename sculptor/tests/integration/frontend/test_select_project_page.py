import pytest
from _pytest.tmpdir import TempPathFactory
from playwright.sync_api import expect

from imbue_core.processes.local_process import run_blocking
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.project_selector_page import PlaywrightSelectProjectPage
from sculptor.testing.resources import no_auto_project


@pytest.mark.no_auto_project
def test_select_valid_git_project(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test selecting a valid git project successfully navigates to home."""

    project_selector = PlaywrightSelectProjectPage(sculptor_page_)
    project_selector.wait_for_select_project_page_to_be_visible()

    # Complete project selection with valid git repo
    project_selector.complete_project_selection(str(pure_local_repo_.base_path))

    # expect to be on the home page now
    home_page = sculptor_page_
    expect(home_page.get_task_starter()).to_be_visible()


@no_auto_project
def test_git_init_confirm_flow(sculptor_page_: PlaywrightHomePage, tmp_path_factory: TempPathFactory) -> None:
    """Test the complete git init + project selection flow."""
    tmp_path = tmp_path_factory.mktemp(basename="temp_repo", numbered=True)
    project_selector = PlaywrightSelectProjectPage(sculptor_page_)
    project_selector.wait_for_select_project_page_to_be_visible()

    # Try to select non-git directory
    project_selector.complete_project_selection(str(tmp_path))

    project_selector.wait_for_git_init_dialog()

    project_selector.confirm_git_init()

    # expect to be on the home page now
    home_page = sculptor_page_
    expect(home_page.get_task_starter()).to_be_visible()

    assert (tmp_path / ".git").exists()


@no_auto_project
def test_git_initial_commit_flow(sculptor_page_: PlaywrightHomePage, tmp_path_factory: TempPathFactory) -> None:
    # create an empty git repo
    tmp_path = tmp_path_factory.mktemp(basename="temp_repo", numbered=True)
    run_blocking(["git", "init"], cwd=tmp_path)
    project_selector = PlaywrightSelectProjectPage(sculptor_page_)
    project_selector.wait_for_select_project_page_to_be_visible()

    # Try to select empty git directory
    project_selector.complete_project_selection(str(tmp_path))

    project_selector.wait_for_initial_commit_dialog()

    project_selector.confirm_initial_commit()

    # expect to be on the home page now
    home_page = sculptor_page_
    expect(home_page.get_task_starter()).to_be_visible()

    # Check that there is now a commit in the repo
    result = run_blocking(["git", "rev-parse", "HEAD"], cwd=tmp_path)
    assert result.returncode == 0


@no_auto_project
def test_invalid_path_shows_error(sculptor_page_: PlaywrightHomePage) -> None:
    """Test entering a non-existent path shows error."""
    made_up_path = "/path/that/does/not/exist"
    expected_error_string = f"Project path does not exist: {made_up_path}"

    project_selector = PlaywrightSelectProjectPage(sculptor_page_)
    project_selector.wait_for_select_project_page_to_be_visible()

    # Try to select non-existent path
    project_selector.complete_project_selection(made_up_path)

    # Should show error message
    project_selector.wait_for_error_message(expected_error_string)
