from threading import Event
from typing import Callable
from typing import ParamSpec
from typing import Protocol
from typing import TypeVar

# TODO: Deduplicate this with APP.shutdown_event.
GLOBAL_SHUTDOWN_EVENT = Event()


P = ParamSpec("P")
T1 = TypeVar("T1", covariant=True)
T2 = TypeVar("T2")


class CancellableFunction(Protocol[P, T1]):
    def __call__(self, shutdown_event: Event, *args: P.args, **kwargs: P.kwargs) -> T1: ...


def globally_cancellable(function: CancellableFunction[P, T2]) -> Callable[P, T2]:
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T2:
        return function(GLOBAL_SHUTDOWN_EVENT, *args, **kwargs)

    return wrapper
