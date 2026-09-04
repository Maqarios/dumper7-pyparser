import pytest

from dumper7_pyparser import Dump, DumpFormatError, TypeKind, TypeRef
from dumper7_pyparser._parse import parse_enums, parse_functions, parse_offsets, parse_structs


# -- TypeRef ---------------------------------------------------------------------


def test_typeref_from_raw_and_str():
    raw = ["TMap", "C", "", [["FName", "S", "", []], ["AActor", "C", "*", []]]]
    ref = TypeRef.from_raw(raw)
    assert ref.name == "TMap" and ref.kind is TypeKind.CLASS and not ref.is_pointer and ref.is_template
    assert [s.name for s in ref.sub_types] == ["FName", "AActor"]
    assert ref.sub_types[1].is_pointer
    assert str(ref) == "TMap<FName, AActor*>"
    assert ref.to_raw() == raw


def test_typeref_reference_and_short_arrays():
    assert str(TypeRef.from_raw(["FVector", "S", "", []], is_reference=True)) == "FVector&"
    assert str(TypeRef.from_raw(["UClass", "C", "*"])) == "UClass*"
    assert TypeRef.from_raw(["int32"]).kind is TypeKind.DEFAULT


def test_typeref_errors():
    with pytest.raises(DumpFormatError):
        TypeRef.from_raw("int")
    with pytest.raises(DumpFormatError):
        TypeRef.from_raw(["X", "Q", "", []])
    with pytest.raises(DumpFormatError):
        TypeRef.from_raw(["X", "D", "", "bad"])


# -- structs -------------------------------------------------------------------------


def test_struct_fields(dump: Dump):
    world = dump.classes.UWorld
    assert world.kind is TypeKind.CLASS and world.is_class
    assert world.size == 2048
    assert world.parents == ("UObject",)
    assert list(world.own_members)[:2] == ["PersistentLevel", "Levels"]
    assert not dump.structs.FVector.is_class


def test_member_fields(dump: Dump):
    m = dump.classes.UWorld.PersistentLevel
    assert (m.name, m.owner, m.offset, m.size, m.array_dim, m.bit_offset) == ("PersistentLevel", "UWorld", 48, 8, 1, None)
    assert m.type == TypeRef("ULevel", TypeKind.CLASS, "*")
    assert not m.is_bitfield
    assert int(m) == 48 and hex(m) == "0x30"


def test_bitfield_and_array_members(dump: Dump):
    bit = dump.classes.AActor.bHidden
    assert bit.is_bitfield and bit.bit_offset == 1 and bit.offset == 88
    arr = dump.classes.UWorld.ViewLocationsRenderedLastFrame
    assert arr.array_dim == 4 and arr.size == 48 and arr.element_size == 12
    assert "[4]" in str(arr) and "bit 1" in str(bit)


def test_nested_template_types(dump: Dump):
    assert str(dump.classes.UWorld.ActorMap.type) == "TMap<FName, AActor*>"
    assert str(dump.classes.UWorld.GameStateClass.type) == "TSubclassOf<AGameStateBase*>"
    assert str(dump.classes.UWorld.WorldType.type) == "EWorldType"
    assert dump.classes.UWorld.WorldType.type.kind is TypeKind.ENUM


def test_struct_parse_tolerates_variants():
    ns = parse_structs(
        [{"A": [{"__InheritInfo": []}, {"__MDKClassSize": [0, 16]}, {"__Unknown": 1}, {"X": [["int32", "D", "", []], 0, 4]}]}],
        TypeKind.STRUCT,
        label="t",
    )
    assert ns.A.size == 16
    assert ns.A.X.array_dim == 1 and ns.A.X.bit_offset is None
    neg = parse_structs([{"B": [{"Y": [["int32", "D", "", []], 0, 4, 1, -1]}]}], TypeKind.STRUCT, label="t")
    assert neg.B.Y.bit_offset is None


