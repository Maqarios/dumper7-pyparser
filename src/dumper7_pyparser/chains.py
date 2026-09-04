"""Pointer chains: describe how to reach a member in memory, using only the dump.

This is an opt-in query layer over :class:`~dumper7_pyparser.Dump`. It never reads
memory. A :class:`Chain` is data (ordered :class:`Hop` steps) plus a rendered
string; walk it with whatever memory reader you use::

    from dumper7_pyparser.chains import chain, find_paths

    c = chain(dump, "GWorld.PersistentLevel.Actors[0]")
    c.offsets      # [0x591F8F8, 0x30, 0x98, 0x0]  (module-relative, Cheat-Engine style)
    print(c.render())

    for route in find_paths(dump, "GWorld", "APlayerController"):
        print(route.path)

Offsets contract for ``Chain.offsets``::

    addr = base                      # module image base, or an instance address
    for off in offsets[:-1]:
        addr = read_pointer(addr + off)
    result = addr + offsets[-1]      # address OF the final member

If ``result_is_pointer`` the final member is itself a pointer: read it once more
to reach the object it points at.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import Member, Struct
from .types import TypeKind, TypeRef

if TYPE_CHECKING:
    from .dump import Dump

__all__ = ["Chain", "ChainError", "Hop", "chain", "find_paths", "element_size", "parse_path"]


class ChainError(LookupError):
    """A path cannot be described from the dump (unknown name, bad index, opaque type, ...)."""


# Sizes of primitives the dump names but never defines. Anything else must come
# from the dump (struct sizes) or be a pointer. FName is deliberately absent: its
# size differs between engine builds (8 or 12 bytes).
PRIMITIVE_SIZES: dict[str, int] = {
    "bool": 1, "char": 1, "int8": 1, "uint8": 1,
    "int16": 2, "uint16": 2,
    "int32": 4, "uint32": 4, "float": 4,
    "int64": 8, "uint64": 8, "double": 8,
    "FString": 16,  # TArray<wchar_t>: pointer + num + max
}

# Friendly names for the globals Dumper-7 writes to OffsetsInfo.json.
GLOBAL_ALIASES: dict[str, str] = {
    "GWorld": "OFFSET_GWORLD",
    "GObjects": "OFFSET_GOBJECTS",
    "GNames": "OFFSET_GNAMES",
}

# What lives at each global. GWorld is a UWorld* (so the root hop dereferences);
# the others are inline engine-internal objects that dumps normally do not define.
GLOBAL_TYPES: dict[str, TypeRef] = {
    "OFFSET_GWORLD": TypeRef("UWorld", TypeKind.CLASS, "*"),
    "OFFSET_GOBJECTS": TypeRef("FUObjectArray", TypeKind.STRUCT),
    "OFFSET_GNAMES": TypeRef("FNamePool", TypeKind.STRUCT),
}

_TOKEN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*)(?:\[(\d+)\])?$")


# -- data ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hop:
    """One memory step: add ``offset`` to the current address; ``type`` lives there."""

    label: str
    offset: int
    type: TypeRef
    deref: bool
    member: Member | None = None
    index: int | None = None
    stride: int | None = None
    note: str = ""

    def describe(self) -> str:
        text = f"+0x{self.offset:X}".ljust(8) + f" {self.label} : {self.type}"
        if self.index is not None and self.stride is not None:
            text += f"  (index {self.index} x {self.stride})"
        if self.note:
            text += f"  -> {self.note}"
        elif self.deref:
            text += "  -> deref"
        return text


@dataclass(frozen=True, slots=True)
class Chain:
    """An ordered list of hops from a base to a member. See the module docstring for the contract."""

    path: str
    base: str  # "module" (rooted at an OffsetsInfo global) or "object" (rooted at a struct instance)
    root_name: str
    root: Struct | None
    hops: tuple[Hop, ...]

    @property
    def result_type(self) -> TypeRef:
        return self.hops[-1].type

    @property
    def result_is_pointer(self) -> bool:
        return self.hops[-1].deref

    @property
    def offsets(self) -> list[int]:
        """Cheat-Engine style offsets: every entry but the last ends in a pointer read."""
        out: list[int] = []
        acc = 0
        last = len(self.hops) - 1
        for i, hop in enumerate(self.hops):
            acc += hop.offset
            if hop.deref and i < last:
                out.append(acc)
                acc = 0
        out.append(acc)
        return out

    def render(self) -> str:
        head = "[module base]" if self.base == "module" else f"[{self.root_name} instance]"
        lines = [self.path, "  " + head]
        lines.extend("  " + hop.describe() for hop in self.hops)
        lines.append("  offsets: [" + ", ".join(f"0x{o:X}" for o in self.offsets) + "]")
        return "\n".join(lines)

    def __str__(self) -> str:
        return f"{self.path}: " + " -> ".join(f"0x{o:X}" for o in self.offsets) + f"  ({self.result_type})"


# -- parsing ---------------------------------------------------------------------------


def parse_path(path: str) -> list[tuple[str, int | None]]:
    """Split ``"A.B[3].C"`` into ``[("A", None), ("B", 3), ("C", None)]``.

    Names may contain ``::`` (package prefixes, or the ``Owner::Member`` form).
    """
    pieces = path.strip().split(".")
    tokens: list[tuple[str, int | None]] = []
    for piece in pieces:
        m = _TOKEN_RE.match(piece)
        if not m:
            raise ChainError(f"bad path element {piece!r} in {path!r} (expected Name or Name[index])")
        tokens.append((m.group(1), int(m.group(2)) if m.group(2) is not None else None))
    if not tokens:
        raise ChainError("empty path")
    return tokens


def element_size(dump: "Dump", ref: TypeRef, pointer_size: int = 8) -> int:
    """Byte size of one ``ref`` value, for array strides."""
    if ref.is_pointer:
        return pointer_size
    if ref.kind in (TypeKind.CLASS, TypeKind.STRUCT):
        defined = dump.type_of(ref)
        if isinstance(defined, Struct):
            return defined.size
    if ref.name in PRIMITIVE_SIZES and not ref.sub_types:
        return PRIMITIVE_SIZES[ref.name]
    raise ChainError(f"unknown element size for {ref}")


# -- walking ----------------------------------------------------------------------------


def _global_key(name: str) -> str | None:
    if name in GLOBAL_ALIASES:
        return GLOBAL_ALIASES[name]
    if name.startswith("OFFSET_"):
        return name
    return None


def _struct_for(dump: "Dump", ref: TypeRef) -> Struct | None:
    found = dump.type_of(ref)
    return found if isinstance(found, Struct) else None


def _resolve_root(dump: "Dump", tokens: list[tuple[str, int | None]]) -> tuple[str, str, Hop | None, Struct | None, TypeRef | None, list]:
    """Return ``(base, root_name, root_hop, cursor_struct, cursor_type, remaining_tokens)``."""
    name, index = tokens[0]
    key = _global_key(name)
    if key is not None:
        if index is not None:
            raise ChainError(f"global {name} cannot be indexed")
        if key not in dump.offsets:
            raise ChainError(f"{key} not present in OffsetsInfo.json")
        ref = GLOBAL_TYPES.get(key, TypeRef("void"))
        hop = Hop(name, dump.offsets[key], ref, ref.is_pointer)
        return "module", name, hop, _struct_for(dump, ref), ref, tokens[1:]

    struct = dump.find_struct(name)
    if struct is not None:
        if index is not None:
            raise ChainError(f"root {name} cannot be indexed")
        return "object", struct.name, None, struct, None, tokens[1:]

    # Owner::Member shorthand, like Dump.resolve()
    owner, sep, member = name.rpartition("::")
    if sep:
        struct = dump.find_struct(owner)
        if struct is not None:
            return "object", struct.name, None, struct, None, [(member, index)] + tokens[1:]

    raise ChainError(f"unknown root {name!r}: not a class, struct, or OffsetsInfo global")


def _require_cursor(dump: "Dump", cursor: Struct | None, cur_type: TypeRef | None, at: str) -> Struct:
    if cursor is not None:
        return cursor
    assert cur_type is not None
    if cur_type.name == "TArray":
        raise ChainError(f"{at} is {cur_type}; add an index like {at}[0] to reach an element")
    if cur_type.kind in (TypeKind.CLASS, TypeKind.STRUCT):
        raise ChainError(f"cannot descend into {at}: {cur_type.name} is not defined in the dump")
    raise ChainError(f"cannot descend into {at}: {cur_type} is not a class or struct")


def chain(dump: "Dump", path: str, *, pointer_size: int = 8) -> Chain:
    """Describe the hops needed to reach ``path``, e.g. ``"GWorld.Levels[0].Actors"``.

    Roots: a class/struct name (object-relative chain), ``Owner::Member``, or an
    OffsetsInfo global (``GWorld``, ``OFFSET_GWORLD``, ...; module-relative chain).
    Index steps are supported for fixed C arrays and ``TArray``.
    """
    tokens = parse_path(path)
    base, root_name, root_hop, cursor, cur_type, rest = _resolve_root(dump, tokens)
    root_struct = cursor
    hops: list[Hop] = [root_hop] if root_hop is not None else []
    pieces: list[str] = [root_name]
    at = root_name

    for name, index in rest:
        struct = _require_cursor(dump, cursor, cur_type, at)
        found = struct.find_member(name)
        if found is None:
            searched = [struct.name] + [a.name for a in struct.ancestors()]
            raise ChainError(f"{struct.name} has no member {name!r} (searched {', '.join(searched)})")
        member, _ = found
        label = f"{name}[{index}]" if index is not None else name
        at = f"{at}.{label}"

        if index is None:
            hops.append(Hop(name, member.offset, member.type, member.type.is_pointer, member))
            cur_type = member.type
        elif member.array_dim > 1:
            if index >= member.array_dim:
                raise ChainError(f"index {index} out of range for {struct.name}.{name}[{member.array_dim}]")
            stride = member.element_size
            hops.append(Hop(label, member.offset + index * stride, member.type, member.type.is_pointer, member, index, stride))
            cur_type = member.type
        elif member.type.name == "TArray" and len(member.type.sub_types) == 1:
            elem = member.type.sub_types[0]
            stride = element_size(dump, elem, pointer_size)
            hops.append(Hop(name, member.offset, member.type, True, member, note="read Data pointer"))
            hops.append(Hop(f"[{index}]", index * stride, elem, elem.is_pointer, None, index, stride))
            cur_type = elem
        else:
            raise ChainError(f"{struct.name}.{name} is {member.type}, not an indexable array")

        pieces.append(label)
        cursor = _struct_for(dump, cur_type)

    if not hops:
        raise ChainError(f"path {path!r} names a type but no member")
    return Chain(".".join(pieces), base, root_name, root_struct, tuple(hops))


# -- discovery --------------------------------------------------------------------------


def _edge(dump: "Dump", member: Member) -> tuple[str, Struct] | None:
    """The struct a member leads to, with the path label to get there, or ``None``."""
    ref = member.type
    if ref.name == "TArray" and len(ref.sub_types) == 1:
        target = _struct_for(dump, ref.sub_types[0])
        return (f"{member.name}[0]", target) if target is not None else None
    if ref.kind in (TypeKind.CLASS, TypeKind.STRUCT) and not ref.sub_types:
        target = _struct_for(dump, ref)
        if target is not None:
            return (f"{member.name}[0]" if member.array_dim > 1 else member.name, target)
    return None


def _name_matches(name: str, wanted: str) -> bool:
    return name == wanted or name.endswith("::" + wanted)


def find_paths(
    dump: "Dump",
    src: str,
    dst: str,
    *,
    max_depth: int = 3,
    include_subclasses: bool = True,
    limit: int = 50,
    pointer_size: int = 8,
) -> list[Chain]:
    """Member routes from ``src`` (a type or a global like ``GWorld``) to type ``dst``.

    Follows plain pointers, embedded structs, fixed arrays and ``TArray`` elements
    (as ``[0]``); ``TMap``/``TSet``/smart pointers are opaque. Shortest routes first.
    """
    base, root_name, root_hop, start, cur_type, rest = _resolve_root(dump, [(src, None)])
    if rest:
        raise ChainError(f"find_paths src must be a type or global, got {src!r}")
    if start is None:
        assert cur_type is not None
        raise ChainError(f"cannot search from {src}: {cur_type.name} is not defined in the dump")

    def matches(s: Struct) -> bool:
        return _name_matches(s.name, dst) or (include_subclasses and s.is_subclass_of(dst))

    found: list[str] = []
    queue: deque[tuple[Struct, list[str], frozenset[str]]] = deque([(start, [root_name], frozenset({start.name}))])
    while queue and len(found) < limit:
        node, pieces, seen = queue.popleft()
        if len(pieces) - 1 >= max_depth:
            continue
        for member in node.members.values():
            edge = _edge(dump, member)
            if edge is None or edge[1].name in seen:
                continue
            label, target = edge
            new_pieces = pieces + [label]
            if matches(target):
                found.append(".".join(new_pieces))
                if len(found) >= limit:
                    break
            queue.append((target, new_pieces, seen | {target.name}))

    # BFS already yields shortest routes first; within a depth, member declaration order.
    found.sort(key=lambda p: p.count("."))
    return [chain(dump, p, pointer_size=pointer_size) for p in found]
