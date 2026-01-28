import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import queue
import subprocess
import time
import traceback
from asyncio import CancelledError
from importlib import resources
from importlib.abc import Traversable
from json import JSONDecodeError
from pathlib import Path
from threading import Event
from threading import Semaphore
from typing import Any
from typing import Generator
from typing import Iterator
from typing import Mapping
from typing import TypeVar
from urllib.parse import urlencode

import anyio
import fastapi
import httpx
import psutil
import sentry_sdk
import typeid.errors
from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocket
from fastapi.websockets import WebSocketDisconnect
from humanfriendly import parse_size
from loguru import logger
from pydantic import ValidationError

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TypeIDPrefixMismatchError
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import MutableEvent
from imbue_core.git import is_path_in_git_repo
from imbue_core.itertools import only
from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import model_dump
from imbue_core.pydantic_serialization import model_dump_json
from imbue_core.pydantic_utils import model_update
from imbue_core.s3_uploader import upload_to_s3
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import without_consent
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.sculptor.user_config import UserConfigField
from imbue_core.sculptor.user_config import calculate_user_config_prior_values
from imbue_core.secrets_utils import Secret
from imbue_core.serialization import SerializedException
from imbue_core.subprocess_utils import ProcessError
from imbue_core.subprocess_utils import ProcessSetupError
from sculptor import version
from sculptor.agents.data_types import SlashCommand
from sculptor.agents.default.claude_code_sdk.config_service_plugin import get_all_supported_slash_commands
from sculptor.config.settings import SculptorSettings
from sculptor.constants import ElementIDs
from sculptor.constants import SCULPTOR_EXIT_CODE_IRRECOVERABLE_ERROR
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import FixID
from sculptor.database.models import FixRequest
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import AgentMessageID
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import ClaudeCodeSDKAgentConfig
from sculptor.interfaces.agents.agent import CodexAgentConfig
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import EphemeralRequestCompleteAgentMessage
from sculptor.interfaces.agents.agent import ForkAgentSystemMessage
from sculptor.interfaces.agents.agent import GitCommitAndPushUserMessage
from sculptor.interfaces.agents.agent import InterruptProcessUserMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoAgentAttemptedMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoUserAttemptedMessage
from sculptor.interfaces.agents.agent import MessageFeedbackUserMessage
from sculptor.interfaces.agents.agent import PersistentMessageTypes
from sculptor.interfaces.agents.agent import PersistentRequestCompleteAgentMessage
from sculptor.interfaces.agents.agent import RemoveQueuedMessageUserMessage
from sculptor.interfaces.agents.agent import RequestCompleteAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.agents.artifacts import DiffArtifact
from sculptor.interfaces.agents.artifacts import LogsArtifact
from sculptor.interfaces.agents.artifacts import SuggestionsArtifact
from sculptor.interfaces.agents.artifacts import TodoListArtifact
from sculptor.interfaces.agents.artifacts import UsageArtifact
from sculptor.interfaces.agents.tasks import TaskState
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.primitives.constants import USER_FACING_LOG_TYPE
from sculptor.primitives.ids import create_organization_id
from sculptor.primitives.ids import create_user_id
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.anthropic_oauth import AnthropicAccountType
from sculptor.services.config_service.anthropic_oauth import cancel_anthropic_oauth as cancel_anthropic_oauth_impl
from sculptor.services.config_service.anthropic_oauth import start_anthropic_oauth as start_anthropic_oauth_impl
from sculptor.services.config_service.data_types import AWSBedrockApiKey
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import OpenAIApiKey
from sculptor.services.config_service.telemetry_info import get_onboarding_telemetry_info
from sculptor.services.config_service.telemetry_info import get_telemetry_info as get_telemetry_info_impl
from sculptor.services.config_service.user_config import get_user_config_instance
from sculptor.services.config_service.user_config import update_user_consent_level
from sculptor.services.data_model_service.data_types import TaskAndDataModelTransaction
from sculptor.services.environment_service.environments.image_tags import add_ancestral_tags_for_fork
from sculptor.services.environment_service.environments.image_tags import get_non_testing_environment_prefix
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_devcontainer_json_path_from_repo_or_default,
)
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_settings
from sculptor.services.environment_service.providers.docker.image_fetch import fetch_image_from_cdn
from sculptor.services.environment_service.providers.docker.image_fetch import get_image_purpose_from_url
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.git_repo_service.default_implementation import LocalReadOnlyGitRepo
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import RemoteReadOnlyGitRepo
from sculptor.services.git_repo_service.default_implementation import RemoteWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import get_global_git_config
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.ref_namespace_stasher import SculptorStashSingleton
from sculptor.services.git_repo_service.ref_namespace_stasher import delete_namespaced_stash_in_project
from sculptor.services.git_repo_service.ref_namespace_stasher import pop_namespaced_stash_into_source_branch
from sculptor.services.git_repo_service.ref_namespace_stasher import read_global_stash_singleton_if_present
from sculptor.services.local_sync_service.api import SyncToTaskResult
from sculptor.services.local_sync_service.errors import ExpectedSyncStartupError
from sculptor.services.local_sync_service.errors import OtherSyncTransitionInProgressError
from sculptor.services.project_service.default_implementation import get_most_recently_used_project_id
from sculptor.services.project_service.default_implementation import update_most_recently_used_project
from sculptor.services.task_service.errors import InvalidTaskOperation
from sculptor.services.task_service.errors import TaskNotFound
from sculptor.startup_checks import check_docker_installed
from sculptor.startup_checks import check_docker_running
from sculptor.startup_checks import check_git_installed
from sculptor.startup_checks import check_is_mutagen_installed
from sculptor.startup_checks import check_is_user_email_field_valid
from sculptor.startup_checks import is_valid_anthropiclike_api_key
from sculptor.tasks.api import DataModelTransaction
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.errors import is_irrecoverable_exception
from sculptor.utils.timeout import log_runtime
from sculptor.web.access_log_filter import should_suppress_access_log
from sculptor.web.auth import AUTHENTIK_SCOPE
from sculptor.web.auth import PKCE_STORE
from sculptor.web.auth import SESSION_TOKEN_HEADER_NAME
from sculptor.web.auth import SessionTokenMiddleware
from sculptor.web.auth import UserSession
from sculptor.web.auth import generate_pkce_verifier_challenge_and_state
from sculptor.web.auth import get_authorization_url
from sculptor.web.auth import get_logout_url
from sculptor.web.auth import get_redirect_url
from sculptor.web.auth import get_token_url
from sculptor.web.data_types import ArchiveTaskRequest
from sculptor.web.data_types import ArtifactDataResponse
from sculptor.web.data_types import ConfigStatusResponse
from sculptor.web.data_types import CreateInitialCommitRequest
from sculptor.web.data_types import CurrentBranchInfo
from sculptor.web.data_types import DefaultSystemPromptRequest
from sculptor.web.data_types import DeleteSyncStashRequest
from sculptor.web.data_types import DependenciesStatus
from sculptor.web.data_types import DiffArtifact
from sculptor.web.data_types import DisableLocalSyncResponse
from sculptor.web.data_types import DownloadDockerTarRequest
from sculptor.web.data_types import EmailConfigRequest
from sculptor.web.data_types import EnableLocalSyncRequest
from sculptor.web.data_types import FeedbackRequest
from sculptor.web.data_types import FixTaskRequest
from sculptor.web.data_types import ForkTaskRequest
from sculptor.web.data_types import GitCommitAndPushRequest
from sculptor.web.data_types import HealthCheckResponse
from sculptor.web.data_types import InitializeGitRepoRequest
from sculptor.web.data_types import MergeActionNotice
from sculptor.web.data_types import MergeActionNoticeKind
from sculptor.web.data_types import MessageRequest
from sculptor.web.data_types import PrivacyConfigRequest
from sculptor.web.data_types import ProjectInitializationRequest
from sculptor.web.data_types import ProviderStatusInfo
from sculptor.web.data_types import ReadFileRequest
from sculptor.web.data_types import RepoInfo
from sculptor.web.data_types import RestoreSyncStashRequest
from sculptor.web.data_types import SendMessageRequest
from sculptor.web.data_types import StartTaskRequest
from sculptor.web.data_types import SystemDependencyInfo
from sculptor.web.data_types import TransferFromLocalToTaskRequest
from sculptor.web.data_types import TransferFromLocalToTaskResponse
from sculptor.web.data_types import TransferFromTaskToLocalRequest
from sculptor.web.data_types import TransferFromTaskToLocalResponse
from sculptor.web.data_types import TransferRepoDecision
from sculptor.web.data_types import TransferRepoDecisionOption
from sculptor.web.data_types import UpdateUserConfigRequest
from sculptor.web.data_types import UserInfo
from sculptor.web.derived import CodingAgentTaskView
from sculptor.web.derived import GlobalLocalSyncInfo
from sculptor.web.derived import LocalRepoInfo
from sculptor.web.derived import LocalSyncState
from sculptor.web.derived import LocalSyncStatus
from sculptor.web.derived import SyncedTaskView
from sculptor.web.derived import TaskInterface
from sculptor.web.derived import TaskViewTypes
from sculptor.web.derived import create_initial_task_view
from sculptor.web.gateway import router as gateway_router
from sculptor.web.merge_actions import merge_into_agent
from sculptor.web.middleware import App
from sculptor.web.middleware import DecoratedAPIRouter
from sculptor.web.middleware import add_logging_context
from sculptor.web.middleware import get_root_concurrency_group
from sculptor.web.middleware import get_services_from_request_or_websocket
from sculptor.web.middleware import get_settings
from sculptor.web.middleware import get_user_session
from sculptor.web.middleware import get_user_session_for_websocket
from sculptor.web.middleware import lifespan
from sculptor.web.middleware import register_on_startup
from sculptor.web.middleware import run_sync_function_with_debugging_support_if_enabled
from sculptor.web.middleware import shutdown_event as shutdown_event_impl
from sculptor.web.repo_polling_manager import read_local_repo_info
from sculptor.web.streams import ServerStopped
from sculptor.web.streams import StreamingUpdate
from sculptor.web.streams import create_initial_task_view
from sculptor.web.streams import stream_everything

UpdateT = TypeVar("UpdateT", bound=StreamingUpdate)


