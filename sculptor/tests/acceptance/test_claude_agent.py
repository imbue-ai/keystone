import os
from pathlib import Path

import pytest
from loguru import logger

from sculptor.agents.default.claude_code_sdk.process_manager_utils import cancel_pending_claude_tool_calls
from sculptor.agents.default.claude_code_sdk.process_manager_utils import is_session_id_valid
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment


@pytest.mark.skip(reason="This test calls claude code directly, which we probably don't want to enable in pipelines")
def test_interrupt_continues_successfully(docker_environment: DockerEnvironment) -> None:
    session_id = "07d726fb-bc15-425f-a526-093b9c12fe61"
    local_path = Path(__file__).parent / "test_data" / f"{session_id}.jsonl"
    docker_environment.copy_from_local(
        local_path, f"/user_home/.claude/projects/-user-home-workspace/{session_id}.jsonl"
    )
    cancel_pending_claude_tool_calls(docker_environment, session_id)

    secrets = {"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]}
    process = docker_environment.run_process_in_background(
        ["bash", "-c", f"claude -p 'keep going' --resume {session_id} --output-format=stream-json --verbose"],
        secrets=secrets,
    )
    process.wait_and_read()
    assert process.returncode == 0


@pytest.mark.skip(reason="This test calls claude code directly, which we probably don't want to enable in pipelines")
@pytest.mark.parametrize(
    "session_id",
    [
        "c358b079-4c1e-46cb-bede-f7b4359af287",
        "d0f81d3c-1e77-4290-9eb4-cb73f6271e39",
    ],
)
def test_is_session_id_valid(docker_environment: DockerEnvironment, session_id: str) -> None:
    local_path = Path(__file__).parent / "test_data" / f"{session_id}.jsonl"
    docker_environment.copy_from_local(
        local_path, f"/user_home/.claude/projects/-user-home-workspace/{session_id}.jsonl"
    )
    is_valid = is_session_id_valid(session_id, docker_environment, is_session_running=False)
    logger.info("Session id {} is valid: {}", session_id, is_valid)
    secrets = {"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]}
    process = docker_environment.run_process_in_background(
        ["bash", "-c", f"claude -p 'hello' --resume {session_id} --output-format=stream-json --verbose"],
        secrets=secrets,
    )
    process.wait_and_read()
    assert (process.returncode == 0) == is_valid  # if the session id is valid, the process should return 0
