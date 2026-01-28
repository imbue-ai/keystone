from unittest.mock import Mock

from pydantic import AnyUrl

from imbue_core.progress_tracking.progress_models import DownloadProgress
from imbue_core.progress_tracking.progress_models import MultiOperationProgress
from imbue_core.progress_tracking.progress_models import OperationState
from imbue_core.progress_tracking.progress_models import SubprocessProgress
from sculptor.utils.model_progress_tracking_handles import DownloadModelHandle
from sculptor.utils.model_progress_tracking_handles import MultiOperationModelHandle
from sculptor.utils.model_progress_tracking_handles import SubprocessModelHandle


class TestDownloadModelHandle:
    def test_initialization(self) -> None:
        """Test that DownloadModelHandle initializes correctly."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")
        description = "Test download"

        _ = DownloadModelHandle(callback, url, description)

        # Callback should not be called during initialization
        callback.assert_not_called()

    def test_start(self) -> None:
        """Test that start() updates state to IN_PROGRESS."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.on_start()

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.state == OperationState.IN_PROGRESS
        assert progress.url == url

    def test_report_size(self) -> None:
        """Test that report_size() updates total_bytes."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.report_size(1024)

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.total_bytes == 1024

    def test_report_progress(self) -> None:
        """Test that report_progress() updates bytes_downloaded."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.report_progress(512)

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.bytes_downloaded == 512

    def test_finish(self) -> None:
        """Test that finish() updates state to COMPLETED."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.finish()

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED

    def test_report_failure(self) -> None:
        """Test that report_failure() updates state to FAILED and sets explanation."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.report_failure("Connection timeout")

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.state == OperationState.FAILED
        assert progress.failure_explanation == "Connection timeout"

    def test_report_failed_attempt(self) -> None:
        """Test that report_failed_attempt() sets explanation without changing state."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = DownloadModelHandle(callback, url)
        handle.report_failed_attempt("Retry attempt 1 failed")

        callback.assert_called_once()
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.state == OperationState.NOT_STARTED  # State should remain unchanged
        assert progress.failure_explanation == "Retry attempt 1 failed"

    def test_full_download_flow(self) -> None:
        """Test a complete download flow with multiple state transitions."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")
        description = "Full download test"

        handle = DownloadModelHandle(callback, url, description)

        # Start download
        handle.on_start()
        assert callback.call_count == 1
        progress: DownloadProgress = callback.call_args[0][0]
        assert progress.state == OperationState.IN_PROGRESS
        assert progress.description == description

        # Report size
        handle.report_size(2048)
        assert callback.call_count == 2
        progress = callback.call_args[0][0]
        assert progress.total_bytes == 2048

        # Report progress
        handle.report_progress(1024)
        assert callback.call_count == 3
        progress = callback.call_args[0][0]
        assert progress.bytes_downloaded == 1024

        # Finish
        handle.finish()
        assert callback.call_count == 4
        progress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED


