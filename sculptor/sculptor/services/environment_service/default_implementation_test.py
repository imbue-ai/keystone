import os
import tempfile
from pathlib import Path
from queue import Empty

import pytest

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.git import get_repo_base_path
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import Project
from sculptor.interfaces.environments.base import LocalImageConfig
from sculptor.primitives.constants import ANONYMOUS_ORGANIZATION_REFERENCE
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1


@pytest.fixture
def test_project(test_settings: SculptorSettings, test_service_collection: CompleteServiceCollection) -> Project:
    project_path: str | Path | None = os.getenv("PROJECT_PATH")
    if isinstance(project_path, str):
        project_path = Path(project_path)
    if not project_path:
        project_path = get_repo_base_path()
    with test_service_collection.data_model_service.open_transaction(request_id=RequestID()) as transaction:
        project = test_service_collection.project_service.initialize_project(
            project_path=project_path,
            organization_reference=ANONYMOUS_ORGANIZATION_REFERENCE,
            transaction=transaction,
        )
        test_service_collection.project_service.activate_project(project)
    assert project is not None, "By now, the project should be initialized."
    return project


def test_simple_local_environment_run(
    initial_commit_repo: tuple[Path, str],
    test_service_collection: CompleteServiceCollection,
    tmp_path: Path,
    test_project: Project,
    test_root_concurrency_group: ConcurrencyGroup,
) -> None:
    service = test_service_collection.environment_service
    config = LocalImageConfig(code_directory=tmp_path)
    with tempfile.TemporaryDirectory() as temp_dir:
        image = service.ensure_image(
            config,
            secrets={},
            active_repo_path=initial_commit_repo[0],
            cached_repo_path=Path(temp_dir),
            project_id=test_project.object_id,
            image_metadata=ImageMetadataV1.from_testing(),
        )
        with service.generate_environment(
            image, test_project.object_id, concurrency_group=test_root_concurrency_group
        ) as environment:
            process = environment.run_process_in_background(["echo", "hello"], secrets={})
            queue = process.get_queue()
            while not process.is_finished() or not queue.empty():
                try:
                    line, is_stdout = queue.get(timeout=0.1)
                except Empty:
                    continue
                if is_stdout:
                    assert line.strip() == "hello"


def test_simple_local_environment_run_with_content(
    initial_commit_repo: tuple[Path, str],
    test_service_collection: CompleteServiceCollection,
    tmp_path: Path,
    test_project: Project,
    test_root_concurrency_group: ConcurrencyGroup,
) -> None:
    service = test_service_collection.environment_service
    config = LocalImageConfig(code_directory=tmp_path)
    test_file_name = "test_file.txt"
    test_file_content = "hello"
    (tmp_path / test_file_name).write_text(test_file_content)
    with tempfile.TemporaryDirectory() as temp_dir:
        image = service.ensure_image(
            config,
            secrets={},
            active_repo_path=initial_commit_repo[0],
            cached_repo_path=Path(temp_dir),
            project_id=test_project.object_id,
            image_metadata=ImageMetadataV1.from_testing(),
        )
        with service.generate_environment(
            image, test_project.object_id, concurrency_group=test_root_concurrency_group
        ) as environment:
            process = environment.run_process_in_background(["cat", test_file_name], secrets={})
            queue = process.get_queue()
            while not process.is_finished() or not queue.empty():
                try:
                    line, is_stdout = queue.get(timeout=0.1)
                except Empty:
                    continue
                if is_stdout:
                    assert line.strip() == test_file_content


class TestDefaultEnvironmentService:
    def test_create_archived_repo_with_missing_file_in_index(
        self,
        empty_temp_git_repo: Path,
        test_root_concurrency_group: ConcurrencyGroup,
        test_service_collection: CompleteServiceCollection,
        test_project: Project,
        tmp_path: Path,
    ) -> None:
        """Exposes and solves for a tar failure when a file exists in git index but not on filesystem."""
        # Create a file and add it to git index
        test_file = empty_temp_git_repo / "CLAUDE.md"
        test_file.write_text("test content")
        test_root_concurrency_group.run_process_to_completion(["git", "add", "CLAUDE.md"], cwd=empty_temp_git_repo)
        # Delete the file from filesystem but keep it in index
        test_file.unlink()

        # Use the EnvironmentService to trigger create_archived_repo
        service = test_service_collection.environment_service
        config = LocalImageConfig(code_directory=tmp_path)
        service.ensure_image(
            config,
            secrets={},
            active_repo_path=empty_temp_git_repo,
            cached_repo_path=tmp_path / "cached",
            project_id=test_project.object_id,
            image_metadata=ImageMetadataV1.from_testing(),
            force_tarball_refresh=True,  # Force creation of tarball
        )
