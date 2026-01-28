import pytest
from toml import TomlDecodeError

from imbue_core.itertools import only
from sculptor.agents.default.constants import VerifierTokenUsageRequirement
from sculptor.agents.default.constants import WEIGHTED_OUTPUT_TOKENS_THRESHOLD_HIGH
from sculptor.agents.default.constants import WEIGHTED_OUTPUT_TOKENS_THRESHOLD_LOW
from sculptor.agents.default.constants import WEIGHTED_OUTPUT_TOKENS_THRESHOLD_MEDIUM
from sculptor.interfaces.agents.checks import CheckSource
from sculptor.interfaces.agents.checks import CheckTrigger
from sculptor.interfaces.agents.constants import DEFAULT_CHECK_TIMEOUT_SECONDS
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.tasks.handlers.run_agent.checks.check_loading import _get_imbue_verify_check_trigger
from sculptor.tasks.handlers.run_agent.checks.check_loading import load_checks
from sculptor.tasks.handlers.run_agent.checks.check_loading import load_checks_from_environment
from sculptor.tasks.handlers.run_agent.checks.constants import CHECK_CONFIG_PATH
from sculptor.tasks.handlers.run_agent.checks.constants import SCULPTOR_SYSTEM_CHECK_NAME

INVALID_TOML = """
[broken_check
command = "this won't parse"
"""


def test_loading_checks_from_valid_toml() -> None:
    """Test parsing valid TOML configurations for checks."""

    # Test various valid TOML configurations
    toml_content = """
# Simple string command
simple_check = "echo 'Hello, World!'"

# Check with all optional fields
[full_check]
command = "pytest tests/"
timeout = 300
description = "Run all unit tests"
is_forked = true
trigger = "AGENT_MESSAGE"
is_enabled = true
is_visible = false
is_local_concurrency_allowed = false

# Check with minimal fields
[minimal_check]
command = "ls -la"

# Check with different trigger
[on_demand_check]
command = "npm test"
trigger = "MANUAL"
description = "Run npm tests on demand"
"""

    checks = load_checks(toml_content)

    # Verify simple check
    assert "simple_check" in checks
    assert checks["simple_check"].name == "simple_check"
    assert checks["simple_check"].command == "echo 'Hello, World!'"
    assert checks["simple_check"].timeout_seconds == DEFAULT_CHECK_TIMEOUT_SECONDS
    assert checks["simple_check"].trigger == CheckTrigger.AGENT_MESSAGE
    assert checks["simple_check"].is_enabled is True
    assert checks["simple_check"].is_visible is True
    assert checks["simple_check"].is_forked is False
    assert checks["simple_check"].is_local_concurrency_allowed is False

    # Verify full check with all fields
    assert "full_check" in checks
    assert checks["full_check"].name == "full_check"
    assert checks["full_check"].command == "pytest tests/"
    assert checks["full_check"].timeout_seconds == 300
    assert checks["full_check"].description == "Run all unit tests"
    assert checks["full_check"].is_forked is True
    assert checks["full_check"].trigger == CheckTrigger.AGENT_MESSAGE
    assert checks["full_check"].is_enabled is True
    assert checks["full_check"].is_visible is False
    assert checks["full_check"].is_local_concurrency_allowed is False

    # Verify minimal check
    assert "minimal_check" in checks
    assert checks["minimal_check"].name == "minimal_check"
    assert checks["minimal_check"].command == "ls -la"
    assert checks["minimal_check"].timeout_seconds == DEFAULT_CHECK_TIMEOUT_SECONDS

    # Verify check with different trigger
    assert "on_demand_check" in checks
    assert checks["on_demand_check"].trigger == CheckTrigger.MANUAL


def test_invalid_toml_syntax() -> None:
    """Test handling of malformed TOML files."""

    # Invalid TOML syntax

    # Should raise an error when parsing
    with pytest.raises(TomlDecodeError):  # toml.TomlDecodeError
        load_checks(INVALID_TOML)


def test_check_with_all_optional_fields() -> None:
    """Test a check definition with every possible field specified."""

    toml_content = """
[complete_check]
command = "python -m pytest --cov"
timeout = 600
description = "Comprehensive test with coverage"
is_forked = true
trigger = "AGENT_MESSAGE"
is_enabled = false
is_visible = true
"""

    checks = load_checks(toml_content)

    assert "complete_check" in checks
    check = checks["complete_check"]
    assert check.name == "complete_check"
    assert check.command == "python -m pytest --cov"
    assert check.timeout_seconds == 600
    assert check.description == "Comprehensive test with coverage"
    assert check.is_forked is True
    assert check.trigger == CheckTrigger.AGENT_MESSAGE
    assert check.is_enabled is False
    assert check.is_visible is True
    assert check.config_error is None


def test_check_with_minimal_fields() -> None:
    """Test that checks work with only required fields."""

    # Absolutely minimal - just a command string
    toml_content = """
minimal = "echo minimal"
"""

    checks = load_checks(toml_content)

    assert "minimal" in checks
    check = checks["minimal"]
    assert check.name == "minimal"
    assert check.command == "echo minimal"
    # All defaults should be applied
    assert check.timeout_seconds == DEFAULT_CHECK_TIMEOUT_SECONDS
    assert check.description == ""
    assert check.is_forked is False
    assert check.trigger == CheckTrigger.AGENT_MESSAGE
    assert check.is_enabled is True
    assert check.is_visible is True
    assert check.config_error is None


