#!/usr/bin/env python3
"""
Analyze Docker snapshot sizes for Sculptor tasks.

This script:
1. Goes through each task
2. Prints the rows in the output of "docker images" for the snapshots for that task
3. Reports the sum of their sizes as indicated by the first row of "docker history".
"""

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import Connection
from sqlalchemy import select

from sculptor.database.core import create_new_engine
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import SavedAgentMessage
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.services.data_model_service.sql_implementation import SAVED_AGENT_MESSAGE_TABLE
from sculptor.services.data_model_service.sql_implementation import TASK_LATEST_TABLE
from sculptor.services.data_model_service.sql_implementation import _row_to_pydantic_model


def run_command(cmd: list[str], timeout: int = 60) -> str:
    """Run a shell command and return its output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return ""
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {' '.join(cmd)}", file=sys.stderr)
        return ""


def parse_size_string(size_str: str) -> int:
    """
    Convert size string like '1.23GB', '456MB', '789B' to bytes.
    """
    size_str = size_str.strip().upper()
    # Remove any trailing 'B' if present
    if size_str.endswith("B") and len(size_str) > 1:
        unit = size_str[-2:]
        if unit in ["KB", "MB", "GB", "TB"]:
            number = float(size_str[:-2])
        else:
            # Just 'B' at the end
            unit = "B"
            number = float(size_str[:-1])
    else:
        # Assume it's just a number in bytes
        try:
            return int(float(size_str))
        except ValueError:
            return 0

    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }

    return int(number * multipliers.get(unit, 1))


def get_first_history_row_size(image_id: str) -> tuple[int, str]:
    """
    Get the size from the first row of docker history for an image.

    Returns (size_in_bytes, full_first_data_line).
    """
    output = run_command(["docker", "history", image_id])
    if not output:
        return 0, ""

    lines = output.strip().split("\n")
    if len(lines) < 2:  # Need at least header + one data line
        return 0, ""

    # Get the first data line (index 1, since 0 is the header)
    first_data_line = lines[1].strip()
    if not first_data_line:
        return 0, ""

    # Parse the line to find SIZE
    fields = first_data_line.split()

    # Find the SIZE field (look for patterns like "106MB", "1.5GB", etc.)
    size_bytes = 0
    for field in fields:
        # Check if this looks like a size (ends with B, KB, MB, GB, TB)
        if field and (
            field.endswith("B")
            or field.endswith("KB")
            or field.endswith("MB")
            or field.endswith("GB")
            or field.endswith("TB")
        ):
            size_bytes = parse_size_string(field)
            break

    return size_bytes, first_data_line


def get_docker_images_row(image_id: str) -> str:
    """
    Get the docker images output row for a specific image ID.
    """
    output = run_command(["docker", "images", "--no-trunc"])
    if not output:
        return ""

    lines = output.strip().split("\n")
    if len(lines) < 2:  # Need at least header + one data line
        return ""

    # Find the line that contains this image ID
    for line in lines[1:]:  # Skip header
        if image_id in line:
            return line.strip()

    return ""


@contextmanager
def open_database_connection() -> Generator[Connection, None, None]:
    """Open a connection to the Sculptor database."""
    sf = Path("~/.sculptor").expanduser()
    db_path = sf / "database.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_new_engine(database_url)

    with engine.connect() as connection:
        yield connection


def format_bytes(bytes_val: int) -> str:
    """Format bytes into human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def main() -> None:
    """Main analysis function."""
    print("=" * 80)
    print("Docker Snapshot Size Analysis for Sculptor Tasks")
    print("=" * 80)
    print()

    # Get all tasks from the database
    print("Querying Sculptor database for tasks...")
    with open_database_connection() as connection:
        statement = select(TASK_LATEST_TABLE)
        result = connection.execute(statement)
        all_tasks = [_row_to_pydantic_model(row, Task) for row in result.all()]

        print(f"Found {len(all_tasks)} tasks")
        print()

        total_size = 0
        active_size = 0
        archived_size = 0
        task_count = 0
        active_task_count = 0
        archived_task_count = 0

        for task in all_tasks:
            image_ids: list[str] = []

            # Get the current/latest image from the task state
            if isinstance(task.current_state, AgentTaskStateV1):
                if isinstance(task.current_state.image, LocalDockerImage):
                    image_ids.append(task.current_state.image.image_id)

            # Get historical images from snapshot messages
            messages_statement = select(SAVED_AGENT_MESSAGE_TABLE).where(
                SAVED_AGENT_MESSAGE_TABLE.c.task_id == str(task.object_id)
            )
            messages_result = connection.execute(messages_statement)
            saved_messages = [_row_to_pydantic_model(row, SavedAgentMessage) for row in messages_result.all()]

            for saved_msg in saved_messages:
                if isinstance(saved_msg.message, AgentSnapshotRunnerMessage):
                    if isinstance(saved_msg.message.image, LocalDockerImage):
                        img_id = saved_msg.message.image.image_id
                        if img_id not in image_ids:
                            image_ids.append(img_id)

            if not image_ids:
                continue

            # Print task header
            task_count += 1
            status = (
                "ARCHIVED" if task.is_archived else "DELETED" if (task.is_deleted or task.is_deleting) else "ACTIVE"
            )
            print(f"Task {task_count}: {task.object_id} ({status})")
            print(f"  Snapshots: {len(image_ids)}")
            print()

            # Print docker images rows for each snapshot
            task_total_size = 0
            for image_id in image_ids:
                # Get the docker images row
                images_row = get_docker_images_row(image_id)
                if images_row:
                    print(f"  {images_row}")

                # Get the size from the first row of docker history
                size_bytes, history_first_row = get_first_history_row_size(image_id)
                if size_bytes > 0:
                    task_total_size += size_bytes
                    print(f"    First history row: {history_first_row}")
                    print(f"    Size from history: {format_bytes(size_bytes)}")
                print()

            print(f"  Total size for task: {format_bytes(task_total_size)}")
            print()
            print("-" * 80)
            print()

            total_size += task_total_size

            # Track separately by status
            if task.is_archived:
                archived_size += task_total_size
                archived_task_count += 1
            else:
                active_size += task_total_size
                active_task_count += 1

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total tasks with snapshots: {task_count}")
    print(f"  Active tasks: {active_task_count}")
    print(f"  Archived tasks: {archived_task_count}")
    print()
    print(f"Total size of all snapshots (from first history row): {format_bytes(total_size)}")
    print(f"  Active task snapshots: {format_bytes(active_size)}")
    print(f"  Archived task snapshots: {format_bytes(archived_size)}")
    print()


if __name__ == "__main__":
    main()
