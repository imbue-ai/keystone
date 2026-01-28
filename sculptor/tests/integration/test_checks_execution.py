import datetime
import tempfile
import time
from pathlib import Path
from queue import Queue
from typing import Generator
from typing import cast

import pytest

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.user_config import UserConfig
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import CheckFinishedRunnerMessage
from sculptor.interfaces.agents.agent import CheckLaunchedRunnerMessage
from sculptor.interfaces.agents.agent import CheckOutputRunnerMessage
from sculptor.interfaces.agents.agent import ChecksDefinedRunnerMessage
from sculptor.interfaces.agents.agent import HelloAgentConfig
from sculptor.interfaces.agents.agent import RestartCheckUserMessage
from sculptor.interfaces.agents.checks import CheckFinishedReason
from sculptor.interfaces.environments.base import LocalImageConfig
from sculptor.primitives.ids import RequestID
from sculptor.primitives.ids import UserReference
from sculptor.primitives.threads import ObservableThread
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.user_config import set_user_config_instance
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.environments.local_environment import LocalEnvironmentConfig
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.tasks.handlers.run_agent.checks.constants import CHECK_CONFIG_PATH
from sculptor.tasks.handlers.run_agent.v1 import _run_agent_in_environment
from sculptor.tasks.handlers.run_agent.v1_checks_test import wait_for_message_type


@pytest.fixture
def test_user_config() -> UserConfig:
    return UserConfig(
        user_email="test@example.com",
        user_git_username="testuser",
        user_id="test_user_id",
        organization_id="test_org_id",
        instance_id="test_instance_id",
        anonymous_access_token="test_token",
        anthropic_api_key="sk-ant-api03-testingkey",
        is_error_reporting_enabled=True,
        is_product_analytics_enabled=True,
        is_llm_logs_enabled=True,
        is_session_recording_enabled=True,
        is_repo_backup_enabled=True,
        is_privacy_policy_consented=True,
        are_suggestions_enabled=True,
    )


@pytest.fixture
def test_settings(test_settings) -> SculptorSettings:
    return test_settings.model_copy(
        update={
            "IS_CHECKS_ENABLED": True,
            "LOCAL_PROVIDER_ENABLED": True,
        }
    )


@pytest.fixture
def backend_services(
    test_root_concurrency_group: ConcurrencyGroup,
    test_service_collection: CompleteServiceCollection,
    test_project,
    test_user_config: UserConfig,
    backend_task: Task,
) -> ServiceCollectionForTask:
    set_user_config_instance(test_user_config)
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        transaction.upsert_project(test_project)
        test_service_collection.task_service.create_task(backend_task, transaction)
    return cast(ServiceCollectionForTask, test_service_collection)


@pytest.fixture
def backend_task(test_project) -> Task:
    return Task(
        object_id=TaskID("tsk_01999999999999999999999999"),
        organization_reference=test_project.organization_reference,
        user_reference=UserReference("usr_123"),
        project_id=test_project.object_id,
        input_data=AgentTaskInputsV1(
            agent_config=HelloAgentConfig(),
            image_config=LocalImageConfig(code_directory=Path("/tmp")),
            git_hash="initialhash",
            initial_branch="main",
            is_git_state_clean=False,
        ),
        parent_task_id=None,
    )


