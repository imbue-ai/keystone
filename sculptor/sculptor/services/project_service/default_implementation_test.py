from pathlib import Path

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from sculptor.database.models import Project
from sculptor.primitives.ids import OrganizationReference
from sculptor.services.project_service.default_implementation import DefaultProjectService


@pytest.fixture
def _test_project_mounted_and_disconnected() -> Project:
    project_id = ProjectID()
    organization_reference = OrganizationReference("test_organization")
    mounted_path = Path.home() / "mnt" / "test_repo"
    project = Project(
        object_id=project_id,
        name="Test Project",
        organization_reference=organization_reference,
        user_git_repo_url=f"file://{mounted_path}",
    )
    # TODO: somehow simulate what happens when a mounted remote directory becomes inaccessible
    return project


@pytest.mark.skip
def test_remote_project_filesystem_disconnected(
    _test_project_service: DefaultProjectService, _test_project_mounted_and_disconnected: Project
) -> None:
    # TODO Millan: this should make a project that uses a remote filesystem, then drop connection to that filesystem
    #  before calling _check_and_update_project_accessibility. Previously, calling this function in such a state would
    #  produce an unhandled exception which crashed the Sculptor backend.

    try:
        _test_project_service._check_and_update_project_accessibility(_test_project_mounted_and_disconnected)
    except OSError as e:
        pytest.fail(f"Remote filesystem project raised an OSError: {e}")
    except Exception as e:
        pass


class TestDefaultProjectService:
    def test_refresh_gitlab_token_with_activated_project_no_keyerror(
        self, _test_project_service: DefaultProjectService
    ) -> None:
        """Test that reproduces a race condition where _refresh_gitlab_token tries to provision tokens for
        active projects that don't have locks.

        Scenario:
            1. A project is activated (added to _active_projects) via activate_project()
            2. The background refresh thread iterates over active projects
            3. It calls _provision_gitlab_token_for_project() for a project without a lock
            4. KeyError is raised when trying to acquire the lock
        """
        project_id = ProjectID()
        organization_reference = OrganizationReference("test_organization")
        project = Project(
            object_id=project_id,
            name="Activated Project",
            organization_reference=organization_reference,
            user_git_repo_url="file:///nonexistent/path/to/repo",
            our_git_repo_url=None,
        )

        # Simulate the race condition: activate_project() adds to _active_projects
        # but does NOT create a lock in _token_refresh_locks
        _test_project_service.activate_project(project)

        # Verify the project is active but has no lock (the race condition state)
        assert project in _test_project_service.get_active_projects()
        assert project.object_id not in _test_project_service._token_refresh_locks

        # The background _refresh_gitlab_token thread calls _provision_gitlab_token_for_project on active projects
        _test_project_service._provision_gitlab_token_for_project(project)
