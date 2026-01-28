import json
import time
from queue import Empty
from queue import Queue

from loguru import logger

from imbue_core.processes.local_process import RunningProcess
from sculptor.agents.default.codex.utils import get_codex_session_file_path
from sculptor.agents.default.errors import CompactionFailure
from sculptor.interfaces.agents.agent import ContextSummaryMessage
from sculptor.interfaces.environments.base import Environment


def get_session_file_streaming_process(environment: Environment, session_id: str) -> RunningProcess:
    session_file_path = get_codex_session_file_path(environment=environment, session_id=session_id)
    return environment.run_process_in_background(command=["tail", "-F", str(session_file_path)], secrets={})


def acquire_final_compaction_message(session_file_queue: Queue, timeout: float | None = None) -> ContextSummaryMessage:
    start_time = time.time()
    while timeout is None or time.time() - start_time < timeout:
        line, is_stdout = session_file_queue.get()
        logger.info("Got: {}", line, is_stdout)
        try:
            parsed_line = json.loads(line)
        except json.decoder.JSONDecodeError:
            continue
        message_type = parsed_line.get("type")
        if message_type == "compacted":
            summary = parsed_line.get("payload").get("message", "")
            return ContextSummaryMessage(content=summary)
    raise CompactionFailure("Failed to acquire final compaction message")


def flush_session_file_queue(session_file_queue: Queue, timeout: float) -> None:
    while True:
        try:
            tmp = session_file_queue.get(timeout=timeout)
            logger.info("Flushed: {}", tmp)
        except Empty:
            break
