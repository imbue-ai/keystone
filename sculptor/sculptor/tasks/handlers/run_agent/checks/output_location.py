from pathlib import Path
from typing import Self

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.pydantic_serialization import FrozenModel
from sculptor.interfaces.agents.tasks import RunID


class CheckRunOutputLocation(FrozenModel):
    """
    See README.md in this folder for documentation on the layout of checks data.
    """

    # contains the path within the container where the agent data is stored
    root_data_path: str
    # the task for which we are running this check
    task_id: TaskID
    # the user message id with which this check is associated
    user_message_id: AgentMessageID
    # randomly generated id for the run of the check
    run_id: RunID
    # mostly here because the check can be None if validation failed, and we need to key off of this
    check_name: str

    @classmethod
    def build_from_folder(cls, folder: str) -> Self:
        # Parse the string path
        try:
            *root_parts, task_id, checks_part, user_message_id, check_name, run_id = folder.rstrip("/").split("/")
        except ValueError:
            raise ValueError(f"Path must contain at least 5 segments: {folder}") from None
        if checks_part != "checks":
            raise ValueError(f"The 4th to last segment must be '/checks/': {folder}")
        return cls(
            root_data_path=str(Path(root_parts[0] if root_parts[0] else "/", *root_parts[1:])),
            task_id=TaskID(task_id),
            user_message_id=AgentMessageID(user_message_id),
            check_name=check_name,
            run_id=RunID(run_id),
        )

    def to_message_folder(self) -> str:
        return f"{self.root_data_path}/{self.task_id}/checks/{self.user_message_id}"

    def to_check_folder(self) -> str:
        return f"{self.to_message_folder()}/{self.check_name}"

    def to_run_folder(self) -> str:
        return f"{self.to_check_folder()}/{self.run_id}"
