"""The ``Dump`` aggregate and the ``load_dump`` entry point."""

from __future__ import annotations

from math import gcd
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

    __slots__ = ("classes", "structs", "enums", "functions", "offsets", "info", "source",
                 "_observed_sizes", "_observed_alignments", "_pairs")

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
        self._observed_sizes: dict[str, int | None] | None = None
        self._observed_alignments: dict[str, int] | None = None
        self._pairs: dict[tuple[TypeRef, TypeRef, int], Struct] = {}
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
        """The definition a :class:`TypeRef` points at, or ``None`` for primitives/unknowns.

        Templates have no definition in the dump, with one exception: ``TPair<K, V>``
        (what a ``TMap`` slot holds) resolves to the synthetic struct of
        :meth:`pair_struct`, so a chain can continue into ``.Key`` / ``.Value``.
        """
        if ref.name == "TPair" and len(ref.sub_types) >= 2 and not ref.is_pointer:
            return self.pair_struct(ref.sub_types[0], ref.sub_types[1])
        if ref.kind is TypeKind.CLASS:
            return self.classes.get(ref.name) or self.structs.get(ref.name)
        if ref.kind is TypeKind.STRUCT:
            return self.structs.get(ref.name) or self.classes.get(ref.name)
        if ref.kind is TypeKind.ENUM:
            return self.enums.get(ref.name)
        return None

    def pair_struct(self, key: TypeRef, value: TypeRef, pointer_size: int = 8) -> Struct:
        """The ``TPair<key, value>`` a ``TMap`` slot holds, as a synthetic :class:`Struct`.

        ``Key`` sits at offset 0, ``Value`` at the first offset aligned for it; the sizes
        and alignments come from :func:`dumper7_pyparser.chains.pair_layout`. Cached per
        pair of types, so the same object comes back every time (the ``members`` cache
        and identity checks work as for dump-defined structs). Raises ``ChainError``
        when a side cannot be sized.
        """
        cached = self._pairs.get((key, value, pointer_size))
        if cached is None:
            from .chains import pair_layout

            layout = pair_layout(self, key, value, pointer_size)
            name = f"TPair<{key}, {value}>"
            members = Namespace({
                "Key": Member("Key", name, key, 0, layout.key_size),
                "Value": Member("Value", name, value, layout.value_offset, layout.value_size),
            }, label=name)
            cached = Struct(name, TypeKind.STRUCT, layout.size, (), members, _dump=self)
            self._pairs[(key, value, pointer_size)] = cached
        return cached

    def observed_size(self, name: str) -> int | None:
        """Byte size the dump's members give the type ``name``, or ``None``.

        For types the dump names but never defines (``FName``, ``FText``,
        ``TSubclassOf``, ``TWeakObjectPtr``, ...): the size shared by every plain
        member of that type (no pointers, bitfields or fixed arrays). ``None`` when no
        such member exists or they disagree.
        """
        sizes = self._observed_sizes
        if sizes is None:
            sizes = {}
            for ns in (self.classes, self.structs):
                for struct in ns.values():
                    for member in struct.own_members.values():
                        ref = member.type
                        if ref.is_pointer or member.bit_offset is not None or member.array_dim != 1:
                            continue
                        if sizes.setdefault(ref.name, member.size) != member.size:
                            sizes[ref.name] = None
            self._observed_sizes = sizes
        return sizes.get(name)

    def observed_alignment(self, name: str) -> int | None:
        """Alignment the dump's members suggest for the type ``name``, or ``None``.

        The dump records offsets but no alignments. Every member of a type sits at a
        multiple of that type's alignment, so the largest power of two dividing all the
        offsets of the plain members of ``name`` (no pointers or bitfields; fixed arrays
        count, their first element is aligned too) is the alignment, provided the type
        occurs often enough that one of them is not over-aligned by chance. Capped at 8,
        ``None`` when no such member exists or every one sits at offset 0.
        """
        aligns = self._observed_alignments
        if aligns is None:
            aligns = {}
            for ns in (self.classes, self.structs):
                for struct in ns.values():
                    for member in struct.own_members.values():
                        ref = member.type
                        if ref.is_pointer or member.bit_offset is not None:
                            continue
                        aligns[ref.name] = gcd(aligns.get(ref.name, 0), member.offset)
            self._observed_alignments = aligns
        g = aligns.get(name, 0)
        if g == 0:
            return None
        return min(g & -g, 8)

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