@pytest.mark.parametrize(
    "entry",
    [
        [{"A": "not a list"}],
        [{"A": [{"X": [["int32", "D", "", []], 0]}]}],
        [{"A": [{"X": [["int32", "D", "", []], "0", 4]}]}],
        [{"A": [{"__InheritInfo": "UObject"}]}],
        ["not an object"],
    ],
)
def test_struct_parse_errors(entry):
    with pytest.raises(DumpFormatError):
        parse_structs(entry, TypeKind.CLASS, label="t")


# -- enums -------------------------------------------------------------------------


def test_enum_fields(dump: Dump):
    e = dump.enums.EWorldType
    assert e.underlying_type == "uint8"
    assert e.Game == 1 and e["PIE"] == 3
    assert e.name_of(7) == "Inactive" and e.name_of(99) is None
    assert list(e)[:2] == [("None", 0), ("Game", 1)]
    assert len(e) == 9 and "Editor" in e
    assert dump.enums.EBig.B == 65536 and dump.enums.EBig.underlying_type == "uint32"
    with pytest.raises(AttributeError):
        e.Missing


def test_enum_parse_variants_and_errors():
    bare = parse_enums([{"E": [{"A": 1}]}])
    assert bare.E.A == 1 and bare.E.underlying_type == ""
    with pytest.raises(DumpFormatError):
        parse_enums([{"E": [[{"A": "x"}], "uint8"]}])
    with pytest.raises(DumpFormatError):
        parse_enums([{"E": 5}])


# -- functions ---------------------------------------------------------------------


def test_function_fields(dump: Dump):
    f = dump.functions.AActor.K2_SetActorLocation
    assert f.owner == "AActor" and f.offset == 123456 and int(f) == 123456
    assert str(f.return_type) == "bool"
    assert [p.name for p in f.params] == ["NewLocation", "bSweep", "SweepHitResult", "bTeleport"]
    assert f.params[0].is_reference and not f.params[1].is_reference
    assert str(f.params[2].type) == "Engine::FHitResult&"
    assert f.flags == ("Final", "Native", "Public", "BlueprintCallable")
    assert f.has_flag("Native") and not f.has_flag("Const")
    assert f.signature == "bool K2_SetActorLocation(FVector& NewLocation, bool bSweep, Engine::FHitResult& SweepHitResult, bool bTeleport)"
    assert dump.functions.AActor.K2_DestroyActor.signature == "void K2_DestroyActor()"
    assert str(dump.functions.UWorld.GetLevels.return_type) == "TArray<ULevel*>"


def test_function_parse_direct_shape_and_errors():
    ns = parse_functions([{"A": {"F": [["void", "D", "", []], [], 1, "Native"]}}])
    assert ns.A.F.flags == ("Native",)
    with pytest.raises(DumpFormatError):
        parse_functions([{"A": [{"F": [["void", "D", "", []], []]}]}])
    with pytest.raises(DumpFormatError):
        parse_functions([{"A": [{"F": [["void", "D", "", []], [["int", "D", "", []]], 1, ""]}]}])
    with pytest.raises(DumpFormatError):
        parse_functions([{"A": 3}])


# -- offsets -----------------------------------------------------------------------


def test_offsets(dump: Dump):
    assert dump.offsets.OFFSET_GWORLD == 1011
    assert dump.offsets.Dumper == 7
    assert dump.offsets["INDEX_PROCESSEVENT"] == 67
    assert list(dump.offsets)[0] == "Dumper"


def test_offsets_object_shape_and_errors():
    assert parse_offsets([{"A": 1}, ["B", 2]]) == {"A": 1, "B": 2}
    with pytest.raises(DumpFormatError):
        parse_offsets([["A"]])
    with pytest.raises(DumpFormatError):
        parse_offsets([["A", "x"]])
    with pytest.raises(DumpFormatError):
        parse_offsets([5])
