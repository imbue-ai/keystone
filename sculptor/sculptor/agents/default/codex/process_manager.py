import shlex
import time
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from queue import Queue
from subprocess import TimeoutExpired
from threading import Event
from typing import Callable
from typing import Mapping

from loguru import logger

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.secrets_utils import Secret
from imbue_core.thread_utils import ObservableThread
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.claude_code_sdk.process_manager_utils import get_user_instructions
from sculptor.agents.default.claude_code_sdk.utils import get_state_file_contents
from sculptor.agents.default.claude_code_sdk.utils import get_warning_message
from sculptor.agents.default.codex.compaction_utils import acquire_final_compaction_message
from sculptor.agents.default.codex.compaction_utils import flush_session_file_queue
from sculptor.agents.default.codex.compaction_utils import get_session_file_streaming_process
from sculptor.agents.default.codex.output_processor import CodexOutputProcessor
from sculptor.agents.default.codex.utils import cancel_pending_codex_tool_calls
from sculptor.agents.default.codex.utils import get_codex_command
from sculptor.agents.default.constants import HIDDEN_SYSTEM_PROMPT
from sculptor.agents.default.constants import MODEL_SHORTNAME_MAP
from sculptor.agents.default.constants import SESSION_ID_STATE_FILE
from sculptor.agents.default.errors import CompactionFailure
from sculptor.agents.default.errors import InterruptFailure
from sculptor.agents.default.errors import InvalidSlashCommandError
from sculptor.agents.default.posthog_utils import emit_posthog_agent_command_event
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import InterruptProcessUserMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import ErrorType
from sculptor.interfaces.agents.errors import IllegalOperationError
from sculptor.interfaces.agents.errors import UncleanTerminationAgentError
from sculptor.interfaces.agents.errors import WaitTimeoutAgentError
from sculptor.interfaces.environments.base import Environment


