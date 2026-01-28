"""
Handles for tracking progress of downloads and subprocesses using Pydantic models.

These handles update progress models and invoke callbacks to report progress updates.
"""

from __future__ import annotations

import shlex
from datetime import datetime
from threading import RLock
from typing import Callable
from typing import Sequence

from pydantic import AnyUrl

from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.progress_tracking.progress_models import BranchNameAndTaskTitleProgress
from imbue_core.progress_tracking.progress_models import DownloadProgress
from imbue_core.progress_tracking.progress_models import MultiOperationProgress
from imbue_core.progress_tracking.progress_models import OperationState
from imbue_core.progress_tracking.progress_models import ProgressID
from imbue_core.progress_tracking.progress_models import ProgressTypes
from imbue_core.progress_tracking.progress_models import RootProgress
from imbue_core.progress_tracking.progress_models import SubprocessProgress
from imbue_core.progress_tracking.progress_tracking import BranchNameAndTaskTitleProgressHandle
from imbue_core.progress_tracking.progress_tracking import DownloadHandle
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import RootProgressHandle
from imbue_core.progress_tracking.progress_tracking import SubprocessHandle
from imbue_core.progress_tracking.progress_tracking import UnstartedHandle
from imbue_core.progress_tracking.progress_tracking import get_unstarted


class DownloadModelHandle(DownloadHandle):
    def __init__(
        self,
        update_callback: Callable[[DownloadProgress], None],
        url: AnyUrl,
        description: str | None = None,
        lock: RLock | None = None,
    ) -> None:
        self._lock = lock if lock is not None else RLock()
        self._progress = DownloadProgress(
            progress_id=ProgressID(),
            url=url,
            description=description,
            latest_update_time=datetime.now(),
            state=OperationState.NOT_STARTED,
        )
        self._update_callback = update_callback

    def on_start(self) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            assign(progress_evolver.state, lambda: OperationState.IN_PROGRESS)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_size(self, total_bytes: int) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.total_bytes, lambda: total_bytes)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_progress(self, total_bytes_downloaded: int) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.bytes_downloaded, lambda: total_bytes_downloaded)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def finish(self) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.state, lambda: OperationState.COMPLETED)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_failure(self, explanation: str) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.state, lambda: OperationState.FAILED)
            assign(progress_evolver.failure_explanation, lambda: explanation)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_failed_attempt(self, explanation: str) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.failure_explanation, lambda: explanation)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)


class SubprocessModelHandle(SubprocessHandle):
    def __init__(
        self,
        update_callback: Callable[[SubprocessProgress], None],
        description: str | None = None,
        lock: RLock | None = None,
    ) -> None:
        self._lock = lock if lock is not None else RLock()
        self._progress = SubprocessProgress(
            progress_id=ProgressID(),
            description=description,
            latest_update_time=datetime.now(),
            state=OperationState.NOT_STARTED,
        )
        self._update_callback = update_callback

    def on_start(self) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            assign(progress_evolver.state, lambda: OperationState.IN_PROGRESS)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_command(self, command: str | Sequence[str]) -> None:
        command_str = command if isinstance(command, str) else shlex.join(command)
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.command, lambda: command_str)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_output_line(self, line: str, is_stderr: bool) -> None:
        # Eventually we would like to wire this up to associate log lines with the progress ID.
        # For now, we do nothing (since log lines are high-volume).
        pass

    def report_return_code(self, return_code: int) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.return_code, lambda: return_code)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def finish(self) -> None:
        """Report that the subprocess has finished successfully.

        If a non-zero return code was previously reported, this will mark the subprocess
        as FAILED instead of COMPLETED with a default failure explanation.
        """
        with self._lock:
            progress_evolver = evolver(self._progress)
            if self._progress.return_code is not None and self._progress.return_code != 0:
                assign(progress_evolver.state, lambda: OperationState.FAILED)
                if self._progress.failure_explanation is None:
                    assign(
                        progress_evolver.failure_explanation,
                        lambda: f"Process exited with non-zero return code {self._progress.return_code}",
                    )
            else:
                assign(progress_evolver.state, lambda: OperationState.COMPLETED)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_failure(self, explanation: str) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.state, lambda: OperationState.FAILED)
            assign(progress_evolver.failure_explanation, lambda: explanation)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)


