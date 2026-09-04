import shutil

import pytest

from dumper7_pyparser import Dump, load_dump
from dumper7_pyparser.__main__ import main
from dumper7_pyparser.chains import Chain, ChainError, chain, element_size, find_paths, parse_path
from dumper7_pyparser.types import TypeRef

GWORLD = 1011  # fixture OFFSET_GWORLD


# -- parse_path --------------------------------------------------------------------


def test_parse_path():
    assert parse_path("UWorld.Levels[3].Actors") == [("UWorld", None), ("Levels", 3), ("Actors", None)]
    assert parse_path("Engine::FHitResult.Location") == [("Engine::FHitResult", None), ("Location", None)]
    for bad in ("", "UWorld.", "UWorld.Levels[x]", "UWorld.Levels[]", "1abc", "A..B"):
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


def test_element_size(dump: Dump):
    assert element_size(dump, TypeRef("AActor", "C", "*")) == 8
    assert element_size(dump, TypeRef("AActor", "C", "*"), pointer_size=4) == 4
    assert element_size(dump, TypeRef("FVector", "S")) == 12
    assert element_size(dump, TypeRef("int32")) == 4
    assert element_size(dump, TypeRef("FString")) == 16
    with pytest.raises(ChainError, match="unknown element size"):
        element_size(dump, TypeRef("FName", "S"))


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