class CodexProcessManager:
    def __init__(
        self,
        environment: Environment,
        task_id: TaskID,
        in_testing: bool,
        secrets: Mapping[str, str | Secret],
        output_message_queue: Queue[Message],
        handle_user_message_callback: Callable[[UserMessageUnion], AbstractContextManager[None, bool | None]],
        system_prompt: str,
        source_branch: str,
        task_branch: str,
    ):
        self.environment = environment
        self.task_id = task_id
        self.in_testing = in_testing
        self._secrets = secrets
        self._output_messages = output_message_queue
        # there are no untracked changes at this point, so we can use the fast path
        self._diff_tracker: DiffTracker | None = DiffTracker(self.environment, self._output_messages)
        self._system_prompt: str = system_prompt
        self._source_branch: str = source_branch
        self._task_branch: str = task_branch
        self._model_name: str | None = MODEL_SHORTNAME_MAP[LLMModel.GPT_5_1_CODEX]
        self._handle_user_message_callback = handle_user_message_callback
        self._message_processing_thread: ObservableThread | None = None
        self._process: RunningProcess | None = None
        self._is_interrupted: Event = Event()

    def process_input_message(
        self, message: CommandInputUserMessage | ChatInputUserMessage | ResumeAgentResponseRunnerMessage
    ) -> None:
        message_processing_thread = self._message_processing_thread
        if message_processing_thread is not None:
            message_processing_thread.join(timeout=0.01)
            if message_processing_thread.is_alive():
                raise IllegalOperationError("Cannot process new message while last message is still being processed")
        self._process = None
        self._message_processing_thread = self.environment.concurrency_group.start_new_thread(
            target=self._process_single_message,
            args=(message,),
        )

    def process_compact_message(self, message: CompactTaskUserMessage) -> None:
        message_processing_thread = self._message_processing_thread
        if message_processing_thread is not None:
            message_processing_thread.join(timeout=0.01)
            if message_processing_thread.is_alive():
                raise IllegalOperationError("Cannot process new message while last message is still being processed")
        self._process = None
        self._message_processing_thread = self.environment.concurrency_group.start_new_thread(
            target=self._process_compact_message,
            args=(message,),
        )

    def interrupt_current_message(self, message: InterruptProcessUserMessage) -> None:
        with self._handle_user_message_callback(message):
            if self._message_processing_thread is None or not self._message_processing_thread.is_alive():
                logger.info("Message processing thread is not alive, skipping interrupt")
                return
            try:
                # TODO: we want to wait for a valid session id but it'll block the event loop right now and requires a larger refactor
                self._wait_until_interrupt_is_safe()
            except InterruptFailure as e:
                self._output_messages.put(
                    get_warning_message(
                        "Failed to interrupt agent safely",
                        e,
                        self.task_id,
                    )
                )
            else:
                logger.debug("Done waiting for a valid session id and process - the agent is now safe to interrupt")
            if self._process is not None:
                self._is_interrupted.set()
                self._process.terminate(force_kill_seconds=10.0)  # pyre-ignore[16]
                message_processing_thread = self._message_processing_thread
                assert (
                    message_processing_thread is not None
                )  # this is to appease pyre - there is no way for message processing thread to be set by this point because push_message is synchronous
                message_processing_thread.join(timeout=30.0)  # wait for the message processing thread to finish
                if message_processing_thread.is_alive():
                    # Note: should this be an expected error?
                    raise TimeoutError("Message processing thread failed to terminate")
                session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
                if session_id is not None:
                    cancel_pending_codex_tool_calls(environment=self.environment, session_id=session_id)

    def get_exception_if_exists(self) -> BaseException | None:
        if self._message_processing_thread is not None and self._message_processing_thread.exception_raw is not None:
            return self._message_processing_thread.exception_raw
        return None

    def stop(self, timeout: float, is_waiting: bool = False) -> None:
        thread_wait_time = max(timeout - 5.0, timeout / 2.0)
        process_wait_time = timeout - thread_wait_time
        if self._process is not None:
            if is_waiting:
                try:
                    self._process.wait(process_wait_time)
                except TimeoutExpired as e:
                    raise WaitTimeoutAgentError(
                        f"Failed to wait for process to finish within {process_wait_time} seconds"
                    ) from e
            else:
                self._process.terminate(force_kill_seconds=process_wait_time)
        message_processing_thread = self._message_processing_thread
        if message_processing_thread is not None:
            # NOTE: if there is an exception in the message processing thread, calling .join() will raise it
            message_processing_thread.join(timeout=thread_wait_time)
            # FIXME: we need more consistent handling -- all .join() calls must be followed by checking that the thread is no longer alive
            if message_processing_thread.is_alive():
                if is_waiting:
                    raise WaitTimeoutAgentError(f"Failed to join message processing thread within {timeout} seconds")
                else:
                    raise UncleanTerminationAgentError(
                        f"Failed to terminate message processing thread within {thread_wait_time} seconds"
                    )

    def _get_combined_system_prompt(self) -> str:
        full_system_prompt = HIDDEN_SYSTEM_PROMPT
        if self._system_prompt:
            full_system_prompt = (
                f"{full_system_prompt}\n <User instructions>\n{self._system_prompt}\n </User instructions>"
            )
        return full_system_prompt

    def _process_single_message(self, message: UserMessageUnion) -> None:
        with self._handle_user_message_callback(message):
            user_instructions = get_user_instructions(
                message,  # pyre-fixme[6]
                self.environment,
                self._output_messages,
                self.task_id,
                self._secrets,
                file_paths=(),
            )
            if user_instructions is None:
                return
            filename = f"{self.environment.get_state_path()}/user_instructions_{message.message_id}.txt"
            self.environment.write_file(filename, user_instructions)
            maybe_session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
            combined_system_prompt = self._get_combined_system_prompt()

            maybe_model = (
                MODEL_SHORTNAME_MAP[message.model_name]
                if isinstance(message, (ChatInputUserMessage, ResumeAgentResponseRunnerMessage)) and message.model_name
                else None
            )
            if maybe_model is not None:
                self._model_name = maybe_model

            codex_command = get_codex_command(
                instructions_file=Path(filename),
                system_prompt=combined_system_prompt,
                session_id=maybe_session_id,
                model_name=maybe_model,
            )
            if user_instructions.strip().startswith("/"):
                try:
                    slash_command = user_instructions.strip().split()[0]
                    _validate_slash_command(slash_command, self.environment)
                except InvalidSlashCommandError as e:
                    self._output_messages.put(get_warning_message(str(e), None, self.task_id))
                    return
            logger.info("Executing codex command in environment: {}", " ".join(codex_command))

            emit_posthog_agent_command_event(
                self.task_id,
                codex_command,
                combined_system_prompt,
                user_instructions,
                event_name=SculptorPosthogEvent.CODEX_COMMAND,
            )

            process = self.environment.run_process_in_background(codex_command, secrets=self._secrets)
            self._process = process
            self._read_output_from_process(process, codex_command)

            # reinitialize the diff tracker with the new tree hash - this will clear the in-memory snapshots but that is okay because we have the new tree hash
            # TODO: _diff_tracker can be None
            self._diff_tracker.update_initial_tree_sha()  # pyre-fixme[16]

    def _process_compact_message(self, message: CompactTaskUserMessage) -> None:
        with self._handle_user_message_callback(message):
            maybe_session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
            assert maybe_session_id is not None

            session_file_streaming_process = get_session_file_streaming_process(
                environment=self.environment, session_id=maybe_session_id
            )
            # Flush the current contents of the session file to prevent seeing old compaction lines
            flush_session_file_queue(session_file_queue=session_file_streaming_process.get_queue(), timeout=0.1)

            window_name = "compaction"
            tmux_session_name = uuid.uuid4().hex
            tmux_command = f"codex resume {shlex.quote(maybe_session_id)}"
            self.environment.run_process_to_completion(
                command=[
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    tmux_session_name,
                    "-n",
                    window_name,
                    "bash",
                    "-c",
                    tmux_command,
                ],
                secrets=self._secrets,
            )

            # TODO: This is horrible and I deep guilt about it; it needs to get replaced with
            #       (1) Codex supporting slash commands from script mode or
            #       (2) A check for typability in the tmux session created above
            time.sleep(1.5)
            self.environment.run_process_to_completion(
                command=["tmux", "send-keys", "-t", f"{tmux_session_name}:{window_name}", "/compact"],
                secrets=self._secrets,
            )
            self.environment.run_process_to_completion(
                command=["tmux", "send-keys", "-t", f"{tmux_session_name}:{window_name}", "C-m"], secrets=self._secrets
            )

            try:
                compaction_message = acquire_final_compaction_message(
                    session_file_queue=session_file_streaming_process.get_queue(), timeout=2 * 60
                )
                self._output_messages.put(compaction_message)
            except CompactionFailure as e:
                log_exception(exc=e, message="Failed to compact history for codex")
                get_warning_message(
                    "Failed to compact history for Codex",
                    e,
                    self.task_id,
                )
            self.environment.run_process_to_completion(
                command=["tmux", "kill-session", "-t", tmux_session_name], secrets=self._secrets
            )

    def _wait_until_interrupt_is_safe(self) -> None:
        start_time = time.time()
        process_start_timeout = 5.0
        while self._process is None and time.time() - start_time < process_start_timeout:
            time.sleep(0.01)
        if self._process is None:
            raise InterruptFailure(
                f"Codex process has not started in {process_start_timeout} seconds, cannot interrupt"
            )

    def _read_output_from_process(self, process: RunningProcess, codex_command: list[str]) -> None:
        assert self._diff_tracker is not None
        output_processor = CodexOutputProcessor(
            process=process,
            source_command=" ".join(codex_command),
            output_message_queue=self._output_messages,
            environment=self.environment,
            diff_tracker=self._diff_tracker,
            source_branch=self._source_branch,
            task_id=self.task_id,
        )
        found_end_message = output_processor.process_output()
        logger.info("Waiting for process to finish")
        process.wait(timeout=5.0)  # process should be done by now, but we'll wait for it to be sure
        assert process.returncode is not None, "Process return code should be set by now"
        logger.info(
            "Process returned return code {}, {}, {}", process.returncode, process.read_stdout(), process.read_stderr()
        )

        # TODO: we can be more strict about when we're interrupted versus not but this is good enough for now
        if self._is_interrupted.is_set():
            logger.info("Agent was interrupted, ignoring exit code")
            self._is_interrupted.clear()
        else:
            if process.returncode != 0:
                # TODO (amy): we need to figure out how to distinguish between claude and environment errors here...
                raise AgentClientError(
                    f"Agent died with exit code {process.returncode} and stderr: {process.read_stderr()} and stdout: {process.read_stdout()}",
                    exit_code=process.returncode,
                    metadata={
                        "source_command": " ".join(codex_command),
                        "error": ErrorType.NONZERO_EXIT_CODE,
                        "stderr": process.read_stderr(),
                        "stdout": process.read_stdout(),
                    },
                )
        logger.info("Process finished.")


def _validate_slash_command(command: str, environment: Environment) -> None:
    raise InvalidSlashCommandError("We currently do not support Codex slash commands.")
