"""Offline CLI for replaying a saved public-page snapshot."""

import argparse
import json
from pathlib import Path

from . import __version__
from .replay import MAX_SNAPSHOT_BYTES, replay


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tao", description=__doc__)
    parser.add_argument("--version", action="version", version=f"tao {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("replay", help="emit an observation as JSON")
    command.add_argument("snapshot", type=Path, help="local UTF-8 snapshot JSON file")
    args = parser.parse_args(argv)

    try:
        with args.snapshot.open("rb") as stream:
            snapshot = stream.read(MAX_SNAPSHOT_BYTES + 1)
        result = replay(snapshot)
    except (OSError, ValueError) as error:
        parser.exit(2, f"tao: {error}\n")

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
