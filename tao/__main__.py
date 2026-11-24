"""Capture and replay observations of public Web pages."""

import argparse
import json
from pathlib import Path

from . import __version__
from .replay import MAX_SNAPSHOT_BYTES, replay


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tao", description=__doc__)
    parser.add_argument("--version", action="version", version=f"tao {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    replay_command = subcommands.add_parser("replay", help="emit an observation as JSON")
    replay_command.add_argument("snapshot", type=Path, help="local UTF-8 snapshot JSON file")
    capture_command = subcommands.add_parser("capture", help="capture one locally reviewed target")
    capture_command.add_argument("target_id")
    capture_command.add_argument("--manifest", type=Path, required=True)
    capture_command.add_argument("--region", required=True, help="observation location, e.g. JP or US")
    capture_command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "capture":
            from .capture import save_capture

            save_capture(args.target_id, args.manifest, args.region, args.out)
            return 0
        with args.snapshot.open("rb") as stream:
            snapshot = stream.read(MAX_SNAPSHOT_BYTES + 1)
        result = replay(snapshot)
    except (OSError, ValueError) as error:
        parser.exit(2, f"tao: {error}\n")

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
