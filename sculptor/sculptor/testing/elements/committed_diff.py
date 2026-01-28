from playwright.sync_api import Locator
from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.elements.base import PlaywrightIntegrationTestElement
from sculptor.testing.elements.file_artifact import PlaywrightFileArtifactElement


class PlaywrightCommittedDiffElement(PlaywrightIntegrationTestElement):
    def get_expand_button(self) -> Locator:
        """Get the expand/collapse button for the committed section."""
        return self.get_by_test_id(ElementIDs.ARTIFACT_COMMITTED_SECTION_EXPAND)

    def ensure_expanded(self):
        """Ensure the committed section is expanded."""
        expand_button = self.get_expand_button()
        if expand_button.get_attribute("data-state") == "collapsed":
            expand_button.click()
        expect(expand_button).to_have_attribute("data-state", "expanded")

    def get_file_artifacts(self) -> Locator:
        """Get all file dropdown elements in the committed section."""
        return self.get_by_test_id(ElementIDs.ARTIFACT_FILE)

    def get_nth_file_artifact_element(self, n: int) -> PlaywrightFileArtifactElement:
        """Get the nth file artifact element in the committed section."""
        file_locators = self.get_by_test_id(ElementIDs.ARTIFACT_FILE)
        expect(file_locators.nth(n)).to_be_visible()
        return PlaywrightFileArtifactElement(locator=file_locators.nth(n), page=self._page)
