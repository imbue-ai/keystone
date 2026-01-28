import itertools
from typing import Any
from typing import Callable
from typing import Iterable
from typing import ParamSpec
from typing import Protocol
from typing import TypeVar

T = TypeVar("T")

P = ParamSpec("P")
R = TypeVar("R")


class _SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...


TK = TypeVar("TK", bound=_SupportsLessThan)
TV = TypeVar("TV")


def first(iterable: Iterable[T]) -> T | None:
    return next(iter(iterable), None)


def group_by_helper(data: Iterable[TV], get_key: Callable[[TV], TK]) -> dict[TK, list[TV]]:
    data = sorted(data, key=get_key)
    return {k: list(g) for k, g in itertools.groupby(data, get_key)}