def validate_project_id(project_id: str) -> ProjectID:
    """Validate and return a ProjectID, raising HTTPException if invalid."""
    try:
        return ProjectID(project_id)
    except (typeid.errors.TypeIDException, TypeIDPrefixMismatchError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid project ID format: {project_id}") from e


def validate_task_id(task_id: str) -> TaskID:
    """Validate and return a TaskID, raising HTTPException if invalid."""
    try:
        return TaskID(task_id)
    except (typeid.errors.TypeIDException, TypeIDPrefixMismatchError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid task ID format: {task_id}") from e


for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

PLEASE_POST_IN_DISCORD = "please post in https://discord.com/channels/1391837726583820409/1393200867657781278"


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Check for shutdown message
        if "Shutting down" in record.getMessage():
            print("\nAttempting shutdown and cleaning up. Please wait this can take a moment ...")

        if record.exc_info and record.exc_info[0] is KeyboardInterrupt:
            logger.debug("Keyboard interrupt received")
            return

        if "BrokenPipeError: [Errno 32] Broken pipe" in record.getMessage():
            level = "WARNING"

        # Suppress access logs for frequently polled routes to reduce log noise
        if record.name == "uvicorn.access":
            if should_suppress_access_log(record.getMessage()):
                return

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# Replace handlers for specific loggers
logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)

loggers = (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "fastapi",
    "asyncio",
    "starlette",
)

for logger_name in loggers:
    logging_logger = logging.getLogger(logger_name)
    logging_logger.handlers = []
    logging_logger.propagate = True


APP = App(title="Sculptor V1 API", lifespan=lifespan)

NUM_WORKER_THREADS = 40


def on_startup():
    # Based on https://github.com/Kludex/starlette/issues/1724#issuecomment-1717476987
    # Which I found from https://github.com/fastapi/fastapi/discussions/4593
    # Also looked at https://github.com/fastapi/fastapi/issues/4221 but that appears to be out of date; Does not work.

    # This is where+how we can set the number of worker threads in the app's underlying pool.
    # I found this to verify the number of workers we actually have (defaults to 40)
    # and ensure that we're not going to run out if we do long-running requests like download_docker_tar_to_cache
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = NUM_WORKER_THREADS


register_on_startup(on_startup)


## Cors section. This should be the only place the backend process cares about SCULPTOR_FRONTEND_PORT
frontend_port = os.environ.get("SCULPTOR_FRONTEND_PORT", 5173)
frontend_host = os.environ.get("SCULPTOR_FRONTEND_HOST", None)
api_port = os.environ.get("SCULPTOR_API_PORT", 5050)

is_integration_testing = os.environ.get("TESTING__INTEGRATION_ENABLED", "false").lower() == "true"


# Add CORS middleware to allow requests from file:// origins and localhost
APP.add_middleware(
    # pyre doesn't understand the typing here
    CORSMiddleware,  # pyre-ignore[6]
    allow_origins=[
        f"http://localhost:{frontend_port}",  # Vite dev server
        f"http://127.0.0.1:{frontend_port}",  # Vite dev server
        f"http://localhost:{api_port}",  # Direct web backend access, this usually doesnt need cors
        f"http://127.0.0.1:{api_port}",  # Direct web backend access, this usually doesnt need cors
        *([f"http://{frontend_host}:{frontend_port}"] if frontend_host is not None else []),
        "null",  # file:// URLs report origin as "null"
    ],
    # If we are running for an integration test, we need to allow any port so that our clients can port-hop.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if is_integration_testing else None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


# FIXME: decide whether we need this middleware or not,
#  and whether we need the below exception handler to properly log exceptions to sentry
# APP.user_middleware = [middleware for middleware in APP.user_middleware if middleware.cls != ServerErrorMiddleware]


@APP.exception_handler(Exception)
async def irrecoverable_exception_handler(request: Request, exception: Exception) -> None:
    if is_irrecoverable_exception(exception):
        logger.opt(exception=exception).info(
            "Irrecoverable exception encountered. Terminating the program immediately."
        )
        telemetry.send_exception_to_posthog(
            SculptorPosthogEvent.IRRECOVERABLE_EXCEPTION, exception, include_traceback=True
        )
        telemetry.flush_sentry_and_exit_program(
            SCULPTOR_EXIT_CODE_IRRECOVERABLE_ERROR, "Irrecoverable exception encountered (see logs for details)."
        )
    raise exception


# Add GZip middleware for compression
# pyre-ignore[6]:
# The signature for middleware classes defined by Starlette (_MiddlewareFactory.__call__) is wrong.
APP.add_middleware(GZipMiddleware, minimum_size=1000)

router = DecoratedAPIRouter(decorator=add_logging_context)


@router.get("/api/v1/session-token", status_code=204)
def set_session_token_cookie(
    response: Response,
    settings: SculptorSettings = Depends(get_settings),
) -> None:
    response.set_cookie(
        key=SESSION_TOKEN_HEADER_NAME,
        value=settings.SESSION_TOKEN or "",
        samesite="strict",
        httponly=True,
    )


# TODO: NOTE: some unit tests rely on this even though it is not used in the app
@router.get("/api/v1/projects/{project_id}/tasks")
def get_tasks(
    project_id: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> tuple[TaskViewTypes, ...]:
    """Get list of all tasks"""
    validated_project_id = validate_project_id(project_id)
    logger.info("Getting all tasks")
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        # FIXME:
        #    The typing here is broken and accessing this return type directly is expressly forbidden by the return type's docstring
        #   > This should ONLY be used to expose the SQL data to the task service, and to the tasks themselves.
        #
        # TODO: only TaskAndDataModelTransaction has get_tasks_for_project, not DataModelTransaction
        tasks = transaction.get_tasks_for_project(validated_project_id, is_archived=False)  # pyre-fixme[16]

    task_views: list[TaskViewTypes] = []
    for task in tasks:
        if not isinstance(task.input_data, AgentTaskInputsV1):
            continue
        task_view = create_initial_task_view(task, services.settings)
        task_view.update_task(task)
        task_views.append(task_view)

    logger.debug("Returning {} tasks", len(task_views))
    return tuple(task_views)


class TaskStartRequestedPayload(PosthogEventPayload):
    object_type: str = without_consent(default="TaskStartRequestedPayload")

    source_branch: str = telemetry.with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    is_including_uncommitted_changes: bool = telemetry.with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    model: LLMModel = telemetry.with_consent(ConsentLevel.PRODUCT_ANALYTICS)


@router.post("/api/v1/projects/{project_id}/tasks")
def start_task(
    project_id: ProjectID,
    request: Request,
    task_request: StartTaskRequest,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> CodingAgentTaskView:
    """Start a new task with the given prompt"""
    prompt = task_request.prompt
    interface = task_request.interface
    source_branch = task_request.source_branch
    model = task_request.model
    task_id = TaskID()

    with logger.contextualize(task_id=task_id), log_runtime("start_task") as timing_attributes_for_posthog:
        if not prompt:
            logger.error("Start task request without prompt")
            raise HTTPException(status_code=422, detail="Prompt is required")

        services = get_services_from_request_or_websocket(request)
        _prevent_action_if_out_of_free_space(services)

        try:
            interface = TaskInterface(interface)
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid interface: {interface}. Must be 'terminal' or 'api'",
            ) from e

        logger.info("Starting new task with interface {}", interface)

        if (
            model == LLMModel.GPT_5_1
            or model == LLMModel.GPT_5_1_CODEX
            or model == LLMModel.GPT_5_1_CODEX_MINI
            or model == LLMModel.GPT_5_2
        ):
            agent_config = CodexAgentConfig()
        else:
            agent_config = ClaudeCodeSDKAgentConfig()

        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.TASK_START_REQUESTED,
                component=ProductComponent.TASK,
                task_id=str(task_id),
                payload=TaskStartRequestedPayload(
                    # the code below treats None and empty string the same way, but None is not distinguishable from field filtered by telemetry
                    source_branch=source_branch or "",
                    is_including_uncommitted_changes=task_request.is_including_uncommitted_changes,
                    model=model,
                ),
            )
        )

        is_git_state_clean = not task_request.is_including_uncommitted_changes

        # little transaction here -- we don't want to span the whole thing bc then it will be slow
        with user_session.open_transaction(services) as transaction:
            project = transaction.get_project(project_id)
            assert project is not None, f"Project {project_id} not found"

        with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
            timing_attributes_for_posthog.set_attribute(
                "is_including_uncommitted_changes", task_request.is_including_uncommitted_changes
            )
            # if we are including uncommitted changes, we must shuffle them off to the side before we do anything else
            # otherwise, there is a race condition where the user may change their repo before this task starts
            # and thus the task would have unexpected changes (from the perspective of the user)
            if task_request.is_including_uncommitted_changes:
                uncommitted_changes_path = get_sculptor_folder() / "user_repo_copies" / str(task_id)
                logger.debug("Exporting current repo state to {}", uncommitted_changes_path)
                uncommitted_changes_path.mkdir(parents=True, exist_ok=True)
                repo.export_current_repo_state(uncommitted_changes_path)
                logger.debug("Done exporting current repo state to {}", uncommitted_changes_path)

            # if no source branch is provided, use the current branch
            if source_branch is None or source_branch == "" or " " in source_branch:
                # for now, just log this -- we shouldn't ever get here, so let's find out on sentry
                logger.error("Empty source branch, this is unexpected: {}", source_branch)
                # figure out the source from the repo's current branch, mark this as using unclean git state
                try:
                    source_branch = repo.get_current_git_branch()
                except GitRepoError:
                    source_branch = ""
            # then figure out the current commit
            if is_git_state_clean:
                initial_commit_hash = repo.get_branch_head_commit_hash(source_branch)
            else:
                initial_commit_hash = repo.get_current_commit_hash()
            repo_path = repo.get_repo_path()
        # TODO: This feels duplicated with some stuff in DockerProvider.create_image.
        devcontainer_json_path: Path = get_devcontainer_json_path_from_repo_or_default(repo_path)
        image_config = LocalDevcontainerImageConfig(
            devcontainer_json_path=str(devcontainer_json_path),
        )

        environment_config = LocalDockerEnvironmentConfig()

        # TODO: post-v1 transition, we should probably make this configurable. Will be nice for testing to ensure that things don't run forever
        max_seconds = None

        task = Task(
            object_id=task_id,
            max_seconds=max_seconds,
            organization_reference=user_session.organization_reference,
            user_reference=user_session.user_reference,
            parent_task_id=None,
            project_id=project.object_id,
            input_data=AgentTaskInputsV1(
                agent_config=agent_config,
                image_config=image_config,
                environment_config=environment_config,
                git_hash=initial_commit_hash,
                initial_branch=source_branch,
                is_git_state_clean=is_git_state_clean,
                system_prompt=project.default_system_prompt,
            ),
        )

        logger.debug("Creating root concurrency group and opening transaction.")
        root_concurrency_group = get_root_concurrency_group(request)
        with (
            root_concurrency_group.make_concurrency_group(name="start_task") as concurrency_group,
            user_session.open_transaction(services) as transaction,
        ):
            logger.debug("Creating task and inserting it into the database.")
            inserted_task = services.task_service.create_task(task, transaction)
            task_id = inserted_task.object_id

            logger.debug("Creating initial messages...")
            messages = []
            input_user_message = ChatInputUserMessage(
                text=prompt,
                message_id=AgentMessageID(),
                model_name=model,
                files=task_request.files,
            )
            messages.append(input_user_message)
            services.task_service.create_message(
                message=input_user_message,
                task_id=task_id,
                transaction=transaction,
            )

            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=SculptorPosthogEvent.TASK_START_MESSAGE,
                    component=ProductComponent.TASK,
                    payload=input_user_message,
                    task_id=str(task_id),
                )
            )

        logger.debug("Creating initial task view.")
        task_view = create_initial_task_view(task, settings)
        assert isinstance(task_view, CodingAgentTaskView)
        logger.debug("Adding messages to task view.")
        for message in messages:
            task_view.add_message(message)
        logger.debug("Done adding messages to task view.")
        return task_view


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/fix")
def add_fix(
    project_id: ProjectID,
    task_id: TaskID,
    request: Request,
    fix_request: FixTaskRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services):
        received_description = fix_request.description

        fix_info = FixRequest(
            description=received_description,
            project_id=project_id,
            task_id=task_id,
            object_id=FixID(),
        )

        posthog_user = telemetry.get_user_posthog_instance()

        if posthog_user and telemetry.is_consent_allowable(
            ConsentLevel.LLM_LOGS, services.config_service.get_user_config().privacy_settings
        ):
            event = telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.FIX_ISSUE_SELECT,
                component=ProductComponent.FIX,
                action=telemetry.UserAction.CLICKED,
                payload=fix_info,
            )
            telemetry.emit_posthog_event(event)


class ForkTaskResponse(SerializableModel):
    id: TaskID


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/fork")
def fork_task(
    project_id: ProjectID,
    task_id: TaskID,
    request: Request,
    fork_request: ForkTaskRequest,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> CodingAgentTaskView:
    prompt = fork_request.prompt
    model = fork_request.model

    services = get_services_from_request_or_websocket(request)
    _prevent_action_if_out_of_free_space(services)
    new_task_id = TaskID()

    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.TASK_FORK_REQUESTED,
            component=ProductComponent.TASK,
            task_id=str(new_task_id),
        )
    )

    logger.info("Forking task {}", fork_request)
    with user_session.open_transaction(services) as transaction:
        task = services.task_service.get_task(task_id, transaction)
        assert task is not None, f"Task {task_id} not found"
        assert not task.is_deleted, "Cannot fork a deleted task"
        input_data = task.input_data
        assert isinstance(input_data, AgentTaskInputsV1), "Can only fork agents"
        current_state = task.current_state
        assert isinstance(current_state, AgentTaskStateV1)

        # Get the project for forking environment config
        project = transaction.get_project(task.project_id)
        assert project is not None, "Project must exist"

        # reset the title and branch name
        mutable_task_state = evolver(current_state)
        assign(mutable_task_state.title, lambda: None)
        assign(mutable_task_state.branch_name, lambda: None)

        # Find the last snapshot message before the next user message after the fork point.
        # This ensures we capture subsequent state changes, like from local syncing.
        found_fork_point = False
        snapshot_message = None
        previous_user_chat_message = None

        for existing_message in services.task_service.get_saved_messages_for_task(task_id, transaction):
            if existing_message.message_id == fork_request.chat_message_id:
                found_fork_point = True
            elif found_fork_point:
                # After finding the fork point, look for the snapshot for the last user message
                if (
                    isinstance(existing_message, AgentSnapshotRunnerMessage)
                    and previous_user_chat_message
                    and existing_message.for_user_message_id == previous_user_chat_message.message_id
                ):
                    snapshot_message = existing_message
                elif isinstance(existing_message, ChatInputUserMessage):
                    # Stop at the next user message -- we have the last snapshot before it
                    break

            if isinstance(existing_message, ChatInputUserMessage):
                previous_user_chat_message = existing_message
        if snapshot_message is None or snapshot_message.image is None:
            raise HTTPException(status_code=400, detail="No snapshot message found to fork from")

        snapshot_image = _fork_image(snapshot_message.image)
        with get_root_concurrency_group(request).make_concurrency_group(name="snapshot") as concurrency_group:
            add_ancestral_tags_for_fork(task_id, new_task_id, snapshot_image.image_id, concurrency_group, settings)

        assign(mutable_task_state.image, lambda: snapshot_image)
        assign(mutable_task_state.branch_name, lambda: None)
        assign(mutable_task_state.title, lambda: None)
        assign(mutable_task_state.last_processed_message_id, lambda: snapshot_message.for_user_message_id)
        assign(mutable_task_state.environment_id, lambda: None)
        updated_task_state = chill(mutable_task_state)

        new_task = Task(
            object_id=new_task_id,
            max_seconds=task.max_seconds,
            organization_reference=task.organization_reference,
            user_reference=task.user_reference,
            parent_task_id=task.object_id,
            project_id=task.project_id,
            input_data=input_data,
            current_state=updated_task_state,
            outcome=TaskState.QUEUED,
        )
        inserted_task = services.task_service.create_task(new_task, transaction)

        # copy all messages from the original task to the new task up to and including the snapshot message
        messages = []
        for existing_message in services.task_service.get_saved_messages_for_task(task_id, transaction):
            messages.append(existing_message)
            services.task_service.create_message(
                task_id=inserted_task.object_id, message=existing_message, transaction=transaction
            )
            # although all elements of `PersistentMessageTypes` are `Message`s, pyre doesn't play nice with pydantic, so we do the assert to make it understand message's attributes
            assert isinstance(existing_message, Message)
            if existing_message.message_id == snapshot_message.message_id:
                break

        # finally make a note that, in fact, we forked this task, in both tasks
        fork_message = ForkAgentSystemMessage(
            parent_task_id=task.object_id,
            child_task_id=inserted_task.object_id,
            fork_point_chat_message_id=fork_request.chat_message_id,
        )

        services.task_service.create_message(task_id=task.object_id, message=fork_message, transaction=transaction)
        services.task_service.create_message(
            task_id=inserted_task.object_id,
            message=fork_message,
            transaction=transaction,
        )
        messages.append(fork_message)

        # Log fork event to PostHog
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.USER_FORK_AGENT,
                component=ProductComponent.TASK,
                payload=fork_message,
                task_id=str(task_id),
            )
        )

        # send the first post-fork message to the agent
        input_user_message = ChatInputUserMessage(
            text=prompt,
            message_id=AgentMessageID(),
            model_name=model,
            files=fork_request.files,
        )

        messages.append(input_user_message)
        services.task_service.create_message(
            task_id=inserted_task.object_id, message=input_user_message, transaction=transaction
        )

        with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=new_task.object_id):
            logger.info("Forked task {} from {}", inserted_task.object_id, task_id)

        # Create and return the task view
        task_view = create_initial_task_view(inserted_task, settings)
        # this is guaranteed because we can only fork agent tasks, but the typing of create_initial_task_view
        # doesn't demonstrate that (yet)
        assert isinstance(task_view, CodingAgentTaskView), "Forked task must result in a CodingAgentTaskView"
        for message in messages:
            task_view.add_message(message)
        return task_view


def _fork_image(image: ImageTypes) -> ImageTypes:
    match image:
        case LocalDockerImage() as docker_image:
            # Just return the same image
            return docker_image
        case _:
            raise NotImplementedError()


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/restore")
def restore_task(
    project_id: str,
    task_id: TaskID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> None:
    validate_project_id(project_id)  # Validate project_id but don't need the result
    logger.info("Restoring task {}", task_id)
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        try:
            services.task_service.restore_task(task_id, transaction)
        except TaskNotFound as e:
            raise HTTPException(status_code=404, detail="Task not found") from e
        except InvalidTaskOperation as e:
            raise HTTPException(status_code=400, detail="Task is not in a failed state - cannot restore") from e
        with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
            logger.info("Restored task {}", task_id)


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/read-file")
def get_file(
    project_id: str,
    task_id: str,
    request: Request,
    read_file_request: ReadFileRequest,
    user_session: UserSession = Depends(get_user_session),
) -> str | bytes:
    """Read a file from the task's local repository"""
    validate_project_id(project_id)  # Validate project_id but don't need the result
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        logger.info("Reading file for task {}: {}", task_id, read_file_request.file_path)
        try:
            validated_task_id = TaskID(task_id)
        except typeid.errors.SuffixValidationException as e:
            raise HTTPException(status_code=422, detail="Invalid task ID format") from e
        task = services.task_service.get_task(validated_task_id, transaction)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        assert isinstance(task.current_state, AgentTaskStateV1)

        if not task.current_state.task_repo_path:
            raise HTTPException(status_code=400, detail="Task repo path not found")

        file_path = read_file_request.file_path
        file_path = task.current_state.task_repo_path / file_path

        environment = services.task_service.get_task_environment(TaskID(task_id), transaction)
        assert environment is not None
        if not environment.exists(str(file_path)):
            logger.error("File not found: {}", file_path)
            raise HTTPException(status_code=404, detail="File not found")

        try:
            return environment.read_file(str(file_path))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file {file_path}") from e


@router.get("/api/v1/projects/{project_id}/tasks/{task_id}/exist")
def get_task_existence(
    project_id: ProjectID,
    task_id: TaskID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> bool:
    """Get if the task exists and it corresponds to the given project"""
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        task = services.task_service.get_task(task_id, transaction)
        if not task:
            return False
        if task.project_id != project_id:
            return False
    return True


def _cleanup_task_file_attachments(
    task_id: TaskID,
    services: CompleteServiceCollection,
    transaction,
) -> None:
    """Clean up files associated with a task.

    Collects all file paths from ChatInputUserMessage messages and deletes them from disk.
    """
    messages = services.task_service.get_saved_messages_for_task(task_id, transaction)
    file_paths: set[str] = set()

    for message in messages:
        if isinstance(message, ChatInputUserMessage) and message.files:
            file_paths.update(message.files)

    if not file_paths:
        return

    for file_path in file_paths:
        try:
            file_file = Path(file_path)
            if file_file.exists():
                file_file.unlink()
                logger.debug("Deleted file: {}", file_path)
        except Exception as e:
            log_exception(e, "Failed to delete {file_path}", file_path=file_path)
    logger.info("Cleaned up {} file(s) for task {}", len(file_paths), task_id)


@router.delete("/api/v1/projects/{project_id}/tasks/{task_id}")
def delete_task(
    project_id: str,
    task_id: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Delete a task by ID"""
    validate_project_id(project_id)  # Validate project_id but don't need the result

    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        logger.info("Deleting task {}", task_id)
        try:
            validated_task_id = TaskID(task_id)
        except typeid.errors.SuffixValidationException as e:
            raise HTTPException(status_code=422, detail="Invalid task ID format") from e

        if services.local_sync_service.is_task_synced(validated_task_id):
            logger.debug("local_sync: unsyncing synced task {} so we can delete it", task_id)
            services.local_sync_service.unsync_from_task(validated_task_id, transaction=transaction)

        _cleanup_task_file_attachments(validated_task_id, services, transaction)

        try:
            services.task_service.delete_task(validated_task_id, transaction)
        except TaskNotFound as e:
            raise HTTPException(status_code=404, detail="Task not found") from e


# TODO: convert everything to explicitly pass a message request
@contextlib.contextmanager
def await_message_response(
    message_id: AgentMessageID,
    task_id: TaskID,
    services: CompleteServiceCollection,
    message_request: MessageRequest | None = None,
    response_container: list[RequestCompleteAgentMessage] | None = None,
) -> Iterator[None]:
    if message_request is not None and not message_request.is_awaited:
        yield
        return
    start_time = time.monotonic()
    with services.task_service.subscribe_to_task(task_id) as updates_queue:
        yield
        logger.debug("Waiting for response to message {} in task {}", message_id, task_id)
        while True:
            if message_request is not None:
                timeout_seconds = message_request.timeout_seconds
                if timeout_seconds is not None and time.monotonic() - start_time > timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for response to message {message_id} in task {task_id}")
            try:
                update = updates_queue.get(timeout=1.0)
            except queue.Empty:
                pass
            else:
                # these are the two possible types of response message
                if isinstance(update, (PersistentRequestCompleteAgentMessage, EphemeralRequestCompleteAgentMessage)):
                    if update.request_id == message_id:
                        if response_container is not None:
                            response_container.append(update)
                        break


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/messages")
def send_message(
    project_id: str,
    task_id: str,
    request: Request,
    message_request: SendMessageRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Send a message to the agent via API interface"""

    services = get_services_from_request_or_websocket(request)
    _prevent_action_if_out_of_free_space(services)

    validate_project_id(project_id)  # Validate project_id but don't need the result
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e
    message_id = AgentMessageID()
    with user_session.open_transaction(services) as transaction:
        task = services.task_service.get_task(validated_task_id, transaction)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        message_str = message_request.message
        if not message_str:
            raise HTTPException(status_code=422, detail="Message required")

        logger.info("Sending message {} to task {}: {}", message_id, validated_task_id, message_str[:100])

        message = ChatInputUserMessage(
            message_id=message_id,
            text=message_str,
            model_name=message_request.model,
            files=message_request.files,
        )
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.TASK_USER_MESSAGE,
                component=ProductComponent.TASK,
                payload=message,
                task_id=str(validated_task_id),
            )
        )

        services.task_service.create_message(
            message=message,
            task_id=validated_task_id,
            transaction=transaction,
        )


def _prevent_action_if_out_of_free_space(services: CompleteServiceCollection) -> None:
    user_config = services.config_service.get_user_config()
    free_gb = (_get_disk_bytes_free(services.settings) or 1_000_000_000_000) / (1024 * 1024 * 1024)
    if user_config is not None and free_gb < user_config.min_free_disk_gb:
        logger.warning("Cannot start a task if you have insufficient free space")
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient disk space ({user_config.min_free_disk_gb} GB free space required to prevent filling your disk)\nPlease either free some space (eg, by deleting old tasks) or increase min_free_disk_gb in settings.",
        )


