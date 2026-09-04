"""Parsed representations of Dumpspace entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from ._namespace import EMPTY, Namespace
from .types import TypeKind, TypeRef

if TYPE_CHECKING:
    from .dump import Dump


@dataclass(slots=True)
class FileInfo:
    """Envelope metadata of one ``*Info.json`` file."""

    updated_at: datetime | None = None
    version: int | None = None
    credit: dict[str, Any] | None = None
    path: Path | None = None


@dataclass(slots=True)
class Member:
    """One field of a class or struct."""

    name: str
    owner: str
    type: TypeRef
    offset: int
    size: int
    array_dim: int = 1
    bit_offset: int | None = None

    @property
    def is_bitfield(self) -> bool:
        return self.bit_offset is not None

    @property
    def element_size(self) -> int:
        """Size of one element for fixed arrays (``size`` is the whole array)."""
        return self.size // self.array_dim if self.array_dim > 0 else self.size

    def __int__(self) -> int:
        return self.offset

    def __index__(self) -> int:
        return self.offset

    def __str__(self) -> str:
        suffix = f"[{self.array_dim}]" if self.array_dim > 1 else ""
        bits = f" : bit {self.bit_offset}" if self.bit_offset is not None else ""
        return f"{self.type} {self.name}{suffix} @ 0x{self.offset:X} (size 0x{self.size:X}){bits}"


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: TypeRef

    @property
    def is_reference(self) -> bool:
        return self.type.is_reference

    def __str__(self) -> str:
        return f"{self.type} {self.name}".strip()


@dataclass(slots=True)
class Function:
    """One UFunction of a class."""

    name: str
    owner: str
    return_type: TypeRef
    params: tuple[Param, ...]
    offset: int
    flags_raw: str

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(f for f in self.flags_raw.split("|") if f)

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags

    @property
    def signature(self) -> str:
        return f"{self.return_type} {self.name}({', '.join(str(p) for p in self.params)})"

    def __int__(self) -> int:
        return self.offset

    def __index__(self) -> int:
        return self.offset

    def __str__(self) -> str:
        return f"{self.signature} @ 0x{self.offset:X}"


@dataclass(slots=True)
class EnumDef:
    """A UEnum: name, underlying integer type, and ordered ``name -> value`` map."""

    name: str
    underlying_type: str
    values: Namespace[int]

    def __getattr__(self, name: str) -> int:
        if name.startswith("_"):  # never resolve private/dunder names against enumerators
            raise AttributeError(name)
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(f"enum {self.name}: {exc}") from None

    def __getitem__(self, name: str) -> int:
        return self.values[name]

    def __contains__(self, name: object) -> bool:
        return name in self.values

    def __iter__(self) -> Iterator[tuple[str, int]]:
        return iter(self.values.items())

    def __len__(self) -> int:
        return len(self.values)

    def name_of(self, value: int) -> str | None:
        """First enumerator name with this value, or ``None``."""
        for name, v in self.values.items():
            if v == value:
                return name
        return None

    def __str__(self) -> str:
        return f"enum {self.name} : {self.underlying_type} ({len(self.values)} values)"


@dataclass(slots=True, eq=False)
class Struct:
    """A UClass or UScriptStruct (both share one layout in Dumpspace)."""

    name: str
    kind: TypeKind
    size: int
    parents: tuple[str, ...]
    own_members: Namespace[Member]
    functions: Namespace[Function] = field(default_factory=lambda: EMPTY)
    _dump: "Dump | None" = field(default=None, repr=False)
    _members_cache: Namespace[Member] | None = field(default=None, repr=False)
    _functions_cache: Namespace[Function] | None = field(default=None, repr=False)

    # -- hierarchy ------------------------------------------------------------

    @property
    def is_class(self) -> bool:
        return self.kind is TypeKind.CLASS

    @property
    def parent_name(self) -> str | None:
        return self.parents[0] if self.parents else None

    @property
    def parent(self) -> "Struct | None":
        return self._lookup(self.parent_name) if self.parents else None

    def ancestors(self) -> Iterator["Struct"]:
        """Parents in order, most-derived first. Names missing from the dump are skipped."""
        for name in self.parents:
            found = self._lookup(name)
            if found is not None:
                yield found

    def is_subclass_of(self, name: str) -> bool:
        return name in self.parents or any(p.endswith("::" + name) for p in self.parents)

    def _lookup(self, name: str | None) -> "Struct | None":
        if name is None or self._dump is None:
            return None
        return self._dump.find_struct(name)

    # -- members --------------------------------------------------------------

    @property
    def members(self) -> Namespace[Member]:
        """All members including inherited ones; the most-derived declaration wins."""
        cached = self._members_cache
        if cached is not None:
            return cached
        if not self.parents:
            merged = self.own_members
        else:
            data: dict[str, Member] = {}
            for ancestor in reversed(list(self.ancestors())):
                data.update(ancestor.own_members)
            data.update(self.own_members)
            merged = Namespace(data, label=f"members of {self.name}")
        self._members_cache = merged
        return merged

    def find_member(self, name: str) -> tuple[Member, "Struct"] | None:
        """Return ``(member, declaring struct)`` walking the inheritance chain."""
        if name in self.own_members:
            return self.own_members[name], self
        for ancestor in self.ancestors():
            if name in ancestor.own_members:
                return ancestor.own_members[name], ancestor
        return None

    # -- functions ------------------------------------------------------------

    @property
    def all_functions(self) -> Namespace[Function]:
        """Own plus inherited functions; the most-derived declaration wins."""
        cached = self._functions_cache
        if cached is not None:
            return cached
        if not self.parents:
            merged = self.functions
        else:
            data: dict[str, Function] = {}
            for ancestor in reversed(list(self.ancestors())):
                data.update(ancestor.functions)
            data.update(self.functions)
            merged = Namespace(data, label=f"functions of {self.name}")
        self._functions_cache = merged
        return merged

    def find_function(self, name: str) -> tuple[Function, "Struct"] | None:
        """Return ``(function, declaring struct)`` walking the inheritance chain."""
        if name in self.functions:
            return self.functions[name], self
        for ancestor in self.ancestors():
            if name in ancestor.functions:
                return ancestor.functions[name], ancestor
        return None

    # -- attribute-style access (members first, then functions) ---------------

    def __getattr__(self, name: str) -> Member | Function:
        if name.startswith("_"):  # guards against recursion if a slot is unset (copy/pickle)
            raise AttributeError(name)
        member = self.members.get(name)
        if member is not None:
            return member
        func = self.all_functions.get(name)
        if func is not None:
            return func
        raise AttributeError(f"{self.name} has no member or function {name!r}")

    def __getitem__(self, name: str) -> Member | Function:
        try:
            return self.members[name]
        except KeyError:
            pass
        try:
            return self.all_functions[name]
        except KeyError:
            raise KeyError(f"{self.name} has no member or function {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self.members or name in self.all_functions

    def __dir__(self) -> list[str]:
        names = set(object.__dir__(self))
        names.update(k for k in self.members if k.isidentifier())
        names.update(k for k in self.all_functions if k.isidentifier())
        return sorted(names)

    def __repr__(self) -> str:
        kw = "class" if self.is_class else "struct"
        base = f" : {self.parent_name}" if self.parents else ""
        return f"<{kw} {self.name}{base} size=0x{self.size:X} members={len(self.own_members)}>"

    __str__ = __repr__
