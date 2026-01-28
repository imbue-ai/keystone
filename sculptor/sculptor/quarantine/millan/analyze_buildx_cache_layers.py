#!/usr/bin/env python3
"""
Analyze Docker buildx cache and image layers for Sculptor tasks.

This script:
1. Uses `docker buildx du --verbose` to get all buildx cache records and their sizes
2. Uses `docker image inspect` on every image for each task to get their layer chains
3. Identifies which image layers are only used by archived tasks
4. Reports buildx cache size and archived-only image layer information
5. Provides recommendations for reclaiming space

Note: Buildx cache IDs are internal and don't directly map to image layer sha256 digests.
This script reports on both systems separately to show total reclaimable space.
"""

import functools
import json
import subprocess
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Generator

from sqlalchemy import Connection
from sqlalchemy import select

from sculptor.database.core import create_new_engine
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import SavedAgentMessage
from sculptor.database.models import Task
from sculptor.interfaces.agents.v1.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.environments.v1.base import LocalDockerImage
from sculptor.services.data_model_service.sql_implementation import SAVED_AGENT_MESSAGE_TABLE
from sculptor.services.data_model_service.sql_implementation import TASK_LATEST_TABLE
from sculptor.services.data_model_service.sql_implementation import _row_to_pydantic_model

"""
I want it to get the layers associated with archived tasks.
Also
"""


@dataclass
class BuildxCacheRecord:
    """Information about a buildx cache record."""

    id: str  # Cache ID
    size_bytes: int
    record_type: str  # e.g., "regular file", "whiteout", etc.
    description: str  # Description from buildx du


@dataclass
class TaskImageInfo:
    """Information about a task's Docker images."""

    task_id: str
    image_ids: list[str]
    is_archived: bool
    is_deleted: bool