# FIXME: it'd be nice to consolidate everything to this... there's really no need for all of these other routes :-P
@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/message")
def send_message_generic(
    project_id: str,
    task_id: str,
    request: Request,
    message_request: MessageRequest,
    user_session: UserSession = Depends(get_user_session),
) -> SerializedException | None:
    """Generically handles sending any message"""
    logger.info("Sending {} to task {} in project {}", type(message_request.message), task_id, project_id)
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException:
        raise HTTPException(status_code=422, detail="Invalid task ID format")
    message_id = message_request.message.message_id
    response_container: list[RequestCompleteAgentMessage] = []
    services = get_services_from_request_or_websocket(request)
    with await_message_response(message_id, validated_task_id, services, message_request, response_container):
        with user_session.open_transaction(services) as transaction:
            task = services.task_service.get_task(validated_task_id, transaction)
            if not task:
                logger.error("Task {} not found", task_id)
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            message = message_request.message
            if isinstance(message, MessageFeedbackUserMessage):
                logger.info(
                    "Received feedback for task {}: type={} comment='{}' issue_type='{}'",
                    validated_task_id,
                    message.feedback_type,
                    message.comment or "",
                    message.issue_type or "",
                )

                all_messages = services.task_service.get_saved_messages_for_task(validated_task_id, transaction)
                logger.trace("Extracted {} messages for task {}", len(all_messages), validated_task_id)

                s3_bytes = json.dumps(
                    FeedbackSavedAgentMessagesPayload(
                        task_id=validated_task_id, messages=list(all_messages)
                    ).model_dump_json()
                ).encode("utf-8")

                s3_upload_url = upload_to_s3(SculptorPosthogEvent.TASK_USER_FEEDBACK.value, ".json", s3_bytes)

                # Create a Posthog event with the feedback
                posthog_event = telemetry.PosthogEventModel(
                    name=SculptorPosthogEvent.TASK_USER_FEEDBACK,
                    component=ProductComponent.TASK,
                    payload=FeedbackRequestPayload(
                        feedback_type=message.feedback_type,
                        message_id=str(message.feedback_message_id),
                        comment=message.comment or "",
                        issue_type=message.issue_type or "",
                        saved_agent_messages_s3_path=s3_upload_url,
                    ),
                    task_id=str(validated_task_id),
                )
                telemetry.emit_posthog_event(posthog_event)

            services.task_service.create_message(
                message=message,
                task_id=validated_task_id,
                transaction=transaction,
            )
    if message_request.is_awaited:
        response = only(response_container)
        return response.error
    else:
        return None


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/compact")
def compact_task(
    project_id: str,
    task_id: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Compacts task context"""
    logger.info("Compacting task {} in project {}", task_id, project_id)
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e
    message_id = AgentMessageID()
    services = get_services_from_request_or_websocket(request)
    with await_message_response(message_id, validated_task_id, services):
        with user_session.open_transaction(services) as transaction:
            task = services.task_service.get_task(validated_task_id, transaction)
            if not task:
                logger.error("Task {} not found", task_id)
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            services.task_service.create_message(
                message=CompactTaskUserMessage(message_id=message_id),
                task_id=validated_task_id,
                transaction=transaction,
            )
    return None


@router.get("/api/v1/telemetry_info")
def get_telemetry_info(user_session: UserSession = Depends(get_user_session)) -> telemetry.TelemetryInfo:
    """Returns telemetry info for the current user.

    If the current user has not initialized their configuration, use an
    anonymous config.
    """
    return get_logged_in_or_anonymous_telemetry_info()


def get_logged_in_or_anonymous_telemetry_info() -> telemetry.TelemetryInfo:
    """Returns telemetry info for the current user.

    If the current user has not initialized their configuration, use an
    anonymous config.
    """
    logged_in_info = get_telemetry_info_impl()
    if not logged_in_info:
        return get_onboarding_telemetry_info()
    return logged_in_info


@router.get("/api/v1/provider_statuses")
def get_provider_statuses(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> tuple[ProviderStatusInfo, ...]:
    """Get the current status of all environment providers"""
    services = get_services_from_request_or_websocket(request)
    provider_statuses = services.environment_service.get_provider_statuses()

    status_list = []
    for provider_tag, status in provider_statuses.items():
        status_info = ProviderStatusInfo(
            provider=provider_tag,
            status=status,
        )
        status_list.append(status_info)

    return tuple(status_list)


# ====================
# Onboarding routes and Helpers
# ====================


def ensure_posthog_user_identified() -> telemetry.TelemetryInfo:
    """Helper to ensure that the current posthog user is identified, and returns the NEW
    Telemetry info.

    This function encapsulates the logic so that it may be adjusted to be called
    at different points of our signup flow as it changes.

    This function WILL NOT LOG any events to PostHog. You need to know what to
    log yourself.
    """
    # Remember that the following may be "signed_in" or "anonymous"
    original_telemetry_info = get_logged_in_or_anonymous_telemetry_info()

    logger.info("Ensuring identification for user {}", original_telemetry_info)

    if not telemetry.is_posthog_identified():
        logger.info("Identification needs to be submitted")
        telemetry.identify_posthog_user(user_config_accessor=get_user_config_instance)
        # Re-get it because it may have changed
        logger.info("We just identified {user}", user=get_logged_in_or_anonymous_telemetry_info())
        return get_logged_in_or_anonymous_telemetry_info()

    logger.info("Identification was unchanged")
    return original_telemetry_info


@router.get("/api/v1/config/status")
def get_config_status(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> ConfigStatusResponse:
    """Check if user config exists and what fields are configured"""
    services = get_services_from_request_or_websocket(request)
    user_config = services.config_service.get_user_config()

    if not user_config:
        return ConfigStatusResponse(
            has_email=False,
            has_api_key=False,
            has_privacy_consent=False,
            has_telemetry_level=False,
        )

    return ConfigStatusResponse(
        has_email=bool(user_config.user_email) and check_is_user_email_field_valid(user_config),
        has_api_key=services.config_service.get_credentials().anthropic is not None,
        has_privacy_consent=user_config.is_privacy_policy_consented,
        has_telemetry_level=user_config.is_telemetry_level_set,
    )


@router.post("/api/v1/config/email")
def save_user_email(
    request: Request,
    email_config_request: EmailConfigRequest,
    user_session: UserSession = Depends(get_user_session),
) -> telemetry.TelemetryInfo:
    """Save user email during onboarding

    This function will determine the updated TelemetryInfo for the signed in user, and return that to the frontend.
    """
    # Get or create user config (since this is the first step)
    root_concurrency_group = get_root_concurrency_group(request)
    services = get_services_from_request_or_websocket(request)
    user_config = services.config_service.get_user_config()

    # Try to get git username from system
    with root_concurrency_group.make_concurrency_group(name="save_user_email") as concurrency_group:
        git_username = get_global_git_config("user.name", concurrency_group)
        if git_username is None:
            # Fall back to email prefix
            git_username = str(email_config_request.user_email).split("@")[0]

    user_config = model_update(
        user_config,
        {
            "user_email": email_config_request.user_email,
            "user_git_username": git_username,
            "user_id": create_user_id(str(email_config_request.user_email)),
            "user_full_name": email_config_request.full_name,
            "organization_id": create_organization_id(str(email_config_request.user_email)),
            # Saving user email counts as consenting to the Policy email
            "is_privacy_policy_consented": True,
        },
    )

    logger.info("Saved your name {}", email_config_request.full_name)
    services.config_service.set_user_config(user_config)

    # This next few lines look superficially similar to fire_posthog_event. However, this is
    # different because we absolutely MUST ensure_posthog_user_identified here,
    # whereas in fire_posthog_event we might want to remove that very soon.

    identified_telemetry_info = ensure_posthog_user_identified()
    # Documenting for the curious that the following event is set up with a trigger in
    # go/posthog to fire a webhook to go/clay.
    fire_posthog_event(
        event_name=SculptorPosthogEvent.ONBOARDING_EMAIL_CONFIRMATION,
        component=ProductComponent.ONBOARDING,
        payload={"did_opt_in_to_marketing": email_config_request.did_opt_in_to_marketing},
    )

    # update sentry to use the email provided
    # NOTE: this is redundant with middleware that sets the user on each request to future-proofe for Sculptor being
    #       a multi-user web service. However, this ensures that any errors outside of request scope are also attributed.
    sentry_sdk.set_user(
        {
            "username": email_config_request.user_email,
            "email": email_config_request.user_email,
            "id": str(identified_telemetry_info.user_config.user_id),
        }
    )

    return identified_telemetry_info


def fire_posthog_event(
    event_name: SculptorPosthogEvent,
    component: ProductComponent,
    payload: Mapping | PosthogEventPayload | None = None,
) -> None:
    """Helper to fire a posthog event with the given name and component

    You may provide a specific subclass Event type, or a dictionary.
    """
    # TODO: To determine whether we need to ensure here, or if we can trust the
    # flow.
    identified_telemetry_info = ensure_posthog_user_identified()

    if isinstance(payload, PosthogEventPayload):
        # Just use the passed-in Payload directly
        merged_payload = payload
    elif isinstance(payload, Mapping):
        # Augment your mapping
        telemetry_info_data = telemetry.make_telemetry_event_data(identified_telemetry_info)
        merged_payload_dict = telemetry_info_data.model_dump()
        merged_payload_dict.update(payload)
        merged_payload = telemetry.SCULPTOR_POSTHOG_EVENT_TO_PAYLOAD_TYPE[event_name](**merged_payload_dict)
    else:
        assert payload is None
        merged_payload = telemetry.make_telemetry_event_data(identified_telemetry_info)

    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=event_name,
            component=component,
            payload=merged_payload,
        )
    )


@router.get("/api/v1/config/dependencies")
def get_dependencies_status(
    request: Request, user_session: UserSession = Depends(get_user_session)
) -> DependenciesStatus:
    """Check if required dependencies are installed"""
    root_concurrency_group = get_root_concurrency_group(request)
    with root_concurrency_group.make_concurrency_group(name="check_dependencies") as concurrency_group:
        ds = DependenciesStatus(
            docker_installed=check_docker_installed(),
            docker_running=check_docker_running(concurrency_group),
            mutagen_installed=check_is_mutagen_installed(concurrency_group),
            git_installed=check_git_installed(concurrency_group),
        )

    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_STARTUP_CHECKS, ProductComponent.ONBOARDING)

    if ds.docker_installed:
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_DOCKER_INSTALLED, ProductComponent.ONBOARDING)

    if ds.docker_running:
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_DOCKER_STARTED, ProductComponent.ONBOARDING)

    # Mutagen is always installed, so we don't track an event.

    if ds.git_installed:
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_GIT_INSTALLED, ProductComponent.ONBOARDING)

    return ds


@router.get("/api/v1/config/system-dependency-info")
def get_docker_settings_info(
    request: Request, user_session: UserSession = Depends(get_user_session)
) -> tuple[SystemDependencyInfo, ...]:
    """Get system dependency information and validation status.

    Returns SystemDependencyInfo with platform-specific requirements and their validation status.
    The frontend can render this generically as a dependency section.
    """
    root_concurrency_group = get_root_concurrency_group(request)
    with root_concurrency_group.make_concurrency_group(name="get_docker_settings") as concurrency_group:
        docker_settings = get_docker_settings(concurrency_group)

    return (docker_settings.to_system_dependency_info(),)


@router.post("/api/v1/config/api-key")
def save_api_key(
    request: Request,
    anthropic_api_key: str = Body(...),
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Save API key during onboarding

    Accepts both Anthropic API keys and third-party proxy keys that are
    compatible with the Anthropic API format.
    """
    if not is_valid_anthropiclike_api_key(anthropic_api_key):
        raise HTTPException(
            status_code=400,
            detail="Invalid API key. Must be non-empty and contain only ASCII characters.",
        )

    services = get_services_from_request_or_websocket(request)
    services.config_service.set_anthropic_credentials(
        AnthropicApiKey(anthropic_api_key=Secret(anthropic_api_key), generated_from_oauth=False)
    )
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_API_KEY_SET, ProductComponent.ONBOARDING)
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_AUTHORIZED, ProductComponent.ONBOARDING)


