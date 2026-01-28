"""Integration tests for the Settings page functionality."""

from pathlib import Path

from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.services.config_service.user_config import load_config
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.playwright_utils import get_playwright_modifier_key
from sculptor.testing.playwright_utils import navigate_to_settings_page


# FIXME(qi): This test fails on dev-electron, but I haven't been able to reproduce the failure locally.
def test_telemetry_settings_update_config_file(sculptor_page_: PlaywrightHomePage, sculptor_folder_: Path):
    sculptor_config_path_ = sculptor_folder_ / "config.toml"
    """Test that changing telemetry settings updates the config file correctly."""
    # Navigate to settings
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)
    account_section = settings_page.click_on_account()

    # Read initial config
    initial_config = load_config(sculptor_config_path_)

    # Verify initial state (should be false by default for most telemetry settings)
    assert initial_config.is_error_reporting_enabled is True
    assert initial_config.is_product_analytics_enabled is True
    assert initial_config.is_llm_logs_enabled is True
    assert initial_config.is_session_recording_enabled is True

    # Change to "Essential only"
    account_section.select_telemetry_level("Essential only")

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Read updated config
    analytics_config = load_config(sculptor_config_path_)

    # Verify error reporting and analytics are enabled
    assert analytics_config.is_error_reporting_enabled is True
    assert analytics_config.is_product_analytics_enabled is True
    assert analytics_config.is_llm_logs_enabled is False
    assert analytics_config.is_session_recording_enabled is False

    # Wait for the toast to disappear
    expect(toast).to_have_count(0)

    # Change to "Send error reports only"
    account_section.select_telemetry_level("Standard")

    # Wait for toast to confirm the change was saved
    expect(toast).to_be_visible()

    # Read updated config
    updated_config = load_config(sculptor_config_path_)

    # Verify only error reporting is enabled
    assert updated_config.is_error_reporting_enabled is True
    assert updated_config.is_product_analytics_enabled is True
    assert updated_config.is_llm_logs_enabled is True
    assert updated_config.is_session_recording_enabled is False

    # Wait for the toast to disappear
    expect(toast).to_have_count(0)

    # Change to full telemetry
    account_section.select_telemetry_level("Full contribution")

    # Wait for toast to confirm the change was saved
    expect(toast).to_be_visible()

    # Read updated config
    full_config = load_config(sculptor_config_path_)

    # Verify all telemetry is enabled
    assert full_config.is_error_reporting_enabled is True
    assert full_config.is_product_analytics_enabled is True
    assert full_config.is_llm_logs_enabled is True
    assert full_config.is_session_recording_enabled is False


def test_git_username_setting_updates_config_file(sculptor_page_: PlaywrightHomePage, sculptor_folder_: Path):
    """Test that changing git username updates the config file correctly."""
    sculptor_config_path_ = sculptor_folder_ / "config.toml"
    # Navigate to settings
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)

    # Change the git username
    account_section = settings_page.click_on_account()
    new_username = "test-user-123"
    account_section.edit_git_username(new_username)

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Read updated config
    updated_config = load_config(sculptor_config_path_)

    # Verify the username was updated
    assert updated_config.user_git_username == new_username

    # Wait for the toast to disappear
    expect(toast).to_have_count(0)

    # Change it back to another value
    another_username = "different-user-456"
    account_section.edit_git_username(another_username)

    # Wait for the config to be updated
    expect(toast).to_be_visible()

    # Read final config
    final_config = load_config(sculptor_config_path_)

    # Verify the username was updated again
    assert final_config.user_git_username == another_username


# FIXME(qi): This test fails on dev-electron, but I haven't been able to reproduce the failure locally.
def test_keybinding_settings_new_agent_shortcut(sculptor_page_: PlaywrightHomePage):
    """Test that new agent keybinding works with default and custom values."""
    mod_key = get_playwright_modifier_key()
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)

    # Test default keybinding (Cmd+N) works
    settings_page.press_keyboard_shortcut(f"{mod_key}+n")

    # Verify task modal opened
    task_modal = settings_page.get_task_modal()
    expect(task_modal).to_be_visible()

    # Close the modal
    task_modal.close()

    # Navigate to settings and change the keybinding
    keybindings_section = settings_page.click_on_keybindings()

    # Set a new keybinding (Cmd+Shift+A)
    keybindings_section.clear_new_agent_hotkey()
    keybindings_section.set_new_agent_hotkey(f"{mod_key}+Shift+a")

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Test that old keybinding no longer works
    settings_page.press_keyboard_shortcut(f"{mod_key}+n")
    expect(task_modal).not_to_be_visible()

    # Test new keybinding works
    settings_page.press_keyboard_shortcut(f"{mod_key}+Shift+a")
    expect(task_modal).to_be_visible()


