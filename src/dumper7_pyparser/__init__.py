"""Object-style access to Dumper-7 Dumpspace JSON dumps.

    from dumper7_pyparser import load_dump

    dump = load_dump("path/to/Dumpspace")
    dump.classes.UWorld.PersistentLevel.offset   # int
    dump.structs.FVector.X.type                  # TypeRef('float')
    dump.enums.EFoo.Bar                          # int
    dump.functions.UWorld.GetName.signature      # str
    dump.offsets.OFFSET_GWORLD                   # int

Pointer-chain queries (opt-in, no memory access) live in ``dumper7_pyparser.chains``.
"""

from ._io import FILE_NAMES
from ._namespace import Namespace
from .dump import Dump, load_dump
from .models import EnumDef, FileInfo, Function, Member, Param, Struct
from .types import DumpFormatError, TypeKind, TypeRef

__all__ = [
    "Dump",
    "DumpFormatError",
    "EnumDef",
    "FILE_NAMES",
    "FileInfo",
    "Function",
    "Member",
    "Namespace",
    "Param",
    "Struct",
    "TypeKind",
    "TypeRef",
    "load_dump",
]

__version__ = "0.1.0"
