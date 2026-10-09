import shutil

import pytest

from dumper7_pyparser import Dump, load_dump
from dumper7_pyparser.__main__ import main
from dumper7_pyparser.chains import Chain, ChainError, chain, element_size, find_paths, parse_path
from dumper7_pyparser.types import TypeKind, TypeRef

GWORLD = 1011  # fixture OFFSET_GWORLD


# -- parse_path --------------------------------------------------------------------


def test_parse_path():
    assert parse_path("UWorld.Levels[3].Actors") == [("UWorld", None, None), ("Levels", 3, None), ("Actors", None, None)]
    assert parse_path("Engine::FHitResult.Location") == [("Engine::FHitResult", None, None), ("Location", None, None)]
    assert parse_path("ULevel.Actors[0](APawn).Controller(Engine::AC)") == [
        ("ULevel", None, None), ("Actors", 0, "APawn"), ("Controller", None, "Engine::AC")]
    for bad in ("", "UWorld.", "UWorld.Levels[x]", "UWorld.Levels[]", "1abc", "A..B", "A.B()", "A.B(1)", "A.B(C)[0]", "A.B(C)(D)"):
        with pytest.raises(ChainError):
            parse_path(bad)


# -- walking -------------------------------------------------------------------------


def test_pointer_member_then_member(dump: Dump):
    c = chain(dump, "UWorld.PersistentLevel.Actors")
    assert isinstance(c, Chain)
    assert c.base == "object" and c.root is dump.classes.UWorld and c.path == "UWorld.PersistentLevel.Actors"
    assert [h.offset for h in c.hops] == [0x30, 0x98]
    assert [h.deref for h in c.hops] == [True, False]
    assert c.offsets == [0x30, 0x98]
    assert str(c.result_type) == "TArray<AActor*>" and not c.result_is_pointer
    assert c.hops[0].member is dump.classes.UWorld.PersistentLevel


def test_chain_ending_on_pointer(dump: Dump):
    c = chain(dump, "UWorld.PersistentLevel")
    assert c.offsets == [0x30] and c.result_is_pointer


def test_tarray_index(dump: Dump):
    c = chain(dump, "UWorld.Levels[1].Actors")
    assert [(h.label, h.offset, h.deref) for h in c.hops] == [("Levels", 0x190, True), ("[1]", 8, True), ("Actors", 0x98, False)]
    assert c.hops[0].note == "read Data pointer"
    assert c.hops[1].index == 1 and c.hops[1].stride == 8 and str(c.hops[1].type) == "ULevel*"
    assert c.offsets == [0x190, 0x8, 0x98]
    assert chain(dump, "UWorld.Levels[1].Actors", pointer_size=4).offsets == [0x190, 0x4, 0x98]


def test_embedded_struct_hops_merge(dump: Dump):
    c = chain(dump, "AActor.PrimaryActorTick.TickInterval")
    assert [h.offset for h in c.hops] == [0x28, 0xC]
    assert c.offsets == [0x34]
    assert c.hops[1].member.owner == "FTickFunction"  # inherited struct member


def test_fixed_array_index(dump: Dump):
    c = chain(dump, "UWorld.ViewLocationsRenderedLastFrame[2].Z")
    assert c.hops[0].label == "ViewLocationsRenderedLastFrame[2]"
    assert c.hops[0].offset == 0x208 + 2 * 12 and c.hops[0].stride == 12
    assert c.offsets == [0x208 + 24 + 8]
    with pytest.raises(ChainError, match="out of range"):
        chain(dump, "UWorld.ViewLocationsRenderedLastFrame[4].Z")


def test_inherited_member_and_owner_forms(dump: Dump):
    assert chain(dump, "APawn.RootComponent").offsets == [0x190]
    assert chain(dump, "APawn::RootComponent").offsets == [0x190]
    assert chain(dump, "APawn::Controller").path == "APawn.Controller"
    c = chain(dump, "Engine::FHitResult.Location.X")
    assert c.offsets == [0x10] and c.root_name == "Engine::FHitResult"
    assert chain(dump, "Engine::FHitResult::Location").offsets == [0x10]


