import pytest

from sculptor.config.settings import SculptorSettings
from sculptor.testing.acceptance_config import set_acceptance_configuration


@pytest.mark.flaky(retries=3)
def test_default_test_settings_do_not_configure_imbue_gateway(test_settings: SculptorSettings):
    assert not test_settings.is_imbue_gateway_configured


def test_acceptance_configuration_adds_imbue_gateway(test_settings: SculptorSettings):
    acceptance_settings = set_acceptance_configuration(test_settings)
    assert acceptance_settings.is_imbue_gateway_configured
