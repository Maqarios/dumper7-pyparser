"""The ``Dump`` aggregate and the ``load_dump`` entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _io, _parse
from ._namespace import EMPTY, Namespace
from .models import EnumDef, FileInfo, Function, Member, Struct
from .types import TypeKind, TypeRef

_KINDS = ("classes", "structs", "enums", "functions", "offsets")


class Dump:
    """All five Dumpspace files, cross-linked.

    ``dump.classes.UWorld.PersistentLevel`` -> :class:`Member`
    ``dump.structs.FVector.X``              -> :class:`Member`
    ``dump.enums.EFoo.Bar``                 -> ``int``
    ``dump.functions.UWorld.GetName``       -> :class:`Function`
    ``dump.offsets.OFFSET_GWORLD``          -> ``int``
    """

    __slots__ = ("classes", "structs", "enums", "functions", "offsets", "info", "source")

    def __init__(
        self,
        *,
        classes: Namespace[Struct] | None = None,
        structs: Namespace[Struct] | None = None,
        enums: Namespace[EnumDef] | None = None,
        functions: Namespace[Namespace[Function]] | None = None,
        offsets: Namespace[int] | None = None,
        info: Namespace[FileInfo] | None = None,
        source: Path | None = None,
    ) -> None:
        self.classes: Namespace[Struct] = classes if classes is not None else Namespace(label="classes")
        self.structs: Namespace[Struct] = structs if structs is not None else Namespace(label="structs")
        self.enums: Namespace[EnumDef] = enums if enums is not None else Namespace(label="enums")
        self.functions: Namespace[Namespace[Function]] = (
            functions if functions is not None else Namespace(label="functions")
        )
        self.offsets: Namespace[int] = offsets if offsets is not None else Namespace(label="offsets")
        self.info: Namespace[FileInfo] = info if info is not None else Namespace(label="info")
        self.source = source
        self._link()

    def _link(self) -> None:
        for ns in (self.classes, self.structs):
            for struct in ns.values():
                struct._dump = self
                struct._members_cache = None
                struct._functions_cache = None
                struct.functions = self.functions.get(struct.name, EMPTY)

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_raw(
        cls,
        *,
        classes: Any = None,
        structs: Any = None,
        enums: Any = None,
        functions: Any = None,
        offsets: Any = None,
        source: Path | None = None,
    ) -> "Dump":
        """Build from already-decoded JSON (envelope or bare ``data`` list per kind)."""
        raw = {"classes": classes, "structs": structs, "enums": enums, "functions": functions, "offsets": offsets}
        parsed: dict[str, Any] = {}
        info: dict[str, FileInfo] = {}
        for kind, value in raw.items():
            if value is None:
                continue
            data, file_info = _io.unwrap(value)
            parsed[kind] = _parse_kind(kind, data)
            info[kind] = file_info
        return cls(**parsed, info=Namespace(info, label="info"), source=source)

    @classmethod
    def from_files(cls, *, strict: bool = False, source: Path | None = None, **paths: str | Path | None) -> "Dump":
        """Build from explicit file paths: ``Dump.from_files(classes=..., offsets=...)``."""
        unknown = set(paths) - set(_KINDS)
        if unknown:
            raise TypeError(f"unknown dump kinds: {sorted(unknown)}")
        parsed: dict[str, Any] = {}
        info: dict[str, FileInfo] = {}
        for kind, path in paths.items():
            if path is None:
                continue
            path = Path(path)
            if not path.is_file():
                if strict:
                    raise FileNotFoundError(path)
                continue
            data, file_info = _io.unwrap(_io.read_json(path), path)
            parsed[kind] = _parse_kind(kind, data)
            info[kind] = file_info
        return cls(**parsed, info=Namespace(info, label="info"), source=source)

    # -- lookup -----------------------------------------------------------------

    def find_struct(self, name: str) -> Struct | None:
        """Class or struct by name (classes first)."""
        found = self.classes.get(name)
        if found is None:
            found = self.structs.get(name)
        return found

    def find(self, name: str) -> Struct | EnumDef | None:
        """Class, struct or enum by name, in that precedence."""
        found = self.find_struct(name)
        if found is None:
            found = self.enums.get(name)
        return found

    def type_of(self, ref: TypeRef) -> Struct | EnumDef | None:
        """The definition a :class:`TypeRef` points at, or ``None`` for primitives/unknowns."""
        if ref.kind is TypeKind.CLASS:
            return self.classes.get(ref.name) or self.structs.get(ref.name)
        if ref.kind is TypeKind.STRUCT:
            return self.structs.get(ref.name) or self.classes.get(ref.name)
        if ref.kind is TypeKind.ENUM:
            return self.enums.get(ref.name)
        return None

    def resolve(self, query: str) -> Member | Function:
        """Resolve ``"Owner::Name"`` to a member (via inheritance) or function."""
        owner_name, sep, name = query.rpartition("::")
        if not sep or not owner_name or not name:
            raise KeyError(f"expected 'Owner::Name', got {query!r}")
        owner = self.find_struct(owner_name)
        if owner is None:
            raise KeyError(f"class/struct {owner_name!r} not found")
        found = owner.find_member(name)
        if found is not None:
            return found[0]
        found_func = owner.find_function(name)
        if found_func is not None:
            return found_func[0]
        raise KeyError(f"{owner.name} has no member or function {name!r}")

    def __repr__(self) -> str:
        parts = ", ".join(f"{k}={len(getattr(self, k))}" for k in _KINDS)
        src = f" from {self.source}" if self.source else ""
        return f"<Dump{src}: {parts}>"


def _parse_kind(kind: str, data: list) -> Any:
    if kind == "classes":
        return _parse.parse_structs(data, TypeKind.CLASS, label="classes")
    if kind == "structs":
        return _parse.parse_structs(data, TypeKind.STRUCT, label="structs")
    if kind == "enums":
        return _parse.parse_enums(data)
    if kind == "functions":
        return _parse.parse_functions(data)
    if kind == "offsets":
        return _parse.parse_offsets(data)
    raise ValueError(kind)


def load_dump(directory: str | Path, *, strict: bool = False) -> Dump:
    """Load a Dumper-7 ``Dumpspace`` folder.

    Each ``*Info.json`` (or ``.json.gz``) that exists is parsed. Missing files
    yield empty namespaces unless ``strict=True``, which raises
    :class:`FileNotFoundError` naming the first missing file.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    paths: dict[str, Path | None] = {}
    for kind in _KINDS:
        path = _io.locate(directory, kind)
        if path is None and strict:
            raise FileNotFoundError(directory / _io.FILE_NAMES[kind])
        paths[kind] = path
    return Dump.from_files(source=directory, **paths)
