import re

from playwright.sync_api import Locator
from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.elements.artifacts_panel import PlaywrightArtifactsPanelElement
from sculptor.testing.elements.chat_panel import PlaywrightChatPanelElement
from sculptor.testing.elements.compaction_header import PlaywrightCompactionBarElement
from sculptor.testing.elements.compaction_panel import PlaywrightCompactionPanelElement
from sculptor.testing.elements.task_header import PlaywrightTaskHeaderElement
from sculptor.testing.elements.task_modal import PlaywrightTaskModalElement
from sculptor.testing.pages.project_layout import PlaywrightProjectLayoutPage


class PlaywrightTaskPage(PlaywrightProjectLayoutPage):
    def get_chat_panel(self) -> PlaywrightChatPanelElement:
        chat_panel = self.get_by_test_id(ElementIDs.CHAT_PANEL)
        return PlaywrightChatPanelElement(locator=chat_panel, page=self._page)

    def get_task_header(self) -> PlaywrightTaskHeaderElement:
        task_header = self.get_by_test_id(ElementIDs.TASK_HEADER)
        return PlaywrightTaskHeaderElement(locator=task_header, page=self._page)

    def get_branch_name_element(self) -> Locator:
        branch_name = self.get_by_test_id(ElementIDs.BRANCH_NAME)
        expect(branch_name).to_be_visible()
        expect(branch_name, "to be generated").not_to_have_attribute("data-is-skeleton", "true")
        return branch_name

    def get_branch_name(self) -> str:
        return self.get_branch_name_element().text_content()

    def get_source_branch_name(self) -> str:
        element = self.get_branch_name_element()
        # await for the data to be non-emtpy as a sanity check
        expect(element, "to have internal attribute").to_have_attribute("data-source-branch", re.compile("."))
        return element.get_attribute("data-source-branch")

    def get_task_id(self) -> str:
        """Extract the task ID from the current URL.

        The URL format is expected to be: /projects/{projectID}/chat/{taskID}
        """
        current_url = self._page.url
        # Extract task ID from URL using regex
        match = re.search(r"/chat/([a-zA-Z0-9_-]+)", current_url)
        if not match:
            raise ValueError(f"Could not extract task ID from URL: {current_url}")
        return match.group(1)

    def get_artifacts_panel(self) -> PlaywrightArtifactsPanelElement:
        artifacts_panel = self.get_by_test_id(ElementIDs.ARTIFACT_PANEL)
        return PlaywrightArtifactsPanelElement(locator=artifacts_panel, page=self._page)

    def navigate_to_home(self) -> None:
        """Navigate to home page via the sidebar home button."""
        sidebar = self.ensure_sidebar_is_open()
        sidebar.navigate_to_home()

    def get_task_modal(self) -> PlaywrightTaskModalElement:
        task_modal = self._page.get_by_test_id(ElementIDs.TASK_MODAL)
        return PlaywrightTaskModalElement(locator=task_modal, page=self._page)

    def get_compaction_bar(self) -> PlaywrightCompactionBarElement:
        compaction_bar = self._page.get_by_test_id(ElementIDs.COMPACTION_BAR)
        return PlaywrightCompactionBarElement(locator=compaction_bar, page=self._page)

    def get_compaction_panel(self) -> PlaywrightCompactionPanelElement:
        compaction_panel = self.get_by_test_id(ElementIDs.COMPACTION_PANEL)
        return PlaywrightCompactionPanelElement(locator=compaction_panel, page=self._page)

    def verify_uncommitted_file(
        self, file_name: str, expected_content: str | None = None, not_expected_content: str | None = None
    ) -> None:
        """Verify a file exists in uncommitted changes with expected content.

        Args:
            file_name: The name of the file to verify
            expected_content: Content that should be present in the file
            not_expected_content: Content that should NOT be present in the file (optional)
        """
        artifacts_panel = self.get_artifacts_panel()
        expect(artifacts_panel).to_be_visible()
        # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
        artifacts_panel.get_combined_diff_tab().click()
        diff_artifact = artifacts_panel.get_combined_diff_section()
        uncommitted_section = diff_artifact.get_uncommitted_section()

        file_artifacts = uncommitted_section.get_file_artifacts()
        expect(file_artifacts).to_have_count(1)
        file_artifact = uncommitted_section.get_nth_file_artifact_element(0)
        expect(file_artifact.get_file_name()).to_contain_text(file_name)
        file_artifact.ensure_body_visible()
        file_body = file_artifact.get_file_body()
        if expected_content:
            expect(file_body).to_contain_text(expected_content)
        if not_expected_content:
            expect(file_body).not_to_contain_text(not_expected_content)

    def verify_committed_file(
        self, file_name: str, expected_content: str, file_index: int = 0, not_expected_content: str | None = None
    ) -> None:
        """Verify a file exists in committed changes with expected content.

        Args:
            file_name: The name of the file to verify
            expected_content: Content that should be present in the file
            file_index: Index of the file in committed section (default 0)
            not_expected_content: Content that should NOT be present in the file (optional)
        """
        self.ensure_committed_changes_section_visible()

        artifacts_panel = self.get_artifacts_panel()
        expect(artifacts_panel).to_be_visible()
        # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
        artifacts_panel.get_combined_diff_tab().click()
        diff_artifact = artifacts_panel.get_combined_diff_section()
        committed_section = diff_artifact.get_committed_section()

        file_artifact = committed_section.get_nth_file_artifact_element(file_index)
        expect(file_artifact.get_file_name()).to_contain_text(file_name)
        file_artifact.ensure_body_visible()
        file_body = file_artifact.get_file_body()
        expect(file_body).to_contain_text(expected_content)
        if not_expected_content:
            expect(file_body).not_to_contain_text(not_expected_content)

    def verify_uncommitted_file_count(self, expected_count: int) -> None:
        """Verify the number of uncommitted files."""

        artifacts_panel = self.get_artifacts_panel()
        expect(artifacts_panel).to_be_visible()
        artifacts_panel.get_combined_diff_tab().click()
        diff_artifact = artifacts_panel.get_combined_diff_section()
        uncommitted_section = diff_artifact.get_uncommitted_section()
        file_artifacts = uncommitted_section.get_file_artifacts()
        expect(file_artifacts).to_have_count(expected_count)

    def ensure_committed_changes_section_visible(self) -> None:
        artifacts_panel = self.get_artifacts_panel()
        expect(artifacts_panel).to_be_visible()
        artifacts_panel.get_combined_diff_tab().click()
        diff_artifact = artifacts_panel.get_combined_diff_section()
        committed_section = diff_artifact.get_committed_section()
        expect(committed_section).to_be_visible()
        committed_section.ensure_expanded()
