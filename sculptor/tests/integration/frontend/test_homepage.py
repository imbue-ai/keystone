"""Integration tests for general Homepage functionality."""

from pathlib import Path

from playwright.sync_api import expect

from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to see the current project's directory")
def test_homepage_shows_current_directory(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test that the homepage shows the current project directory.
    Expectation: current directory shows up on homepage
    """

    home_page = sculptor_page_

    # Get the repository path
    repo_path = str(Path(*pure_local_repo_.base_path.parts[-2:]))

    # Check if the repo indicator shows the directory
    repo_indicator = home_page.get_repository_indicator()

    # The repo indicator should contain the directory path
    expect(repo_indicator).to_be_visible()
    expect(repo_indicator).to_contain_text(repo_path)


@user_story("to see the version of the Sculptor that I am using")
def test_homepage_shows_version_string(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the homepage shows the Sculptor version at the bottom.
    Expectation: version string shows up on the bottom of the page
    """

    home_page = sculptor_page_

    # Get the version element using the page's getter method
    version_element = home_page.get_version_element()

    # The version element should be visible and contain version information
    expect(version_element).to_be_visible()
    # Just verify it has some text - we don't know the exact version
    expect(version_element).not_to_be_empty()
    expect(version_element).not_to_contain_text("version unknown")


# @user_story("to see the product explode sometimes")
# def test_homepage_can_explode(sculptor_page_: PlaywrightHomePage) -> None:
#     home_page = sculptor_page_
#
#     assert False, "oops, it exploded"
