from pathlib import Path
from typing import Callable
from typing import Mapping
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union

import modal
from loguru import logger
from pydantic import AnyUrl

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.event_utils import MutableEvent
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.secrets_utils import Secret
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import ModalEnvironmentConfig
from sculptor.interfaces.environments.base import ModalImage
from sculptor.interfaces.environments.base import ProviderTag
from sculptor.primitives.ids import ModalImageObjectID
from sculptor.primitives.ids import ModalSandboxObjectID
from sculptor.services.environment_service.environments.utils import check_provider_health_on_failure
from sculptor.services.environment_service.providers.modal.errors import ModalExecutionInvalidError
from sculptor.services.environment_service.providers.provider_types import ModalMarker
from sculptor.utils.timeout import log_runtime

# https://github.com/python/typeshed/tree/main/stdlib/_typeshed
if TYPE_CHECKING:
    # for proper file mode typing
    from _typeshed import OpenBinaryModeReading
    from _typeshed import OpenBinaryModeWriting
    from _typeshed import OpenTextModeReading
    from _typeshed import OpenTextModeWriting


class ModalEnvironment(Environment[ModalMarker]):
    object_type: str = "ModalEnvironment"
    environment_id: ModalSandboxObjectID
    app_name: str
    config: ModalEnvironmentConfig
    _cached_root_path: Path | None = None

    @property
    def sandbox_id(self) -> ModalSandboxObjectID:
        return self.environment_id

    @property
    def sandbox(self) -> modal.Sandbox | None:
        try:
            return modal.Sandbox.from_id(sandbox_id=self.sandbox_id)
        except modal.exception.NotFoundError:
            return None

    # This should be replaced by understanding the workspaceFolder from the user's devcontainer
    # def get_root_path(self) -> Path:
    #     assert self.sandbox is not None
    #     if self._cached_root_path is None:
    #         process = self.sandbox.exec("pwd")
    #         self._cached_root_path = Path(process.stdout.read().strip())
    #     return self._cached_root_path

    def get_config(self) -> ModalEnvironmentConfig:
        return self.config

    def get_extra_logger_context(self) -> Mapping[str, str | float | int | bool | None]:
        return {"sandbox_id": self.sandbox_id, "provider": ProviderTag.MODAL}

    def get_container_user(self) -> str:
        """Modal sandboxes use root by default."""
        return "root"

    def get_container_user_home_directory(self) -> Path:
        """Modal sandboxes use root home directory by default."""
        return Path("/root")

    def get_file_mtime(self, path: str) -> float:
        """Get file modification time - not implemented for Modal."""
        raise NotImplementedError("get_file_mtime is not implemented for ModalEnvironment")

    @check_provider_health_on_failure
    def run_process_in_background(
        self,
        command: Sequence[str],
        secrets: Mapping[str, str | Secret],
        cwd: str | None = None,
        is_interactive: bool = False,
        run_with_sudo_privileges: bool = False,
        run_as_root: bool = False,
        shutdown_event: MutableEvent | None = None,
        timeout: float | None = None,
        is_checked_by_group: bool = False,
        on_output: Callable[[str, bool], None] | None = None,
    ) -> RunningProcess:
        raise NotImplementedError()

        # if run_with_sudo_privileges or run_as_root:
        #     raise NotImplementedError()

        # if not self.is_alive():
        #     raise ModalExecutionInvalidError(f"Sandbox {self.sandbox_id} is not alive")
        # sandbox = self.sandbox
        # assert sandbox is not None

        # tag = generate_id()
        # all_secrets = {**secrets, "SCULPTOR_PROCESS_TAG": tag}

        # modal_secrets = []
        # modal_secrets.append(modal.Secret.from_dict(dict[str, str | None](all_secrets)))

        # process = sandbox.exec(
        #     *command,
        #     secrets=modal_secrets,
        # )
        # return ModalProcess(process=process, sandbox_id=self.sandbox_id, command=command, tag=tag)

    @check_provider_health_on_failure
    def snapshot(self, user_config: UserConfig, task_id: TaskID, settings: SculptorSettings) -> ModalImage:
        with (
            self._snapshot_time_awareness(),
            log_runtime("Snapshotting modal image"),
        ):
            sandbox = self.sandbox
            assert sandbox is not None
            image = sandbox.snapshot_filesystem()
            snapshot = ModalImage(
                image_id=ModalImageObjectID(image.object_id), app_name=self.app_name, project_id=self.project_id
            )
            if self._on_snapshot is not None:
                self._on_snapshot(snapshot, False)
            return snapshot

    @check_provider_health_on_failure
    def persist(self, user_config: UserConfig, task_id: TaskID, settings: SculptorSettings) -> None:
        # TODO: if we wanted, we could provide a reference to data_model_service to this class so that we could
        #  save these in the database directly, but for now we'll just save them in a janky way
        snapshot = self.snapshot(user_config, task_id, settings)
        if self._on_snapshot is not None:
            self._on_snapshot(snapshot, True)

    @check_provider_health_on_failure
    def is_alive(self) -> bool:
        if self.sandbox is None:
            return False
        try:
            return self.sandbox.poll() is None
        except modal.exception.SandboxTimeoutError:
            return False

    @check_provider_health_on_failure
    def exists(self, path: str) -> bool:
        raise NotImplementedError()

    @check_provider_health_on_failure
    def read_file(
        self,
        path: str,
        mode: Union["OpenTextModeReading", "OpenBinaryModeReading"] = "r",
    ) -> str | bytes:
        if not self.is_alive():
            raise ModalExecutionInvalidError(f"Sandbox {self.sandbox_id} is not alive")
        assert self.sandbox is not None
        with self.sandbox.open(path, mode) as file:
            if True:
                # FIXME: we need to raise FileNotFoundError() if this file does not exist, but idk what this does in that case right now
                raise NotImplementedError()
            return file.read()

    @check_provider_health_on_failure
    def write_file(
        self,
        path: str,
        content: str | bytes,
        mode: Union["OpenTextModeWriting", "OpenBinaryModeWriting"] = "w",
    ) -> None:
        if not self.is_alive():
            raise ModalExecutionInvalidError(f"Sandbox {self.sandbox_id} is not alive")
        parent = Path(path).parent
        sandbox = self.sandbox
        assert sandbox is not None
        if parent:
            sandbox.mkdir(str(parent), parents=True)

        with sandbox.open(path, mode) as file:
            file.write(content)

    def move_file(
        self,
        original_path: str,
        new_path: str,
    ) -> None:
        raise NotImplementedError()

    def delete_file_or_directory(self, path: str) -> None:
        raise NotImplementedError()

    def get_server_url(self, name: str) -> AnyUrl:
        assert self.sandbox is not None
        tunnels = self.sandbox.tunnels()
        internal_port = self.config.server_port_by_name[name]
        tunnel = tunnels[internal_port]
        host, port = tunnel.tcp_socket
        return AnyUrl(f"http://{host}:{port}")

    def close(self) -> None:
        stop_modal_sandbox(self.sandbox_id)

    def destroy(self, is_killing: bool = False) -> None:
        self.close()

    @check_provider_health_on_failure
    def copy_from_local(self, local_path: Path, env_path: str, recursive: bool = True) -> None:
        # FIXME: modal doesn't have a copy function - we'd probably need to iterate over the files and write them one by one
        raise NotImplementedError()

    @check_provider_health_on_failure
    def copy_to_local(self, env_path: str, local_path: Path, recursive: bool = True) -> None:
        # FIXME: modal doesn't have a copy function - we'd probably need to iterate over the files and read them one by one
        raise NotImplementedError()


def stop_modal_sandbox(sandbox_id: ModalSandboxObjectID) -> None:
    logger.info("Deleting sandbox {}", sandbox_id)
    try:
        sandbox = modal.Sandbox.from_id(sandbox_id)
        sandbox.terminate()
    except modal.exception.SandboxTimeoutError:
        pass


# TODO: we'll need to delete all associated private volumes if we have any
def remove_modal_sandbox(sandbox_id: ModalSandboxObjectID) -> None:
    stop_modal_sandbox(sandbox_id)
