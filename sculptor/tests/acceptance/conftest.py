from pathlib import Path
from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup  # noqa: F401
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import Project
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import RequestID
from sculptor.primitives.ids import UserReference
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.service_collections.service_collection import get_services
from sculptor.testing.acceptance_config import set_acceptance_configuration
from sculptor.testing.repo_resources import generate_test_project_repo
from sculptor.testing.resources import container_prefix_  # noqa: F401
from sculptor.testing.resources import credentials_  # noqa: F401
from sculptor.testing.resources import database_url_  # noqa: F401
from sculptor.testing.resources import sculptor_factory_  # noqa: F401
from sculptor.testing.resources import sculptor_folder_  # noqa: F401
from sculptor.testing.resources import sculptor_launch_mode_  # noqa: F401
from sculptor.testing.resources import sculptor_page_  # noqa: F401
from sculptor.testing.resources import snapshot_path_  # noqa: F401


@pytest.fixture
def test_service_collection(
    test_settings: SculptorSettings,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[CompleteServiceCollection, None, None]:
    test_settings = set_acceptance_configuration(test_settings)
    services = get_services(test_root_concurrency_group, test_settings)
    with services.run_all():
        yield services


@pytest.fixture
def dockerfile_path(sculptor_root_path: Path) -> Generator[Path, None, None]:
    yield sculptor_root_path / "claude-container" / "Dockerfile"


@pytest.fixture
def mock_repo_path(
    request: pytest.FixtureRequest, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[Path, None, None]:
    with generate_test_project_repo(request, test_root_concurrency_group) as repo:
        yield repo.base_path


@pytest.fixture
def test_user_email() -> str:
    return "test@imbue.com"


@pytest.fixture
def test_user_org_project(
    test_service_collection: CompleteServiceCollection,
    mock_repo_path: Path,
    test_user_email: str,
) -> tuple[UserReference, OrganizationReference, Project]:
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        user_reference = UserReference("test_user")  # Using UserReference for consistency
        organization_reference = OrganizationReference(
            "test_organization"
        )  # Using OrganizationReference for consistency
        project_id = ProjectID()
        project = Project(
            object_id=project_id,
            name="Test Project",
            organization_reference=organization_reference,
            user_git_repo_url=f"file://{mock_repo_path}",
        )
        transaction.upsert_project(project)
        return user_reference, organization_reference, project


@pytest.fixture
def test_project(test_user_org_project: tuple[UserReference, OrganizationReference, Project]) -> Project:
    return test_user_org_project[2]


@pytest.fixture
def test_user_reference(test_user_org_project: tuple[UserReference, OrganizationReference, Project]) -> UserReference:
    return test_user_org_project[0]


@pytest.fixture
def test_organization_reference(
    test_user_org_project: tuple[UserReference, OrganizationReference, Project],
) -> OrganizationReference:
    return test_user_org_project[1]


@pytest.fixture
def test_project_id(test_user_org_project: tuple[UserReference, OrganizationReference, Project]) -> ProjectID:
    return test_user_org_project[2].object_id
