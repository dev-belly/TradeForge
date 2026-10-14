"""Replay a saved public L2 episode offline through TradeForge's matching core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradeforge.application.public_depth import run_public_depth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/public-depth"))
    args = parser.parse_args()
    print(json.dumps(run_public_depth(args.capture, args.out), indent=2))


if __name__ == "__main__":
    main()
