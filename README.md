# dumper7-pyparser

Object-style Python access to the JSON files that [Dumper-7](https://github.com/Encryqed/Dumper-7)
writes in its `Dumpspace/` output folder (the same files served by
[dumpspace.spuckwaffel.com](https://dumpspace.spuckwaffel.com)).

Zero runtime dependencies. Python 3.10+.

## Install

```bash
pip install git+https://github.com/Maqarios/dumper7-pyparser
# or, from a checkout:
pip install .
```

## Usage

```python
from dumper7_pyparser import load_dump

dump = load_dump("path/to/Dumpspace")   # plain .json or .json.gz, any subset of the five files

# classes and structs
world = dump.classes.UWorld
world.size                               # 0x800
world.PersistentLevel                    # Member
world.PersistentLevel.offset             # 0x30
hex(world.PersistentLevel)               # '0x30'  (Member supports int()/hex())
str(world.ActorMap.type)                 # 'TMap<FName, AActor*>'
world.PersistentLevel.type.name          # 'ULevel'
world.PersistentLevel.type.is_pointer    # True

dump.structs.FVector.X.offset            # 0

# inheritance is transparent
pawn = dump.classes.APawn
pawn.RootComponent.owner                 # 'AActor'   (declared on a parent)
pawn.own_members                         # only APawn's own fields
pawn.members                             # merged chain, most-derived wins
pawn.parent.name                         # 'AActor'
[a.name for a in pawn.ancestors()]       # ['AActor', 'UObject']
pawn.find_member("Outer")                # (Member, declaring Struct)

# functions (own + inherited via attribute access)
pawn.K2_DestroyActor.offset
dump.functions.AActor.K2_SetActorLocation.signature
# 'bool K2_SetActorLocation(FVector& NewLocation, bool bSweep, FHitResult& SweepHitResult, bool bTeleport)'
dump.functions.AActor.K2_SetActorLocation.flags   # ('Final', 'Native', 'Public', 'BlueprintCallable')

# enums
dump.enums.EWorldType.Game               # 1
dump.enums.EWorldType.name_of(1)         # 'Game'
dump.enums.EWorldType.underlying_type    # 'uint8'

# global offsets
dump.offsets.OFFSET_GWORLD
dump.offsets.OFFSET_GOBJECTS

# lookups
dump.resolve("APawn::RootComponent")     # Member or Function, walks inheritance
dump.find("FVector")                     # class, struct or enum by name
dump.type_of(world.PersistentLevel.type) # the Struct/EnumDef a TypeRef points at

# file metadata
dump.info.classes.updated_at             # datetime (UTC)
dump.info.offsets.credit                 # {'dumper_used': 'Dumper-7', ...}
```

### Getting a dump

Run Dumper-7 with the Dumpspace generator enabled and point `load_dump` at its `Dumpspace/` folder.
Public dumps from dumpspace.spuckwaffel.com can be fetched directly as the same five files:

```
https://raw.githubusercontent.com/Spuckwaffel/dumpspace/main/Games/<Engine>/<Location>/ClassesInfo.json.gz
```

where `<Engine>` and `<Location>` come from that repo's `Games/GameList.json` (e.g. `Unreal-Engine-4/Mordhau`).

### Caveats

- Current Dumper-7 never sets the `&` reference marker on parameters, so `Param.is_reference` is
  `False` for its output. Out-parameters are not distinguishable from the dump alone.
- `TArray`, `TMap`, `TSubclassOf` and other templates have no definition in the dump, so
  `dump.type_of()` returns `None` for them; inspect `TypeRef.sub_types` instead.

### Names that are not valid attributes

Dumper-7 emits `Package::Name` when a name is not unique (e.g. `Engine::FHitResult`).
Attribute access falls back to the unique key with that suffix, so `dump.structs.FVector`
still works when the file says `CoreUObject::FVector`. If the suffix is ambiguous, or the name
clashes with a real attribute such as `size` or `keys`, use item access: `dump.structs["Engine::FHitResult"]`,
`dump.classes.UWorld["size"]`.

### Command line

```bash
python -m dumper7_pyparser path/to/Dumpspace                 # counts + metadata
python -m dumper7_pyparser path/to/Dumpspace UWorld::Levels  # resolve Owner::Name
```

## File format

Each of `ClassesInfo.json`, `StructsInfo.json`, `EnumsInfo.json`, `FunctionsInfo.json`,
`OffsetsInfo.json` is `{"updated_at": "<ms epoch>", "version": 10202, "data": [...]}`.

| File | `data` entry |
|---|---|
| Classes / Structs | `{"Name": [{"__InheritInfo": ["Parent", ...]}, {"__MDKClassSize": n}, {"Member": [type, offset, size, arrayDim, bitOffset?]}, ...]}` |
| Enums | `{"EName": [[{"Value": n}, ...], "uint8"]}` |
| Functions | `{"Owner": [{"Func": [returnType, [[type, "&" or "", "name"], ...], offset, "Flag\|Flag"]}, ...]}` |
| Offsets | `["OFFSET_NAME", n]` |

A `type` is `[name, kind, extended, [subTypes...]]` where `kind` is `D`efault, `S`truct, `C`lass,
`E`num or `F`unction and `extended` is `"*"` for pointers. Template arguments live in `subTypes`.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