@router.post("/api/v1/config/bedrock-key")
def save_bedrock_key(
    request: Request,
    bedrock_api_key: str = Body(...),
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Save AWS Bedrock key during onboarding"""
    # AWS Bedrock Bearer tokens don't have a specific format to validate,
    # so we just check that it's not empty and contains only ASCII characters
    if not bedrock_api_key or not bedrock_api_key.isascii():
        raise HTTPException(
            status_code=400,
            detail="Invalid AWS Bedrock key. Must be non-empty and contain only ASCII characters.",
        )

    services = get_services_from_request_or_websocket(request)
    services.config_service.set_anthropic_credentials(AWSBedrockApiKey(bedrock_api_key=Secret(bedrock_api_key)))
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_API_KEY_SET, ProductComponent.ONBOARDING)
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_AUTHORIZED, ProductComponent.ONBOARDING)


@router.post("/api/v1/config/openai-key")
def save_openai_key(
    request: Request,
    openai_api_key: str = Body(...),
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Save OpenAI API key"""
    # OpenAI API keys start with "sk-" and contain alphanumeric characters and hyphens
    if not openai_api_key or not openai_api_key.isascii():
        raise HTTPException(
            status_code=400,
            detail="Invalid OpenAI API key. Must be non-empty and contain only ASCII characters.",
        )

    services = get_services_from_request_or_websocket(request)
    services.config_service.set_openai_credentials(
        OpenAIApiKey(openai_api_key=Secret(openai_api_key), generated_from_oauth=False)
    )
    # TODO: Add specific telemetry event for OpenAI API key setting when available
    # fire_posthog_event(SculptorPosthogEvent.ONBOARDING_OPENAI_API_KEY_SET, ProductComponent.ONBOARDING)


class UserConfigWithSourcePayload(PosthogEventPayload):
    source: str = without_consent()
    current_config: UserConfig = without_consent()
    prior_values: dict[str, Any] | None = without_consent()


def make_user_config_settings_edited_payload(
    source: str,
    new_config: UserConfig,
    old_config: UserConfig | None = None,
) -> PosthogEventPayload:
    prior_values = None
    if old_config:
        privacy_settings = new_config.privacy_settings
        prior_values = calculate_user_config_prior_values(old_config, new_config, privacy_settings)

    return UserConfigWithSourcePayload(source=source, current_config=new_config, prior_values=prior_values)


@router.post("/api/v1/config/privacy")
def save_privacy_settings(
    configure_privacy_request: PrivacyConfigRequest,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Save privacy and telemetry consent settings"""
    services = get_services_from_request_or_websocket(request)
    old_user_config = services.config_service.get_user_config()
    if not old_user_config:
        raise HTTPException(status_code=400, detail="User config not initialized. Please complete email setup first.")

    telemetry_level = configure_privacy_request.telemetry_level
    if telemetry_level not in (2, 3, 4):
        raise HTTPException(status_code=400, detail="Telemetry level must be an integer between 2 and 4")

    user_config = update_user_consent_level(old_user_config, telemetry_level)

    user_config = model_update(
        user_config,
        {
            "is_privacy_policy_consented": True,
            "is_repo_backup_enabled": configure_privacy_request.is_repo_backup_enabled,
            "is_telemetry_level_set": True,
        },
    )

    services.config_service.set_user_config(user_config)
    fire_posthog_event(
        SculptorPosthogEvent.USER_CONFIG_SETTINGS_EDITED,
        ProductComponent.CONFIGURATION,
        payload=make_user_config_settings_edited_payload(
            source="privacy_settings",
            new_config=user_config,
            old_config=old_user_config,
        ),
    )


@router.post("/api/v1/config/complete")
def complete_onboarding(request: Request, user_session: UserSession = Depends(get_user_session)) -> None:
    """Complete onboarding by saving config to disk and initializing services"""
    services = get_services_from_request_or_websocket(request)
    user_config = services.config_service.get_user_config()
    if not user_config:
        raise HTTPException(status_code=400, detail="User config not initialized")
    if not check_is_user_email_field_valid(user_config):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if not user_config.is_privacy_policy_consented:
        raise HTTPException(status_code=400, detail="Privacy policy not consented")

    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_USER_CONFIG_SETTINGS, ProductComponent.ONBOARDING)
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_COMPLETED, ProductComponent.ONBOARDING)
    fire_posthog_event(
        SculptorPosthogEvent.USER_CONFIG_SETTINGS_EDITED,
        ProductComponent.CONFIGURATION,
        payload=make_user_config_settings_edited_payload(
            source="complete_onboarding",
            new_config=user_config,
        ),
    )

    logger.info("Onboarding completed successfully")


@router.get("/api/v1/config")
def get_user_config(request: Request, user_session: UserSession = Depends(get_user_session)) -> UserConfig | None:
    """Get the current user config"""
    services = get_services_from_request_or_websocket(request)
    return services.config_service.get_user_config()


@router.put("/api/v1/config")
def update_user_config(
    update_config_request: UpdateUserConfigRequest,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> UserConfig:
    """Update user config"""
    services = get_services_from_request_or_websocket(request)
    old_user_config = services.config_service.get_user_config()
    services.config_service.set_user_config(update_config_request.user_config)
    fire_posthog_event(
        SculptorPosthogEvent.USER_CONFIG_SETTINGS_EDITED,
        ProductComponent.CONFIGURATION,
        payload=make_user_config_settings_edited_payload(
            source="update_user_config",
            new_config=update_config_request.user_config,
            old_config=old_user_config,
        ),
    )
    return update_config_request.user_config


@router.post("/api/v1/start_anthropic_oauth")
def start_anthropic_oauth(
    account_type: AnthropicAccountType,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> str:
    services = get_services_from_request_or_websocket(request)
    services.config_service.remove_anthropic_credentials()
    _, url = start_anthropic_oauth_impl(services.config_service, account_type)

    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_OAUTH_STARTED, ProductComponent.ONBOARDING)
    return url


@router.post("/api/v1/cancel_anthropic_oauth")
def cancel_anthropic_oauth(user_session: UserSession = Depends(get_user_session)) -> None:
    cancel_anthropic_oauth_impl()
    fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_OAUTH_CANCELLED, ProductComponent.ONBOARDING)


@router.get("/api/v1/anthropic_credentials_exists")
def anthropic_credentials_exists(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> bool:
    services = get_services_from_request_or_websocket(request)
    do_credentials_exist = services.config_service.get_credentials().anthropic is not None
    if do_credentials_exist:
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_CREDENTIALS_EXIST, ProductComponent.ONBOARDING)
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_ANTHROPIC_AUTHORIZED, ProductComponent.ONBOARDING)
    return do_credentials_exist


@router.get("/api/v1/openai_credentials_exists")
def openai_credentials_exists(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> bool:
    services = get_services_from_request_or_websocket(request)
    do_credentials_exist = services.config_service.get_credentials().openai is not None
    if do_credentials_exist:
        fire_posthog_event(SculptorPosthogEvent.ONBOARDING_OPENAI_AUTHORIZED, ProductComponent.ONBOARDING)
    return do_credentials_exist


@router.put("/api/v1/projects/{project_id}/default_system_prompt")
def update_default_system_prompt(
    project_id: str,
    request: Request,
    default_system_prompt_request: DefaultSystemPromptRequest,
    user_session: UserSession = Depends(get_user_session),
) -> str | None:
    """Update the default system prompt"""
    default_system_prompt = default_system_prompt_request.default_system_prompt
    if default_system_prompt is None:
        raise HTTPException(status_code=422, detail="default_system_prompt field required")

    logger.info("Updating default system prompt")
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        project = transaction.get_project(ProjectID(project_id))
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        updated_project = transaction.upsert_project(
            project.evolve(project.ref().default_system_prompt, default_system_prompt)
        )

    return updated_project.default_system_prompt


@router.patch("/api/v1/projects/{project_id}/tasks/{task_id}/archive")
def archive_task(
    project_id: str,
    task_id: str,
    request: Request,
    archive_request: ArchiveTaskRequest,
    user_session: UserSession = Depends(get_user_session),
) -> bool:
    """Archive or unarchive a task"""
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        try:
            validated_task_id = TaskID(task_id)
        except typeid.errors.SuffixValidationException as e:
            raise HTTPException(status_code=422, detail="Invalid task ID format") from e

        is_archived = archive_request.is_archived
        if is_archived is None:
            raise HTTPException(status_code=422, detail="is_archived field required")

        if is_archived and services.local_sync_service.is_task_synced(validated_task_id):
            logger.debug("local_sync: unsyncing synced task {} so we can archive it", task_id)
            services.local_sync_service.unsync_from_task(validated_task_id, transaction=transaction)

        try:
            updated_task = services.task_service.set_archived(validated_task_id, is_archived, transaction)
        except TaskNotFound as e:
            raise HTTPException(status_code=404, detail="Task not found") from e

    return is_archived


@router.get("/api/v1/projects/{project_id}/files_and_folders")
def get_files_and_folders(
    project_id: str,
    query: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> list[str]:
    """Get files in the project"""
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        project = transaction.get_project(ProjectID(project_id))
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.organization_reference != user_session.organization_reference:
            raise HTTPException(status_code=403, detail="You do not have access to this project")
    try:
        with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
            files = repo.list_matching_files(pattern=query)
            folders = repo.list_matching_folders(pattern=query)
            return folders + files

    except subprocess.CalledProcessError as e:
        log_exception(e, "Failed to get files and folders")
        raise HTTPException(status_code=500, detail="Failed to get repository information") from e
    except Exception as e:
        log_exception(e, "Unexpected error getting files and folders")
        raise HTTPException(status_code=500, detail="Unexpected error getting files and folders") from e


@router.get("/api/v1/projects/{project_id}/tasks/{task_id}/available_slash_commands")
def get_available_slash_commands(
    project_id: str,
    task_id: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> tuple[SlashCommand, ...]:
    """Get a list of available slash commands for the given task."""
    project_id_ = validate_project_id(project_id)
    task_id_ = validate_task_id(task_id)
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        project = transaction.get_project(project_id_)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.organization_reference != user_session.organization_reference:
            raise HTTPException(status_code=403, detail="You do not have access to this project")
        task = services.task_service.get_task(task_id_, transaction)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if not isinstance(task.input_data, AgentTaskInputsV1) or not isinstance(
            task.input_data.agent_config, ClaudeCodeSDKAgentConfig
        ):
            return ()
        task_environment = services.task_service.get_task_environment(task_id=task.object_id, transaction=transaction)
        if task_environment is None:
            # No slash commands if the task is not active.
            return ()
        return get_all_supported_slash_commands(task_environment)


# TODO: post-V1 transition, this should transition to CRUD on Projects (which should know this data)
@router.get("/api/v1/projects/{project_id}/current_branch")
def get_current_branch(
    project_id: ProjectID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> CurrentBranchInfo:
    """Get just the current branch (fast endpoint)"""
    services = get_services_from_request_or_websocket(request)
    try:
        with user_session.open_transaction(services) as transaction:
            project = transaction.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if not project.is_path_accessible:
                raise HTTPException(status_code=404, detail="Project path not accessible")

        with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
            try:
                current_branch = repo.get_current_git_branch()
                num_uncommitted_changes = repo.get_num_uncommitted_changes()
            except FileNotFoundError as e:
                raise HTTPException(status_code=500, detail=f"Could not find repository: {e}") from e
            except ProcessSetupError:
                if project.is_path_accessible:
                    raise
                raise HTTPException(status_code=404, detail="Project path has become inaccessible")

        return CurrentBranchInfo(
            current_branch=current_branch,
            num_uncommitted_changes=num_uncommitted_changes,
        )
    except HTTPException:
        raise
    except subprocess.CalledProcessError as e:
        log_exception(e, "Failed to get current branch", priority=ExceptionPriority.LOW_PRIORITY)
        raise HTTPException(status_code=404, detail="Failed to get current branch information")
    except Exception as e:
        log_exception(e, "Unexpected error getting current branch", priority=ExceptionPriority.LOW_PRIORITY)
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/v1/projects/{project_id}/repo_info")
def get_repo_info(
    project_id: ProjectID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> RepoInfo:
    """Get repository information including path and recent branches"""
    services = get_services_from_request_or_websocket(request)
    try:
        with user_session.open_transaction(services) as transaction:
            project = transaction.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if not project.is_path_accessible:
                raise HTTPException(status_code=404, detail="Project path not accessible")

        with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
            repo_path = repo.get_repo_path()
            try:
                branches = repo.get_all_branches()
                current_branch = repo.get_current_git_branch()
                num_uncommitted_changes = repo.get_num_uncommitted_changes()
            except FileNotFoundError as e:
                raise HTTPException(status_code=500, detail=f"Could not find repository: {e}") from e
            except ProcessSetupError:
                # The is_path_accessible attribute is set in _check_and_update_project_accessibility, which
                # used to fail when the project repo is a remote mounted directory which got disconnected.
                # Properly catching the OSError there should prevent an unnecessary re-raise here, preventing
                # Sentry spam and hopefully preventing the backend from crashing.
                if project.is_path_accessible:
                    raise
                raise HTTPException(status_code=404, detail=f"Project path {repo_path} has become inaccessible")

        if not branches:
            raise HTTPException(status_code=500, detail=f"Could not find any branches in repository {repo_path}")

        return RepoInfo(
            repo_path=repo_path,
            current_branch=current_branch,
            recent_branches=branches,
            project_id=project.object_id,
            num_uncommitted_changes=num_uncommitted_changes,
        )
    except HTTPException:
        raise
    except subprocess.CalledProcessError as e:
        log_exception(e, "Failed to get repo info", priority=ExceptionPriority.LOW_PRIORITY)
        raise HTTPException(status_code=500, detail="Failed to get repository information")
    except Exception as e:
        log_exception(e, "Unexpected error getting repo info", priority=ExceptionPriority.LOW_PRIORITY)
        raise HTTPException(status_code=500, detail=str(e))


@APP.websocket("/api/v1/stream/ws")
async def stream_everything_websocket(
    websocket: WebSocket,
    user_session: UserSession = Depends(get_user_session_for_websocket),
    shutdown_event: Event = Depends(shutdown_event_impl),
) -> None:
    """Unified stream for all updates: tasks, task details, user data, notifications.

    Streams for ALL projects and ALL tasks for the authenticated user.
    """
    services = get_services_from_request_or_websocket(websocket)
    root_concurrency_group = get_root_concurrency_group(websocket)
    with root_concurrency_group.make_concurrency_group(name="stream_everything_websocket") as stream_concurrency_group:
        await to_websocket_stream(
            user_session,
            stream_everything(
                user_session=user_session,
                shutdown_event=shutdown_event,
                services=services,
                concurrency_group=stream_concurrency_group,
            ),
            websocket,
            stream_concurrency_group.shutdown_event,
        )


async def _try_to_gracefully_close_on_error(websocket: WebSocket, error: SerializedException) -> None:
    try:
        await websocket.send_json(model_dump(error, is_camel_case=True))
    except WebSocketDisconnect:
        return
    except Exception as e:
        logger.info("Failed to send WebSocket error message to client: {}", e)

    try:
        await websocket.close(code=1011, reason="Internal Server Error")
    except Exception as e:
        logger.info("Failed to gracefully close websocket after error: {}", e)
        return


async def to_websocket_stream(
    user_session: UserSession,
    generator: Generator[UpdateT | None, None, None],
    websocket: WebSocket,
    close_event: MutableEvent,
) -> None:
    try:
        await websocket.accept()
    except RuntimeError as e:
        # suppressing this when we are shutting down, doesn't seem to matter
        if (
            "Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'" in str(e)
            and hasattr(APP, "shutdown_event")
            and APP.shutdown_event.is_set()
        ):
            with logger.contextualize(**user_session.logger_kwargs):
                error = SerializedException.build(e)
            await _try_to_gracefully_close_on_error(websocket, error)
        else:
            raise
    try:
        itr = iter(generator)
        while True:
            loop = asyncio.get_event_loop()
            to_yield = await loop.run_in_executor(
                None,
                run_sync_function_with_debugging_support_if_enabled,
                _get_next_elem_for_websocket,
                (itr, user_session),
                {},
            )
            if to_yield is None:
                with logger.contextualize(**user_session.logger_kwargs):
                    logger.debug("Stream ended normally.")
                    await websocket.close(code=1000, reason="Stream ended normally")
                    return
            await websocket.send_json(to_yield)
            # sigh, asyncio is strictly the worst thing in existence
            await asyncio.sleep(0.00001)
    except ServerStopped:
        with logger.contextualize(**user_session.logger_kwargs):
            logger.debug("Server is stopping, closing update stream.")
            await websocket.close(code=1001, reason="Server is stopping")
            return
    except WebSocketDisconnect:
        with logger.contextualize(**user_session.logger_kwargs):
            logger.debug("WebSocket client disconnected")
        return
    except TaskNotFound as e:
        with logger.contextualize(**user_session.logger_kwargs):
            log_exception(e, "Task not found", priority=ExceptionPriority.LOW_PRIORITY)
            error = SerializedException.build(e)
        await _try_to_gracefully_close_on_error(websocket, error)
        raise
    except CancelledError as e:
        error = SerializedException.build(e)
        await _try_to_gracefully_close_on_error(websocket, error)
    except BaseException as e:
        with logger.contextualize(**user_session.logger_kwargs):
            log_exception(
                e,
                "Error in event stream generator",
                priority=ExceptionPriority.MEDIUM_PRIORITY,
            )
            error = SerializedException.build(e)
        await _try_to_gracefully_close_on_error(websocket, error)
        raise
    finally:
        close_event.set()
        generator.close()


def _get_next_elem(itr) -> str:
    entry = next(itr)
    if entry is None:
        logger.trace("Sending keepalive event")
        to_yield = ": keepalive\n\n"
    else:
        logger.trace("Sending event {}", type(entry))
        to_yield = f"data: {model_dump_json(entry, is_camel_case=True)}\n\n"
    return to_yield


def _get_next_elem_for_websocket(
    itr: Iterator[UpdateT | None], user_session: UserSession
) -> str | dict[str, Any] | None:
    with logger.contextualize(**user_session.logger_kwargs):
        try:
            entry = next(itr)
            # Do not raise StopIteration from this function as it cannot be properly propagated through the executor boundary.
        except StopIteration:
            return None
        if entry is None:
            logger.trace("Sending keepalive event")
            to_yield = "null"
        else:
            logger.trace("Sending event {}", type(entry))
            to_yield = entry.model_dump(mode="json", by_alias=True)
        return to_yield


@router.get("/api/v1/projects/{project_id}/tasks/{task_id}/artifacts/{artifact_name}")
def get_artifact_data(
    project_id: str,
    task_id: TaskID,
    artifact_name: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> ArtifactDataResponse:
    services = get_services_from_request_or_websocket(request)
    return _get_typed_artifact_data(artifact_name, services, str(task_id), user_session)


@router.get("/api/v1/projects/{project_id}/tasks/{task_id}/artifacts/{artifact_name}/raw")
def get_artifact_data_raw(
    project_id: str,
    task_id: str,
    artifact_name: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> str:
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e
    services = get_services_from_request_or_websocket(request)
    artifact_data = _get_artifact_data(artifact_name, services, task_id, user_session)
    return artifact_data


def _get_artifact_data(
    artifact_name: str,
    services: CompleteServiceCollection,
    task_id_str: str,
    user_session: UserSession,
) -> str:
    try:
        task_id = TaskID(task_id_str)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e
    with user_session.open_transaction(services) as transaction:
        task = services.task_service.get_task(task_id, transaction)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    artifact_data_url = services.task_service.get_artifact_file_url(task_id, artifact_name)
    assert str(artifact_data_url).startswith("file://"), "Only local file artifacts are supported"
    artifact_data_path = Path(str(artifact_data_url).replace("file://", ""))
    if not artifact_data_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact_data = artifact_data_path.read_text(encoding="utf-8")
    logger.debug("Returning artifact at path {}", artifact_data_path)
    return artifact_data


def _get_typed_artifact_data(
    artifact_name: str,
    services: CompleteServiceCollection,
    task_id_str: str,
    user_session: UserSession,
) -> ArtifactDataResponse:
    """Get artifact data and return it with proper typing based on artifact type."""
    raw_data = _get_artifact_data(artifact_name, services, task_id_str, user_session)
    try:
        artifact_type = ArtifactType(artifact_name)
    except ValueError as e:
        logger.error("Unknown artifact type: {}", artifact_name)
        raise HTTPException(status_code=400, detail=f"Unknown artifact type: {artifact_name}") from e

    # happens occasionally, better to do this than cause flaky test errors
    if raw_data == "":
        raise HTTPException(status_code=404, detail="Artifact is empty")

    try:
        parsed_json = json.loads(raw_data)

        if not isinstance(parsed_json, dict) or "object_type" not in parsed_json:
            logger.error("Artifact missing object_type field: {}", artifact_name)
            raise HTTPException(status_code=500, detail="Invalid artifact format")

        if parsed_json["object_type"] == "SuggestionsArtifact":
            return SuggestionsArtifact.model_validate(parsed_json)
        elif parsed_json["object_type"] == "TodoListArtifact":
            return TodoListArtifact.model_validate(parsed_json)
        elif parsed_json["object_type"] == "LogsArtifact":
            return LogsArtifact.model_validate(parsed_json)
        elif parsed_json["object_type"] == "DiffArtifact":
            return DiffArtifact.model_validate(parsed_json)
        elif parsed_json["object_type"] == "UsageArtifact":
            return UsageArtifact.model_validate(parsed_json)
        else:
            logger.error("Unknown object_type: {}", parsed_json["object_type"])
            raise HTTPException(
                status_code=500,
                detail=f"Unknown artifact object_type: {parsed_json['object_type']}",
            )

    except json.JSONDecodeError as e:
        log_exception(
            e,
            "Failed to parse artifact JSON",
            priority=ExceptionPriority.MEDIUM_PRIORITY,
        )
        raise HTTPException(status_code=500, detail="Invalid artifact JSON") from e
    except ValidationError as e:
        log_exception(
            e,
            "Failed to validate artifact data",
            priority=ExceptionPriority.MEDIUM_PRIORITY,
        )
        raise HTTPException(status_code=422, detail="Invalid artifact data") from e


def _raise_http_exception_if_task_is_not_ready_to_sync(task_id: TaskID, task: Task | None) -> None:
    """Check if a task is ready to be synced."""
    not_ready_please_hold = (
        f"not ready to sync - wait a bit and try again. If this error persists, {PLEASE_POST_IN_DISCORD}"
    )
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in DB! URL may be incorrect or a system-wide issue occurred.",
        )
    state = task.current_state
    if state is None:
        # TODO it should be trivial to get a task title
        raise HTTPException(status_code=405, detail=f"Task '{task_id}' {not_ready_please_hold}")
    # TODO We should probably be using generics
    assert isinstance(state, AgentTaskStateV1), f"Impossible: Task {task_id} is not an AgentTaskStateV1."
    if state.task_repo_path is None:
        # TODO it should be trivial to get a task title
        raise HTTPException(
            status_code=405,
            detail=f"Task '{state.title or task_id}' {not_ready_please_hold}",
        )


def _validate_stash_singleton_not_present(
    git_repo_service: GitRepoService,
    transaction: DataModelTransaction,
) -> None:
    # TODO: is_strict=True skims refs from all project repos,
    # even if the file marker indicates no stash is present.
    existing = read_global_stash_singleton_if_present(git_repo_service, transaction, is_strict=True)
    if existing is not None:
        existing_stash, owning_project = existing
        msg = (
            "Cannot perform this operation with an existing sculptor stash",
            f"(owned by {owning_project.user_git_repo_url} under refs/sculptor/stashes).",
            "Please restore or delete it before proceeding.",
        )
        raise HTTPException(status_code=409, detail=" ".join(msg))


@router.post("/api/sync/projects/{project_id}/tasks/{task_id}/enable")
def enable_task_sync(
    project_id: str,
    task_id: TaskID,
    request: Request,
    enable_sync_request: EnableLocalSyncRequest,
    user_session: UserSession = Depends(get_user_session),
) -> SyncToTaskResult:
    """Enable sync for a task (only one task can be synced at a time)"""
    services = get_services_from_request_or_websocket(request)
    if services.local_sync_service.is_task_synced(task_id):
        raise HTTPException(status_code=409, detail=f"Task '{task_id}' is already paired")

    try:
        # TODO: Consider removing special desync path from sync_to_task
        # and instead just calling unsync_from_task and then sync_to_task as two separate / atomic operations
        with services.data_model_service.open_transaction(user_session.request_id) as transaction:
            if enable_sync_request.is_stashing_ok and not services.local_sync_service.get_session_state():
                _validate_stash_singleton_not_present(services.git_repo_service, transaction)
            task = services.task_service.get_task(task_id, transaction)
            _raise_http_exception_if_task_is_not_ready_to_sync(task_id, task)
            # TODO: surface unsync_from_task failures to user even if cauesd by switch
            return services.local_sync_service.sync_to_task(
                task_id=task_id, transaction=transaction, is_stashing_ok=enable_sync_request.is_stashing_ok
            )
    except ExpectedSyncStartupError as e:
        logger.trace("Invalid state to start local syncing from for task {}: {}", task_id, e)
        # hmm... there is a blockers enum in this error now but IDK how to surface that to the frontend.
        # I'm thinking it could be used to pulse the Merge/Push button or something
        raise HTTPException(status_code=409, detail=e.message) from e
    except OtherSyncTransitionInProgressError as e:
        logger.trace("Blocking task {} from local syncing: {}", task_id, e)
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException as e:
        logger.trace("Blocking unready task {} from local syncing: {}", task_id, e)
        raise e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        log_exception(e, "Failed to enable sync for task {task_id}", task_id=task_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/sync/projects/{project_id}/tasks/{task_id}/disable")
def disable_task_sync(
    project_id: str,
    task_id: TaskID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> DisableLocalSyncResponse:
    """Disable sync for a task"""
    services = get_services_from_request_or_websocket(request)
    try:
        with services.data_model_service.open_transaction(user_session.request_id) as transaction:
            result = services.local_sync_service.unsync_from_task(task_id, transaction=transaction)
        # if the stash apply failed we want the client to know immediately about the new state to avoid jitter
        repo_info = read_local_repo_info(services, ProjectID(project_id))
        return DisableLocalSyncResponse(result=result, resulting_repo_info=repo_info)
    except OtherSyncTransitionInProgressError as e:
        logger.trace("Blocking task {} from unsyncing: {}", task_id, e)
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        log_exception(e, "Failed to disable sync for task {task_id}", task_id=task_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


# TODO(mjr): Now that we stream all task updates across all projects the whole state polling thing can be diced into a substream I think
@router.get("/api/sync/global_singleton_state")
def get_global_sync_state_stopgap(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> GlobalLocalSyncInfo | None:
    """Get the current global sync state information"""
    services = get_services_from_request_or_websocket(request)
    sync_view: SyncedTaskView | None = None
    stash: SculptorStashSingleton | None = None
    try:
        sync_view = _get_global_sync_state(services, user_session)
        with services.data_model_service.open_transaction(user_session.request_id) as transaction:
            stash_and_project = read_global_stash_singleton_if_present(services.git_repo_service, transaction)
            stash = stash_and_project[0] if stash_and_project is not None else None
        return GlobalLocalSyncInfo(synced_task=sync_view, stash_singleton=stash)

    except Exception as e:
        if not isinstance(e, FileNotFoundError):
            # Could be quite spammy if there's a real issue
            log_exception(
                e,
                "Failed to get global sync state. sync_view={sync_view}, stash={stash}",
                ExceptionPriority.LOW_PRIORITY,
                sync_view=sync_view,
                stash=stash,
            )
        raise HTTPException(status_code=500, detail=str(e)) from e


# TODO(mjr): Yes I know this shouldbe a DELETE I just didn't want to refactor the request
# also our REST semantics are pretty bad anyways
@router.post("/api/sync/projects/{project_id}/stash/delete")
def delete_sync_stash(
    project_id: ProjectID,
    request: Request,
    delete_request: DeleteSyncStashRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Get the current global sync state information"""
    services = get_services_from_request_or_websocket(request)
    singleton, project = _validated_stash_singleton(
        project_id,
        services,
        user_session,
        delete_request.absolute_stash_ref,
    )
    ref = singleton.stash.absolute_stash_ref
    try:
        with (
            services.local_sync_service.maybe_acquire_sync_transition_lock() as is_local_sync_transition_locked,
            services.git_repo_service.open_local_user_git_repo_for_write(project) as repo,
        ):
            if not is_local_sync_transition_locked:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot delete stash '{ref}' during acting Pairing Mode transition (could race)",
                )
            logger.info("Deleting sync stash '{ref}' from project {project_id}", ref=ref, project_id=project_id)
            delete_namespaced_stash_in_project(project_id, repo, singleton.stash)
    except Exception as e:
        log_exception(
            e, "Failed to delete sync stash. stash_singleton={s}", ExceptionPriority.MEDIUM_PRIORITY, s=singleton
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/sync/projects/{project_id}/stash/restore")
def restore_sync_stash(
    project_id: ProjectID,
    request: Request,
    restore_request: RestoreSyncStashRequest,
    user_session: UserSession = Depends(get_user_session),
) -> LocalRepoInfo | None:
    """Restore the global sync stash singleton into its source branch and return resulting repo info"""
    services = get_services_from_request_or_websocket(request)
    if services.local_sync_service.get_session_state() is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot restore on global stash singleton while a Pairing Mode session is active.",
        )
    singleton, project = _validated_stash_singleton(
        project_id,
        services,
        user_session,
        restore_request.absolute_stash_ref,
    )
    try:
        with (
            services.local_sync_service.maybe_guarantee_no_new_or_active_session() as is_no_active_session_guaranteed,
            services.git_repo_service.open_local_user_git_repo_for_write(project) as repo,
        ):
            if not is_no_active_session_guaranteed:
                ref = singleton.stash.absolute_stash_ref
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot restore stash '{ref}' with active Pairing Mode session",
                )
            pop_namespaced_stash_into_source_branch(project_id, repo, singleton.stash)
        return read_local_repo_info(services, project_id)
    except GitRepoError as e:
        if "intermediate" in str(e).lower():
            error_detail = (
                f"Cannot restore stash '{restore_request.absolute_stash_ref}': {e}",
                "Please ensure repo state is pristine and try again.",
            )
            logger.info("Conflict restoring stash: {}", error_detail)
            raise HTTPException(status_code=409, detail="\n".join(error_detail)) from e
        log_exception(
            e, "Failed to restore sync stash. stash_singleton={s}", ExceptionPriority.MEDIUM_PRIORITY, s=singleton
        )
        raise
    except Exception as e:
        log_exception(
            e, "Failed to restore sync stash. stash_singleton={s}", ExceptionPriority.MEDIUM_PRIORITY, s=singleton
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


def _validated_stash_singleton(
    project_id: ProjectID,
    services: CompleteServiceCollection,
    user_session: UserSession,
    stash_ref_name: str,
) -> tuple[SculptorStashSingleton, Project]:
    with services.data_model_service.open_transaction(user_session.request_id) as transaction:
        singleton_and_project = read_global_stash_singleton_if_present(services.git_repo_service, transaction)
        if singleton_and_project is None:
            raise HTTPException(status_code=404, detail="No global stash singleton present to restore from")
    singleton, project = singleton_and_project
    stash = singleton.stash

    if singleton.owning_project_id != project_id:
        raise HTTPException(
            status_code=409,
            detail=f"Stash singleton owned by {singleton.owning_project_id}, not {project_id}",
        )

    if stash.absolute_stash_ref != stash_ref_name:
        raise HTTPException(
            status_code=409,
            detail=f"Stash ref name mismatch: {stash.absolute_stash_ref} != {stash_ref_name}",
        )

    return singleton, project


def _get_global_sync_state(services: CompleteServiceCollection, user_session: UserSession) -> SyncedTaskView | None:
    """Get the current global sync state information"""
    # Get the current sync session state from the service
    session_state = services.local_sync_service.get_session_state()
    with services.data_model_service.open_transaction(user_session.request_id) as transaction:
        if session_state is None:
            return None

        sync_info = session_state.info
        # Get the task to retrieve title
        task = services.task_service.get_task(sync_info.task_id, transaction)
        if task is None or not isinstance(task.current_state, AgentTaskStateV1):
            return None

        # Get the project to retrieve project path
        project = transaction.get_project(sync_info.project_id)
    assert project is not None and project.user_git_repo_url, "Project is missing or has no user git repo URL"

    # Convert session state to LocalSyncState
    sync_status = LocalSyncStatus.INACTIVE
    if session_state.high_level_status.value == "ACTIVE":
        sync_status = LocalSyncStatus.ACTIVE
    elif session_state.high_level_status.value == "PAUSED":
        sync_status = LocalSyncStatus.PAUSED

    sync_state = LocalSyncState(
        status=sync_status,
        last_updated=session_state.start_time,
        notices=session_state.notices,
    )
    return SyncedTaskView.build(
        task=task,
        sync=sync_state,
        sync_started_at=session_state.start_time,
    )


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/interrupt")
def interrupt_task(
    project_id: str,
    task_id: str,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Interrupts a given task while it is thinking."""
    logger.info("Getting task {}", task_id)
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e
    message_id = AgentMessageID()
    services = get_services_from_request_or_websocket(request)
    with await_message_response(message_id, validated_task_id, services):
        with user_session.open_transaction(services) as transaction:
            task = services.task_service.get_task(validated_task_id, transaction)
            if not task:
                logger.error("Task {} not found", task_id)
                raise HTTPException(status_code=404, detail="Task not found")
            services.task_service.create_message(
                message=InterruptProcessUserMessage(message_id=message_id),
                task_id=validated_task_id,
                transaction=transaction,
            )


def _validate_transfer_repo_parameters(
    project_id: str,
    task_id: str,
    services: CompleteServiceCollection,
    user_session: UserSession,
) -> tuple[Project, Task, Environment]:
    try:
        validated_task_id = TaskID(task_id)
        validated_project_id = ProjectID(project_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task identifiers format") from e

    with user_session.open_transaction(services) as transaction:
        project = transaction.get_project(validated_project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        task = services.task_service.get_task(task_id=validated_task_id, transaction=transaction)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        current_state = task.current_state
        if not isinstance(current_state, AgentTaskStateV1):
            raise HTTPException(
                status_code=400,
                detail=f"Task {task_id} is not an AgentTask.",
            )

        task_environment = services.task_service.get_task_environment(task_id=task.object_id, transaction=transaction)
        if task_environment is None:
            raise HTTPException(
                status_code=500,
                detail=f"Task {task_id} does not have an active environment",
            )

    task_repo_path = current_state.task_repo_path
    if task_repo_path is None:
        raise HTTPException(
            status_code=500,
            detail=f"Task {task_id} does not have a task repository path set.",
        )

    task_repo = RemoteReadOnlyGitRepo(environment=task_environment)
    task_local_branch = task_repo.get_current_git_branch()
    if task_local_branch != current_state.branch_name:
        # TODO: is this just a warning for the user, with a request for the agent to change the branch?
        raise HTTPException(
            status_code=409,
            detail=f"Agent is on branch '{task_local_branch}' which doesn't match expected '{current_state.branch_name}'.",
        )

    # This is a 409 as we expect the frontend action to be blocked on such a state, even if it's allowed right now.
    task_repo_status = task_repo.get_current_status()
    if task_repo_status.is_in_intermediate_state:
        raise HTTPException(
            status_code=409,
            detail=f"Agent repository is in an inconsistent state: {task_repo_status.describe()}. Have the agent resolve it before synchronizing.",
        )

    return project, task, task_environment


# FIXME: this is work in progress and will be cleaned up with PROD-1905
@APP.post("/api/v1/projects/{project_id}/tasks/{task_id}/transfer-to-agent", operation_id="transferToAgent")
def transfer_from_local_to_task(
    project_id: str,
    task_id: str,
    request: Request,
    transfer_request: TransferFromLocalToTaskRequest,
    user_session: UserSession = Depends(get_user_session),
) -> TransferFromLocalToTaskResponse:
    services = get_services_from_request_or_websocket(request)
    project, task, task_environment = _validate_transfer_repo_parameters(project_id, task_id, services, user_session)

    logger.debug("Request to merge local branch into that of the agent, request: {}", transfer_request)

    task_repo = RemoteWritableGitRepo(environment=task_environment)

    with services.git_repo_service.open_local_user_git_repo_for_read(project) as local_repo:
        assert isinstance(local_repo, LocalReadOnlyGitRepo)

        with log_runtime("ManualSync.merge_into_agent"):
            merge_action_result = merge_into_agent(task_repo, local_repo, transfer_request.target_local_branch)

    with user_session.open_transaction(services) as transaction:
        # TODO: IMO(mjr) the definition of success here is odd.
        # We kinda have have {USER_ACTION_REJECTED, SUCCESS_WITH_FOLLOWUP_REQUIRED, SUCCESS_CLEAN_AND_SIMPLE}
        message = ManualSyncMergeIntoAgentAttemptedMessage(
            is_attempt_unambiguously_successful=merge_action_result.success,
            is_merge_in_progress=task_repo.is_merge_in_progress,
            labels=[n.label for n in merge_action_result.notices],
            source_local_branch=transfer_request.target_local_branch,
            local_branch=transfer_request.assumptions.local_branch,  # we pull it from assumptions because if it materially doesn't match them, then the operation will raise http 409
        )
        services.task_service.create_message(
            message=message,
            task_id=task.object_id,
            transaction=transaction,
        )
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.MANUAL_SYNC_MERGE_INTO_AGENT_ATTEMPTED,
                component=ProductComponent.MANUAL_SYNC,
                payload=message,
                task_id=str(task_id),
            )
        )

        return TransferFromLocalToTaskResponse(
            success=merge_action_result.success,
            notices=tuple(merge_action_result.notices),
            missing_decisions=None,
        )


# FIXME: this is work in progress and will be cleaned up with PROD-1905
# TODO: strikes me as odd that we have the decision flow here and not in the merge-into-agent flow above
@APP.post("/api/v1/projects/{project_id}/tasks/{task_id}/transfer-to-local", operation_id="transferToLocal")
def transfer_from_task_to_local(
    project_id: str,
    task_id: str,
    request: Request,
    transfer_request: TransferFromTaskToLocalRequest,
    user_session: UserSession = Depends(get_user_session),
) -> TransferFromTaskToLocalResponse:
    services = get_services_from_request_or_websocket(request)
    project, task, task_environment = _validate_transfer_repo_parameters(project_id, task_id, services, user_session)
    task_repo = RemoteReadOnlyGitRepo(environment=task_environment)
    with log_runtime("ManualSync._transfer_from_task_to_local"):
        # TODO: log http status exceptions to Posthog?
        response = _transfer_from_task_to_local(
            git_repo_service=services.git_repo_service,
            user_session=user_session,
            project=project,
            task_repo=task_repo,
            request=transfer_request,
            services=services,
            task=task,
        )

    message_only_for_posthog = ManualSyncMergeIntoUserAttemptedMessage(
        reached_operation_label=response.reached_operation_or_failure_label if response.success else None,
        reached_operation_failure_label=None if response.success else response.reached_operation_or_failure_label,
        reached_decision_label=response.missing_decisions[0].id if response.missing_decisions else None,
        selection_by_decision_label=transfer_request.user_choice_by_decision_id,
        target_local_branch=transfer_request.target_local_branch,
        local_branch=transfer_request.assumptions.local_branch,  # we pull it from assumptions because if it doesn't match them, then the operation will raise http 409
    )
    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.MANUAL_SYNC_MERGE_INTO_USER_ATTEMPTED,
            component=ProductComponent.MANUAL_SYNC,
            payload=message_only_for_posthog,
            task_id=str(task_id),
        )
    )
    return response


def _transfer_from_task_to_local(
    git_repo_service: GitRepoService,
    user_session: UserSession,
    project: Project,
    task_repo: RemoteReadOnlyGitRepo,
    request: TransferFromTaskToLocalRequest,
    services: CompleteServiceCollection,
    task: Task,
) -> TransferFromTaskToLocalResponse:
    # FIXME: should we explicitly guard against concurrent operations or rely
    #        on the user repo lock below?
    logger.debug("Request to sync local repository to that of the agent, request: {}", request)

    notices: list[MergeActionNotice] = []

    task_local_branch = task_repo.get_current_git_branch()

    task_repo_status = task_repo.get_current_status()
    # Just covering a data race, as the repo state is checked at the beginning of the request and
    # we shouldn't be able to get here easily.
    if task_repo_status.is_in_intermediate_state:
        raise HTTPException(
            status_code=409,
            detail=f"Agent repository got into an inconsistent state while request was being processed: {task_repo_status.describe(is_file_changes_list_included=False)}. Have the agent resolve it before trying again.",
        )
    if not task_repo_status.files.are_clean_including_untracked:
        logger.trace("Uncommitted changes in task repo: {}", task_repo_status.describe())
        merge_option = "Merge & ignore uncommitted changes"
        commit_option = "Commit & Merge"
        decision_needed = TransferRepoDecision(
            id="TASK_HAS_UNCOMMITTED_CHANGES",
            title="Agent branch has uncommitted work",
            message=f"The agent branch `{task_local_branch}` has changes that aren't committed yet. You can choose to ignore them or commit and proceed with the merge",
            # NOTE: this has newlines and is not meant for a single-liner
            # TODO: make a nicer function? push rich data and deal with it in the frontend?
            detailed_context_title=", ".join(task_repo_status.files.description.splitlines()),
            # TODO(PROD-2835): connect the dialog to the "Review Changes" popover instead of showing the same data over
            # Alternative: extract the specific files in our git status parsing to give a little bit more overview than
            #              just number of files.
            detailed_context=task_repo_status.describe(),
            is_commit_message_required=True,
            options=(
                TransferRepoDecisionOption(option=merge_option),
                TransferRepoDecisionOption(option=commit_option, is_default=True),
            ),
        )
        option_selected = decision_needed.resolve_user_choice(request.user_choices)
        if option_selected is None:
            return TransferFromTaskToLocalResponse(
                success=False,
                notices=(
                    *notices,
                    MergeActionNotice(message="Agent repository has uncommitted changes."),
                ),
                missing_decisions=[decision_needed],
            )
        if option_selected.choice == merge_option:
            notices.append(MergeActionNotice(message="Ignoring uncommitted changes in Agent's repository"))
        elif option_selected.choice == commit_option:
            # Commit the changes in the task environment
            if not option_selected.commit_message:
                return TransferFromTaskToLocalResponse(
                    success=False,
                    notices=(
                        *notices,
                        MergeActionNotice(
                            message="Commit message is required but was not provided",
                            kind=MergeActionNoticeKind.ERROR,
                        ),
                    ),
                    reached_operation_or_failure_label="COMMIT_MESSAGE_MISSING",
                )

            _commit_changes_in_task(
                user_session=user_session,
                services=services,
                task_id=task.object_id,
                commit_message=option_selected.commit_message,
            )

            # A sanity and data-race check: verify the commit was successful and there are
            # no more changes. We could skip this entirely too and just roll with it.
            task_repo_status_after_commit = task_repo.get_current_status()
            if not task_repo_status_after_commit.files.are_clean_including_untracked:
                raise HTTPException(
                    status_code=409,
                    detail="Commit was attempted but some changes remain uncommitted",
                )

            notices.append(
                MergeActionNotice(
                    message="Successfully committed changes in Agent's repository",
                    details=f"Commit message: {option_selected.commit_message}",
                )
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Agent repository has uncommitted changes, user's choice is not understood: {option_selected}.",
            )

    with git_repo_service.open_local_user_git_repo_for_write(project) as local_repo:
        assert isinstance(local_repo, LocalWritableGitRepo)

        # validate assumptions, but only if it matters
        local_branch = local_repo.get_current_git_branch()
        is_pull_into_current_branch = local_branch == request.target_local_branch
        is_expecting_pull_into_current_branch = request.assumptions.local_branch == request.target_local_branch
        if is_pull_into_current_branch != is_expecting_pull_into_current_branch:
            flag_to_name = lambda is_pull: "Pull" if is_pull else "Fetch"
            raise HTTPException(
                status_code=409,
                detail=f"The local branch was changed and the {flag_to_name(is_expecting_pull_into_current_branch)} operation would become a {flag_to_name(is_pull_into_current_branch)} instead. Please try again in a few seconds.",
            )

        if is_pull_into_current_branch:
            logger.trace("Local branch is the same as target branch, merging into the working tree")

            local_repo_status = local_repo.get_current_status()
            if local_repo_status.is_in_intermediate_state:
                return TransferFromTaskToLocalResponse(
                    success=False,
                    reached_operation_or_failure_label="MERGE_INTO_INTERMEDIATE_STATE_IMPOSSIBLE",
                    notices=(
                        *notices,
                        MergeActionNotice(
                            kind=MergeActionNoticeKind.ERROR,
                            message="Merge can only proceed against a repository with clean status",
                            details=local_repo_status.describe(),
                        ),
                    ),
                    missing_decisions=None,
                )
            if not local_repo_status.files.are_clean_including_untracked:
                notices.append(
                    MergeActionNotice(
                        kind=MergeActionNoticeKind.WARNING,
                        message="Merging into a repository with uncommitted changes.",
                        details=local_repo_status.files.description,
                    )
                )

            ff_merge_result = local_repo.pull_from_remote(
                remote=str(task_repo.get_repo_url()),
                remote_branch=task_local_branch,
                is_fast_forward_only=True,
            )
            if ff_merge_result.is_merged:
                if ff_merge_result.was_up_to_date:
                    ff_merge_notice = "Already up to date! No merge needed."
                else:
                    ff_merge_notice = "Agent branch merged successfully into local repository (fast-forwarded)"
                return TransferFromTaskToLocalResponse(
                    success=True,
                    reached_operation_or_failure_label="LOCAL_BRANCH_FAST_FORWARDED",
                    notices=(
                        *notices,
                        MergeActionNotice(
                            message=ff_merge_notice,
                            kind=MergeActionNoticeKind.SUCCESS,
                            details=ff_merge_result.raw_output,
                        ),
                    ),
                )
            elif ff_merge_result.is_stopped_by_uncommitted_changes:
                return TransferFromTaskToLocalResponse(
                    success=False,
                    reached_operation_or_failure_label="LOCAL_UNCOMMITTED_CHANGES_BLOCK_FF_MERGE",
                    notices=(
                        *notices,
                        MergeActionNotice(
                            kind=MergeActionNoticeKind.ERROR,
                            message="The merge was blocked by uncommitted changes in your local repository.",
                            details=ff_merge_result.raw_output,
                        ),
                    ),
                )
            else:
                merge_keep_conflict = "Merge"
                merge_abort_on_conflict = "Merge, but abort on conflict"
                decision_needed = TransferRepoDecision(
                    id="FF_MERGE_NOT_POSSIBLE",
                    title="Confirm merge",
                    message="\n\n".join(
                        [
                            f"Your local branch `{request.target_local_branch}` and the agent branch `{task_local_branch}` have diverged—both have new commits.",
                            "To continue, Sculptor needs to run a git merge, which may create merge conflicts. If you want the agent's help resolving conflicts, first push your local branch into the agent branch.",
                        ]
                    ),
                    options=(
                        TransferRepoDecisionOption(option=merge_keep_conflict),
                        TransferRepoDecisionOption(option=merge_abort_on_conflict, is_default=True),
                    ),
                )
                user_choice = decision_needed.resolve_user_choice(request.user_choices)
                if user_choice is None:
                    return TransferFromTaskToLocalResponse(
                        success=False,
                        notices=(
                            *notices,
                            MergeActionNotice(
                                message="Fast forward not possible. User decision needed to continue.",
                                details=ff_merge_result.raw_output,
                            ),
                        ),
                        missing_decisions=[decision_needed],
                    )

                if user_choice.choice not in (merge_keep_conflict, merge_abort_on_conflict):
                    raise HTTPException(status_code=400, detail=f"Unexpected response to user decision: {user_choice}")
                should_abort_on_conflict = user_choice.choice == merge_abort_on_conflict

                notices.append(
                    MergeActionNotice(
                        message=f"Merging Agent changes into the local branch{' (aborting on conflicts)' if should_abort_on_conflict else ''}."
                    )
                )
                merge_result = local_repo.pull_from_remote(
                    remote=str(task_repo.get_repo_url()),
                    remote_branch=task_local_branch,
                    is_fast_forward_only=False,
                    should_abort_on_conflict=should_abort_on_conflict,
                )
                if merge_result.is_merged:
                    return TransferFromTaskToLocalResponse(
                        success=True,
                        reached_operation_or_failure_label="LOCAL_BRANCH_UPDATED_VIA_MERGE",
                        notices=(
                            *notices,
                            MergeActionNotice(
                                kind=MergeActionNoticeKind.SUCCESS,
                                message="Local repository updated.",
                                details=merge_result.raw_output,
                            ),
                        ),
                    )
                else:
                    if local_repo.is_merge_in_progress:
                        # if `merge_result.is_aborted` then we have an expectation mismatch to signal to the user
                        failure_label = "MERGE_CONFLICT"
                        user_alert_title = "Merge conflict"
                        user_alert_message = "Merge created conflicts in your local repo. Resolve them and commit locally to finish—or abort the merge."
                    elif merge_result.is_aborted:
                        failure_label = "MERGE_CONFLICT_AND_ABORTED"
                        user_alert_title = "Merge aborted"
                        user_alert_message = (
                            "Merge aborted due to conflicts, as requested. No changes were applied to your local repo."
                        )
                    elif merge_result.is_stopped_by_uncommitted_changes:
                        # this can only happen in case of a race, the earlier attempted fast-forward merge would have stopped the flow earlier
                        user_alert_title = "Merge not possible"
                        failure_label = "MERGE_STOPPED_BY_UNTRACKED_FILES"
                        user_alert_message = "The merge was blocked by uncommitted changes in your local repository. Commit or remove them and try again"
                    else:
                        failure_label = "MERGE_FAILED_WITHOUT_CONFLICT"
                        user_alert_title = "Merge failed"
                        user_alert_message = "Merge didn't complete, but no conflicts were created."

                    return TransferFromTaskToLocalResponse(
                        success=False,
                        reached_operation_or_failure_label=failure_label,
                        notices=(
                            *notices,
                            MergeActionNotice(
                                kind=MergeActionNoticeKind.ERROR,
                                message=user_alert_message,
                                details=merge_result.raw_output,
                            ),
                        ),
                        missing_decisions=[
                            TransferRepoDecision(
                                id="MERGE_FAILED_ALERT",
                                title=user_alert_title,
                                message=user_alert_message,
                                options=(),  # user can only cancel
                                detailed_context_title=merge_result.description,
                                detailed_context=merge_result.raw_output,
                            )
                        ],
                    )
        else:
            logger.trace("Syncing to a branch that is not checked out locally")
            # fast-forward or forced reset only available
            try:
                # we could first validate that the merge-base matches the local branch HEAD
                # to verify that fast-forward is possible

                # Attempt to fast-forward the unchecked branch, this should always be safe.
                fast_forward_succeeded = local_repo.maybe_fetch_remote_branch_into_local(
                    local_branch=request.target_local_branch,
                    remote=task_repo.get_repo_url(),
                    remote_branch=task_local_branch,
                    dry_run=False,
                    force=False,
                )
                if fast_forward_succeeded:
                    return TransferFromTaskToLocalResponse(
                        success=True,
                        reached_operation_or_failure_label="LOCAL_BRANCH_FAST_FORWARDED",
                        notices=(
                            *notices,
                            MergeActionNotice(
                                kind=MergeActionNoticeKind.SUCCESS,
                                message="Local branch fast-forwarded to that of the Agent.",
                                # FIXME: attach the git log here
                                #  git fetch uses stdout and stderr differently from other commands,
                                #  to extract the full output, a change to writable git interfaces.
                                details=None,
                            ),
                        ),
                    )
                else:
                    forced_fetch_possible = local_repo.maybe_fetch_remote_branch_into_local(
                        local_branch=request.target_local_branch,
                        remote=task_repo.get_repo_url(),
                        remote_branch=task_local_branch,
                        dry_run=True,
                        force=True,
                    )
                    if not forced_fetch_possible:
                        # no idea what's blocking us!
                        return TransferFromTaskToLocalResponse(
                            success=False,
                            reached_operation_or_failure_label="LOCAL_BRANCH_FORCE_FETCH_IMPOSSIBLE",
                            notices=(
                                *notices,
                                MergeActionNotice(
                                    kind=MergeActionNoticeKind.ERROR,
                                    message="Fetching the branch is not possible, even if forced. Try again and contact support if failed.",
                                    # FIXME: attach the git log here
                                    details=None,
                                ),
                            ),
                        )
                    else:
                        overwrite_option = "Overwrite with agent branch"
                        decision_needed = TransferRepoDecision(
                            id="FORCE_FETCH",
                            title="Replace local branch with agent branch?",
                            message="\n\n".join(
                                [
                                    f"Your local branch `{request.target_local_branch}` has diverged from the agent branch `{task_local_branch}`.",
                                    "You can choose to **Overwrite**, which will replace your local branch with the agent branch, **and your local changes will be lost**.",
                                    "\n".join(
                                        [
                                            "If you want Sculptor to perform a merge instead, cancel and either:",
                                            f"check out `{request.target_local_branch}` locally and try again, or",
                                            "push your branch into the agent first then fetch it back.",
                                        ]
                                    ),
                                ]
                            ),
                            options=(
                                TransferRepoDecisionOption(
                                    option=overwrite_option,
                                    is_destructive=True,
                                ),
                            ),
                        )
                        option_selected = decision_needed.resolve_user_choice(request.user_choices)
                        if option_selected is None:
                            return TransferFromTaskToLocalResponse(
                                success=False,
                                notices=(
                                    *notices,
                                    MergeActionNotice(
                                        message="Local branch is diverged. User confirmation to overwrite needed."
                                    ),
                                ),
                                missing_decisions=[decision_needed],
                            )
                        if option_selected.choice != overwrite_option:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Local branch divergent but user's choice is not understood: {option_selected}.",
                            )
                        # TODO: verify that the decision applies to the same operation (before/after local git commits)
                        # FIXME: show the actual git output
                        if local_repo.maybe_fetch_remote_branch_into_local(
                            local_branch=request.target_local_branch,
                            remote=task_repo.get_repo_url(),
                            remote_branch=task_local_branch,
                            dry_run=False,
                            force=True,
                        ):
                            return TransferFromTaskToLocalResponse(
                                success=True,
                                reached_operation_or_failure_label="LOCAL_BRANCH_UPDATE_FORCED",
                                notices=(
                                    *notices,
                                    MergeActionNotice(
                                        kind=MergeActionNoticeKind.WARNING,
                                        message="Local branch updated forcefully to that of the Agent",
                                    ),
                                ),
                            )
                        else:
                            return TransferFromTaskToLocalResponse(
                                success=False,
                                notices=(
                                    *notices,
                                    MergeActionNotice(
                                        kind=MergeActionNoticeKind.ERROR,
                                        message="Local branch could not be forcefully updated to that of the Agent",
                                    ),
                                ),
                                missing_decisions=None,
                            )

            except GitRepoError as e:
                # we are not expecting any errors from the normal fetch operation
                # one reason could be that repo is dead, another is that there was
                # a race and the user has actually checked out this branch while we
                # were attempting to fetch it
                return TransferFromTaskToLocalResponse(
                    success=False,
                    reached_operation_or_failure_label="UNEXPECTED_GIT_FAILURE",
                    notices=(
                        MergeActionNotice(
                            kind=MergeActionNoticeKind.ERROR,
                            message="Unhandled error when performing a git operation.",
                            details=str(e),
                        ),
                    ),
                )


def _commit_changes_in_task(
    user_session: UserSession,
    services: CompleteServiceCollection,
    task_id: TaskID,
    commit_message: str,
) -> None:
    """Triggers a git commit operation in the Task loop and awaits for its result"""
    message_id = AgentMessageID()

    with await_message_response(message_id, task_id, services):
        with user_session.open_transaction(services) as transaction:
            task = services.task_service.get_task(task_id, transaction)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            services.task_service.create_message(
                message=GitCommitAndPushUserMessage(
                    message_id=message_id,
                    commit_message=commit_message,
                    is_pushing=False,
                ),
                task_id=task_id,
                transaction=transaction,
            )


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/git-commit")
def git_commit_in_task(
    project_id: str,
    task_id: str,
    request: Request,
    git_request: GitCommitAndPushRequest,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> None:
    """Triggers a git commit and push operation for the given task."""
    logger.info("Git commit and push requested for task {}", task_id)
    try:
        validated_task_id = TaskID(task_id)
    except typeid.errors.SuffixValidationException as e:
        raise HTTPException(status_code=422, detail="Invalid task ID format") from e

    services = get_services_from_request_or_websocket(request)
    _commit_changes_in_task(
        user_session=user_session,
        services=services,
        task_id=validated_task_id,
        commit_message=git_request.commit_message,
    )


@router.post("/api/v1/testing/cleanup-images")
def trigger_image_cleanup(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
    settings: SculptorSettings = Depends(get_settings),
) -> None:
    """
    Manually trigger image cleanup for testing purposes.
    This endpoint is only available in testing mode.
    """
    # Only allow this endpoint in testing mode
    if not settings.TESTING.INTEGRATION_ENABLED:
        raise HTTPException(status_code=403, detail="This endpoint is only available in testing mode")

    logger.info("Manual image cleanup triggered by user {}", user_session.user_reference)

    services = get_services_from_request_or_websocket(request)
    try:
        services.environment_service.remove_stale_images()
    except Exception as e:
        log_exception(e, "Error during manual image cleanup")
        raise HTTPException(status_code=500, detail=f"Failed to cleanup images: {str(e)}") from e


@router.get("/api/v1/health")
def get_health_check(request: Request) -> HealthCheckResponse:
    services = get_services_from_request_or_websocket(request)
    user_config = services.config_service.get_user_config()
    free_gb = (_get_disk_bytes_free(services.settings) or 1_000_000_000_000) / (1024 * 1024 * 1024)

    root_concurrency_group = get_root_concurrency_group(request)
    with root_concurrency_group.make_concurrency_group(name="get_docker_ram_usage") as concurrency_group:
        sculptor_container_ram_used_bytes, docker_ram_limit_bytes = _get_sculptor_container_ram_used(
            concurrency_group=concurrency_group
        )
    sculptor_container_ram_used_gb = sculptor_container_ram_used_bytes / (1024 * 1024 * 1024)
    docker_ram_limit_gb = docker_ram_limit_bytes / (1024 * 1024 * 1024) if docker_ram_limit_bytes else None
    return HealthCheckResponse(
        version=str(version.__version__),
        free_disk_gb=free_gb,
        min_free_disk_gb=user_config.min_free_disk_gb if user_config else 0,
        free_disk_gb_warn_limit=user_config.free_disk_gb_warn_limit if user_config else 0,
        sculptor_container_ram_used_gb=sculptor_container_ram_used_gb,
        docker_ram_limit_gb=docker_ram_limit_gb,
    )


# The /login and /callback endpoints below are used for the OAuth2 flow with Proof of Key Exchange (PKCE) with Authentik.
# Here's a good description of the flow (even if for a different auth provider):
#   - https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce
#
# We could delegate this to a library like Authlib.
# For now, we didn't do it because:
#   - Authlib's fastAPI integration assumes async setup which we don't use.
#     (It can be circumvented by going to lower-level bits of Authlib but then we don't get that much from the library.)
#   - Authlib's licensing is a little unclear.
#   - And there are typing issues related to dynamic attributes in Authlib.
#
# None of these reasons are too strong. But the implementation below isn't too complex so I didn't feel compelled to switch (yet).


class PostHogEventStamp(telemetry.PosthogEventPayload):
    """A simple wrapper for IDs that can be used for joining and grouping events in PostHog."""

    stamp: str = telemetry.without_consent()


def _get_posthog_event_stamp(code_verifier: str) -> PostHogEventStamp:
    """
    Use the already existing code verifier to generate a unique event ID for PostHog.

    code_verifier is sensitive, so we hash it.

    (We hash twice so that the event_id is not the same as the code_challenge. code_challenge is supposed to not be sensitive but still.)

    """
    stamp = hashlib.sha256(hashlib.sha256(code_verifier.encode()).digest()).hexdigest()[:32]
    return PostHogEventStamp(stamp=stamp)


# TODO: let's double-check if it's fine that this endpoint can be called from the null origin.
@APP.get("/api/v1/auth/login", operation_id="login")
def login(next_path: str = "/", settings: SculptorSettings = Depends(get_settings)) -> RedirectResponse:
    state, code_verifier, code_challenge = generate_pkce_verifier_challenge_and_state()
    PKCE_STORE.set(state, code_verifier, next_path)

    params = {
        "response_type": "code",
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "redirect_uri": get_redirect_url(settings),
        "scope": AUTHENTIK_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    authorization_url = get_authorization_url(settings)
    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.LOGIN_INITIATED,
            component=ProductComponent.AUTH,
            payload=_get_posthog_event_stamp(code_verifier),
        )
    )
    return RedirectResponse(f"{authorization_url}?{urlencode(params)}")


@APP.get("/api/v1/auth/callback", operation_id="authCallback")
async def auth_callback(code: str, state: str, settings: SculptorSettings = Depends(get_settings)) -> RedirectResponse:
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    code_verifier_and_next_path = PKCE_STORE.get(state)
    if code_verifier_and_next_path is None:
        raise HTTPException(status_code=400, detail="Invalid state or expired login")

    # Exchange code for token using PKCE code_verifier (for public clients).
    code_verifier, next_path = code_verifier_and_next_path
    token_url = get_token_url(settings)
    with httpx.Client() as client:
        token_response = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.AUTHENTIK_CLIENT_ID,
                "code": code,
                "redirect_uri": get_redirect_url(settings),
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        if not token_response.is_success:
            raise HTTPException(
                status_code=token_response.status_code,
                detail="Failed to exchange code for tokens",
            )
        tokens = token_response.json()

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    protocol, domain, port = settings.PROTOCOL, settings.DOMAIN, settings.FRONTEND_PORT
    PKCE_STORE.delete(code_verifier)
    redirect_url = f"{protocol}://{domain}:{port}{next_path}?accessToken={access_token}&refreshToken={refresh_token}"
    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.LOGIN_SUCCEEDED,
            component=ProductComponent.AUTH,
            payload=_get_posthog_event_stamp(code_verifier),
        )
    )
    return RedirectResponse(url=redirect_url)


class TokenPair(SerializableModel):
    access_token: str
    refresh_token: str


class RefreshData(SerializableModel):
    refresh_token: str


@APP.post("/api/v1/auth/renew-tokens", operation_id="renewTokens")
async def renew_tokens(refresh_data: RefreshData, settings: SculptorSettings = Depends(get_settings)) -> TokenPair:
    """
    Endpoint to fetch a new access token and a new refresh token using the refresh token stored in a cookie.

    """
    token_url = get_token_url(settings)

    with httpx.Client() as client:
        authentik_response = client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.AUTHENTIK_CLIENT_ID,
                "refresh_token": refresh_data.refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        if not authentik_response.is_success:
            raise HTTPException(
                status_code=authentik_response.status_code,
                detail="Failed to refresh token",
            )
        tokens = authentik_response.json()

    return TokenPair(access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])


@APP.get("/api/v1/auth/logout", operation_id="logout")
async def logout(
    settings: SculptorSettings = Depends(get_settings),
    user_session: UserSession = Depends(get_user_session),
) -> RedirectResponse:
    protocol, domain, port = settings.PROTOCOL, settings.DOMAIN, settings.FRONTEND_PORT
    # When done, redirect to the home page.
    next_url = f"{protocol}://{domain}:{port}/"
    logout_url = get_logout_url(settings, next_url)
    response = RedirectResponse(url=logout_url)
    return response


@APP.get("/api/v1/auth/me", operation_id="currentUser")
async def current_user(user_session: UserSession = Depends(get_user_session)) -> UserInfo | None:
    if user_session.is_anonymous:
        return None
    return UserInfo(user_reference=user_session.user_reference, email=user_session.user_email)


@router.delete("/api/v1/projects/{project_id}/tasks/{task_id}/messages/{message_id}")
def delete_message(
    project_id: ProjectID,
    task_id: TaskID,
    request: Request,
    message_id: AgentMessageID,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Delete a message from the task"""
    services = get_services_from_request_or_websocket(request)
    new_message_id = AgentMessageID()
    with await_message_response(new_message_id, task_id, services):
        with user_session.open_transaction(services) as transaction:
            services.task_service.create_message(
                message=RemoveQueuedMessageUserMessage(message_id=new_message_id, target_message_id=message_id),
                task_id=task_id,
                transaction=transaction,
            )


class FeedbackRequestPayload(telemetry.PosthogEventPayload):
    """Payload for feedback request. All fields are consented to since user has to explicitly submit feedback."""

    feedback_type: str | None = telemetry.without_consent()
    message_id: str | None = telemetry.without_consent()
    comment: str | None = telemetry.without_consent()
    issue_type: str | None = telemetry.without_consent()
    saved_agent_messages_s3_path: str | None = telemetry.without_consent()


class FeedbackSavedAgentMessagesPayload(SerializableModel):
    """Payload for saved agent messages in feedback request.

    This is not used for PostHog events, but to pack and serialize for s3 storage.
    """

    task_id: TaskID
    messages: list[PersistentMessageTypes]


@router.post("/api/v1/projects/{project_id}/tasks/{task_id}/messages/{message_id}/feedback")
def submit_feedback(
    project_id: str,
    task_id: str,
    message_id: str,
    request: Request,
    feedback_request: FeedbackRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Submit feedback for an entire task."""
    validate_project_id(project_id)  # Validate project_id but don't need the result

    try:
        validated_task_id = TaskID(task_id)
    except TypeIDPrefixMismatchError as e:
        raise HTTPException(status_code=422, detail=f"Invalid task ID {e}") from e

    feedback_type = feedback_request.feedback_type
    comment = feedback_request.comment or ""
    issue_type = feedback_request.issue_type or ""

    if feedback_type not in ["positive", "negative"]:
        raise HTTPException(status_code=422, detail="feedback_type must be 'positive' or 'negative'")

    logger.info(
        "Received feedback for task {}: type={} comment='{}' issue_type='{}'",
        validated_task_id,
        feedback_type,
        comment,
        issue_type,
    )

    # Extract all messages for the task to include in the feedback
    # Here we upload to s3 as the payload size could exceed PostHog's 1MB event size limit.
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        all_messages = services.task_service.get_saved_messages_for_task(validated_task_id, transaction)
        logger.trace("Extracted {} messages for task {}", len(all_messages), validated_task_id)
        for message in all_messages:
            logger.trace(
                "Message ID: {}, Source: {}, Text: {}", message.message_id, message.source, message.model_dump()
            )

        s3_bytes = json.dumps(
            FeedbackSavedAgentMessagesPayload(task_id=validated_task_id, messages=list(all_messages)).model_dump_json()
        ).encode("utf-8")

        # Create a S3 upload for the DB transaction contents
        s3_upload_url = upload_to_s3(SculptorPosthogEvent.TASK_USER_FEEDBACK.value, ".json", s3_bytes)

        # Create a Posthog event with the feedback
        posthog_event = telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.TASK_USER_FEEDBACK,
            component=ProductComponent.TASK,
            payload=FeedbackRequestPayload(
                feedback_type=feedback_type,
                message_id=message_id,
                comment=comment,
                issue_type=issue_type,
                saved_agent_messages_s3_path=s3_upload_url,
            ),
            task_id=str(validated_task_id),
        )
        telemetry.emit_posthog_event(posthog_event)


@router.get("/api/v1/ping_sentry")
def ping_sentry(
    user_session: UserSession = Depends(get_user_session),
) -> None:
    log_exception(
        Exception("This is a test logged exception"),
        message="This is a test logged exception",
    )
    raise Exception("This is a test raised exception")


@router.post("/api/v1/projects/{project_id}/set-most-recently-used")
def set_most_recently_used_project(
    project_id: ProjectID,
) -> None:
    update_most_recently_used_project(project_id=project_id)


@router.get("/api/v1/projects/most-recently-used")
def get_most_recently_used_project() -> ProjectID | None:
    return get_most_recently_used_project_id()


@router.delete("/api/v1/projects/{project_id}")
def delete_project(
    project_id: ProjectID,
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Mark a project as deleted."""
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        project = transaction.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        # NOTE: we are unable to delete tasks from inside project service because it doesn't have access to task service
        assert isinstance(transaction, TaskAndDataModelTransaction)
        tasks = transaction.get_tasks_for_project(project_id=project_id, is_archived=False)
        active_tasks = [task for task in tasks if not task.is_deleting and not task.is_deleted]
        logger.info("Deleting {} tasks for project {}", len(active_tasks), project_id)
        for task in active_tasks:
            services.task_service.delete_task(task.object_id, transaction)
        logger.info("Successfully deleted {} tasks for project {}", len(active_tasks), project_id)
        logger.info("Deleting project {}", project_id)
        services.project_service.delete_project(project, transaction)
        logger.info("Successfully deleted project {}", project_id)


@router.get("/api/v1/projects/active")
def get_active_projects(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> tuple[Project, ...]:
    """Get all currently active projects for the session."""

    services = get_services_from_request_or_websocket(request)
    return services.project_service.get_active_projects()


@router.post("/api/v1/projects/initialize")
def initialize_project(
    request: Request,
    initialization_request: ProjectInitializationRequest,
    user_session: UserSession = Depends(get_user_session),
) -> Project:
    project_path = Path(initialization_request.project_path).expanduser()

    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project path does not exist: {project_path}")
    if not project_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Project path is not a directory: {project_path}")

    if not (project_path / ".git").exists():
        if is_path_in_git_repo(project_path):
            raise HTTPException(
                status_code=400,
                detail="Selected directory is inside a git repository. Please select the root of the git repository.",
            )
        raise HTTPException(
            status_code=400,
            detail="Selected directory is not a git repository. Please initialize it first using /api/v1/projects/init-git",
        )

    # ensure we have an initial commit, and if not, offer to create one
    root_concurrency_group = get_root_concurrency_group(request)
    with root_concurrency_group.make_concurrency_group(name="initialize_project") as concurrency_group:
        check_repo = LocalReadOnlyGitRepo(repo_path=project_path, concurrency_group=concurrency_group)
        is_initial_commit_present = check_repo.has_any_commits()

    if not is_initial_commit_present:
        raise HTTPException(
            status_code=409,
            detail="Selected git repository has no commits. Please create an initial commit first.",
        )

    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        project = services.project_service.initialize_project(
            project_path=project_path,
            organization_reference=user_session.organization_reference,
            transaction=transaction,
        )
        services.project_service.activate_project(project)
    return project


@router.get("/api/v1/projects")
def list_projects(
    request: Request,
    user_session: UserSession = Depends(get_user_session),
) -> tuple[Project, ...]:
    services = get_services_from_request_or_websocket(request)
    with user_session.open_transaction(services) as transaction:
        return transaction.get_projects(organization_reference=user_session.organization_reference)


@router.post("/api/v1/projects/init-git")
def initialize_git_repository(
    request: Request,
    init_git_repo_request: InitializeGitRepoRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    """Initialize a directory as a git repository with an initial commit."""
    project_path = Path(init_git_repo_request.project_path).expanduser()

    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project path does not exist: {project_path}")
    if not project_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Project path is not a directory: {project_path}")
    if (project_path / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Directory is already a git repository: {project_path}")

    logger.info("Initializing git repository at: {}", project_path)

    root_concurrency_group = get_root_concurrency_group(request)
    initialization_error: Exception | None = None
    with root_concurrency_group.make_concurrency_group(name="initialize_git_repository") as concurrency_group:
        try:
            # Initialize repository (using global git config for user.email and user.name)
            repo = LocalWritableGitRepo.from_new_repository(
                repo_path=project_path, concurrency_group=concurrency_group
            )
            repo.create_commit("Initial commit", allow_empty=True)
        except (GitRepoError, Exception) as e:
            log_exception(e, "Failed to initialize git repository")
            initialization_error = e

    if initialization_error is not None:
        error_msg = str(initialization_error)
        stderr = getattr(initialization_error, "stderr", None)
        if stderr:
            error_msg = str(stderr)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize git repository: {error_msg}",
        ) from initialization_error


@router.post("/api/v1/projects/create-initial-commit")
def create_initial_commit(
    request: Request,
    create_initial_commit_request: CreateInitialCommitRequest,
    user_session: UserSession = Depends(get_user_session),
) -> None:
    project_path = Path(create_initial_commit_request.project_path).expanduser()

    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project path does not exist: {project_path}")
    if not project_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Project path is not a directory: {project_path}")

    logger.info("Creating initial commit in git repository at: {}", project_path)

    root_concurrency_group = get_root_concurrency_group(request)
    initialization_error: Exception | None = None
    with root_concurrency_group.make_concurrency_group(name="create_initial_commit") as concurrency_group:
        try:
            repo = LocalWritableGitRepo(repo_path=project_path, concurrency_group=concurrency_group)
            repo.stage_all_files()
            repo.create_commit("Initial commit", allow_empty=True)
        except (GitRepoError, Exception) as e:
            initialization_error = e
            log_exception(e, "Failed to create initial commit")
    if initialization_error is not None:
        error_msg = str(initialization_error)
        stderr = getattr(initialization_error, "stderr", None)
        if stderr:
            error_msg = str(stderr)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create initial commit: {error_msg}",
        ) from initialization_error


docker_download_semaphore = Semaphore(2)


@router.post("/api/v1/download_docker_tar_to_cache", status_code=fastapi.status.HTTP_200_OK)
def download_docker_tar_to_cache(
    request: Request,
    response: Response,
    download_docker_tar_request: DownloadDockerTarRequest,
    user_session: UserSession = Depends(get_user_session),
):
    if not docker_download_semaphore.acquire(False):
        # We only allow 2 of these long-running requests at a time
        # so that we don't exhaust all the workers if there's a bug in the frontend that spams this url
        response.status_code = fastapi.status.HTTP_429_TOO_MANY_REQUESTS
        return

    try:
        BASE_UPDATE_URL = "ghcr.io/imbue-ai/"

        if not download_docker_tar_request.url.startswith(BASE_UPDATE_URL):
            logger.info(
                f"download_docker_tar_to_cache got a url that doesn't point at where we make our releases! {download_docker_tar_request.url}"
            )
            response.status_code = fastapi.status.HTTP_400_BAD_REQUEST
            # don't download anything, we don't trust this url
            return

        image_purpose = get_image_purpose_from_url(download_docker_tar_request.url)
        if image_purpose is None:
            logger.info(
                f"download_docker_tar_to_cache got a url without an image_purpose {download_docker_tar_request.url}"
            )
            response.status_code = fastapi.status.HTTP_400_BAD_REQUEST
            # nothing to do, just return
            return

        root_concurrency_group = get_root_concurrency_group(request)
        with root_concurrency_group.make_concurrency_group(name="download_docker_tar") as concurrency_group:
            fetch_image_from_cdn(download_docker_tar_request.url, image_purpose, concurrency_group)
    finally:
        docker_download_semaphore.release()


# Dummy routes to include WebSocket types in OpenAPI schema


@router.get("/_ws_types/streaming_update")
def _ws_type_streaming_update() -> StreamingUpdate:
    """Include StreamingUpdate in schema"""
    raise HTTPException(status_code=501, detail="This endpoint exists only for OpenAPI schema generation")


# we generate UserConfigField at runtime so pyre doesn't like it as an annotation
@router.get("/_types/user_config_field")
def _type_user_config_field() -> UserConfigField:  # pyre-ignore[11]
    """Include UserConfigField enum in schema"""
    raise HTTPException(status_code=501, detail="This endpoint exists only for OpenAPI schema generation")


@router.get("/_element_tags")
def _element_tags() -> ElementIDs:
    """Include UserUpdate in schema"""
    raise HTTPException(status_code=501, detail="This endpoint exists only for OpenAPI schema generation")


APP.include_router(router)
APP.include_router(gateway_router)

# pyre doesn't understand the typing here
APP.add_middleware(SessionTokenMiddleware, settings_factory=get_settings)  # pyre-ignore[6]


# TODO (PROD-2161): either we can remove this or leave it for debugging, it might fail depending on what we change with the build process
# To avoid conflicts with the API routes, we write this route last. This route
# must be loaded _after_ APP.include_router, which performs delayed routing.
@APP.get("/{filename:path}")
def serve_static(filename: str = "index.html") -> StreamingResponse:
    """Serve the static files from frontend-dist, serving "index.html" when no filename is provided"""
    try:
        response = _load_file(filename, resources.files("sculptor") / ".." / "frontend-dist")
    except FileNotFoundError:
        try:
            # try this path instead, is helpful for being able to sensibly run tests locally...
            response = _load_file(filename, resources.files("sculptor") / ".." / "frontend" / "dist")
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=f"File not found: {filename}") from e
    return response


def _load_file(filename: str, static_dir: Traversable) -> StreamingResponse:
    if not filename:
        filename = "index.html"

    initial_file_path = static_dir / filename

    with resources.as_file(initial_file_path) as resolved_initial_file_path:
        if not resolved_initial_file_path.exists():
            # If we don't have the url, return the home page since this is a
            # single-page webapp. The React router should parse the url to
            # render the correct "synthetic" page.
            final_file_path = static_dir / "index.html"
        else:
            final_file_path = initial_file_path

    with resources.as_file(final_file_path) as resolved_final_file_path:
        mime_type, _ = mimetypes.guess_type(resolved_final_file_path)
        response = StreamingResponse(
            create_file_generator(resolved_final_file_path),
            media_type=mime_type,
            headers={"Content-Length": str(resolved_final_file_path.stat().st_size)},
        )
    return response


def create_file_generator(file_path: Path) -> Generator[bytes, None, None]:
    with open(file_path, "rb") as f:
        chunk = f.read(8192)
        while chunk:
            yield chunk
            chunk = f.read(8192)


def _get_disk_bytes_free(settings: SculptorSettings) -> int | None:
    db_path = Path(settings.DATABASE_URL.split("sqlite:///")[-1])
    if not db_path.exists():
        return None
    return psutil.disk_usage(str(db_path)).free


def _get_sculptor_container_ram_used(concurrency_group: ConcurrencyGroup) -> tuple[float, float | None]:
    total_ram_used = 0
    total_docker_limit = None
    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "stats", "--format", "json", "--no-stream"]
        )
        for line in result.stdout.splitlines():
            # strip and check against null b/c it still produces a single newline even if it doesn't give any stats.
            line = line.strip()
            if not line:
                continue
            stat = json.loads(line)
            if stat.get("Name", "").startswith(get_non_testing_environment_prefix()):
                mem_usage = stat.get("MemUsage", "")
                if mem_usage:
                    used, total = mem_usage.split(" / ")
                    total_ram_used += parse_size(used)
                    total_docker_limit = parse_size(total)
    except ProcessError:
        # This is expected to fail if docker is not running.
        pass
    except JSONDecodeError:
        # This can fail to decode if the user has a very old docker version,
        # but then we can't start and warn them about it.
        # So best to just pass and not crash.
        pass
    except Exception as e:
        logger.warning("Got a weird error {} trying to get docker stats: {}", e, traceback.format_exc())
        pass

    return total_ram_used, total_docker_limit
