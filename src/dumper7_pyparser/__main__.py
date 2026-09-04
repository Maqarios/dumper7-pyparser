"""``python -m dumper7_pyparser <dir> [Owner::Name ...]`` — quick inspection."""

from __future__ import annotations

import argparse
import sys

from .dump import load_dump
from .models import Function, Member


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dumper7_pyparser", description=__doc__)
    parser.add_argument("directory", help="folder containing the *Info.json files")
    parser.add_argument("queries", nargs="*", help="Owner::Name lookups to resolve")
    parser.add_argument("--strict", action="store_true", help="fail if any of the five files is missing")
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
    return status


if __name__ == "__main__":
    sys.exit(main())
