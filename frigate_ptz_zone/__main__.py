"""Keep a Frigate zone on the ground while a PTZ camera pans and tilts."""
from __future__ import annotations

import argparse
import logging
import sys

from . import service
from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="frigate_ptz_zone", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="follow the cameras and serve the zone editor (default)")
    add = sub.add_parser("add", help="record the zone as drawn in Frigate now as a keyframe")
    add.add_argument("camera")
    add.add_argument("label", nargs="*")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "add":
        service.add_keyframe(settings, args.camera, " ".join(args.label))
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        service.run(settings)


if __name__ == "__main__":
    main()
