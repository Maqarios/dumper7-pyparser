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

### Pointer chains

`dumper7_pyparser.chains` is an opt-in query layer that answers "how do I reach this member in
memory" using only the dump. It never reads memory: a chain is data plus a rendered string, and you
walk it with your own reader.

```python
from dumper7_pyparser.chains import chain, find_paths

c = chain(dump, "GWorld.PersistentLevel.Actors[0]")   # values below are from a real Mordhau dump
c.offsets            # [0x59200F8, 0x30, 0x98, 0x0]   module-relative, Cheat-Engine style
c.base               # 'module'  ('object' when rooted at a class, e.g. "UWorld.PersistentLevel")
c.result_type        # TypeRef('AActor*')
print(c.render())
# GWorld.PersistentLevel.Actors[0]
#   [module base]
#   +0x59200F8 GWorld : UWorld*  -> deref
#   +0x30     PersistentLevel : ULevel*  -> deref
#   +0x98     Actors : TArray<AActor*>  -> read Data pointer
#   +0x0      [0] : AActor*  (index 0 x 8)  -> deref
#   offsets: [0x59200F8, 0x30, 0x98, 0x0]

for route in find_paths(dump, "GWorld", "APlayerController"):
    print(route)     # e.g. GWorld.OwningGameInstance.LocalPlayers[0].PlayerController: 0x59200F8 -> 0x180 -> 0x38 -> 0x0 -> 0x30  (APlayerController*)
```

Offsets contract: `addr = base; for off in offsets[:-1]: addr = read_pointer(addr + off); result = addr + offsets[-1]`.
`base` is the game module's image base for `module` chains and an instance address for `object`
chains. The result is the address *of* the final member; if `result_is_pointer` read it once more.
Embedded structs are folded into a single offset.

- Roots: a class/struct, `Owner::Member`, or an `OffsetsInfo.json` global (`GWorld`, `GObjects`,
  `GNames`, or any raw `OFFSET_*` key). Only `GWorld` is a pointer the chain can descend through.
- Index steps: fixed C arrays and `TArray` (data pointer at +0, stride from the dump or a small
  primitive table; `pointer_size=` defaults to 8). `TMap`, `TSet` and smart pointers raise `ChainError`.
- `find_paths(dump, src, dst, max_depth=3, include_subclasses=True, limit=50)` does a breadth-first
  search over member types and returns runnable chains, using `[0]` for array elements.

```bash
python -m dumper7_pyparser path/to/Dumpspace --chain "GWorld.Levels[0].Actors" --paths GWorld APlayerController
```

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
