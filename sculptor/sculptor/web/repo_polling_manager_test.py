from contextlib import contextmanager
from typing import Iterator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from sculptor.database.models import Project
from sculptor.primitives.ids import OrganizationReference
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.web import repo_polling_manager


class _FakePollingSource:
    def __init__(self, *, project_id: ProjectID) -> None:
        self.project_id = project_id
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @contextmanager
    def thread_polling_into_queue(self) -> Iterator[None]:
        self.start()
        try:
            yield
        finally:
            self.stop()


class _FakeProjectService:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    def get_active_projects(self) -> list[Project]:
        return self._projects


class _FakeRepoCallback:
    def __init__(self, _services, project_id: ProjectID) -> None:
        self.project_id = project_id

    def __call__(self) -> None:
        return None


class _FakeRepoServices:
    def __init__(self, project_service: _FakeProjectService) -> None:
        self.project_service = project_service


def _make_project(*, is_deleted: bool = False) -> Project:
    return Project(
        object_id=ProjectID(),
        organization_reference=OrganizationReference("org"),
        name="project",
        is_deleted=is_deleted,
    )


class _RepoManagerFixture:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._created_sources: dict[ProjectID, _FakePollingSource] = {}
        monkeypatch.setattr(
            repo_polling_manager,
            "StopGapBackgroundPollingStreamSource",
            self._fake_source_factory,
        )
        monkeypatch.setattr(
            repo_polling_manager,
            "_LocalRepoInfoExfiltrationCallback",
            _FakeRepoCallback,
        )

    def _fake_source_factory(self, *, polling_callback, output_queue, check_interval_in_seconds, concurrency_group):
        source = _FakePollingSource(project_id=polling_callback.project_id)
        self._created_sources[source.project_id] = source
        return source

    def create_manager(
        self, projects: list[Project]
    ) -> tuple[repo_polling_manager._LocalRepoInfoPollingManager, dict[ProjectID, _FakePollingSource]]:
        self._created_sources.clear()
        services = _FakeRepoServices(_FakeProjectService(projects))
        manager = repo_polling_manager._LocalRepoInfoPollingManager(
            services=services,  # pyre-ignore[6]: deliberately using a fake services object
            queue=None,  # pyre-ignore[6]: not using a queue for the test even though this is invalid in general
            concurrency_group=None,  # pyre-ignore[6]: not using a concurrency group for the test even though this is invalid in general
        )
        return manager, self._created_sources


@pytest.fixture
def repo_manager_fixture(monkeypatch: pytest.MonkeyPatch) -> _RepoManagerFixture:
    return _RepoManagerFixture(monkeypatch)


def test_initialize_starts_watchers(repo_manager_fixture: _RepoManagerFixture) -> None:
    project = _make_project()
    manager, sources = repo_manager_fixture.create_manager([project])

    manager.initialize()

    assert project.object_id in manager._sources_by_project_id
    assert sources[project.object_id].started


def test_sync_projects_adds_and_removes(repo_manager_fixture: _RepoManagerFixture) -> None:
    project = _make_project()
    manager, sources = repo_manager_fixture.create_manager([project])
    manager.initialize()

    deleted = project.model_copy(update={"is_deleted": True})
    new_project = _make_project()
    transaction = CompletedTransaction(
        request_id=None,
        updated_models=(deleted, new_project),
    )

    manager.update_pollers_based_on_stream([transaction])

    assert project.object_id not in manager._sources_by_project_id
    assert sources[project.object_id].stopped
    assert new_project.object_id in manager._sources_by_project_id
    assert sources[new_project.object_id].started


def test_shutdown_stops_all(repo_manager_fixture: _RepoManagerFixture) -> None:
    project_a = _make_project()
    project_b = _make_project()
    manager, sources = repo_manager_fixture.create_manager([project_a, project_b])
    manager.initialize()

    manager.shutdown()

    assert not manager._sources_by_project_id
    assert sources[project_a.object_id].stopped
    assert sources[project_b.object_id].stopped
