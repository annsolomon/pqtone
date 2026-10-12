"""`pqt-edge` command line: run the agent, or inspect its outbox."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config
from .outbox import Outbox


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pqt-edge")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the agent")
    r.add_argument("--config", required=True)
    o = sub.add_parser("outbox", help="outbox statistics (read-only)")
    o.add_argument("--path", required=True)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    if a.cmd == "outbox":
        print(json.dumps(Outbox(a.path).stats(), indent=2))
        return 0
    from .agent import Agent

    Agent(config.load(a.config)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
