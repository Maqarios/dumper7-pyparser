"""Read-only ordered mapping that also answers attribute access."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Generic, TypeVar, overload

T = TypeVar("T")
_D = TypeVar("_D")
_MISSING = object()


class Namespace(Mapping[str, T], Generic[T]):
    """Ordered, read-only ``str -> T`` mapping with attribute access.

    ``ns.Foo`` is the same as ``ns["Foo"]``. If a key is not present verbatim,
    a unique key ending in ``::Foo`` (Dumper-7's ``Package::Name`` form for
    colliding names) is accepted as well; two such candidates raise ``KeyError``.

    Attribute access can never shadow a key: Python resolves real attributes
    and methods (``keys``, ``get``, ...) before ``__getattr__`` runs, so a key
    with one of those names is reachable only through ``ns["keys"]``.
    """

    __slots__ = ("_data", "_label")

    def __init__(self, data: Mapping[str, T] | None = None, *, label: str = "namespace") -> None:
        object.__setattr__(self, "_data", dict(data) if data else {})
        object.__setattr__(self, "_label", label)

    # -- mapping protocol ---------------------------------------------------

    def __getitem__(self, key: str) -> T:
        data = self._data
        if key in data:
            return data[key]
        suffix = "::" + key
        candidates = [k for k in data if k.endswith(suffix)]
        if len(candidates) == 1:
            return data[candidates[0]]
        if candidates:
            raise KeyError(f"{key!r} is ambiguous in {self._label}; candidates: {sorted(candidates)}")
        raise KeyError(f"{key!r} not found in {self._label}")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key in self._data:
            return True
        try:
            self[key]
        except KeyError:
            return False
        return True

    @overload
    def get(self, key: str) -> T | None: ...
    @overload
    def get(self, key: str, default: _D) -> T | _D: ...

    def get(self, key: str, default=None):  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    # -- attribute access ---------------------------------------------------

    def __getattr__(self, name: str) -> T:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(str(exc)) from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is read-only")

    def __dir__(self) -> list[str]:
        names = set(super().__dir__())
        names.update(k for k in self._data if k.isidentifier())
        return sorted(names)

    # -- misc ----------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<Namespace {self._label}: {len(self._data)} entries>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Namespace):
            return self._data == other._data
        if isinstance(other, Mapping):
            return self._data == dict(other)
        return NotImplemented

    def __hash__(self) -> int:  # Mapping sets __hash__ = None; keep it unhashable explicitly
        raise TypeError("Namespace is unhashable")


EMPTY: Namespace = Namespace(label="empty")
