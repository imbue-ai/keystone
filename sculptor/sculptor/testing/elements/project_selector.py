from playwright.sync_api import Locator
from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.elements.base import PlaywrightIntegrationTestElement
from sculptor.testing.elements.open_new_repo_dialog import OpenNewRepoDialogElement
from sculptor.testing.elements.project_git_init_dialog import PlaywrightGitInitDialogElement


class PlaywrightProjectSelectorElement(PlaywrightIntegrationTestElement):
    """Project selector component that appears in sidebar and other locations."""

    def get_selector_trigger(self) -> Locator:
        """Get the main project selector dropdown trigger."""
        return self

    def get_project_options(self) -> Locator:
        """Get all project options in the dropdown."""
        return self._page.get_by_test_id(ElementIDs.PROJECT_SELECT_ITEM)

    def get_open_new_repo_button(self) -> Locator:
        """Get the 'Open New Repo' option."""
        return self._page.get_by_test_id(ElementIDs.OPEN_NEW_REPO_BUTTON)

    def get_open_new_repo_dialog(self) -> OpenNewRepoDialogElement:
        """Get the open new repo dialog."""
        return OpenNewRepoDialogElement(self._page.get_by_test_id(ElementIDs.OPEN_NEW_REPO_DIALOG), page=self._page)

    def get_git_init_dialog(self) -> PlaywrightGitInitDialogElement:
        """Get the git init dialog element."""
        dialog = self._page.get_by_test_id(ElementIDs.PROJECT_GIT_INIT_DIALOG)
        return PlaywrightGitInitDialogElement(locator=dialog, page=self._page)

    def select_project_by_name(self, project_name: str, path_contains: str | None = None) -> None:
        """Select a project by its name from the dropdown.

        path_contains is optional in most cases, but is required to disambiguate if there are multiple
        projects with the same name but different paths. In that case, some distinguishing part of the
        rendered path must be provided in path_contains.
        """
        # Open the dropdown
        self.get_selector_trigger().click()

        # Find and click the project option
        project_options = self.get_project_options()
        matching_options = []

        for option in project_options.all():
            option_text = option.inner_text().strip()
            if option_text.startswith(project_name):
                if path_contains is None or path_contains in option_text:
                    matching_options.append(option)

        if len(matching_options) == 0:
            raise ValueError(f"Project '{project_name}' not found in dropdown")

        if len(matching_options) > 1:
            raise ValueError(
                f"Multiple projects named '{project_name}' found. Specify the 'path_contains' parameter to disambiguate."
            )

        matching_options[0].click()
        expect(self).to_contain_text(project_name)

    def open_new_repo_dialog(self) -> OpenNewRepoDialogElement:
        """Open the 'Open New Repo' dialog."""
        expect(self.get_selector_trigger()).to_be_visible()
        self.get_selector_trigger().click()

        expect(self.get_open_new_repo_button()).to_be_visible()
        self.get_open_new_repo_button().click()

        dialog = self.get_open_new_repo_dialog()
        expect(dialog).to_be_visible()
        return dialog

    def get_current_project_name(self) -> str:
        """Get the currently selected project name."""
        return self.get_selector_trigger().inner_text().strip()
