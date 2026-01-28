from playwright.sync_api import Locator
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.elements.base import PlaywrightIntegrationTestElement
from sculptor.testing.elements.feedback_buttons import PlaywrightFeedbackButtonsElement
from sculptor.testing.elements.feedback_dialog import PlaywrightFeedbackDialogElement
from sculptor.testing.elements.file_preview_and_upload import PlaywrightFilePreviewAndUploadMixin
from sculptor.testing.elements.task_modal import PlaywrightTaskModalElement


class PlaywrightChatPanelElement(PlaywrightFilePreviewAndUploadMixin, PlaywrightIntegrationTestElement):
    def get_chat_input(self) -> Locator:
        return self.get_by_test_id(ElementIDs.CHAT_INPUT)

    def get_send_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.SEND_BUTTON)

    def get_stop_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.STOP_BUTTON)

    def get_stop_button_spinner(self) -> Locator:
        return self.get_by_test_id(ElementIDs.STOP_BUTTON_SPINNER)

    def get_tool_call(self) -> Locator:
        return self.get_by_test_id(ElementIDs.TOOL_CALL)

    def get_context_summary_messages(self) -> Locator:
        return self.get_by_test_id(ElementIDs.CONTEXT_SUMMARY)

    def get_queued_message_card(self) -> Locator:
        return self.get_by_test_id(ElementIDs.QUEUED_MESSAGE_CARD)

    def get_delete_queued_message_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.DELETE_QUEUED_MESSAGE_BUTTON)

    def get_messages(self) -> Locator:
        all_messages = self.get_by_test_id(ElementIDs.CHAT_PANEL_MESSAGE)

        # Filter for assistant or user messages to avoid snapshot messages. May need changes in the future
        return all_messages.locator(
            f"[data-testid='{ElementIDs.ASSISTANT_MESSAGE}'], [data-testid='{ElementIDs.USER_MESSAGE}']"
        )

    def get_error_block(self) -> Locator:
        return self.get_by_test_id(ElementIDs.ERROR_BLOCK)

    def get_error_block_retry_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.ERROR_BLOCK_RETRY_BUTTON)

    def get_open_system_prompt_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.CHAT_PANEL_SYSTEM_PROMPT_OPEN_BUTTON)

    def get_system_prompt_text(self) -> Locator:
        return self.get_by_test_id(ElementIDs.CHAT_PANEL_SYSTEM_PROMPT_TEXT)

    def get_save_system_prompt_button(self) -> Locator:
        return self.get_by_test_id(ElementIDs.CHAT_PANEL_SYSTEM_PROMPT_SAVE_BUTTON)

    def get_action_bar(self, message_index: int) -> PlaywrightFeedbackButtonsElement:
        """Get the action bar for a specific message."""
        messages = self.get_messages()
        message = messages.nth(message_index)
        expect(message).to_be_visible()
        action_bar = message.get_by_test_id(ElementIDs.MESSAGE_ACTION_BAR)

        return PlaywrightFeedbackButtonsElement(locator=action_bar, page=self._page)

    def get_feedback_dialog(self) -> PlaywrightFeedbackDialogElement:
        feedback_dialog = self._page.get_by_test_id(ElementIDs.FEEDBACK_DIALOG)
        return PlaywrightFeedbackDialogElement(locator=feedback_dialog, page=self._page)

    def open_feedback_dialog(self, message_index: int, thumbs_up_button: bool | None = True) -> Locator:
        """Open the Feedback Dialog."""
        if thumbs_up_button:
            self.get_action_bar(message_index=message_index).get_thumbs_up_button().click()
        else:
            self.get_action_bar(message_index=message_index).get_thumbs_down_button().click()
        dialog = PlaywrightFeedbackDialogElement(
            locator=self._page.get_by_test_id(ElementIDs.FEEDBACK_DIALOG), page=self._page
        )
        expect(dialog).to_be_visible()

        return dialog

    def get_model_selector(self) -> Locator:
        return self.get_by_test_id(ElementIDs.MODEL_SELECTOR)

    def get_model_options(self) -> Locator:
        """Get all model options in the dropdown."""
        return self._page.get_by_test_id(ElementIDs.MODEL_OPTION)

    def get_forked_to_block(self, block_index: int | None = None) -> Locator:
        """Get the forked to block (shown in parent task).

        Args:
            block_index: Index of the forked to block. If None, asserts there's only one block.
        """
        blocks = self.get_by_test_id(ElementIDs.FORKED_TO_BLOCK)
        if block_index is not None:
            return blocks.nth(block_index)
        else:
            # If no index specified, assert there's only one block
            expect(blocks).to_have_count(1)
            return blocks.first

    def get_forked_from_block(self, block_index: int | None = None) -> Locator:
        """Get the forked from block (shown in child task)."""
        blocks = self.get_by_test_id(ElementIDs.FORKED_FROM_BLOCK)
        if block_index is not None:
            return blocks.nth(block_index)
        else:
            # If no index specified, assert there's only one block
            expect(blocks).to_have_count(1)
            return blocks.first

    def get_fork_button(self, message_index: int | None = None) -> Locator:
        """Wait for the requested message to be done snapshotting, then return the fork button."""
        if message_index is None:
            # Use the last message
            messages = self.get_messages()
            message_index = messages.count() - 1

        action_bar = self.get_action_bar(message_index=message_index)
        fork_button = action_bar.get_fork_button()
        expect(fork_button).to_be_visible()
        return fork_button

    def fork_task(self, prompt: str, message_index: int | None = None) -> None:
        """Execute the fork workflow: click fork button, enter prompt, and submit."""
        fork_button = self.get_fork_button(message_index)
        fork_button.click()

        # Task modal should open in fork mode
        task_modal = PlaywrightTaskModalElement(
            locator=self._page.get_by_test_id(ElementIDs.TASK_MODAL), page=self._page
        )
        expect(task_modal).to_be_visible()

        # Enter prompt
        prompt_input = task_modal.get_input_element()
        prompt_input.type(prompt)

        # Click "Fork Task" button
        task_modal.fork_task()
        expect(task_modal).not_to_be_visible()

    def navigate_to_forked_task(self, block_index: int = 0) -> None:
        """Navigate to forked task by clicking the ForkedToBlock button."""
        forked_to_blocks = self.get_by_test_id(ElementIDs.FORKED_TO_BLOCK)
        forked_to_block = forked_to_blocks.nth(block_index)
        expect(forked_to_block).to_be_visible()
        forked_to_block.get_by_test_id(ElementIDs.FORK_BLOCK_BUTTON).click()

    def navigate_to_parent_task(self, block_index: int = 0) -> None:
        """Navigate to parent task by clicking the ForkedFromBlock button."""
        forked_from_blocks = self.get_by_test_id(ElementIDs.FORKED_FROM_BLOCK)
        forked_from_block = forked_from_blocks.nth(block_index)
        expect(forked_from_block).to_be_visible()
        # aad7cde6-a1ff-440e-a7c9-209272d31dc8:
        # For reasons I don't fully understand,
        # this often triggers a beforeunload dialog,
        # requiring a workaround in Electron testing (look for the UUID for more details).
        forked_from_block.get_by_test_id(ElementIDs.FORK_BLOCK_BUTTON).click()