@pytest.mark.parametrize(
    "path, match",
    [
        ("Nope.X", "unknown root"),
        ("UWorld.Nope", "has no member 'Nope'"),
        ("APawn.Nope", "searched APawn, AActor, UObject"),
        ("UWorld.Levels.Actors", "add an index"),
        ("UWorld.PersistentLevel[0]", "not an indexable array"),
        ("UWorld.ActorMap[0]", "not an indexable array"),
        ("FVector.X.Y", "not a class or struct"),
        ("APawn.RootComponent.Nope", "not defined in the dump"),
        ("UWorld[0].Levels", "cannot be indexed"),
        ("GWorld[0].Levels", "cannot be indexed"),
        ("UWorld", "no member"),
    ],
)
def test_chain_errors(dump: Dump, path, match):
    with pytest.raises(ChainError, match=match):
        chain(dump, path)


def test_cast_retypes_a_pointer_to_a_subclass(dump: Dump):
    """``Actors[0](APawn)``: the element is declared AActor*, the caller knows it is a pawn."""
    c = chain(dump, "ULevel.Actors[0](APawn).Controller")
    assert c.path == "ULevel.Actors[0](APawn).Controller"
    assert [h.label for h in c.hops] == ["Actors", "[0]", "Controller"]
    element = c.hops[1]
    assert element.cast is dump.classes.APawn and element.type == TypeRef("APawn", "C", "*") and element.deref
    assert element.member is None and element.index == 0 and "deref as APawn (declared AActor*)" in element.describe()
    assert c.hops[0].cast is None and c.hops[2].cast is None
    plain = chain(dump, "ULevel.Actors[0]")
    assert c.offsets == [*plain.offsets, dump.classes.APawn.members.Controller.offset]
    # a chain may end on the cast pointer itself; a cast to the declared class is a no-op
    end = chain(dump, "ULevel.Actors[0](APawn)")
    assert end.result_is_pointer and end.result_type.name == "APawn" and end.offsets == plain.offsets
    same = chain(dump, "UWorld.PersistentLevel(ULevel).Actors")
    assert same.offsets == chain(dump, "UWorld.PersistentLevel.Actors").offsets and same.hops[0].cast is dump.classes.ULevel
    # a member pointer, cast
    assert chain(dump, "ULevel.OwningWorld(UWorld).PersistentLevel").result_type.name == "ULevel"


@pytest.mark.parametrize(
    "path, match",
    [
        ("ULevel.Actors[0](UWorld).Levels", "does not derive from AActor"),
        ("ULevel.Actors[0](ANope).Controller", "not defined in the dump"),
        ("AActor.Tags[0](APawn)", "nothing to cast"),
        ("UWorld.OwningGameInstance(UWorld)", "nothing to cast"),      # declared type not in the dump
        ("ULevel.Actors(APawn)", "nothing to cast"),                   # the array, not an element
        ("APawn(AActor).Tags", "root APawn cannot be cast"),
        ("GWorld(UWorld).Levels", "root GWorld cannot be cast"),
    ],
)
def test_cast_errors(dump: Dump, path, match):
    with pytest.raises(ChainError, match=match):
        chain(dump, path)


def test_element_size(dump: Dump):
    assert element_size(dump, TypeRef("AActor", "C", "*")) == 8
    assert element_size(dump, TypeRef("AActor", "C", "*"), pointer_size=4) == 4
    assert element_size(dump, TypeRef("FVector", "S")) == 12
    assert element_size(dump, TypeRef("int32")) == 4
    assert element_size(dump, TypeRef("FString")) == 16
    assert element_size(dump, TypeRef("EBig", "E")) == 4            # the enum's underlying type
    assert element_size(dump, TypeRef("EWorldType", "E")) == 1