@pytest.fixture
def backend_environment(
    backend_services: ServiceCollectionForTask,
    test_project,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> Generator[LocalEnvironment, None, None]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        code_dir = Path(tmp_dir) / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        (code_dir / ".git").mkdir(parents=True, exist_ok=True)
        image_config = LocalImageConfig(code_directory=code_dir)
        environment_config = LocalEnvironmentConfig()

        image = backend_services.environment_service.ensure_image(
            image_config,
            test_project.object_id,
            {},
            code_dir,
            Path(tmp_dir),
            ImageMetadataV1.from_daily_cache(day=datetime.date.today()),
        )
        with backend_services.environment_service.generate_environment(
            image,
            test_project.object_id,
            test_root_concurrency_group,
            environment_config,
        ) as environment:
            yield environment


@pytest.fixture
def input_message_queue() -> Queue[Message]:
    return Queue()


@pytest.fixture
def running_task_thread(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    test_project,
    test_settings,
) -> Generator[ObservableThread, None, None]:
    task_state = AgentTaskStateV1()

    def run_task():
        return _run_agent_in_environment(
            task=backend_task,
            task_data=backend_task.input_data,
            task_state=task_state,
            input_message_queue=input_message_queue,
            environment=backend_environment,
            services=backend_services,
            project=test_project,
            settings=test_settings,
        )

    thread = ObservableThread(target=run_task)
    thread.start()

    try:
        yield thread
    finally:
        pass
        thread.join(timeout=10)


@pytest.mark.skip()
def test_checks_are_defined_and_executed(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """successful_check = "echo \\"Hello World\\""
failing_check = "echo \\"\\" && exit -1"
pytest_check = "pytest tests/"
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Hello!"))

    state, new = wait_for_message_type(ChecksDefinedRunnerMessage, state, backend_task.object_id, backend_services)

    checks_defined_message = None
    for msg in new:
        if isinstance(msg, ChecksDefinedRunnerMessage):
            checks_defined_message = msg
            break

    assert checks_defined_message is not None
    assert len(checks_defined_message.check_by_name) >= 3

    check_names = list(checks_defined_message.check_by_name.keys())
    assert "successful_check" in check_names
    assert "failing_check" in check_names
    assert "pytest_check" in check_names

    time.sleep(2)

    all_messages = get_all_messages_for_task(backend_task.object_id, backend_services)

    check_launched_messages = [msg for msg in all_messages if isinstance(msg, CheckLaunchedRunnerMessage)]
    assert len(check_launched_messages) >= 3

    check_finished_messages = [msg for msg in all_messages if isinstance(msg, CheckFinishedRunnerMessage)]
    assert len(check_finished_messages) >= 3

    successful_check_finished = None
    failing_check_finished = None
    pytest_check_finished = None

    for msg in check_finished_messages:
        if msg.check.name == "successful_check":
            successful_check_finished = msg
        elif msg.check.name == "failing_check":
            failing_check_finished = msg
        elif msg.check.name == "pytest_check":
            pytest_check_finished = msg

    assert successful_check_finished is not None
    assert successful_check_finished.finished_reason == CheckFinishedReason.FINISHED
    assert successful_check_finished.exit_code == 0

    assert failing_check_finished is not None
    assert failing_check_finished.finished_reason == CheckFinishedReason.FINISHED
    assert failing_check_finished.exit_code == 255

    assert pytest_check_finished is not None
    assert pytest_check_finished.finished_reason == CheckFinishedReason.FINISHED
    assert isinstance(pytest_check_finished.exit_code, int)


@pytest.mark.skip()
def test_suggestions_appear_when_checks_fail(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """failing_check = "echo \\"Test failed\\" && exit 1"
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Run the failing check"))

    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    suggestion_messages = [msg for msg in new if isinstance(msg, CheckOutputRunnerMessage)]
    assert len(suggestion_messages) > 0, "Expected suggestions to appear when check fails"

    suggestion = suggestion_messages[0]
    assert suggestion.output_entries[0].title == "Fix failing_check"
    assert suggestion.output_entries[0].description is not None


@pytest.mark.skip()
def test_checks_work_across_multiple_messages(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """test_check = "echo \\"Hello\\""
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="First message"))
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    first_message_check_finished = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "test_check":
            first_message_check_finished = msg
            break

    assert first_message_check_finished is not None
    assert first_message_check_finished.exit_code == 0

    send_message(input_message_queue, ChatInputUserMessage(text="Second message"))
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    second_message_check_finished = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "test_check":
            second_message_check_finished = msg
            break

    assert second_message_check_finished is not None
    assert second_message_check_finished.exit_code == 0
    assert second_message_check_finished.run_id != first_message_check_finished.run_id


@pytest.mark.skip()
def test_checks_can_be_rerun_manually(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """test_check = "echo \\"Hello\\""
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    initial_message = ChatInputUserMessage(text="Run the check")
    send_message(input_message_queue, initial_message)
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    first_run_id = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "test_check":
            first_run_id = msg.run_id
            break

    assert first_run_id is not None

    send_message(
        input_message_queue,
        RestartCheckUserMessage(user_message_id=initial_message.message_id, check_name="test_check"),
    )
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    second_run_id = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "test_check":
            second_run_id = msg.run_id
            break

    assert second_run_id is not None
    assert second_run_id != first_run_id


@pytest.mark.skip()
def test_check_status_updates_correctly(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """slow_check = "sleep 2 && echo \\"Done\\""
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Run slow check"))

    state, new = wait_for_message_type(CheckLaunchedRunnerMessage, state, backend_task.object_id, backend_services)

    launched_message = None
    for msg in new:
        if isinstance(msg, CheckLaunchedRunnerMessage) and msg.check.name == "slow_check":
            launched_message = msg
            break

    assert launched_message is not None
    assert launched_message.check.name == "slow_check"

    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    finished_message = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "slow_check":
            finished_message = msg
            break

    assert finished_message is not None
    assert finished_message.check.name == "slow_check"
    assert finished_message.run_id == launched_message.run_id
    assert finished_message.finished_reason == CheckFinishedReason.FINISHED
    assert finished_message.exit_code == 0


@pytest.mark.skip()
def test_suggestions_can_be_used(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """failing_check = "echo \\"Test failed\\" && exit 1"
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Run the failing check"))
    state, new = wait_for_message_type(CheckOutputRunnerMessage, state, backend_task.object_id, backend_services)

    suggestion_message = None
    for msg in new:
        if isinstance(msg, CheckOutputRunnerMessage):
            suggestion_message = msg
            break

    assert suggestion_message is not None
    assert len(suggestion_message.output_entries) > 0

    suggestion = suggestion_message.output_entries[0]
    assert suggestion.title == "Fix failing_check"
    assert suggestion.description is not None
    assert len(suggestion.actions) > 0


@pytest.mark.skip()
def test_checks_with_mixed_results(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """passing_check = "echo \\"Success\\""
failing_check = "echo \\"Failure\\" && exit 1"
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Run mixed checks"))
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    check_finished_messages = [msg for msg in new if isinstance(msg, CheckFinishedRunnerMessage)]
    assert len(check_finished_messages) >= 2

    passing_check = None
    failing_check = None
    for msg in check_finished_messages:
        if msg.check.name == "passing_check":
            passing_check = msg
        elif msg.check.name == "failing_check":
            failing_check = msg

    assert passing_check is not None
    assert passing_check.exit_code == 0
    assert failing_check is not None
    assert failing_check.exit_code == 1

    suggestion_messages = [msg for msg in new if isinstance(msg, CheckOutputRunnerMessage)]
    assert len(suggestion_messages) > 0, "Expected suggestions for failing check"


@pytest.mark.skip()
def test_check_interruption_on_new_message(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """slow_check = "sleep 5 && echo \\"Done\\""
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Start slow check"))
    state, new = wait_for_message_type(CheckLaunchedRunnerMessage, state, backend_task.object_id, backend_services)

    launched_message = None
    for msg in new:
        if isinstance(msg, CheckLaunchedRunnerMessage) and msg.check.name == "slow_check":
            launched_message = msg
            break

    assert launched_message is not None

    send_message(input_message_queue, ChatInputUserMessage(text="Interrupt with new message"))
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    interrupted_message = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "slow_check":
            interrupted_message = msg
            break

    assert interrupted_message is not None
    assert interrupted_message.finished_reason == CheckFinishedReason.INTERRUPTED
    assert interrupted_message.run_id == launched_message.run_id


@pytest.mark.skip()
def test_manual_check_triggering(
    backend_task: Task,
    backend_services: ServiceCollectionForTask,
    input_message_queue: Queue[Message],
    backend_environment: LocalEnvironment,
    running_task_thread: ObservableThread,
) -> None:
    def _write_check_config(environment: LocalEnvironment, content: str):
        environment.write_file(str(environment.get_workspace_path() / CHECK_CONFIG_PATH), content)

    check_config = """[manual_check]
command = "echo \\"Manual check executed\\""
trigger = "MANUAL"
"""

    _write_check_config(backend_environment, check_config)

    state = get_all_messages_for_task(backend_task.object_id, backend_services)

    send_message(input_message_queue, ChatInputUserMessage(text="Define manual check"))
    state, new = wait_for_message_type(ChecksDefinedRunnerMessage, state, backend_task.object_id, backend_services)

    checks_defined_message = None
    for msg in new:
        if isinstance(msg, ChecksDefinedRunnerMessage):
            checks_defined_message = msg
            break

    assert checks_defined_message is not None
    assert "manual_check" in checks_defined_message.check_by_name

    initial_message = ChatInputUserMessage(text="Trigger manual check")
    send_message(input_message_queue, initial_message)
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    send_message(
        input_message_queue,
        RestartCheckUserMessage(user_message_id=initial_message.message_id, check_name="manual_check"),
    )
    state, new = wait_for_message_type(CheckFinishedRunnerMessage, state, backend_task.object_id, backend_services)

    manual_check_finished = None
    for msg in new:
        if isinstance(msg, CheckFinishedRunnerMessage) and msg.check.name == "manual_check":
            manual_check_finished = msg
            break

    assert manual_check_finished is not None
    assert manual_check_finished.exit_code == 0


def get_all_messages_for_task(task_id: TaskID, services: ServiceCollectionForTask) -> list[Message]:
    all_messages: list[Message] = []
    with services.task_service.subscribe_to_task(task_id) as queue:
        while queue.qsize() > 0:
            all_messages.append(queue.get_nowait())
    if all_messages:
        all_messages.pop(0)
    return all_messages


def send_message(input_message_queue: Queue[Message], message: Message):
    input_message_queue.put_nowait(message)