def expect_message_to_have_role(message: Locator, role: ElementIDs) -> None:
    expect(message).to_have_attribute("data-testid", role)


def wait_for_completed_message_count(chat_panel: PlaywrightChatPanelElement, expected_message_count: int) -> None:
    """Wait for assistant to finish responding."""
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel.get_messages()).to_have_count(expected_message_count)
    expect(chat_panel).to_have_attribute("data-is-streaming", "false")


def send_chat_message(chat_panel, message: str) -> None:
    """Send a message in chat and verify input is cleared."""
    chat_input = chat_panel.get_chat_input()
    chat_input.type(message)
    chat_panel.get_send_button().click()
    expect(chat_input).to_have_text("")


def select_model_by_name(chat_panel: PlaywrightChatPanelElement, model_name: str) -> str:
    """Select a model by its exact name from the model selector dropdown and return the selected text.

    Args:
        chat_panel: The chat panel element
        model_name: The exact name of the model to select

    Returns:
        The text shown in the selector after selection
    """
    model_selector = chat_panel.get_model_selector()
    # Open the dropdown
    model_selector.click()

    # Get all options and find the one with exact matching text
    options = chat_panel.get_model_options()

    # Check each option for exact match
    target_model_option = only([option for option in options.all() if option.inner_text().strip() == model_name])
    target_model_option.click()

    return model_selector.inner_text()