def test_keybinding_settings_search_agents_shortcut(sculptor_page_: PlaywrightHomePage):
    """Test that search agents keybinding works with default and custom values."""
    mod_key = get_playwright_modifier_key()
    home_page = sculptor_page_
    # wait for the page to load
    expect(home_page.get_task_starter()).to_be_visible()

    # Test default keybinding (Cmd+K) works
    home_page.press_keyboard_shortcut(f"{mod_key}+k")

    # Verify search modal opened
    search_modal = home_page.get_search_modal()
    expect(search_modal).to_be_visible()
    search_modal.close()

    # Navigate to settings and change the keybinding
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)
    keybindings_section = settings_page.click_on_keybindings()

    # Set a new keybinding (Cmd+Shift+F)
    keybindings_section.clear_search_agents_hotkey()
    keybindings_section.set_search_agents_hotkey(f"{mod_key}+Shift+f")

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Test that old keybinding no longer works
    settings_page.press_keyboard_shortcut(f"{mod_key}+k")
    expect(search_modal).not_to_be_visible()

    # Test new keybinding works
    settings_page.press_keyboard_shortcut(f"{mod_key}+Shift+f")
    expect(search_modal).to_be_visible()


def test_keybinding_settings_toggle_sidebar_shortcut(sculptor_page_: PlaywrightHomePage):
    """Test that toggle sidebar keybinding works with default and custom values."""
    mod_key = get_playwright_modifier_key()
    home_page = sculptor_page_

    # Ensure sidebar is open initially
    sidebar = home_page.ensure_sidebar_is_open()

    # Test default keybinding (Cmd+S) works to close sidebar
    home_page.press_keyboard_shortcut(f"{mod_key}+s")
    expect(sidebar).not_to_be_visible()

    # Test default keybinding works to open sidebar
    home_page.press_keyboard_shortcut(f"{mod_key}+s")
    expect(sidebar).to_be_visible()

    # Navigate to settings and change the keybinding
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)
    keybindings_section = settings_page.click_on_keybindings()

    # Set a new keybinding (Cmd+Shift+S)
    keybindings_section.clear_toggle_sidebar_hotkey()
    keybindings_section.set_toggle_sidebar_hotkey(f"{mod_key}+Shift+s")

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Test that old keybinding no longer works
    settings_page.press_keyboard_shortcut(f"{mod_key}+s")
    expect(sidebar).to_be_visible()

    # Test new keybinding works to close sidebar
    settings_page.press_keyboard_shortcut(f"{mod_key}+Shift+s")
    expect(sidebar).not_to_be_visible()

    # Test new keybinding works to open sidebar
    settings_page.press_keyboard_shortcut(f"{mod_key}+Shift+s")
    expect(sidebar).to_be_visible()


# FIXME(qi): This test fails on dev-electron, but I haven't been able to reproduce the failure locally.
def test_default_model_setting_affects_new_task_modal(sculptor_page_: PlaywrightHomePage):
    """Test that changing the default model affects the initial model selection in new task modal."""
    mod_key = get_playwright_modifier_key()
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()
    settings_page = navigate_to_settings_page(sidebar=sidebar, page=sculptor_page_)

    # Open task modal and check initial default model
    settings_page.press_keyboard_shortcut(f"{mod_key}+n")

    # Verify task modal opened
    task_modal = settings_page.get_task_modal()
    expect(task_modal).to_be_visible()

    # Check initial model selector value (should be Claude 4.5 Sonnet by default)
    model_selector = task_modal.get_model_selector()
    expect(model_selector).to_contain_text("Sonnet")
    task_modal.close()

    # Change default model to Claude 4.1 Opus
    general_section = settings_page.click_on_general()
    general_section.select_default_model("Claude 4.5 Opus")

    # Wait for toast to confirm the change was saved
    toast = settings_page.get_by_test_id(ElementIDs.TOAST)
    expect(toast).to_be_visible()

    # Open task modal again and verify the default model changed
    settings_page.press_keyboard_shortcut(f"{mod_key}+n")
    expect(task_modal).to_be_visible()

    # Check that model selector now shows Claude 4.1 Opus as default
    model_selector = task_modal.get_model_selector()
    expect(model_selector).to_contain_text("Opus")
    task_modal.close()
