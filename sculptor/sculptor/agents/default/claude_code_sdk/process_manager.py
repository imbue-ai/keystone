import time
from contextlib import AbstractContextManager
from pathlib import Path
from queue import Queue
from subprocess import TimeoutExpired
from threading import Event
from typing import Callable
from typing import Mapping

from loguru import logger

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.secrets_utils import Secret
from imbue_core.thread_utils import ObservableThread
from sculptor.agents.default.claude_code_sdk.config_service_plugin import SUPPORTED_BUILTIN_SLASH_COMMANDS
from sculptor.agents.default.claude_code_sdk.config_service_plugin import get_all_supported_slash_commands
from sculptor.agents.default.claude_code_sdk.constants import VALIDATED_SESSION_ID_STATE_FILE
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.claude_code_sdk.output_processor import ClaudeOutputProcessor
from sculptor.agents.default.claude_code_sdk.process_manager_utils import cancel_pending_claude_tool_calls
from sculptor.agents.default.claude_code_sdk.process_manager_utils import get_claude_command
from sculptor.agents.default.claude_code_sdk.process_manager_utils import get_user_instructions
from sculptor.agents.default.claude_code_sdk.process_manager_utils import is_session_id_valid
from sculptor.agents.default.claude_code_sdk.utils import get_state_file_contents
from sculptor.agents.default.claude_code_sdk.utils import get_warning_message
from sculptor.agents.default.constants import HIDDEN_SYSTEM_PROMPT
from sculptor.agents.default.constants import MODEL_SHORTNAME_MAP
from sculptor.agents.default.constants import SESSION_ID_STATE_FILE
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


