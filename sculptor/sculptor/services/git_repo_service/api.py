from abc import ABC
from abc import abstractmethod
from contextlib import contextmanager
from typing import Generator

from sculptor.database.models import Project
from sculptor.primitives.service import Service
from sculptor.services.git_repo_service.git_repos import ReadOnlyGitRepo
from sculptor.services.git_repo_service.git_repos import WritableGitRepo


class GitRepoService(Service, ABC):
    """
    Provides an interface to the user's local git repository.

    All interactions with that repository should be done through this service.

    The two different context managers are mostly for convention, to declare your intent when accessing the repository.
    """

    @abstractmethod
    @contextmanager
    def open_local_user_git_repo_for_read(self, project: Project) -> Generator[ReadOnlyGitRepo, None, None]:
        """
        Open a local git repository for read access.

        Note that this access is exclusive --
        no other threads or processes will be able to access the repository while inside the context manager.

        This does *not* mean that there will be no concurrent access to the repository
        (because the user may, at any time, cause git commands to run on the repository).
        """

    @abstractmethod
    @contextmanager
    def open_local_user_git_repo_for_write(self, project: Project) -> Generator[WritableGitRepo, None, None]:
        """
        Open a local git repository for write access.

        Note that this access is exclusive --
        no other threads or processes will be able to access the repository while inside the context manager.

        This does *not* mean that there will be no concurrent access to the repository
        (because the user may, at any time, cause git commands to run on the repository).
        """