class MultiOperationModelHandle(ProgressHandle):
    def __init__(self, update_callback: Callable[[MultiOperationProgress], None], lock: RLock | None = None) -> None:
        self._lock = lock if lock is not None else RLock()
        self._progress = MultiOperationProgress.from_empty()
        self._update_callback = update_callback
        self._operation_index_by_progress_id: dict[ProgressID, int] = {}

    def _update_operation(self, operation_progress: ProgressTypes) -> None:
        """Update or add an operation (download or subprocess).

        This method assumes the caller holds self._lock.
        """
        new_operations: list[ProgressTypes] = list(self._progress.operations)
        progress_id = operation_progress.progress_id

        if progress_id in self._operation_index_by_progress_id:
            # Update existing operation
            index = self._operation_index_by_progress_id[progress_id]
            new_operations[index] = operation_progress
        else:
            # Add new operation and track its index
            index = len(new_operations)
            new_operations.append(operation_progress)
            self._operation_index_by_progress_id[progress_id] = index

        progress_evolver = evolver(self._progress)
        assign(progress_evolver.operations, lambda: new_operations)
        assign(progress_evolver.latest_update_time, lambda: datetime.now())
        self._progress = chill(progress_evolver)
        self._update_callback(self._progress)

    def on_start(self) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            assign(progress_evolver.state, lambda: OperationState.IN_PROGRESS)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_download(self, url: AnyUrl, description: str | None = None) -> UnstartedHandle[DownloadModelHandle]:
        return get_unstarted(lambda: DownloadModelHandle(self._update_operation, url, description, lock=self._lock))

    def track_subprocess(self, description: str | None = None) -> UnstartedHandle[SubprocessModelHandle]:
        return get_unstarted(lambda: SubprocessModelHandle(self._update_operation, description, lock=self._lock))

    def finish(self) -> None:
        """Report that the overall operation has finished successfully."""
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.state, lambda: OperationState.COMPLETED)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def report_failure(self, explanation: str) -> None:
        """Report that the overall operation has failed."""
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.state, lambda: OperationState.FAILED)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)


class BranchNameAndTitleModelHandle(MultiOperationModelHandle, BranchNameAndTaskTitleProgressHandle):
    def __init__(
        self, update_callback: Callable[[BranchNameAndTaskTitleProgress], None], lock: RLock | None = None
    ) -> None:
        self._lock = lock if lock is not None else RLock()
        self._progress = BranchNameAndTaskTitleProgress.from_empty()
        self._update_callback = update_callback
        self._operation_index_by_progress_id: dict[ProgressID, int] = {}

    def report_generated_branch_name(self, branch_name: str, task_title: str) -> None:
        """Report the generated branch name and task title."""
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.generated_branch_name, lambda: branch_name)
            assign(progress_evolver.generated_task_title, lambda: task_title)
            assign(progress_evolver.latest_update_time, lambda: datetime.now())
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)


class RootProgressModelHandle(RootProgressHandle):
    def __init__(self, update_callback: Callable[[RootProgress], None]) -> None:
        self._lock = RLock()
        self._progress = RootProgress.from_empty()
        self._update_callback = update_callback

    def _update_agent_branch_checkout(self, multi_operation_progress: MultiOperationProgress) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.agent_branch_checkout, lambda: multi_operation_progress)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_agent_branch_checkout(self) -> UnstartedHandle[MultiOperationModelHandle]:
        return get_unstarted(lambda: MultiOperationModelHandle(self._update_agent_branch_checkout, lock=self._lock))

    def _update_container_setup(self, multi_operation_progress: MultiOperationProgress) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.container_setup, lambda: multi_operation_progress)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_container_setup(self, container_name: str) -> UnstartedHandle[MultiOperationModelHandle]:
        return get_unstarted(lambda: MultiOperationModelHandle(self._update_container_setup, lock=self._lock))

    def _update_snapshot_uncommitted_changes(self, multi_operation_progress: MultiOperationProgress) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.snapshot_uncommitted_changes, lambda: multi_operation_progress)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_snapshot_uncommitted_changes(self) -> UnstartedHandle[MultiOperationModelHandle]:
        return get_unstarted(
            lambda: MultiOperationModelHandle(self._update_snapshot_uncommitted_changes, lock=self._lock)
        )

    def _update_branch_name_and_task_title_generation(self, multi_operation_progress: MultiOperationProgress) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.branch_name_and_task_title_generation, lambda: multi_operation_progress)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_branch_name_and_task_title_generation(
        self, source_branch: str
    ) -> UnstartedHandle[BranchNameAndTitleModelHandle]:
        return get_unstarted(
            lambda: BranchNameAndTitleModelHandle(self._update_branch_name_and_task_title_generation, lock=self._lock)
        )

    def _update_image_build(self, multi_operation_progress: MultiOperationProgress) -> None:
        with self._lock:
            progress_evolver = evolver(self._progress)
            assign(progress_evolver.image_build, lambda: multi_operation_progress)
            self._progress = chill(progress_evolver)
            self._update_callback(self._progress)

    def track_image_build(self) -> UnstartedHandle[MultiOperationModelHandle]:
        return get_unstarted(lambda: MultiOperationModelHandle(self._update_image_build, lock=self._lock))
