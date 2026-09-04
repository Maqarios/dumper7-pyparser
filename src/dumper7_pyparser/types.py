"""Type references as encoded in Dumpspace type arrays."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DumpFormatError(ValueError):
    """Raised when a dump file does not match the Dumpspace layout."""


class TypeKind(str, Enum):
    """The one-letter kind tag DSGen writes as the second element of a type array."""

    DEFAULT = "D"   # primitives and anything the dumper could not classify
    STRUCT = "S"
    CLASS = "C"
    ENUM = "E"
    FUNCTION = "F"  # only used for function definitions themselves

    @classmethod
    def from_letter(cls, letter: Any) -> "TypeKind":
        try:
            return cls(letter)
        except ValueError:
            raise DumpFormatError(f"unknown type kind letter {letter!r}") from None


@dataclass(frozen=True, slots=True)
class TypeRef:
    """One ``[typeName, kind, extendedType, [subTypes...]]`` array.

    ``name`` never contains ``*``; pointer-ness is carried by ``extended``.
    Template arguments (``TArray<AActor*>``) are in ``sub_types``.
    ``is_reference`` is only meaningful for function parameters.
    """

    name: str
    kind: TypeKind = TypeKind.DEFAULT
    extended: str = ""
    sub_types: tuple["TypeRef", ...] = field(default_factory=tuple)
    is_reference: bool = False

    @property
    def is_pointer(self) -> bool:
        return "*" in self.extended

    @property
    def is_template(self) -> bool:
        return bool(self.sub_types)

    @classmethod
    def from_raw(cls, raw: Any, *, is_reference: bool = False) -> "TypeRef":
        if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
            raise DumpFormatError(f"malformed type array: {raw!r}")
        name = raw[0]
        kind = TypeKind.from_letter(raw[1]) if len(raw) > 1 else TypeKind.DEFAULT
        extended = raw[2] if len(raw) > 2 and isinstance(raw[2], str) else ""
        subs: tuple[TypeRef, ...] = ()
        if len(raw) > 3 and raw[3]:
            if not isinstance(raw[3], list):
                raise DumpFormatError(f"malformed sub-type list in {raw!r}")
            subs = tuple(cls.from_raw(s) for s in raw[3])
        return cls(name, kind, extended, subs, is_reference)

    def to_raw(self) -> list:
        return [self.name, self.kind.value, self.extended, [s.to_raw() for s in self.sub_types]]

    def __str__(self) -> str:
        text = self.name
        if self.sub_types:
            text += "<" + ", ".join(str(s) for s in self.sub_types) + ">"
        text += self.extended
        if self.is_reference:
            text += "&"
        return text
