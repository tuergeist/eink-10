"""Tiny CLI to push a PNG to the server. For local testing.

Usage:
    pdm run push path/to/image.png            # passthrough
    pdm run push path/to/image.png --dither   # let server quantize
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib import error, request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--server",
        default=os.environ.get("EINK_SERVER", "http://127.0.0.1:8989"),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("EINK_PUSH_TOKEN", ""),
        help="defaults to $EINK_PUSH_TOKEN",
    )
    parser.add_argument(
        "--dither",
        action="store_true",
        help="apply server-side Floyd-Steinberg quantization to 8 grays",
    )
    args = parser.parse_args(argv)

    if not args.token:
        print("error: EINK_PUSH_TOKEN not set (and --token not given)", file=sys.stderr)
        return 2
    if not args.image.exists():
        print(f"error: {args.image} not found", file=sys.stderr)
        return 2

    url = args.server.rstrip("/") + "/image"
    if args.dither:
        url += "?dither=floyd-steinberg"

    body = args.image.read_bytes()
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {args.token}",
            "Content-Type": "image/png",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            print(f"{resp.status} {resp.reason}")
            print(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        print(f"{e.code} {e.reason}", file=sys.stderr)
        print(e.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except error.URLError as e:
        print(f"connection error: {e.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
