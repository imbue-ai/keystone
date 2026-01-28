import time
from functools import wraps
from typing import Callable
from typing import Concatenate
from typing import ParamSpec
from typing import TypeVar

from loguru import logger

from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.subprocess_utils import ProcessError
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.provider_status import DownStatus
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.services.environment_service.providers.docker.errors import ProviderIsDownError
from sculptor.services.environment_service.providers.docker.statuses import DockerDaemonNotRunningStatus
from sculptor.services.environment_service.providers.docker.statuses import DockerNotAvailableStatus
from sculptor.services.environment_service.providers.docker.statuses import DockerPermissionDeniedStatus

# We want the decorator to be able to wrap any `Environment` method and preserve its signature.
#
# Without using `ParamSpec` here, the type checker will treat the parameter types of the
# wrapped function as `Any` which reduces the usefulness of type checking.
P = ParamSpec("P")
T = TypeVar("T")
EnvironmentT = TypeVar("EnvironmentT", bound=Environment)


def _is_environment_error_transient(environment_error: Exception) -> bool:
    return False


def check_provider_health_on_failure(
    # `Concatenate` is used here to indicate that the first argument must be of type `EnvironmentT`
    # (i.e., the `self` argument of an instance method), and the rest are the original parameters of the wrapped function.
    func: Callable[Concatenate[EnvironmentT, P], T],
    retries_on_transient_error: int = 3,
    secs_between_retries: float = 0.5,
) -> Callable[Concatenate[EnvironmentT, P], T]:
    """
    Decorator for Environment methods that retries on transient errors and
    runs a provider health check after each failure.

    If the health check indicates the provider is down, it replaces the original
    exception with a ProviderError containing the health check details.
    """

    @wraps(func)
    def wrapper(self: EnvironmentT, *args: P.args, **kwargs: P.kwargs) -> T:
        last_error: Exception | None = None
        for attempt in range(retries_on_transient_error):
            try:
                return func(self, *args, **kwargs)
            except Exception as original_error:
                last_error = original_error

                # Run health check if configured
                provider_health_check = self._provider_health_check
                if provider_health_check is not None:
                    try:
                        logger.debug("Checking provider health: {}", type(self))
                        health_status = provider_health_check()
                    except Exception as health_check_error:
                        log_exception(original_error, message="Provider health check failed")
                        raise health_check_error

                    if isinstance(health_status, DownStatus):
                        logger.debug("Provider is down")
                        details_msg = f" (details: {health_status.details})" if health_status.details else ""
                        raise ProviderIsDownError(
                            f"Provider is unavailable: {health_status.message}{details_msg}"
                        ) from original_error

                    logger.debug("Provider health check passed")
                if not _is_environment_error_transient(original_error):
                    raise
                time.sleep(secs_between_retries)
        if last_error is None:
            raise ValueError("Environment function did not succeed or run provider health checks")
        raise last_error

    return wrapper


def get_docker_status(concurrency_group: ConcurrencyGroup) -> ProviderStatus:
    """
    Get the current status of the Docker provider.

    Returns:
        ProviderStatus: The current status of the Docker provider.
    """
    try:
        concurrency_group.run_process_to_completion(
            command=["docker", "ps"],
            timeout=15.0,
        )
        return OkStatus(message="Docker is available")
    except ProcessError as e:
        error_msg = str(e).lower()
        if "permission denied" in error_msg:
            return DockerPermissionDeniedStatus()
        elif "cannot connect" in error_msg or "daemon" in error_msg:
            return DockerDaemonNotRunningStatus()
        else:
            return DockerNotAvailableStatus(message=str(e))
