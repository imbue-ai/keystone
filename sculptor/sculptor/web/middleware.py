import inspect
from contextlib import asynccontextmanager
from functools import wraps
from threading import Event
from threading import Thread
from typing import Any
from typing import Callable

from fastapi import APIRouter
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer
from loguru import logger
from pydantic import alias_generators
from starlette import status
from starlette.requests import HTTPConnection
from starlette.requests import Request
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import is_live_debugging
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from imbue_core.s3_uploader import wait_for_s3_uploads
from imbue_core.sculptor.user_config import UserConfig
from sculptor.config.settings import SculptorSettings
from sculptor.primitives.constants import ANONYMOUS_ORGANIZATION_REFERENCE
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.service_collections.service_collection import get_services
from sculptor.services.config_service.api import ConfigService
from sculptor.services.project_service.default_implementation import update_most_recently_used_project
from sculptor.utils.errors import set_sentry_user_for_current_scope
from sculptor.utils.shutdown import GLOBAL_SHUTDOWN_EVENT
from sculptor.web.auth import InvalidTokenError
from sculptor.web.auth import UserSession
from sculptor.web.auth import authenticate
from sculptor.web.auth import authenticate_anonymous
from sculptor.web.streams import ServerStopped

# Don't use auto_error since fastAPI seems to think 403 is the appropriate response in case of missing auth but it's actually 401.
SECURITY = HTTPBearer(auto_error=False)


def mount_static_files(app: FastAPI, static_directory: str) -> None:
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend-dist")


# TODO: we can probably @cache this rather than rebuild every request
# Note that this is overridden in tests to use the test settings
def get_settings() -> SculptorSettings:
    return SculptorSettings()


_DEFAULT_EVENT = Event()


def shutdown_event() -> Event:
    return _DEFAULT_EVENT


# This is the dependency that actually creates the service collection when the application starts up.
# (The service collection is then stored in the app state for later use.)
def services_factory(
    root_concurrency_group: ConcurrencyGroup, settings: SculptorSettings = Depends(get_settings)
) -> CompleteServiceCollection:
    return get_services(root_concurrency_group, settings)


# This is a convenience function to get the already created services from the app state.
def get_services_from_request_or_websocket(request_or_websocket: Request | WebSocket) -> CompleteServiceCollection:
    return request_or_websocket.app.state.services


def get_root_concurrency_group(request_or_websocket: Request | WebSocket) -> ConcurrencyGroup:
    return request_or_websocket.app.state.root_concurrency_group


def get_user_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(SECURITY),
    settings: SculptorSettings = Depends(get_settings),
) -> UserSession:
    services = get_services_from_request_or_websocket(request)
    return _get_user_session(
        request=request,
        credentials=credentials,
        services=services,
        settings=settings,
    )


def get_user_session_for_websocket(
    websocket: WebSocket,
    settings: SculptorSettings = Depends(get_settings),
) -> UserSession:
    services = get_services_from_request_or_websocket(websocket)
    return _get_user_session(
        request=websocket,
        credentials=None,
        services=services,
        settings=settings,
    )


def _get_user_session(
    request: HTTPConnection,
    credentials: HTTPAuthorizationCredentials | None,
    services: CompleteServiceCollection,
    settings: SculptorSettings,
) -> UserSession:
    header_request_id = request.headers.get("Sculptor-Request-ID", None)
    if header_request_id is None:
        request_id = RequestID()
    else:
        request_id = RequestID(header_request_id)
    access_token: str | None = None
    if credentials is not None:
        access_token = credentials.credentials
    elif "jwt" in request.query_params:
        # Support JWT in query parameters for EventSource connections which cannot supply headers.
        access_token = request.query_params["jwt"]

    if access_token is None and not settings.ALLOW_ANONYMOUS_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if access_token is not None:
        try:
            user_session = authenticate(json_web_token=access_token, services=services, request_id=request_id)
        except InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        # CSRF-like vulnerabilities are mitigated using the SessionTokenMiddleware.
        user_session = authenticate_anonymous(services, request_id)
    # FIXME: after we move to actually being logged in, we should get the user email from the session maybe?
    #       set_sentry_user_for_current_scope(user_session.user_email)
    #  for now we get it from the current config if that exists:
    current_config = services.config_service.get_user_config()
    user_email = user_session.user_email
    user_id = user_session.user_reference
    if current_config is not None and current_config.user_email:
        user_email = current_config.user_email
        user_id = current_config.user_id
    # NOTE: this is slightly redundant with global configuration of Sentry in the app startup and after user
    #       sets their email. Clean it up if we ever move to a multi-user system.
    set_sentry_user_for_current_scope(
        user_email=user_email,
        user_id=user_id,
    )
    user_session.logger_kwargs.update(
        dict(
            request_id=str(user_session.request_id),
            user_reference=str(user_session.user_reference),
            route=request.url.path,
        )
    )
    return user_session


class DecoratedAPIRouter(APIRouter):
    def __init__(self, *args, decorator=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.decorator = decorator

    # pyre-ignore[14]: we're using kwargs instead of spelling out every keyword argument here, but Pyre mistakenly thinks it's not consistent with the overridden method
    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs):
        if "operation_id" not in kwargs or kwargs["operation_id"] is None:
            kwargs["operation_id"] = alias_generators.to_camel(endpoint.__name__)

        if self.decorator:
            endpoint = self.decorator(endpoint)
        return super().add_api_route(path, endpoint, **kwargs)


