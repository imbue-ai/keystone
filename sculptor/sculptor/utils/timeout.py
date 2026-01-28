import threading
import time
from contextlib import contextmanager
from enum import Enum
from functools import wraps
from typing import Any
from typing import Callable
from typing import Generator
from typing import ParamSpec
from typing import TypeVar

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.errors import ExpectedError
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent

# Threshold for TIMING LOG messages - only log if duration exceeds this value
TIMING_LOG_THRESHOLD_SECONDS: float = 0.05  # 50ms

P = ParamSpec("P")
T = TypeVar("T")


def format_timing_log(
    function_name: str,
    duration: float,
    is_operation_successful: bool = True,
    attributes: dict[str, Any] | None = None,
) -> str:
    """
    Format a timing log message in a machine-parseable format.

    Format: TIMING_LOG, function=<name>, duration_s=<00.000000>, status=<success|failed>[, attributes=<dict>]
    """
    status = "success" if is_operation_successful else "failed"
    parts = [
        "TIMING_LOG",
        f"function={function_name}",
        f"duration_s={duration:09.6f}",
        f"status={status}",
    ]
    if attributes:
        parts.append(f"attributes={attributes}")
    return ", ".join(parts)


class TimeoutException(ExpectedError):
    pass


class TimingAttributes(MutableModel):
    """
    Wrapper for timing attributes dictionary used in log_runtime context manager.
    Provides a type-safe way to set timing attributes.
    """

    _attributes: dict[str, bool | float | int | Enum] = PrivateAttr(default_factory=dict)

    def set_attribute(self, key: str, value: bool | float | int | Enum) -> None:
        """Set a timing attribute with the given key and value."""
        self._attributes[key] = value


class RuntimeMeasurementPayload(PosthogEventPayload):
    function_name: str = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Name of the function being measured"
    )
    duration_seconds: float = with_consent(ConsentLevel.PRODUCT_ANALYTICS, description="Runtime duration in seconds")
    is_operation_successful: bool = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS,
        description="Whether the operation succeeded or caused an exception",
    )
    attributes: dict[str, Any] = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Extra details about the runtime measurement"
    )


def monitor_thread(timeout: float, finished_event: threading.Event, on_timeout: Callable[[float], None]) -> None:
    if not finished_event.wait(timeout):
        on_timeout(timeout)


def raise_timeout_exception(timeout: float) -> None:
    raise TimeoutException(f"Timeout of {timeout}s exceeded")


@contextmanager
def timeout_monitor(
    concurrency_group: ConcurrencyGroup, timeout: float, on_timeout: Callable[[float], None] = raise_timeout_exception
) -> Generator[None, None, None]:
    finished_event = threading.Event()
    monitor = concurrency_group.start_new_thread(target=monitor_thread, args=(timeout, finished_event, on_timeout))
    try:
        yield
    finally:
        finished_event.set()
        monitor.join()


@contextmanager
def log_runtime(function_name: str) -> Generator[TimingAttributes, None, None]:
    is_operation_successful = False
    timing_attributes_for_posthog = TimingAttributes()
    start_time = time.monotonic()
    try:
        yield timing_attributes_for_posthog
        is_operation_successful = True
    finally:
        end_time = time.monotonic()
        duration = end_time - start_time
        timing_details_for_posthog = timing_attributes_for_posthog._attributes
        if duration >= TIMING_LOG_THRESHOLD_SECONDS:
            logger.debug(
                format_timing_log(
                    function_name,
                    duration,
                    is_operation_successful,
                    timing_details_for_posthog if timing_details_for_posthog else None,
                )
            )

        # Emit PostHog event for runtime tracking
        try:
            payload = RuntimeMeasurementPayload(
                function_name=function_name,
                duration_seconds=duration,
                is_operation_successful=is_operation_successful,
                attributes=timing_details_for_posthog,
            )
            emit_posthog_event(
                PosthogEventModel(
                    name=SculptorPosthogEvent.RUNTIME_MEASUREMENT,
                    component=ProductComponent.CROSS_COMPONENT,
                    payload=payload,
                )
            )
        except Exception as e:
            # Don't let PostHog errors break the original function
            logger.debug("Failed to emit PostHog runtime event: {}", e)


# when we upgrade to python 3.12 we can make this function generic!
def log_runtime_decorator(label: str | None = None) -> Callable[[Callable[P, T]], Callable[P, T]]:  # pyre-fixme[34]
    """
    Decorator version of log_runtime context manager.

    Usage:
        @log_runtime_decorator("processing data")
        def my_function():
            # function code

        @log_runtime_decorator()  # Uses function name as label
        def another_function():
            # function code
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            func_label = label if label is not None else func.__name__
            with log_runtime(func_label):
                return func(*args, **kwargs)

        return wrapper

    return decorator


class IntervalTimer:
    def __init__(self):
        self._base_time = time.monotonic()

    def get_and_restart(self) -> float:
        now = time.monotonic()
        elapsed_time = now - self._base_time
        self._base_time = now
        return elapsed_time