def test_invalid_trigger_values() -> None:
    """Test that invalid CheckTrigger enum values are handled properly."""

    toml_content = """
[bad_trigger_check]
command = "echo test"
trigger = "INVALID_TRIGGER_TYPE"
"""

    checks = load_checks(toml_content)

    # Check should exist but have a config_error
    assert "bad_trigger_check" in checks
    check = checks["bad_trigger_check"]
    assert check.name == "bad_trigger_check"
    assert check.command == "echo test"
    assert check.config_error is not None
    assert "Invalid check trigger" in check.config_error
    assert "INVALID_TRIGGER_TYPE" in check.config_error


def test_load_checks_from_environment_when_missing(environment: LocalEnvironment) -> None:
    checks, suggestions_from_loading = load_checks_from_environment(
        environment, is_imbue_verify_check_enabled=False, is_imbue_scout_check_enabled=False
    )
    system_check = checks[SCULPTOR_SYSTEM_CHECK_NAME]
    assert system_check.source == CheckSource.SYSTEM
    assert only(suggestions_from_loading).title == "Define your own custom checks"


def test_imbue_verify_trigger_none_always_runs() -> None:
    trigger = _get_imbue_verify_check_trigger("auto", 0, VerifierTokenUsageRequirement.NONE)
    assert trigger == CheckTrigger.AGENT_MESSAGE


def test_imbue_verify_trigger_low_at_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto", WEIGHTED_OUTPUT_TOKENS_THRESHOLD_LOW, VerifierTokenUsageRequirement.LOW
    )
    assert trigger == CheckTrigger.AGENT_MESSAGE


def test_imbue_verify_trigger_low_below_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto", WEIGHTED_OUTPUT_TOKENS_THRESHOLD_LOW - 1, VerifierTokenUsageRequirement.LOW
    )
    assert trigger == CheckTrigger.MANUAL


def test_imbue_verify_trigger_medium_at_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto", WEIGHTED_OUTPUT_TOKENS_THRESHOLD_MEDIUM, VerifierTokenUsageRequirement.MEDIUM
    )
    assert trigger == CheckTrigger.AGENT_MESSAGE


def test_imbue_verify_trigger_medium_below_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto",
        WEIGHTED_OUTPUT_TOKENS_THRESHOLD_MEDIUM - 1,
        VerifierTokenUsageRequirement.MEDIUM,
    )
    assert trigger == CheckTrigger.MANUAL


def test_imbue_verify_trigger_high_at_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto", WEIGHTED_OUTPUT_TOKENS_THRESHOLD_HIGH, VerifierTokenUsageRequirement.HIGH
    )
    assert trigger == CheckTrigger.AGENT_MESSAGE


def test_imbue_verify_trigger_high_below_threshold() -> None:
    trigger = _get_imbue_verify_check_trigger(
        "auto", WEIGHTED_OUTPUT_TOKENS_THRESHOLD_HIGH - 1, VerifierTokenUsageRequirement.HIGH
    )
    assert trigger == CheckTrigger.MANUAL


def test_imbue_verify_trigger_manual_ignores_tokens() -> None:
    trigger = _get_imbue_verify_check_trigger("manual", 100000, VerifierTokenUsageRequirement.NONE)
    assert trigger == CheckTrigger.MANUAL


def test_verifier_token_usage_requirement_from_string_valid() -> None:
    assert VerifierTokenUsageRequirement.from_string("none") == VerifierTokenUsageRequirement.NONE
    assert VerifierTokenUsageRequirement.from_string("NONE") == VerifierTokenUsageRequirement.NONE
    assert VerifierTokenUsageRequirement.from_string("low") == VerifierTokenUsageRequirement.LOW
    assert VerifierTokenUsageRequirement.from_string("LOW") == VerifierTokenUsageRequirement.LOW
    assert VerifierTokenUsageRequirement.from_string("medium") == VerifierTokenUsageRequirement.MEDIUM
    assert VerifierTokenUsageRequirement.from_string("MEDIUM") == VerifierTokenUsageRequirement.MEDIUM
    assert VerifierTokenUsageRequirement.from_string("high") == VerifierTokenUsageRequirement.HIGH
    assert VerifierTokenUsageRequirement.from_string("HIGH") == VerifierTokenUsageRequirement.HIGH


def test_verifier_token_usage_requirement_from_string_invalid() -> None:
    assert VerifierTokenUsageRequirement.from_string("invalid") == VerifierTokenUsageRequirement.NONE
    assert VerifierTokenUsageRequirement.from_string("") == VerifierTokenUsageRequirement.NONE
    assert VerifierTokenUsageRequirement.from_string("very_high") == VerifierTokenUsageRequirement.NONE


def test_load_checks_from_environment_when_invalid(environment: LocalEnvironment) -> None:
    environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), INVALID_TOML)
    checks, suggestions_from_loading = load_checks_from_environment(
        environment, is_imbue_verify_check_enabled=False, is_imbue_scout_check_enabled=False
    )
    system_check = checks[SCULPTOR_SYSTEM_CHECK_NAME]
    assert system_check.source == CheckSource.SYSTEM
    assert only(suggestions_from_loading).title == f"Fix check configuration in {CHECK_CONFIG_PATH}"


def test_load_checks_from_environment_when_defined(environment: LocalEnvironment) -> None:
    environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), """my_command = 'echo hello'""")
    checks, suggestions_from_loading = load_checks_from_environment(
        environment, is_imbue_verify_check_enabled=False, is_imbue_scout_check_enabled=False
    )
    user_check = checks["my_command"]
    assert user_check.source == CheckSource.USER
    assert user_check.command == "echo hello"
    assert user_check.name == "my_command"
    assert len(suggestions_from_loading) == 0