def run_command(cmd: list[str], timeout: int = 60) -> str:
    """Run a shell command and return its output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        return result.stdout
    except subprocess.CalledProcessError as e:
        # TODO: track the failures in a different way
        # print(f"Error running command {' '.join(cmd)}: {e}", file=sys.stderr)
        # print(f"stderr: {e.stderr}", file=sys.stderr)
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


def get_buildx_cache_records() -> dict[str, BuildxCacheRecord]:
    """
    Parse `docker buildx du --verbose` to get all cache records.

    Returns a dict mapping cache IDs to BuildxCacheRecord objects.
    """
    output = run_command(["docker", "buildx", "du", "--verbose"], timeout=300)
    if not output:
        return {}

    cache_records: dict[str, BuildxCacheRecord] = {}

    # Parse the output - it's in multi-line format with key-value pairs
    # Format is:
    # ID:         sxt5akkgnpjaie0u4xsq6ai4h
    # Parent:     ...
    # Size:       1.25GB
    # Description: ...
    # Type:       regular
    # (blank line separates records)

    lines = output.strip().split("\n")
    current_record: dict[str, Any] = {}

    for line in lines:
        line = line.strip()

        # Blank line indicates end of a record
        if not line:
            if current_record.get("ID") and current_record.get("Size"):
                cache_id = current_record["ID"]
                size_bytes = parse_size_string(current_record["Size"])
                description = current_record.get("Description", "")
                record_type = current_record.get("Type", "unknown")

                cache_records[cache_id] = BuildxCacheRecord(
                    id=cache_id,
                    size_bytes=size_bytes,
                    record_type=record_type,
                    description=description,
                )

            # Reset for next record
            current_record = {}
            continue

        # Parse key-value pairs (key: value)
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if key in ["ID", "Size", "Description", "Type"]:
                current_record[key] = value

    # Don't forget the last record if there's no trailing blank line
    if current_record.get("ID") and current_record.get("Size"):
        cache_id = current_record["ID"]
        size_bytes = parse_size_string(current_record["Size"])
        description = current_record.get("Description", "")
        record_type = current_record.get("Type", "unknown")

        cache_records[cache_id] = BuildxCacheRecord(
            id=cache_id,
            size_bytes=size_bytes,
            record_type=record_type,
            description=description,
        )

    return cache_records


def get_layers_from_history(image_id: str) -> list[tuple[str, int, str, str]]:
    """
    Check docker history for all layers where the IMAGE column is not "<missing>".

    Returns a list of tuples (layer_id, size_bytes, created_by) for each non-missing layer.

    Example docker history output:
    IMAGE          CREATED          CREATED BY                  SIZE      COMMENT
    12e19227310e   15 minutes ago   tail -f /dev/null          106MB
    <missing>      20 minutes ago   RUN pip install foo        50MB
    """
    output = run_command(["docker", "history", image_id])
    if not output:
        return []

    lines = output.strip().split("\n")
    if len(lines) < 2:  # Need at least header + one data line
        return []

    result_layers = []

    # Parse all data lines (skip the header)
    for line_idx in range(1, len(lines)):
        data_line = lines[line_idx].strip()
        if not data_line:
            continue

        # Split by whitespace and extract fields
        # Format: IMAGE CREATED CREATED_BY... SIZE COMMENT
        parts = data_line.split(None, 1)  # Split into at most 2 parts
        if not parts:
            continue

        image_column = parts[0]

        # Skip lines where IMAGE column is "<missing>"
        if image_column == "<missing>":
            continue

        # Parse the whole line to find SIZE and CREATED BY
        fields = data_line.split()

        # Find the SIZE field (look for patterns like "106MB", "1.5GB", etc.)
        size_bytes = 0
        size_index = -1
        for i, field in enumerate(fields):
            # Check if this looks like a size (ends with B, KB, MB, GB, TB)
            if field and (
                field.endswith("B")
                or field.endswith("KB")
                or field.endswith("MB")
                or field.endswith("GB")
                or field.endswith("TB")
            ):
                size_bytes = parse_size_string(field)
                size_index = i
                break

        # Extract CREATED BY - it's between CREATED and SIZE
        # Typically: IMAGE CREATED [CREATED_BY...] SIZE [COMMENT]
        created_by = ""
        if size_index > 2:  # Need at least IMAGE, CREATED, something, SIZE
            # Join fields from after CREATED to before SIZE
            created_by_fields = fields[2:size_index]
            created_by = " ".join(created_by_fields)

        # Create a unique layer ID for this layer
        layer_id = f"image-layer:{image_column}"
        result_layers.append((layer_id, size_bytes, created_by, image_id))

    return result_layers


@functools.lru_cache(maxsize=None)
def get_image_layers(image_id: str) -> list[str]:
    """
    Get all layer IDs for a Docker image using `docker image inspect`.

    Returns a list of layer IDs (sha256:...).
    """
    output = run_command(["docker", "image", "inspect", image_id])
    if not output:
        return []

    try:
        inspect_data = json.loads(output)
        if inspect_data and len(inspect_data) > 0:
            rootfs = inspect_data[0].get("RootFS", {})
            layers = rootfs.get("Layers", [])
            return layers
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Error parsing inspect data for {image_id}: {e}", file=sys.stderr)

    return []


@functools.lru_cache(maxsize=None)
def get_image_size(image_id: str) -> int:
    """
    Get the total size of a Docker image in bytes.
    """
    output = run_command(["docker", "image", "inspect", image_id])
    if not output:
        return 0

    try:
        inspect_data = json.loads(output)
        if inspect_data and len(inspect_data) > 0:
            return inspect_data[0].get("Size", 0)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Error parsing inspect data for {image_id}: {e}", file=sys.stderr)

    return 0


@contextmanager
def open_database_connection() -> Generator[Connection, None, None]:
    """Open a connection to the Sculptor database."""
    sf = Path("~/.sculptor").expanduser()
    db_path = sf / "database.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_new_engine(database_url)

    with engine.connect() as connection:
        yield connection


def get_tasks_and_images() -> list[TaskImageInfo]:
    """
    Query the Sculptor database to get all tasks and their associated images.
    """
    task_infos: list[TaskImageInfo] = []

    with open_database_connection() as connection:
        # Get all tasks from the database
        statement = select(TASK_LATEST_TABLE)
        result = connection.execute(statement)
        all_tasks = [_row_to_pydantic_model(row, Task) for row in result.all()]

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

            if image_ids:
                task_infos.append(
                    TaskImageInfo(
                        task_id=str(task.object_id),
                        image_ids=image_ids,
                        is_archived=task.is_archived,
                        is_deleted=task.is_deleted or task.is_deleting,
                    )
                )

    return task_infos


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
    print("Docker Buildx Cache Layer Analysis for Sculptor Tasks")
    print("=" * 80)
    print()

    # Step 1: Get all buildx cache records
    print("Step 1: Analyzing Docker buildx cache (this may take a while)...")
    cache_records = get_buildx_cache_records()
    print(f"Found {len(cache_records)} cache records")
    total_cache_size = sum(record.size_bytes for record in cache_records.values())
    print(f"Total cache size: {format_bytes(total_cache_size)}")
    print()

    # Step 2: Get all tasks and their images from the database
    print("Step 2: Querying Sculptor database for tasks and images...")
    task_infos = get_tasks_and_images()
    archived_tasks = [t for t in task_infos if t.is_archived]
    active_tasks = [t for t in task_infos if not t.is_archived and not t.is_deleted]
    print(f"Found {len(task_infos)} tasks total:")
    print(f"  - {len(archived_tasks)} archived tasks")
    print(f"  - {len(active_tasks)} active tasks")
    print()

    # Step 3: Get layers for each image
    print("Step 3: Inspecting Docker images to get layer and size information...")
    print("(This may take a while...)")
    print()

    # Map: layer_id -> list of task_ids that use it
    layer_to_tasks: dict[str, list[str]] = defaultdict(list)
    # Map: task_id -> set of layer_ids
    task_to_layers: dict[str, set[str]] = {}
    # Map: image_id -> size in bytes
    image_sizes: dict[str, int] = {}
    # Map: layer_id -> size for top layers from history
    top_layer_sizes: dict[str, int] = {}
    # Map: layer_id -> created_by for top layers from history
    top_layer_created_by: dict[str, str] = {}

    snapshot_layerid_to_imageid = {}

    total_images = sum(len(task.image_ids) for task in task_infos)
    print(f"Found {total_images} total images")
    processed_images = 0

    for task_info in task_infos:
        task_layers: set[str] = set()

        # if task_info.is_deleted:
        #     print(f"Skipping {task_info.task_id} because it's deleted")
        #     continue

        for image_id in task_info.image_ids:
            processed_images += 1
            if processed_images % 10 == 0:
                print(f"  Processed {processed_images}/{total_images} images...")

            layers = get_image_layers(image_id)

            for layer_id in layers:
                task_layers.add(layer_id)
                layer_to_tasks[layer_id].append(task_info.task_id)

            # Check for layers from docker history
            history_layers = get_layers_from_history(image_id)
            for layer_id, layer_size, created_by, image_id_for_layer in history_layers:
                snapshot_layerid_to_imageid[layer_id] = image_id_for_layer
                task_layers.add(layer_id)
                layer_to_tasks[layer_id].append(task_info.task_id)
                top_layer_sizes[layer_id] = layer_size
                top_layer_created_by[layer_id] = created_by

            # Get image size
            size = get_image_size(image_id)
            image_sizes[image_id] = size

        task_to_layers[task_info.task_id] = task_layers

    print(f"  Processed {processed_images}/{total_images} images... Done!")
    print()

    # Step 4: Identify layers only used by archived tasks
    print("Step 4: Identifying layers only used by archived tasks...")

    archived_task_ids = {t.task_id for t in archived_tasks}
    active_task_ids = {t.task_id for t in active_tasks}

    # Find layers that are:
    # 1. Used by at least one archived task
    # 2. NOT used by any active task
    archived_only_layers: set[str] = set()

    for layer_id, task_ids in layer_to_tasks.items():
        using_tasks = set(task_ids)

        # Check if used by archived tasks
        used_by_archived = bool(using_tasks & archived_task_ids)
        # Check if used by active tasks
        used_by_active = bool(using_tasks & active_task_ids)

        if used_by_archived and not used_by_active:
            archived_only_layers.add(layer_id)

    print(f"Found {len(archived_only_layers)} layers used only by archived tasks")
    print()

    # Step 5: Get sizes of archived-only layers from buildx cache
    print("Step 5: Calculating sizes of archived-only layers from buildx cache...")

    # Map layer descriptions to cache records (for matching)
    # Since we can't directly match sha256 layer IDs to buildx cache IDs,
    # we need to get the actual layer sizes by inspecting each layer

    # Get unique layer sizes by checking which images use each layer
    layer_sizes: dict[str, int] = {}

    for layer_id in archived_only_layers:
        # Find an image that has this layer to get more info
        for task_info in archived_tasks:
            for image_id in task_info.image_ids:
                if layer_id in get_image_layers(image_id):
                    # Use docker history to get this layer's size
                    output = run_command(["docker", "history", "--no-trunc", "--format", "{{json .}}", image_id])
                    if output:
                        # Parse history to find layer sizes
                        # Note: This gives us cumulative info, not individual layer sizes
                        # For now, we'll estimate based on the image inspect data
                        pass
                    break
            if layer_id in layer_sizes:
                break

    print(f"Found {len(archived_only_layers)} layers used only by archived tasks")
    print()

    # Step 6: Get detailed info for each layer (archived-only and all others)
    print("Step 6: Getting detailed information and sizes for all layers...")

    # Get size and details for all layers
    all_layer_details = {}

    for layer_id in layer_to_tasks.keys():
        # Check if this is a layer from history
        if layer_id.startswith("image-layer:"):
            # These layers have their size and created_by already stored
            created_by = top_layer_created_by.get(layer_id, "Layer from docker history")
            # Extract the image ID from the layer ID (format: image-layer:IMAGE_COLUMN:IMAGE_ID)
            example_image = snapshot_layerid_to_imageid[layer_id]
            layer_info = {
                "id": layer_id,
                "size": top_layer_sizes.get(layer_id, 0),
                "created_by": created_by,
                "comment": "",
                "example_image": example_image,
            }
            all_layer_details[layer_id] = layer_info
            continue

        # Find an image that contains this layer
        example_image = None
        for task_info in task_infos:
            for image_id in task_info.image_ids:
                if layer_id in get_image_layers(image_id):
                    example_image = image_id
                    break
            if example_image:
                break

        # Get history to find this layer's creation command/description
        layer_info = {
            "id": layer_id,
            "size": 0,
            "created_by": "",
            "comment": "",
            "example_image": example_image,
        }

        if example_image:
            # Get the full layer list for this image
            image_layers = get_image_layers(example_image)
            layer_index = image_layers.index(layer_id) if layer_id in image_layers else -1

            output = run_command(["docker", "history", "--no-trunc", "--format", "{{json .}}", example_image])
            if output:
                # Parse history - the order is reversed (newest first)
                history_lines = output.strip().split("\n")

                # Try to match by index (history is in reverse order compared to layers)
                if 0 <= layer_index < len(history_lines):
                    # History is newest first, layers are oldest first
                    hist_index = len(history_lines) - layer_index - 1
                    if 0 <= hist_index < len(history_lines):
                        try:
                            hist = json.loads(history_lines[hist_index])
                            layer_info["created_by"] = hist.get("CreatedBy", "")
                            layer_info["size"] = parse_size_string(hist.get("Size", "0B"))
                            layer_info["comment"] = hist.get("Comment", "")
                        except json.JSONDecodeError:
                            pass

        all_layer_details[layer_id] = layer_info

    print()

    # Step 7: Report archived-only layers with all info
    print("=" * 80)
    print("ARCHIVED-ONLY LAYERS - DETAILED INFORMATION")
    print("=" * 80)
    print()

    for layer_id in sorted(archived_only_layers):
        detail = all_layer_details.get(layer_id, {})
        layer_size = detail.get("size", 0)
        created_by = detail.get("created_by", "")

        # Truncate description if too long
        description = created_by[:100] + "..." if len(created_by) > 100 else created_by

        print(f"Layer ID: {layer_id}")
        print(f"  Size: {format_bytes(layer_size)}")
        print(f"  Description: {description}")
        if detail.get("example_image"):
            print(f"  Example Image: {detail['example_image']}")
        if detail.get("comment"):
            print(f"  Comment: {detail['comment']}")
        print()

    print()

    # Step 8: Report non-archived-only layers (shared or active-only)
    print("=" * 80)
    print("NON-ARCHIVED-ONLY LAYERS (shared or used by active tasks)")
    print("=" * 80)
    print()

    # Find layers that are NOT archived-only
    non_archived_only_layers = set()
    for layer_id, task_ids in layer_to_tasks.items():
        if layer_id not in archived_only_layers:
            non_archived_only_layers.add(layer_id)

    print(f"Found {len(non_archived_only_layers)} layers that are NOT archived-only")
    print()

    for layer_id in sorted(non_archived_only_layers):
        detail = all_layer_details.get(layer_id, {})
        layer_size = detail.get("size", 0)
        created_by = detail.get("created_by", "")

        # Truncate description if too long
        description = created_by[:100] + "..." if len(created_by) > 100 else created_by

        task_ids = layer_to_tasks[layer_id]
        # Deduplicate task IDs
        unique_task_ids = list(set(task_ids))

        print(f"Layer ID: {layer_id}")
        print(f"  Size: {format_bytes(layer_size)}")
        print(f"  Description: {description}")
        print(f"  Used by {len(unique_task_ids)} task(s):")
        for task_id in sorted(unique_task_ids):
            # Determine if this task is archived or active
            task_info = next((t for t in task_infos if t.task_id == task_id), None)
            if task_info:
                status = "ARCHIVED" if task_info.is_archived else "ACTIVE"
                print(f"    - {task_id} ({status})")
        print()

    print()

    # Step 9: Calculate cumulative sizes
    archived_only_total_size = sum(
        all_layer_details.get(layer_id, {}).get("size", 0) for layer_id in archived_only_layers
    )

    non_archived_only_total_size = sum(
        all_layer_details.get(layer_id, {}).get("size", 0) for layer_id in non_archived_only_layers
    )

    # Step 10: Report summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total buildx cache size: {format_bytes(total_cache_size)}")
    print()
    print(f"Archived tasks: {len(archived_tasks)}")
    print(f"Active tasks: {len(active_tasks)}")
    print()
    print(f"Total unique layers: {len(layer_to_tasks)}")
    print(f"Layers used ONLY by archived tasks: {len(archived_only_layers)}")
    print(f"Layers shared or used by active tasks: {len(non_archived_only_layers)}")
    print()
    print(f"Cumulative size of archived-only layers: {format_bytes(archived_only_total_size)}")
    print(f"Cumulative size of non-archived-only layers: {format_bytes(non_archived_only_total_size)}")
    print()


if __name__ == "__main__":
    main()
