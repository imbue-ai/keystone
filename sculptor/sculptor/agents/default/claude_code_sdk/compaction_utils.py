import json
from queue import Queue
from typing import Literal

from loguru import logger

from sculptor.agents.default.claude_code_sdk.utils import get_state_file_contents
from sculptor.agents.default.constants import INPUT_TO_OUTPUT_TOKEN_COST_RATIO
from sculptor.agents.default.constants import TOKEN_AND_COST_STATE_FILE
from sculptor.agents.default.constants import WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN
from sculptor.agents.default.utils import stream_token_and_cost_info
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.errors import EnvironmentFailure


def _write_file(environment: Environment, path: str, content: str, mode: Literal["w", "a"] = "w") -> None:
    try:
        environment.write_file(path, content, mode=mode)
    except EnvironmentFailure as e:
        logger.debug("Failed to write file {}: {}", path, e)


def update_weighted_tokens_since_last_verifier_check(
    environment: Environment,
    input_tokens: int,
    output_tokens: int,
) -> None:
    weighted_tokens = round(input_tokens * INPUT_TO_OUTPUT_TOKEN_COST_RATIO + output_tokens)
    _write_file(
        environment,
        str(environment.get_state_path() / WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN),
        f"{weighted_tokens}\n",
        mode="a",
    )
    logger.debug("Appended weighted tokens since last verifier check: {}", weighted_tokens)


def get_weighted_tokens_since_last_verifier_check(environment: Environment) -> int:
    token_state_content = get_state_file_contents(environment, WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN)
    if not token_state_content:
        return 0

    lines = token_state_content.strip().splitlines()

    if len(lines) == 1 and lines[0] != "RESET":
        try:
            old_total = int(lines[0].strip())
            _write_file(
                environment,
                str(environment.get_state_path() / WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN),
                f"RESET\n{old_total}\n",
            )
            return old_total
        except ValueError:
            _write_file(
                environment,
                str(environment.get_state_path() / WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN),
                "RESET\n",
            )
            return 0

    total = 0
    for line in reversed(lines):
        line = line.strip()
        if line == "RESET":
            break
        if not line:
            continue
        try:
            total += int(line)
        except ValueError:
            logger.debug("Skipping malformed line in token state: {}", line)
            continue

    return total


def reset_weighted_tokens_since_last_verifier_check(environment: Environment) -> None:
    _write_file(
        environment,
        str(environment.get_state_path() / WEIGHTED_TOKENS_SINCE_LAST_VERIFIER_CHECK_RUN),
        "RESET\n",
        mode="a",
    )
    logger.debug("Appended RESET marker to weighted tokens file")


def update_token_and_cost_state(
    environment: Environment,
    source_branch: str,
    output_message_queue: Queue[Message],
    session_id: str,
    cost_usd: float,
    task_id: TaskID,
) -> None:
    """Update cumulative token count and cost, persisting to state file."""
    cumulative_tokens = 0
    cumulative_cost_usd = cost_usd

    token_state_content = get_state_file_contents(environment, TOKEN_AND_COST_STATE_FILE)
    if token_state_content:
        try:
            token_state = json.loads(token_state_content)
            cumulative_cost_usd += token_state.get("cost_usd", 0.0)
        except json.JSONDecodeError:
            logger.warning("Failed to parse token state file, resetting to zero")

    try:
        session_path = session_id + ".jsonl"
        content = environment.read_file(str(environment.get_claude_jsonl_path() / session_path)).splitlines()
        last_block = content[-1]
        json_block = json.loads(last_block)
        if "message" in json_block:
            info = json_block["message"]
            if "usage" in info:
                tokens = info["usage"]
                cumulative_tokens = (
                    tokens["input_tokens"]
                    + tokens["output_tokens"]
                    + tokens["cache_creation_input_tokens"]
                    + tokens["cache_read_input_tokens"]
                )
    except FileNotFoundError:
        # TODO(andrew.laack): Maybe notify users here too?
        pass
    except json.decoder.JSONDecodeError:
        # TODO(andrew.laack): We likely want to surface this error to users.
        pass
    except EnvironmentFailure as e:
        logger.debug("Failed to read session file: {}", e)

    token_state = {"tokens": cumulative_tokens, "cost_usd": cumulative_cost_usd}

    _write_file(environment, str(environment.get_state_path() / TOKEN_AND_COST_STATE_FILE), json.dumps(token_state))
    logger.info("Updated token state: {} tokens, ${:.4f}", cumulative_tokens, cumulative_cost_usd)
    stream_token_and_cost_info(
        environment=environment,
        source_branch=source_branch,
        output_message_queue=output_message_queue,
        task_id=task_id,
    )
