"""Run the Syn Bank Coverage Desk dashboard.

::

    python -m src.syn_wallet.serve                # http://127.0.0.1:8000
    python -m src.syn_wallet.serve --port 9000
    python -m src.syn_wallet.serve --demo         # never call the AI, even with a key

Loads every published table once at startup, so pages render from memory.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import config as paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Syn Bank dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--processed-dir", type=Path, default=paths.PROCESSED_DIR)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Force deterministic answers: never call the AI even if a key is set.",
    )
    parser.add_argument("--reload", action="store_true", help="Reload on code changes.")
    args = parser.parse_args()

    if args.demo:
        # An explicit flag, not "unset the key": the copilot loads .env at
        # import time, so anything popped here would simply be put back. The
        # copilot already handles being unavailable by answering
        # deterministically and labelling the answer as a demo.
        os.environ["SYN_COPILOT_DEMO"] = "1"

    import uvicorn

    from .api.app import create_app

    application = create_app(args.processed_dir)
    print(f"\n  Syn Bank Coverage Desk  ->  http://{args.host}:{args.port}\n")
    uvicorn.run(application, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
