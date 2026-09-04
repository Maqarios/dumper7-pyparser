"""Raw ``data`` lists -> model namespaces."""

from __future__ import annotations

from typing import Any, Iterator

from ._namespace import Namespace
from .models import EnumDef, Function, Member, Param, Struct
from .types import DumpFormatError, TypeKind, TypeRef

INHERIT_KEY = "__InheritInfo"
SIZE_KEY = "__MDKClassSize"


def _single_key_entries(data: list, what: str) -> Iterator[tuple[str, Any]]:
    """Yield ``(name, value)`` from DSGen's list of one-key objects.

    Multi-key objects are tolerated and flattened; anything else is an error.
    """
    for entry in data:
        if not isinstance(entry, dict):
            raise DumpFormatError(f"{what}: expected an object entry, got {type(entry).__name__}: {entry!r}")
        for name, value in entry.items():
            yield name, value


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DumpFormatError(f"{what}: expected an integer, got {value!r}")
    return value


# -- classes / structs -----------------------------------------------------------


def parse_struct_entry(name: str, body: Any, kind: TypeKind) -> Struct:
    if not isinstance(body, list):
        raise DumpFormatError(f"{name}: expected a member list, got {type(body).__name__}")

    parents: tuple[str, ...] = ()
    size = 0
    members: dict[str, Member] = {}

    for item in body:
        if not isinstance(item, dict):
            raise DumpFormatError(f"{name}: expected an object in member list, got {item!r}")
        for key, value in item.items():
            if key == INHERIT_KEY:
                if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                    raise DumpFormatError(f"{name}: malformed {INHERIT_KEY}: {value!r}")
                parents = tuple(value)
            elif key == SIZE_KEY:
                # Real dumps store a number; some tooling stores [flags, size].
                if isinstance(value, list):
                    value = value[-1] if value else 0
                size = _int(value, f"{name}.{SIZE_KEY}")
            elif key.startswith("__"):
                continue  # unknown metadata, ignore
            else:
                members[key] = parse_member(name, key, value)

    return Struct(
        name=name,
        kind=kind,
        size=size,
        parents=parents,
        own_members=Namespace(members, label=f"own members of {name}"),
    )


def parse_member(owner: str, name: str, raw: Any) -> Member:
    where = f"{owner}.{name}"
    if not isinstance(raw, list) or len(raw) < 3:
        raise DumpFormatError(f"{where}: expected [type, offset, size, arrayDim, bitOffset?], got {raw!r}")
    type_ref = TypeRef.from_raw(raw[0])
    offset = _int(raw[1], f"{where} offset")
    size = _int(raw[2], f"{where} size")
    array_dim = _int(raw[3], f"{where} arrayDim") if len(raw) > 3 else 1
    bit_offset = _int(raw[4], f"{where} bitOffset") if len(raw) > 4 else None
    if bit_offset is not None and bit_offset < 0:
        bit_offset = None
    return Member(name, owner, type_ref, offset, size, array_dim, bit_offset)


def parse_structs(data: list, kind: TypeKind, *, label: str) -> Namespace[Struct]:
    out: dict[str, Struct] = {}
    for name, body in _single_key_entries(data, label):
        out[name] = parse_struct_entry(name, body, kind)
    return Namespace(out, label=label)


# -- enums -----------------------------------------------------------------------


def parse_enum_entry(name: str, body: Any) -> EnumDef:
    # Dumpspace: [ [ {Name: value}, ... ], "uint8" ]; tolerate a bare member list.
    if isinstance(body, list) and body and isinstance(body[0], list):
        member_list, underlying = body[0], body[1] if len(body) > 1 else ""
    elif isinstance(body, list):
        member_list, underlying = body, ""
    else:
        raise DumpFormatError(f"enum {name}: expected a list, got {body!r}")
    if not isinstance(underlying, str):
        raise DumpFormatError(f"enum {name}: malformed underlying type {underlying!r}")

    values: dict[str, int] = {}
    for item in member_list:
        if not isinstance(item, dict):
            raise DumpFormatError(f"enum {name}: expected an object entry, got {item!r}")
        for member, value in item.items():
            values[member] = _int(value, f"enum {name}.{member}")
    return EnumDef(name, underlying, Namespace(values, label=f"values of {name}"))


def parse_enums(data: list, *, label: str = "enums") -> Namespace[EnumDef]:
    out: dict[str, EnumDef] = {}
    for name, body in _single_key_entries(data, label):
        out[name] = parse_enum_entry(name, body)
    return Namespace(out, label=label)


# -- functions -------------------------------------------------------------------


def parse_function_entry(owner: str, name: str, raw: Any) -> Function:
    where = f"{owner}::{name}"
    if not isinstance(raw, list) or len(raw) < 4:
        raise DumpFormatError(f"{where}: expected [returnType, params, offset, flags], got {raw!r}")
    return_type = TypeRef.from_raw(raw[0])
    params_raw = raw[1]
    if not isinstance(params_raw, list):
        raise DumpFormatError(f"{where}: malformed parameter list {params_raw!r}")
    params: list[Param] = []
    for p in params_raw:
        if not isinstance(p, list) or len(p) < 3:
            raise DumpFormatError(f"{where}: expected [type, '&' or '', name] parameter, got {p!r}")
        params.append(Param(str(p[2]), TypeRef.from_raw(p[0], is_reference=p[1] == "&")))
    offset = _int(raw[2], f"{where} offset")
    flags = raw[3] if isinstance(raw[3], str) else ""
    return Function(name, owner, return_type, tuple(params), offset, flags)


def parse_functions(data: list, *, label: str = "functions") -> Namespace[Namespace[Function]]:
    out: dict[str, Namespace[Function]] = {}
    for owner, body in _single_key_entries(data, label):
        funcs: dict[str, Function] = {}
        if isinstance(body, list):
            # Dumpspace: owner -> [ {name: def}, ... ]
            for name, raw in _single_key_entries(body, f"functions of {owner}"):
                funcs[name] = parse_function_entry(owner, name, raw)
        elif isinstance(body, dict):
            # Direct shape: owner -> {name: def}
            for name, raw in body.items():
                funcs[name] = parse_function_entry(owner, name, raw)
        else:
            raise DumpFormatError(f"functions of {owner}: expected a list, got {body!r}")
        out[owner] = Namespace(funcs, label=f"functions of {owner}")
    return Namespace(out, label=label)


# -- offsets ---------------------------------------------------------------------


def parse_offsets(data: list, *, label: str = "offsets") -> Namespace[int]:
    out: dict[str, int] = {}
    for entry in data:
        if isinstance(entry, list):
            if len(entry) < 2 or not isinstance(entry[0], str):
                raise DumpFormatError(f"{label}: expected [name, value], got {entry!r}")
            out[entry[0]] = _int(entry[1], f"offset {entry[0]}")
        elif isinstance(entry, dict):
            for name, value in entry.items():
                out[name] = _int(value, f"offset {name}")
        else:
            raise DumpFormatError(f"{label}: unexpected entry {entry!r}")
    return Namespace(out, label=label)
