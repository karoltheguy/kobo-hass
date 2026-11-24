"""Command line entry point: `hadash serve` or `hadash render`."""

from __future__ import annotations

import argparse
import logging
import sys

from .app import Dashboard, create_app
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hadash")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--mock", action="store_true", help="use a fake Home Assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    render = sub.add_parser("render", help="write one dashboard PNG and exit")
    render.add_argument("-o", "--output", default="dashboard.png")
    render.add_argument("--pressed", help="draw this button id as pressed")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config(args.config)
    if args.mock:
        config.mock = True

    if args.cmd == "render":
        with open(args.output, "wb") as fh:
            fh.write(Dashboard(config).png(pressed=args.pressed))
        print(args.output)
        return 0

    app = create_app(config)
    host, port = args.host or config.host, args.port or config.port
    print(f"hadash: preview at http://{'localhost' if host == '0.0.0.0' else host}:{port}/",
          file=sys.stderr)
    app.run(host=host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
