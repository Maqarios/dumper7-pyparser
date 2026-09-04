import pytest

from dumper7_pyparser import Dump, Function, Member, TypeKind, TypeRef, load_dump
from dumper7_pyparser.__main__ import main


def test_inherited_member_lookup(dump: Dump):
    pawn = dump.classes.APawn
    assert pawn.RootComponent.offset == 400
    assert pawn.RootComponent.owner == "AActor"
    assert "RootComponent" not in pawn.own_members
    assert "RootComponent" in pawn.members and "RootComponent" in pawn
    assert pawn["Outer"].owner == "UObject"
    assert pawn.find_member("Outer") == (dump.classes.UObject.Outer, dump.classes.UObject)
    assert pawn.find_member("Nope") is None


def test_most_derived_wins(dump: Dump):
    pawn = dump.classes.APawn
    assert pawn.Flags.owner == "APawn" and pawn.Flags.offset == 576
    assert dump.classes.AActor.Flags.owner == "UObject"


def test_parent_and_ancestors(dump: Dump):
    pawn = dump.classes.APawn
    assert pawn.parent is dump.classes.AActor
    assert pawn.parent_name == "AActor"
    assert [a.name for a in pawn.ancestors()] == ["AActor", "UObject"]
    assert dump.classes.UObject.parent is None
    assert pawn.is_subclass_of("UObject") and not pawn.is_subclass_of("UWorld")
    # struct inheritance resolves through the structs namespace too
    tick = dump.structs.FActorTickFunction
    assert tick.parent is dump.structs.FTickFunction
    assert tick.bCanEverTick.is_bitfield


def test_missing_member_raises_attribute_error(dump: Dump):
    with pytest.raises(AttributeError):
        dump.classes.UWorld.Nope
    with pytest.raises(KeyError):
        dump.classes.UWorld["Nope"]
    assert not hasattr(dump.classes.UWorld, "Nope")
    with pytest.raises(AttributeError):
        dump.classes.Nope


def test_functions_attached_to_classes(dump: Dump):
    world = dump.classes.UWorld
    assert world.functions.GetGameState is dump.functions.UWorld.GetGameState
    assert isinstance(world.GetGameState, Function)
    assert isinstance(world["GetLevels"], Function)
    assert "GetGameState" in world
    assert len(dump.classes.ULevel.functions) == 0


def test_inherited_functions(dump: Dump):
    pawn = dump.classes.APawn
    assert len(pawn.functions) == 0  # own functions only
    assert list(pawn.all_functions) == ["K2_SetActorLocation", "K2_DestroyActor"]
    assert pawn.K2_DestroyActor is dump.functions.AActor.K2_DestroyActor
    assert pawn["K2_DestroyActor"].owner == "AActor"
    assert "K2_DestroyActor" in pawn
    assert pawn.find_function("K2_DestroyActor") == (dump.functions.AActor.K2_DestroyActor, dump.classes.AActor)
    assert pawn.find_function("Nope") is None
    assert dump.classes.UObject.all_functions is dump.classes.UObject.functions


def test_dir_completion(dump: Dump):
    names = dir(dump.classes.APawn)
    assert {"RootComponent", "Controller", "K2_DestroyActor", "members", "parent"} <= set(names)


def test_package_prefixed_struct_names(dump: Dump):
    with pytest.raises(AttributeError, match="ambiguous"):
        dump.structs.FHitResult
    assert dump.structs["Engine::FHitResult"].size == 136
    assert dump.structs.FVector is dump.structs["FVector"]


def test_resolve(dump: Dump):
    m = dump.resolve("APawn::RootComponent")
    assert isinstance(m, Member) and m.owner == "AActor"
    f = dump.resolve("APawn::K2_DestroyActor")  # inherited function
    assert isinstance(f, Function) and f.owner == "AActor"
    assert dump.resolve("Engine::FHitResult::Location").offset == 16
    for bad in ("NoSeparator", "Nope::X", "UWorld::Nope", "::X"):
        with pytest.raises(KeyError):
            dump.resolve(bad)


def test_find_and_type_of(dump: Dump):
    assert dump.find("UWorld") is dump.classes.UWorld
    assert dump.find("FVector") is dump.structs.FVector
    assert dump.find("EWorldType") is dump.enums.EWorldType
    assert dump.find("Nope") is None
    assert dump.type_of(dump.classes.UWorld.PersistentLevel.type) is dump.classes.ULevel
    assert dump.type_of(dump.classes.UWorld.WorldType.type) is dump.enums.EWorldType
    assert dump.type_of(dump.classes.AActor.PrimaryActorTick.type) is dump.structs.FActorTickFunction
    assert dump.type_of(dump.classes.UWorld.ActorMap.type) is None  # TArray/TMap are not in the dump
    assert dump.type_of(TypeRef("int32")) is None
    assert dump.type_of(dump.classes.UWorld.PersistentLevel.type.sub_types or dump.classes.UWorld.Levels.type.sub_types[0]) is dump.classes.ULevel


def test_repr(dump: Dump):
    text = repr(dump)
    assert "classes=5" in text and "offsets=7" in text
    assert repr(dump.classes.APawn) == "<class APawn : AActor size=0x258 members=3>"
    assert repr(dump.structs.FVector).startswith("<struct FVector size=0xC")


def test_cli(fixture_dir, capsys):
    assert main([str(fixture_dir), "APawn::RootComponent", "UWorld::GetGameState"]) == 0
    out = capsys.readouterr().out
    assert "0x190" in out and "declared in AActor" in out and "GetGameState" in out
    assert main([str(fixture_dir), "UWorld::Nope"]) == 1
    assert "Nope" in capsys.readouterr().err