def test_element_size_of_undefined_types_comes_from_the_dump_s_members(dump: Dump):
    assert dump.observed_size("FName") == 8                         # UObject.Name
    assert element_size(dump, TypeRef("FName", "S")) == 8
    assert element_size(dump, TypeRef("TWeakObjectPtr", "D", "", [TypeRef("AActor", "C", "*")])) == 8
    assert element_size(dump, TypeRef("TSubclassOf", "C", "", [TypeRef("UObject", "C", "*")])) == 8
    c = chain(dump, "AActor.Tags[2]")
    assert c.offsets == [0x198, 0x10] and str(c.result_type) == "FName"
    assert dump.observed_size("FNoSuchType") is None
    with pytest.raises(ChainError, match="unknown element size"):
        element_size(dump, TypeRef("FNoSuchType", "S"))


def test_observed_size_refuses_types_whose_members_disagree():
    from dumper7_pyparser.models import Member, Struct
    from dumper7_pyparser._namespace import Namespace

    def struct(name: str, size: int) -> Struct:
        member = Member("Value", name, TypeRef("FOdd"), 0, size)
        return Struct(name, TypeKind.STRUCT, size, (), Namespace({"Value": member}, label=name))

    agreeing = Dump(structs=Namespace({"FA": struct("FA", 8), "FB": struct("FB", 8)}, label="structs"))
    assert agreeing.observed_size("FOdd") == 8
    mixed = Dump(structs=Namespace({"FA": struct("FA", 8), "FB": struct("FB", 12)}, label="structs"))
    assert mixed.observed_size("FOdd") is None
    with pytest.raises(ChainError, match="unknown element size"):
        element_size(mixed, TypeRef("FOdd"))


# -- global roots ----------------------------------------------------------------------


def test_gworld_root(dump: Dump):
    c = chain(dump, "GWorld.PersistentLevel.Actors[0]")
    assert c.base == "module" and c.root_name == "GWorld" and c.root is dump.classes.UWorld
    assert c.hops[0].label == "GWorld" and c.hops[0].offset == GWORLD and c.hops[0].deref
    assert str(c.hops[0].type) == "UWorld*"
    assert c.offsets == [GWORLD, 0x30, 0x98, 0x0]
    assert c.render().splitlines()[1].strip() == "[module base]"
    assert chain(dump, "OFFSET_GWORLD.PersistentLevel").offsets == [GWORLD, 0x30]
    assert chain(dump, "GWorld.PersistentLevel").path == "GWorld.PersistentLevel"


def test_other_globals(dump: Dump):
    g = chain(dump, "GObjects")
    assert g.base == "module" and g.offsets == [123] and not g.hops[0].deref and g.root is None
    assert chain(dump, "OFFSET_PROCESSEVENT").offsets == [1213]
    with pytest.raises(ChainError, match="not defined in the dump"):
        chain(dump, "GObjects.ObjObjects")
    with pytest.raises(ChainError, match="unknown root"):
        chain(dump, "INDEX_PROCESSEVENT.X")
    with pytest.raises(ChainError, match="unknown root"):
        chain(dump, "Dumper.X")


def test_missing_offsets_file(fixture_dir, tmp_path):
    for name in ("ClassesInfo.json", "StructsInfo.json"):
        shutil.copy(fixture_dir / name, tmp_path / name)
    d = load_dump(tmp_path)
    assert chain(d, "UWorld.PersistentLevel").offsets == [0x30]
    with pytest.raises(ChainError, match="OFFSET_GWORLD not present"):
        chain(d, "GWorld.PersistentLevel")


# -- rendering ------------------------------------------------------------------------


def test_render_and_str(dump: Dump):
    c = chain(dump, "UWorld.Levels[0].Actors")
    text = c.render()
    lines = text.splitlines()
    assert lines[0] == "UWorld.Levels[0].Actors"
    assert lines[1].strip() == "[UWorld instance]"
    assert "+0x190" in lines[2] and "TArray<ULevel*>" in lines[2] and "read Data pointer" in lines[2]
    assert "[0]" in lines[3] and "index 0 x 8" in lines[3] and "deref" in lines[3]
    assert lines[-1].strip() == "offsets: [0x190, 0x0, 0x98]"
    assert str(c) == "UWorld.Levels[0].Actors: 0x190 -> 0x0 -> 0x98  (TArray<AActor*>)"
    assert "\n" not in str(c)