class TestSubprocessModelHandle:
    def test_initialization(self) -> None:
        """Test that SubprocessModelHandle initializes correctly."""
        callback = Mock()
        description = "Test subprocess"

        _ = SubprocessModelHandle(callback, description)

        # Callback should not be called during initialization
        callback.assert_not_called()

    def test_start(self) -> None:
        """Test that start() updates state to IN_PROGRESS."""
        callback = Mock()
        description = "Test subprocess"

        handle = SubprocessModelHandle(callback, description)
        handle.on_start()

        callback.assert_called_once()
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.IN_PROGRESS
        assert progress.description == description

    def test_report_command(self) -> None:
        """Test that report_command() updates the command field."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.report_command("git pull origin main")

        callback.assert_called_once()
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.command == "git pull origin main"

    def test_report_output_line(self) -> None:
        """Test that report_output_line() does not trigger an update."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.report_output_line("Line 1 of output", False)

        callback.assert_not_called()

    def test_report_return_code(self) -> None:
        """Test that report_return_code() updates the return_code field."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.report_return_code(0)

        callback.assert_called_once()
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.return_code == 0

    def test_finish(self) -> None:
        """Test that finish() updates state to COMPLETED."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.finish()

        callback.assert_called_once()
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED

    def test_report_failure(self) -> None:
        """Test that report_failure() updates state to FAILED and sets explanation."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.report_failure("Command failed with exit code 1")

        callback.assert_called_once()
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.FAILED
        assert progress.failure_explanation == "Command failed with exit code 1"

    def test_full_subprocess_flow(self) -> None:
        """Test a complete subprocess flow with multiple state transitions."""
        callback = Mock()
        description = "Full subprocess test"

        handle = SubprocessModelHandle(callback, description)

        # Start subprocess
        handle.on_start()
        assert callback.call_count == 1
        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.IN_PROGRESS

        # Report command
        handle.report_command("pytest tests/")
        assert callback.call_count == 2
        progress = callback.call_args[0][0]
        assert progress.command == "pytest tests/"

        # Report output lines
        handle.report_output_line("Running tests...", False)
        assert callback.call_count == 2

        handle.report_output_line("All tests passed", False)
        assert callback.call_count == 2

        # Report return code
        handle.report_return_code(0)
        assert callback.call_count == 3
        progress = callback.call_args[0][0]
        assert progress.return_code == 0

        # Finish
        handle.finish()
        assert callback.call_count == 4
        progress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED

    def test_subprocess_finish_with_error_return_code(self) -> None:
        """Test that finish() marks subprocess as FAILED when return code is non-zero."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.on_start()
        handle.report_return_code(1)  # Non-zero return code
        handle.finish()

        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.FAILED
        assert progress.return_code == 1
        assert progress.failure_explanation == "Process exited with non-zero return code 1"

    def test_subprocess_finish_without_return_code(self) -> None:
        """Test that finish() marks subprocess as COMPLETED when no return code is set."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.on_start()
        handle.finish()  # No return code reported

        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED
        assert progress.return_code is None

    def test_subprocess_finish_preserves_existing_failure_explanation(self) -> None:
        """Test that finish() doesn't overwrite an existing failure explanation."""
        callback = Mock()

        handle = SubprocessModelHandle(callback, "Test subprocess")
        handle.on_start()
        handle.report_failure("Custom error message")  # Set custom explanation
        handle.report_return_code(1)  # Non-zero return code
        handle.finish()

        progress: SubprocessProgress = callback.call_args[0][0]
        assert progress.state == OperationState.FAILED
        assert progress.return_code == 1
        # Should preserve the custom explanation, not replace with default
        assert progress.failure_explanation == "Custom error message"


