"""This module contains checks that we want to run on startup in sculptor.

This allows us to detect conditions where sculptor might not safely run, and ask the user to fix this.

The design is decoupled: check execution is separate from result presentation, allowing
for flexible handling (CLI errors now, web modals in the future).
"""

import os
import re
import shutil
import subprocess
import sys

from loguru import logger

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry_utils import without_consent
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.subprocess_utils import ProcessError


class CheckResult(SerializableModel):
    """Result of running a single startup check."""

    name: str
    passed: bool
    error_message: str


class CheckResultPayload(PosthogEventPayload):
    """Payload wrapper for checking results."""

    results: list[CheckResult] = without_consent()


def handle_check_results_cli(results: list[CheckResult]) -> None:
    """Handle check results for CLI: print errors and exit if any failed.

    This maintains the same behavior as sculptor_v0: show all failed checks
    and exit with code 78 (EX_CONFIG) if any failed.

    Args:
        results: The check results to handle
    """
    if all(result.passed for result in results):
        # No failures, nothing to do
        return

    # Print all failed check messages
    for result in results:
        if not result.passed:
            logger.error(result.error_message)

    # Print onboarding guidelines link if any checks failed
    logger.error(
        "For help with setting up Sculptor, please see the onboarding guidelines: https://imbue-ai.notion.site/A-Guide-to-Sculptor-22aa550faf95801b8639dd3288e21974?source=copy_link"
    )

    # Exit with standard misconfiguration code
    sys.exit(78)  # 78 is EX_CONFIG, which is the standard exit code for misconfiguration.


def is_valid_anthropiclike_api_key(api_key: str | None = None) -> bool:
    """Check if an API key is valid (Anthropic or Anthropic-like API).

    This accepts both official Anthropic API keys and third-party proxy keys
    that are compatible with the Anthropic API format.

    Returns True if:
    - The key is not None
    - The key is not empty
    - The key contains only ASCII characters

    Returns False otherwise.

    Note: The error that Anthropic returns when the API key is non-ASCII
    is confusing and hard to debug, so we validate ASCII characters here.
    """
    if api_key is None:
        return False
    # Reject empty strings
    if not api_key:
        return False
    # Reject non-ASCII characters as they cause confusing errors
    if not api_key.isascii():
        return False
    return True


def check_anthropic_api_key() -> bool:
    """Please set the environment variable ANTHROPIC_API_KEY, and make sure that all characters are ASCII: `export ANTHROPIC_API_KEY=...`"""
    return is_valid_anthropiclike_api_key(os.environ.get("ANTHROPIC_API_KEY", None))


def check_docker_installed() -> bool:
    """Please install the version of docker that is appropriate for your platform: osx, https://docs.docker.com/desktop/setup/install/mac-install/  , or linux, `sudo apt install -y docker.io`."""
    try:
        subprocess.run(
            ["docker", "--version"],
            check=True,
            capture_output=True,
            timeout=5.0,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_docker_running(concurrency_group: ConcurrencyGroup) -> bool:
    """Please ensure that your docker daemon is running and accessible as a non-root user. Hint: if you normally need to run as root, try `sudo usermod -aG docker $USER && newgrp docker`."""
    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "ps"],
            timeout=30.0,
        )
        # This will always return true since run_process_to_completion() would raise an error otherwise.
        return result.returncode == 0
    except ProcessError:
        return False


def check_git_installed(concurrency_group: ConcurrencyGroup) -> bool:
    """Please install git to allow sculptor to work with your repository."""
    try:
        result = concurrency_group.run_process_to_completion(
            ["git", "--version"],
            timeout=5.0,
        )
        return result.returncode == 0
    except ProcessError:
        return False


def check_is_mutagen_installed(concurrency_group: ConcurrencyGroup) -> bool:
    """Please run `brew install mutagen-io/mutagen/mutagen` to allow sculptor to work with your repository."""
    return shutil.which("mutagen") is not None


def check_is_privacy_policy_consented(user_config: UserConfig) -> bool:
    """Please consent to our research preview privacy notice and terms of service."""
    return user_config.is_privacy_policy_consented


def check_is_user_email_field_valid(config: UserConfig) -> bool:
    """Please enter a valid email address."""
    # Matches things like .@..., <some string>@<another>.<last one>
    # which excludes '@' from each of the string parts but allow all other characters
    # including special characters and '.' a dot itself.
    pattern = r"^[^@]+@[^@]+\.[^@]+$"
    if re.match(pattern, config.user_email):
        return True
    else:
        return False