# -- discovery ------------------------------------------------------------------------


def paths(chains):
    return [c.path for c in chains]


def test_find_paths_world_to_level(dump: Dump):
    result = find_paths(dump, "UWorld", "ULevel")
    assert paths(result) == ["UWorld.PersistentLevel", "UWorld.Levels[0]"]
    assert all(isinstance(c, Chain) and c.base == "object" for c in result)
    assert result[1].offsets == [0x190, 0x0]


def test_find_paths_from_global(dump: Dump):
    result = find_paths(dump, "GWorld", "ULevel")
    assert paths(result) == ["GWorld.PersistentLevel", "GWorld.Levels[0]"]
    assert result[0].base == "module" and result[0].offsets == [GWORLD, 0x30]


def test_find_paths_through_arrays_not_maps(dump: Dump):
    result = paths(find_paths(dump, "UWorld", "AActor"))
    assert "UWorld.PersistentLevel.Actors[0]" in result
    assert "UWorld.Levels[0].Actors[0]" in result
    assert not any("ActorMap" in p for p in result)
    assert [p.count(".") for p in result] == sorted(p.count(".") for p in result)


def test_find_paths_subclasses_and_depth(dump: Dump):
    direct = paths(find_paths(dump, "ULevel", "UObject", max_depth=1))
    # inherited members first (UObject.Outer), then own members in declaration order
    assert direct == ["ULevel.Outer", "ULevel.Actors[0]", "ULevel.OwningWorld"]
    deeper = paths(find_paths(dump, "ULevel", "UObject"))
    assert deeper[:3] == direct and "ULevel.OwningWorld.Outer" in deeper
    assert "ULevel.OwningWorld.PersistentLevel" not in deeper  # would return to the start struct
    assert paths(find_paths(dump, "ULevel", "UObject", max_depth=1, include_subclasses=False)) == ["ULevel.Outer"]
    assert paths(find_paths(dump, "UWorld", "AActor", max_depth=1)) == []
    assert paths(find_paths(dump, "UWorld", "AActor", limit=1)) == ["UWorld.PersistentLevel.Actors[0]"]
    assert paths(find_paths(dump, "APawn", "UWorld")) == []  # USceneComponent is absent: dead end


def test_find_paths_no_cycles(dump: Dump):
    # ULevel -> OwningWorld (UWorld) -> PersistentLevel (ULevel) must not recurse into itself
    result = paths(find_paths(dump, "ULevel", "ULevel", max_depth=4))
    assert result == []
    result = paths(find_paths(dump, "ULevel", "FVector", max_depth=4))
    assert "ULevel.OwningWorld.ViewLocationsRenderedLastFrame[0]" in result
    assert not any(p.count("OwningWorld") > 1 for p in result)


def test_find_paths_errors(dump: Dump):
    with pytest.raises(ChainError):
        find_paths(dump, "Nope", "ULevel")
    with pytest.raises(ChainError, match="not defined in the dump"):
        find_paths(dump, "GObjects", "ULevel")
    with pytest.raises(ChainError):
        find_paths(dump, "UWorld.Levels", "ULevel")


# -- CLI ----------------------------------------------------------------------------------


def test_cli_chain_and_paths(fixture_dir, capsys):
    assert main([str(fixture_dir), "--chain", "GWorld.Levels[0].Actors", "--paths", "UWorld", "ULevel"]) == 0
    out = capsys.readouterr().out
    assert "[module base]" in out and "offsets: [0x3F3, 0x190, 0x0, 0x98]" in out
    assert "UWorld.PersistentLevel: 0x30" in out and "UWorld.Levels[0]: 0x190 -> 0x0" in out
    assert main([str(fixture_dir), "--chain", "UWorld.Nope"]) == 1
    assert "no member" in capsys.readouterr().err
    assert main([str(fixture_dir), "--paths", "Nope", "ULevel"]) == 1
