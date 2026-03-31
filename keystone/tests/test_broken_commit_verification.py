"""Tests for broken-commit re-verification (mutation-augmented eval).

Unit tests run without Modal. Integration tests marked @pytest.mark.modal.
"""

import json

from keystone.schema import BootstrapResult, InferenceCost, VerificationResult

_AGENT_DICT = {
    "start_time": "2024-01-01T00:00:00",
    "end_time": "2024-01-01T00:01:00",
    "duration_seconds": 60,
    "exit_code": 0,
    "timed_out": False,
    "cost_limit_exceeded": False,
    "cost": InferenceCost().model_dump(),
}


def test_bootstrap_result_new_fields_default() -> None:
    """New broken-commit fields have sensible defaults."""
    result = BootstrapResult(success=True, agent=_AGENT_DICT)
    assert result.broken_commit_verifications == {}
    assert result.post_broken_commits_verification is None
    assert result.unexpected_broken_commit_passes == 0


def test_bootstrap_result_serialization_with_broken_commits() -> None:
    """BootstrapResult round-trips with broken-commit fields populated."""
    vr_pass = VerificationResult(success=True, tests_passed=5, tests_failed=0)
    vr_fail = VerificationResult(
        success=False,
        error_message="Tests failed (exit 1)",
        tests_passed=3,
        tests_failed=2,
    )

    result = BootstrapResult(
        success=True,
        agent=_AGENT_DICT,
        broken_commit_verifications={
            "abc123": vr_fail,
            "def456": vr_pass,
        },
        post_broken_commits_verification=vr_pass,
        unexpected_broken_commit_passes=1,
    )

    # Round-trip through JSON
    json_str = result.model_dump_json()
    parsed = BootstrapResult(**json.loads(json_str))
    assert parsed.unexpected_broken_commit_passes == 1
    assert len(parsed.broken_commit_verifications) == 2
    assert parsed.broken_commit_verifications["abc123"].tests_failed == 2
    assert parsed.broken_commit_verifications["def456"].tests_failed == 0
    assert parsed.post_broken_commits_verification is not None
    assert parsed.post_broken_commits_verification.success is True


def test_unexpected_broken_commit_passes_computation() -> None:
    """Verify the count of broken commits where tests_failed == 0."""
    verifications = {
        "hash1": VerificationResult(success=False, tests_passed=3, tests_failed=2),
        "hash2": VerificationResult(success=True, tests_passed=5, tests_failed=0),
        "hash3": VerificationResult(success=True, tests_passed=5, tests_failed=0),
        "hash4": VerificationResult(success=False, tests_passed=0, tests_failed=5),
    }
    unexpected = sum(1 for v in verifications.values() if v.tests_failed == 0)
    assert unexpected == 2


def test_bootstrap_result_skipped_on_failure() -> None:
    """When bootstrap fails, broken_commit_verifications should be empty."""
    agent_failed = {**_AGENT_DICT, "exit_code": 1}
    result = BootstrapResult(
        success=False,
        error_message="Build failed",
        agent=agent_failed,
    )
    assert result.broken_commit_verifications == {}
    assert result.unexpected_broken_commit_passes == 0
