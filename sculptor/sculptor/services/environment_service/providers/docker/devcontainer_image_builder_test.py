import pytest

from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import DevcontainerError
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import _validate_forward_ports
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_image_reference,
)


class MockTaskSpecificContext(TaskSpecificContext):
    """Mock implementation of TaskSpecificContext for testing."""

    def __init__(self):
        self.warnings: list[str] = []
        self.notifications: list[tuple[str, str]] = []

    def emit_warning(self, message: str) -> None:
        self.warnings.append(message)

    def emit_notification(self, message: str, importance: str = "ACTIVE") -> None:
        self.notifications.append((message, importance))


def test_get_default_devcontainer_image_reference():
    """Test that get_default_devcontainer_image_reference returns a string and doesn't raise."""
    result = get_default_devcontainer_image_reference()

    assert isinstance(result, str)
    assert len(result) > 0


class TestValidateForwardPorts:
    """Tests for the _validate_forward_ports function."""

    def test_empty_list(self):
        """Test that an empty list returns an empty list."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([], context)
        assert result == []
        assert len(context.warnings) == 0
        assert len(context.notifications) == 0

    def test_none_value(self):
        """Test that None returns an empty list."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports(None, context)
        assert result == []
        assert len(context.warnings) == 0
        assert len(context.notifications) == 0

    def test_valid_ports(self):
        """Test that valid ports are returned unchanged."""
        context = MockTaskSpecificContext()
        valid_ports = [80, 443, 3000, 8080, 5432]
        result = _validate_forward_ports(valid_ports, context)
        assert result == valid_ports
        assert len(context.warnings) == 0
        assert len(context.notifications) == 0

    def test_boundary_valid_ports(self):
        """Test that boundary valid ports (1 and 65535) are accepted."""
        context = MockTaskSpecificContext()
        boundary_ports = [1, 65535]
        result = _validate_forward_ports(boundary_ports, context)
        assert result == boundary_ports
        assert len(context.warnings) == 0
        assert len(context.notifications) == 0

    def test_invalid_port_zero(self):
        """Test that port 0 is rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([0], context)
        assert result == []
        assert len(context.warnings) == 2  # Individual warning + summary warning
        assert "outside the valid range" in context.warnings[0]
        assert "Port validation failed" in context.warnings[1]
        assert len(context.notifications) == 0

    def test_invalid_port_negative(self):
        """Test that negative ports are rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([-1, -100], context)
        assert result == []
        assert len(context.warnings) == 3  # 2 individual warnings + 1 summary warning
        assert "outside the valid range" in context.warnings[0]
        assert "outside the valid range" in context.warnings[1]
        assert "2 port(s)" in context.warnings[2]
        assert len(context.notifications) == 0

    def test_invalid_port_too_large(self):
        """Test that ports > 65535 are rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([65536, 99999], context)
        assert result == []
        assert len(context.warnings) == 3  # 2 individual warnings + 1 summary warning
        assert "outside the valid range" in context.warnings[0]
        assert "outside the valid range" in context.warnings[1]
        assert "2 port(s)" in context.warnings[2]
        assert len(context.notifications) == 0

    def test_invalid_port_string(self):
        """Test that string ports are rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports(["8080", "invalid"], context)
        assert result == []
        assert len(context.warnings) == 3  # 2 individual warnings + 1 summary warning
        assert "must be an integer" in context.warnings[0]
        assert "must be an integer" in context.warnings[1]
        assert "2 port(s)" in context.warnings[2]
        assert len(context.notifications) == 0

    def test_invalid_port_null(self):
        """Test that null/None values in the list are rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([None], context)
        assert result == []
        assert len(context.warnings) == 2  # Individual warning + summary warning
        assert "must be an integer" in context.warnings[0]
        assert "Port validation failed" in context.warnings[1]
        assert len(context.notifications) == 0

    def test_invalid_port_float(self):
        """Test that float ports are rejected."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([80.5, 443.9], context)
        assert result == []
        assert len(context.warnings) == 3  # 2 individual warnings + 1 summary warning
        assert "must be an integer" in context.warnings[0]
        assert "must be an integer" in context.warnings[1]
        assert "2 port(s)" in context.warnings[2]
        assert len(context.notifications) == 0

    def test_mixed_valid_and_invalid_ports(self):
        """Test that valid ports are kept and invalid ports are filtered out."""
        context = MockTaskSpecificContext()
        mixed_ports = [3000, 0, 8080, "invalid", 65536, 5432, None, 80]
        result = _validate_forward_ports(mixed_ports, context)
        # Should keep only: 3000, 8080, 5432, 80
        assert result == [3000, 8080, 5432, 80]
        assert len(context.warnings) == 5  # 4 individual warnings + 1 summary warning
        assert len(context.notifications) == 0
        # Check that summary warning has message for multiple failures
        assert "4 port(s)" in context.warnings[4]

    def test_single_invalid_port_notification_message(self):
        """Test that single invalid port gets specific error in warning."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([0], context)
        assert result == []
        assert len(context.warnings) == 2  # Individual warning + summary warning
        assert len(context.notifications) == 0
        # Should include the specific error in the summary warning
        assert "Skipping invalid" in context.warnings[1]

    def test_multiple_invalid_ports_notification_message(self):
        """Test that multiple invalid ports get summary in warning."""
        context = MockTaskSpecificContext()
        result = _validate_forward_ports([0, -1], context)
        assert result == []
        assert len(context.warnings) == 3  # 2 individual warnings + 1 summary warning
        assert len(context.notifications) == 0
        # Should have summary message in the last warning
        assert "2 port(s)" in context.warnings[2]

    def test_not_a_list_raises_error(self):
        """Test that non-list values raise DevcontainerError."""
        context = MockTaskSpecificContext()
        with pytest.raises(DevcontainerError, match="forwardPorts must be a list"):
            _validate_forward_ports("not a list", context)

        with pytest.raises(DevcontainerError, match="forwardPorts must be a list"):
            _validate_forward_ports(8080, context)

        with pytest.raises(DevcontainerError, match="forwardPorts must be a list"):
            _validate_forward_ports({"port": 8080}, context)

    def test_large_list_of_valid_ports(self):
        """Test that a large list of valid ports is handled correctly."""
        context = MockTaskSpecificContext()
        large_port_list = list(range(1000, 2000))
        result = _validate_forward_ports(large_port_list, context)
        assert result == large_port_list
        assert len(context.warnings) == 0
        assert len(context.notifications) == 0

    def test_duplicate_ports(self):
        """Test that duplicate ports are rejected with warnings."""
        context = MockTaskSpecificContext()
        duplicate_ports = [8080, 8080, 3000, 3000]
        result = _validate_forward_ports(duplicate_ports, context)
        # Should only keep first occurrence of each port
        assert result == [8080, 3000]
        # Should have 2 individual warnings for duplicates + 1 summary warning
        assert len(context.warnings) == 3
        assert "duplicate" in context.warnings[0].lower()
        assert "duplicate" in context.warnings[1].lower()
        assert "2 port(s)" in context.warnings[2]
        assert len(context.notifications) == 0

    def test_mixed_duplicates_and_invalid_ports(self):
        """Test that both duplicates and invalid ports are handled correctly."""
        context = MockTaskSpecificContext()
        mixed_ports = [8080, 8080, 0, 3000, 3000, "invalid", 65536, 443]
        result = _validate_forward_ports(mixed_ports, context)
        # Should only keep: 8080, 3000, 443 (first occurrence of each valid, non-duplicate port)
        assert result == [8080, 3000, 443]
        # Should have warnings for: 1 duplicate 8080, 1 port 0, 1 duplicate 3000, 1 string, 1 out of range = 5 + 1 summary
        assert len(context.warnings) == 6
        assert "5 port(s)" in context.warnings[5]
        assert len(context.notifications) == 0
