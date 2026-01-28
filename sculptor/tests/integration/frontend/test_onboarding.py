from pathlib import Path

import pytest
from loguru import logger
from playwright.sync_api import expect

from sculptor.services.config_service.data_types import Credentials
from sculptor.testing.dependency_stubs import DependencyState
from sculptor.testing.dependency_stubs import disable_dependency
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.onboarding_page import PlaywrightOnboardingPage
from sculptor.testing.resources import custom_sculptor_folder_populator
from sculptor.testing.user_stories import user_story


def _dont_populate_sculptor_folder(path: Path, credentials: Credentials) -> None:
    logger.info("Skipping population of Sculptor folder for onboarding test.")
    pass


@pytest.mark.flaky
@custom_sculptor_folder_populator.with_args(_dont_populate_sculptor_folder)
def test_full_onboarding_flow(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    test_email = "test@user.com"
    test_api_key = "sk-ant-123"
    onboarding_page = PlaywrightOnboardingPage(sculptor_page_)

    # Complete email step using POM
    welcome_step = onboarding_page.get_welcome_step()
    welcome_step.complete_step(test_email)

    # Complete installation step using POM
    installation_step = onboarding_page.get_installation_step()
    installation_step.complete_step(test_api_key, telemetry_option_index=1)

    # Verify we are on the home page
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    expect(task_starter).to_be_visible()


@user_story("to sign up for Sculptor even when Docker is not installed")
@custom_sculptor_folder_populator.with_args(_dont_populate_sculptor_folder)
@disable_dependency("docker", state=DependencyState.NOT_INSTALLED)
def test_onboarding_without_docker_installed(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that a user can reach the onboarding page when Docker is not installed.

    Verifies:
    1. The onboarding page loads successfully (gets past startup/health boundaries)
    2. The Docker status shows an "Install" button (indicating Docker is NOT installed)
    3. The complete button is disabled (signup is blocked until Docker is installed)
    """
    test_email = "test@user.com"
    onboarding_page = PlaywrightOnboardingPage(sculptor_page_)

    # Complete email step to get to the installation step
    welcome_step = onboarding_page.get_welcome_step()
    welcome_step.complete_step(test_email)

    # Verify we can reach the installation step
    installation_step = onboarding_page.get_installation_step()
    expect(installation_step).to_be_visible()

    # Verify Docker card is visible and shows "Install" button (Docker is NOT installed)
    docker_card = installation_step.get_docker_card()
    expect(docker_card).to_be_visible()
    docker_status = installation_step.get_docker_status()
    expect(docker_status).to_contain_text("Install")

    # Verify the complete button is disabled (signup is blocked)
    complete_button = installation_step.get_complete_button()
    expect(complete_button).to_be_visible()
    expect(complete_button).to_be_disabled()


@user_story("to sign up for Sculptor when Docker is installed but not running")
@custom_sculptor_folder_populator.with_args(_dont_populate_sculptor_folder)
@disable_dependency("docker", state=DependencyState.NOT_RUNNING)
def test_onboarding_with_docker_not_started(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that a user can reach the onboarding page when Docker is installed but not running.

    Verifies:
    1. The onboarding page loads successfully (gets past startup/health boundaries)
    2. The Docker status shows "Launch Docker" button (indicating Docker is installed but NOT running)
    3. The complete button is disabled (signup is blocked until Docker is started)
    """
    test_email = "test@user.com"
    onboarding_page = PlaywrightOnboardingPage(sculptor_page_)

    # Complete email step to get to the installation step
    welcome_step = onboarding_page.get_welcome_step()
    welcome_step.complete_step(test_email)

    # Verify we can reach the installation step
    installation_step = onboarding_page.get_installation_step()
    expect(installation_step).to_be_visible()

    # Verify Docker card is visible and shows "Launch Docker" button (Docker is installed but NOT running)
    docker_card = installation_step.get_docker_card()
    expect(docker_card).to_be_visible()
    docker_status = installation_step.get_docker_status()
    expect(docker_status).to_contain_text("Launch Docker")

    # Verify the complete button is visible
    complete_button = installation_step.get_complete_button()
    expect(complete_button).to_be_visible()


@user_story("to sign up for Sculptor even when Git is not installed")
@custom_sculptor_folder_populator.with_args(_dont_populate_sculptor_folder)
@disable_dependency("git", state=DependencyState.NOT_INSTALLED)
def test_onboarding_without_git_installed(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test that a user can reach the onboarding page when Git is not installed.

    Verifies:
    1. The onboarding page loads successfully (gets past startup/health boundaries)
    2. The Git status shows an "Install" button (indicating Git is NOT installed)
    3. The complete button is disabled (signup is blocked until Git is installed)
    """
    test_email = "test@user.com"
    onboarding_page = PlaywrightOnboardingPage(sculptor_page_)

    # Complete email step to get to the installation step
    welcome_step = onboarding_page.get_welcome_step()
    welcome_step.complete_step(test_email)

    # Verify we can reach the installation step
    installation_step = onboarding_page.get_installation_step()
    expect(installation_step).to_be_visible()

    # Verify Git card is visible and shows "Install" button (Git is NOT installed)
    git_card = installation_step.get_git_card()
    expect(git_card).to_be_visible()
    git_status = installation_step.get_git_status()

    # Verify it shows "Install" button, not "Installed" text
    expect(git_status).not_to_contain_text("Installed")
    expect(git_status.get_by_role("button", name="Install")).to_be_visible()

    # Verify the complete button is disabled (signup is blocked)
    complete_button = installation_step.get_complete_button()
    expect(complete_button).to_be_visible()
    expect(complete_button).to_be_disabled()
