"""``python -m dumper7_pyparser <dir> [Owner::Name ...] [--chain PATH] [--paths SRC DST]`` — quick inspection."""

from __future__ import annotations

import argparse
import sys

from .chains import ChainError, chain, find_paths
from .dump import load_dump
from .models import Function, Member


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dumper7_pyparser", description=__doc__)
    parser.add_argument("directory", help="folder containing the *Info.json files")
    parser.add_argument("queries", nargs="*", help="Owner::Name lookups to resolve")
    parser.add_argument("--strict", action="store_true", help="fail if any of the five files is missing")
    parser.add_argument("--chain", action="append", default=[], metavar="PATH",
                        help="describe a pointer chain, e.g. GWorld.Levels[0].Actors (repeatable)")
    parser.add_argument("--paths", nargs=2, metavar=("SRC", "DST"),
                        help="discover member routes from a type/global to a type")
    parser.add_argument("--max-depth", type=int, default=3, help="max hops for --paths (default 3)")
    parser.add_argument("--pointer-size", type=int, default=8, help="pointer size in bytes (default 8)")
    args = parser.parse_args(argv)

    dump = load_dump(args.directory, strict=args.strict)
    print(dump)
    for kind, info in dump.info.items():
        stamp = info.updated_at.isoformat() if info.updated_at else "?"
        print(f"  {kind:<9} version={info.version} updated_at={stamp} ({info.path})")

    status = 0
    for query in args.queries:
        try:
            hit = dump.resolve(query)
        except KeyError as exc:
            print(f"{query}: {exc}", file=sys.stderr)
            status = 1
            continue
        if isinstance(hit, Member):
            print(f"{query}: {hit}  [declared in {hit.owner}]")
        elif isinstance(hit, Function):
            print(f"{query}: {hit}  flags={hit.flags_raw}")

    for path in args.chain:
        try:
            print(chain(dump, path, pointer_size=args.pointer_size).render())
        except ChainError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            status = 1

    if args.paths:
        src, dst = args.paths
        try:
            routes = find_paths(dump, src, dst, max_depth=args.max_depth, pointer_size=args.pointer_size)
        except ChainError as exc:
            print(f"{src} -> {dst}: {exc}", file=sys.stderr)
            status = 1
        else:
            print(f"{len(routes)} route(s) from {src} to {dst} (max depth {args.max_depth}):")
            for route in routes:
                print(f"  {route}")
    return status


if __name__ == "__main__":
    sys.exit(main())
