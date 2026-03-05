"""Custom syrupy extension for soft-field snapshot testing.

Stores ALL fields in the snapshot file (for eyeballing diffs with --snapshot-update),
but only enforces matching on non-soft fields. Soft fields are present in the persisted
snapshot for human review but won't cause assertion failures if they differ.
"""

from __future__ import annotations

import datetime

from syrupy.extensions.amber import AmberSnapshotExtension


class _TzInfo(datetime.tzinfo):
    """Minimal tzinfo shim for parsing syrupy's ``TzInfo(offset)`` in snapshots."""

    def __init__(self, offset: int | float) -> None:
        self._offset = datetime.timedelta(hours=offset)

    def utcoffset(self, dt: datetime.datetime | None = None) -> datetime.timedelta:
        return self._offset

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _TzInfo):
            return self._offset == other._offset
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._offset)

    def __repr__(self) -> str:
        return f"TzInfo({self._offset.total_seconds() / 3600})"


# Namespace for eval — only the constructors syrupy uses in .ambr files.
_AMBR_EVAL_NS: dict[str, object] = {
    "dict": dict,
    "list": list,
    "datetime": datetime,
    "TzInfo": _TzInfo,
}


def _ambr_to_python(s: str) -> object:
    """Parse .ambr serialized format back to a Python object.

    The .ambr format uses ``dict({...})``, ``list([...])``, and standard Python
    literals plus ``datetime.datetime(...)`` and ``TzInfo(...)``.
    """
    return eval(s, {"__builtins__": {}}, _AMBR_EVAL_NS)  # noqa: S307


def _strip_paths(data: object, paths: frozenset[str]) -> object:
    """Remove fields at dot-separated paths from a nested dict.

    >>> _strip_paths({"a": 1, "b": {"c": 2, "d": 3}}, frozenset({"a", "b.c"}))
    {'b': {'d': 3}}
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        # Collect paths that start with this key
        exact = False
        nested: set[str] = set()
        for p in paths:
            if p == key:
                exact = True
                break
            if p.startswith(key + "."):
                nested.add(p[len(key) + 1 :])

        if exact:
            continue  # soft field — omit from comparison

        if nested:
            frozen_nested = frozenset(nested)
            if isinstance(value, dict):
                result[key] = _strip_paths(value, frozen_nested)
            elif isinstance(value, list):
                result[key] = [
                    _strip_paths(item, frozen_nested) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        else:
            result[key] = value

    return result


class SoftAmberExtension(AmberSnapshotExtension):
    """Amber snapshot extension that ignores *soft* fields during comparison.

    Subclass this and set ``soft_fields`` to a ``frozenset`` of dot-separated
    paths that should be stored in the snapshot but not enforced::

        class MyExt(SoftAmberExtension):
            soft_fields = frozenset({"agent.cost", "agent.duration_seconds"})

        assert data == snapshot(extension_class=MyExt)

    On ``--snapshot-update``, the full data (including soft fields) is written.
    On normal runs, soft fields are stripped from *both* sides before comparison.
    """

    soft_fields: frozenset[str] = frozenset()

    def matches(self, *, serialized_data: str, snapshot_data: str) -> bool:
        if not self.soft_fields:
            return super().matches(serialized_data=serialized_data, snapshot_data=snapshot_data)

        try:
            current = _ambr_to_python(serialized_data)
            stored = _ambr_to_python(snapshot_data)
        except Exception:
            # Parsing failed — fall back to exact string comparison
            return super().matches(serialized_data=serialized_data, snapshot_data=snapshot_data)

        current_stripped = _strip_paths(current, self.soft_fields)
        stored_stripped = _strip_paths(stored, self.soft_fields)
        return current_stripped == stored_stripped
