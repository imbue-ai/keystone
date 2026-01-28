from pathlib import Path

from playwright.sync_api import expect

from sculptor.testing.dependency_stubs import DependencyState
from sculptor.testing.dependency_stubs import disable_dependency
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to use Sculptor even when Docker is not installed or running")
@disable_dependency("docker", state=DependencyState.NOT_RUNNING)
def test_can_start_without_docker(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that Sculptor can start and show the homepage without Docker running."""
    home_page = sculptor_page_

    # Verify homepage loads and basic elements are visible
    task_starter = home_page.get_task_starter()
    expect(task_starter).to_be_visible()

    # Verify version is displayed
    version_element = home_page.get_version_element()
    expect(version_element).to_be_visible()
    expect(version_element).not_to_be_empty()

    # Verify repository indicator shows the current project
    repo_indicator = home_page.get_repository_indicator()
    expect(repo_indicator).to_be_visible()

    # Verify the repo path is shown
    repo_path = str(Path(*pure_local_repo_.base_path.parts[-2:]))
    expect(repo_indicator).to_contain_text(repo_path)