class TestMultiOperationModelHandle:
    def test_initialization(self) -> None:
        """Test that MultiOperationModelHandle initializes correctly."""
        callback = Mock()

        _ = MultiOperationModelHandle(callback)

        # Callback should not be called during initialization
        callback.assert_not_called()

    def test_start(self) -> None:
        """Test that start() updates state to IN_PROGRESS."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        handle.on_start()

        callback.assert_called_once()
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert progress.state == OperationState.IN_PROGRESS
        assert progress.operations == []

    def test_track_download(self) -> None:
        """Test that track_download() creates a download handle and tracks it."""
        callback = Mock()
        url = AnyUrl("https://example.com/file.zip")

        handle = MultiOperationModelHandle(callback)
        unstarted_download_handle = handle.track_download(url, "Test download")

        # The parent callback should NOT be called until the download reports an update
        callback.assert_not_called()

        # Start the download to trigger the first update
        _ = unstarted_download_handle.start()

        # Now the callback should be called and the operation added
        callback.assert_called_once()
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert len(progress.operations) == 1
        download_operation = progress.operations[0]
        assert isinstance(download_operation, DownloadProgress)
        assert download_operation.url == url
        assert download_operation.state == OperationState.IN_PROGRESS

    def test_track_subprocess(self) -> None:
        """Test that track_subprocess() creates a subprocess handle and tracks it."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        unstarted_subprocess_handle = handle.track_subprocess("Test subprocess")

        # The parent callback should NOT be called until the subprocess reports an update
        callback.assert_not_called()

        # Start the subprocess to trigger the first update
        _ = unstarted_subprocess_handle.start()

        # Now the callback should be called and the operation added
        callback.assert_called_once()
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert len(progress.operations) == 1
        subprocess_operation = progress.operations[0]
        assert isinstance(subprocess_operation, SubprocessProgress)
        assert subprocess_operation.description == "Test subprocess"
        assert subprocess_operation.state == OperationState.IN_PROGRESS

    def test_multiple_operations(self) -> None:
        """Test tracking multiple operations simultaneously."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        handle.on_start()

        # Track downloads and subprocess (no operations added yet)
        download_handle = handle.track_download(AnyUrl("https://example.com/file1.zip"), "Download 1")
        download_handle2 = handle.track_download(AnyUrl("https://example.com/file2.zip"), "Download 2")
        subprocess_handle = handle.track_subprocess("Build process")

        # Should have 1 callback: just the start
        assert callback.call_count == 1

        # Start the operations (this adds them to the operations list)
        download_handle.start()
        download_handle2.start()
        subprocess_handle.start()

        # Should have 4 callbacks now (1 start + 3 operation starts)
        assert callback.call_count == 4

        progress: MultiOperationProgress = callback.call_args[0][0]
        assert len(progress.operations) == 3

        # Verify operation types
        download_ops = [op for op in progress.operations if isinstance(op, DownloadProgress)]
        subprocess_ops = [op for op in progress.operations if isinstance(op, SubprocessProgress)]
        assert len(download_ops) == 2
        assert len(subprocess_ops) == 1

    def test_operation_updates(self) -> None:
        """Test that updates to tracked operations are reflected in the parent."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        unstarted_download_handle = handle.track_download(AnyUrl("https://example.com/file.zip"), "Test download")

        # Track download doesn't trigger callback
        callback.assert_not_called()

        # Start download (first update, adds the operation)
        download_handle = unstarted_download_handle.start()
        assert callback.call_count == 1

        # Report size
        download_handle.report_size(1024)
        assert callback.call_count == 2
        progress: MultiOperationProgress = callback.call_args[0][0]
        download_operation = progress.operations[0]
        assert isinstance(download_operation, DownloadProgress)
        assert download_operation.total_bytes == 1024

        # Report progress
        download_handle.report_progress(512)
        assert callback.call_count == 3
        progress = callback.call_args[0][0]
        download_operation = progress.operations[0]
        assert isinstance(download_operation, DownloadProgress)
        assert download_operation.bytes_downloaded == 512

        # Finish
        download_handle.finish()
        assert callback.call_count == 4
        progress = callback.call_args[0][0]
        assert progress.operations[0].state == OperationState.COMPLETED

    def test_finish(self) -> None:
        """Test that finish() updates state to COMPLETED."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        handle.finish()

        callback.assert_called_once()
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED

    def test_report_failure(self) -> None:
        """Test that report_failure() updates state to FAILED."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        handle.report_failure("Overall operation failed")

        callback.assert_called_once()
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert progress.state == OperationState.FAILED

    def test_complex_workflow(self) -> None:
        """Test a complex workflow with multiple operations and state transitions."""
        callback = Mock()

        handle = MultiOperationModelHandle(callback)
        handle.on_start()
        initial_callback_count = callback.call_count

        # Track multiple operations (doesn't trigger callbacks)
        unstarted_download1 = handle.track_download(AnyUrl("https://example.com/file1.zip"), "Download 1")
        unstarted_download2 = handle.track_download(AnyUrl("https://example.com/file2.zip"), "Download 2")
        unstarted_subprocess1 = handle.track_subprocess("Build")

        # No additional callbacks yet
        assert callback.call_count == initial_callback_count

        # Start all operations (this adds them to the operations list)
        download1 = unstarted_download1.start()
        download2 = unstarted_download2.start()
        subprocess1 = unstarted_subprocess1.start()

        # Update first download
        download1.report_size(2048)
        download1.report_progress(1024)
        download1.finish()

        # Update second download
        download2.report_size(4096)
        download2.report_progress(2048)

        # Update subprocess
        subprocess1.report_command("make build")
        subprocess1.report_return_code(0)
        subprocess1.finish()

        # Complete second download
        download2.report_progress(4096)
        download2.finish()

        # Finish overall operation
        handle.finish()

        # Verify final state
        progress: MultiOperationProgress = callback.call_args[0][0]
        assert progress.state == OperationState.COMPLETED
        assert len(progress.operations) == 3

        # All operations should be completed
        for op in progress.operations:
            assert op.state == OperationState.COMPLETED