class ClaudeProcessManager:
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
        self._model_name: str | None = MODEL_SHORTNAME_MAP[LLMModel.CLAUDE_4_SONNET]
        self._handle_user_message_callback = handle_user_message_callback
        self._message_processing_thread: ObservableThread | None = None
        self._process: RunningProcess | None = None
        self._is_interrupted: Event = Event()
        self._session_id_written_event: Event = Event()

    def process_input_message(
        self, message: CommandInputUserMessage | ChatInputUserMessage | ResumeAgentResponseRunnerMessage
    ) -> None:
        message_processing_thread = self._message_processing_thread
        if message_processing_thread is not None:
            message_processing_thread.join(timeout=0.01)
            if message_processing_thread.is_alive():
                raise IllegalOperationError("Cannot process new message while last message is still being processed")
        self._process = None
        self._session_id_written_event.clear()
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
        self._session_id_written_event.clear()
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
                self._wait_until_interrupt_is_safe(should_wait_for_valid_session=False)
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
                if session_id is not None and is_session_id_valid(
                    session_id, self.environment, is_session_running=False
                ):
                    cancel_pending_claude_tool_calls(self.environment, session_id)

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

    def _claude_compact_context(self, session_id: str | None) -> None:
        claude_command = [
            "bash",
            "-c",
            f"claude --resume {session_id} -p --output-format=stream-json --verbose /compact",
        ]
        process = self.environment.run_process_in_background(claude_command, secrets=self._secrets)
        self._process = process
        source_command = " ".join(claude_command)

        _found_end_message = ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=source_command,
            output_message_queue=self._output_messages,
            environment=self.environment,
            task_id=self.task_id,
            session_id_written_event=self._session_id_written_event,
            source_branch=self._source_branch,
            diff_tracker=None,
            is_compacting=True,
        )

    def _wait_until_interrupt_is_safe(self, should_wait_for_valid_session: bool) -> None:
        start_time = time.time()
        process_start_timeout = 5.0
        while self._process is None and time.time() - start_time < process_start_timeout:
            time.sleep(0.01)
        if self._process is None:
            raise InterruptFailure(
                f"Claude code process has not started in {process_start_timeout} seconds, cannot interrupt"
            )
        if should_wait_for_valid_session:
            session_id_written_timeout = 30.0
            if not self._session_id_written_event.wait(timeout=session_id_written_timeout):
                raise InterruptFailure(
                    f"Session ID not written in {session_id_written_timeout} seconds - the interrupted user message may be rolled back"
                )
            session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
            assert session_id is not None
            start_time = time.time()
            session_id_valid_timeout = 10.0
            while not is_session_id_valid(session_id, self.environment, is_session_running=True):
                time.sleep(0.1)
                if time.time() - start_time > session_id_valid_timeout:
                    raise InterruptFailure(
                        f"Session ID not valid in {session_id_valid_timeout} seconds - the interrupted user message may be rolled back"
                    )
        else:
            if not self._session_id_written_event.is_set():
                raise InterruptFailure(
                    "The interrupt occurred before the session id was written - the interrupted user message will be rolled back"
                )
            else:
                session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
                assert session_id is not None
                if not is_session_id_valid(session_id, self.environment, is_session_running=True):
                    raise InterruptFailure(
                        "The interrupt occurred before the session id was written properly - the interrupted user message will be rolled back"
                    )

    def _maybe_save_files_to_environment(self, message: UserMessageUnion) -> tuple[str, ...]:
        if not isinstance(message, ChatInputUserMessage):
            return tuple()

        file_paths = []
        for local_file_path in message.files:
            file_path = self.environment.get_images_path() / local_file_path.split("/")[-1]
            self.environment.write_file(path=str(file_path), content=Path(local_file_path).read_bytes())
            file_paths.append(str(file_path))

        return tuple(file_paths)

    def _process_single_message(self, message: UserMessageUnion) -> None:
        with self._handle_user_message_callback(message):
            # if the message includes files, we need to save them to the environment first
            file_paths = self._maybe_save_files_to_environment(message)

            user_instructions = get_user_instructions(
                # TODO: should the message be `UserMessageUnion` or `CommandInputUserMessage | ChatInputUserMessage | ResumeAgentResponseRunnerMessage`?
                # (i.e. should we update the type signature of _process_single_message or get_user_instructions?)
                message=message,  # pyre-fixme[6]
                environment=self.environment,
                output_message_queue=self._output_messages,
                task_id=self.task_id,
                secrets=self._secrets,
                file_paths=file_paths,
            )
            if user_instructions is None:
                return
            if user_instructions.strip().startswith("/"):
                try:
                    slash_command = user_instructions.strip().split()[0]
                    _validate_slash_command(slash_command, self.environment)
                except InvalidSlashCommandError as e:
                    self._output_messages.put(get_warning_message(str(e), None, self.task_id))
                    return
            filename = f"{self.environment.get_state_path()}/user_instructions_{message.message_id}.txt"
            self.environment.write_file(filename, user_instructions)
            maybe_session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
            if maybe_session_id is not None:
                if is_session_id_valid(maybe_session_id, self.environment, is_session_running=False):
                    # if the session id is valid, we can resume from it and we should save it to the state file
                    self.environment.write_file(
                        str(self.environment.get_state_path() / VALIDATED_SESSION_ID_STATE_FILE), maybe_session_id
                    )
                else:
                    self._output_messages.put(
                        get_warning_message(
                            "Rolling back to the last valid session id - this means your last user message may not be in the agent context",
                            None,
                            self.task_id,
                        )
                    )
                    # otherwise, use the previous validated session id if it exists
                    maybe_session_id = get_state_file_contents(self.environment, VALIDATED_SESSION_ID_STATE_FILE)
            combined_system_prompt = self._get_combined_system_prompt()
            maybe_model = (
                MODEL_SHORTNAME_MAP[message.model_name]
                if isinstance(message, (ChatInputUserMessage, ResumeAgentResponseRunnerMessage)) and message.model_name
                else None
            )
            if maybe_model is not None:
                self._model_name = maybe_model
            claude_command = get_claude_command(
                instructions_file=Path(filename),
                system_prompt=combined_system_prompt,
                session_id=maybe_session_id,
                model_name=maybe_model,
                enable_streaming=True,
            )
            logger.info("Executing claude command in environment: {}", " ".join(claude_command))

            emit_posthog_agent_command_event(
                self.task_id,
                claude_command,
                combined_system_prompt,
                user_instructions,
                SculptorPosthogEvent.CLAUDE_COMMAND,
            )

            process = self.environment.run_process_in_background(claude_command, secrets=self._secrets)
            self._process = process
            self._read_output_from_process(process, claude_command)

            # reinitialize the diff tracker with the new tree hash - this will clear the in-memory snapshots but that is okay because we have the new tree hash
            # TODO: _diff_tracker can be None
            self._diff_tracker.update_initial_tree_sha()  # pyre-fixme[16]

    def _process_compact_message(self, message: UserMessageUnion) -> None:
        with self._handle_user_message_callback(message):
            maybe_session_id = get_state_file_contents(self.environment, SESSION_ID_STATE_FILE)
            if maybe_session_id is not None:
                if is_session_id_valid(maybe_session_id, self.environment, is_session_running=False):
                    # if the session id is valid, we can resume from it and we should save it to the state file
                    self.environment.write_file(
                        str(self.environment.get_state_path() / VALIDATED_SESSION_ID_STATE_FILE),
                        maybe_session_id,
                    )
                else:
                    self._output_messages.put(
                        get_warning_message(
                            "Rolling back to the last valid session id - this means your last user message may not be in the agent context",
                            None,
                            self.task_id,
                        )
                    )
                    # otherwise, use the previous validated session id if it exists
                    maybe_session_id = get_state_file_contents(self.environment, VALIDATED_SESSION_ID_STATE_FILE)
            self._claude_compact_context(maybe_session_id)

    def _read_output_from_process(self, process: RunningProcess, claude_command: list[str]) -> None:
        assert self._diff_tracker is not None
        _found_end_message = ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=" ".join(claude_command),
            output_message_queue=self._output_messages,
            environment=self.environment,
            diff_tracker=self._diff_tracker,
            source_branch=self._source_branch,
            task_id=self.task_id,
            session_id_written_event=self._session_id_written_event,
            streaming_enabled=True,
        )
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
                        "source_command": " ".join(claude_command),
                        "error": ErrorType.NONZERO_EXIT_CODE,
                        "stderr": process.read_stderr(),
                        "stdout": process.read_stdout(),
                    },
                )
            # elif not found_end_message:
            #     raise ClaudeClientError(
            #         f"Agent exited with exit code {process.returncode}, but it did not have the final message -- it was probably terminated.",
            #         exit_code=AGENT_EXIT_CODE_FROM_SIGINT,
            #         metadata={
            #             "source_command": " ".join(claude_command),
            #             "error": ErrorType.RESPONSE_INCOMPLETE,
            #             "stderr": process.read_stderr(),
            #             "stdout": process.read_stdout(),
            #         },
            #     )
        logger.info("Process finished.")


def _validate_slash_command(command: str, environment: Environment) -> None:
    if not any(slash_command.value == command for slash_command in get_all_supported_slash_commands(environment)):
        builtin_commands_with_leading_slash = [f"/{cmd}" for cmd in SUPPORTED_BUILTIN_SLASH_COMMANDS]
        raise InvalidSlashCommandError(
            " ".join(
                [
                    "Invalid slash command:",
                    f"Please note that we currently only support {', '.join(builtin_commands_with_leading_slash)}",
                    'and your custom commands. The "Synchronize Claude Code Configuration" setting also needs to be enabled.',
                ]
            )
        )