def add_logging_context(func):
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Get the user_session from the function's kwargs or bound arguments
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        user_session: UserSession | None = bound.arguments.get("user_session")

        if user_session is None:
            # If not in kwargs, try to find it positionally
            for param_name, param in sig.parameters.items():
                if param.annotation.__name__ == "UserSession" or param_name == "user_session":
                    user_session = bound.arguments.get(param_name)
                    break

        if user_session is None:
            # Some endpoints allow anonymous access and that's fine.
            return run_sync_function_with_debugging_support_if_enabled(func, args, kwargs)

        with logger.contextualize(**user_session.logger_kwargs):
            return run_sync_function_with_debugging_support_if_enabled(func, args, kwargs)

    return sync_wrapper


def run_sync_function_with_debugging_support_if_enabled(func, args, kwargs):
    """
    If we are not debugging, then we run the function directly and return the result.

    If we are debugging, then we run the function in a thread.
    This allows the debugger to catch unhandled exceptions.
    Without this, fastapi and uvicorn end up returning a 500 instead of raising,
    so the auto-attach behavior doesn't work.

    This function *should* be called in each place that we call a sync function.
    """
    if is_live_debugging():
        output_container = []
        thread = Thread(
            target=_run_in_thread_so_that_unhandled_exceptions_can_be_caught_by_a_debugger,
            args=(func, args, kwargs, output_container),
        )
        thread.start()
        thread.join()
        result = only(output_container)
        if isinstance(result, BaseException):
            raise result
        return result
    # in the normal case, we're already in a thread in an async context anyway, so just call the function
    else:
        return func(*args, **kwargs)


# simply part of the implementation of `run_sync_function_with_debugging_support_if_enabled`, see docstring there
def _run_in_thread_so_that_unhandled_exceptions_can_be_caught_by_a_debugger(
    func, args, kwargs, output_container
) -> None:
    try:
        result = func(*args, **kwargs)
    except (HTTPException, ServerStopped) as e:
        output_container.append(e)
    except BaseException as e:
        output_container.append(e)
        # these we re-raise, since we want the debugger to catch them
        raise
    else:
        output_container.append(result)


on_startup_callback = lambda: None


def register_on_startup(callback: Callable) -> None:
    global on_startup_callback
    on_startup_callback = callback


class App(FastAPI):
    # pyre-ignore[13]: Pyre doesn't like uninitialized fields; we are in fact initializing this field, just in a hacky outside the __init__ method.
    shutdown_event: Event


@asynccontextmanager
async def lifespan(app: App):
    """
    Formerly `@app.on_event("startup")`, this is used to initialize the application.
    (It has to be async.)

    """
    if get_settings in app.dependency_overrides:
        settings = app.dependency_overrides[get_settings]()
    else:
        settings = get_settings()

    config_service: ConfigService | None = None
    try:
        with ConcurrencyGroup(name="lifespan") as root_concurrency_group:
            if services_factory in app.dependency_overrides:
                services = app.dependency_overrides[services_factory](root_concurrency_group, settings)
            else:
                services = services_factory(root_concurrency_group, settings)
            with services.run_all():
                config_service = services.config_service
                app.state.services = services
                app.state.root_concurrency_group = root_concurrency_group
                if shutdown_event in app.dependency_overrides:
                    event = app.dependency_overrides[shutdown_event]()
                else:
                    event = shutdown_event()
                app.shutdown_event = event
                # activate all known projects
                with services.data_model_service.open_transaction(request_id=RequestID()) as transaction:
                    for project in transaction.get_projects():
                        services.project_service.activate_project(project)

                # Set initial project if provided via CLI by setting it as the most recently used project.
                initial_project_path = getattr(app.state, "initial_project", None)
                if initial_project_path:
                    logger.info("Setting initial project from CLI: {}", initial_project_path)

                    with services.data_model_service.open_transaction(request_id=RequestID()) as transaction:
                        project = services.project_service.initialize_project(
                            project_path=initial_project_path,
                            organization_reference=ANONYMOUS_ORGANIZATION_REFERENCE,
                            transaction=transaction,
                        )
                        services.project_service.activate_project(project)
                        update_most_recently_used_project(project_id=project.object_id)

                if settings.SERVE_STATIC_FILES_DIR is not None:
                    mount_static_files(app, settings.SERVE_STATIC_FILES_DIR)

                logger.info("Using DB: {}", services.settings.DATABASE_URL)

                logger.info("Server is ready to accept requests!")
                on_startup_callback()
                yield
                # Set the global IS_SHUTTING_DOWN flag as the first thing so that all the threads immediately know we are shutting down.
                # (Even before the context managers exit handlers are called.)
                GLOBAL_SHUTDOWN_EVENT.set()
    finally:
        GLOBAL_SHUTDOWN_EVENT.set()
        user_config: UserConfig | None = None
        try:
            if config_service is not None:
                user_config = config_service.get_user_config()
        except Exception as e:
            log_exception(e, "Error while shutting down services and concurrency groups")
            raise
        finally:
            try:
                if user_config and user_config.is_error_reporting_enabled:
                    wait_for_s3_uploads(5.0, is_shutting_down=True)
            except Exception as e:
                log_exception(e, "Error in waiting for S3 uploads")
                raise
